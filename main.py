"""
ConsultCastAI backend, AI Curator LLC.

AI-powered practice partner for AI consultants: pick a persona and a
scenario, hold a real spoken back-and-forth with a skeptical SMB owner,
get live coaching, get a debrief.

Draft status: solo-founder scale. No SSO, no secrets manager, no cloud
storage required by default, see README.md for the local -> production path.
"""

import os
import secrets
import time

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import auth
import claude_client
import anam_client
import content
import coaching
import emailer
import store
import users
from models import (
    SignupRequest,
    LoginRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SessionRecord,
    ConversationTurn,
    StartSessionRequest,
    TurnRequest,
    TurnResponse,
    EndSessionResponse,
    AvatarTokenRequest,
    AvatarTokenResponse,
)
from prompts import build_system_prompt, build_debrief_prompt, build_opener_prompt

app = FastAPI(title="ConsultCastAI Backend")

# CORS: locked to known frontend origins in production, open to localhost in
# dev. CONSULTCASTAI_ENV=production is the explicit signal, set it on whatever
# host serves this in prod. Since ConsultCastAI is hosted as a page/subpath on
# ai-curator.ai rather than its own domain for now, set
# CONSULTCASTAI_ALLOWED_ORIGINS to that site's origin (e.g.
# "https://www.ai-curator.ai"), not a dedicated ConsultCastAI domain.
_IS_PROD = os.environ.get("CONSULTCASTAI_ENV") == "production"
_PROD_ORIGINS = [
    o.strip() for o in os.environ.get("CONSULTCASTAI_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if _IS_PROD:
    _ALLOWED_ORIGINS = _PROD_ORIGINS
    _ALLOW_ORIGIN_REGEX = None
else:
    _ALLOWED_ORIGINS = ["null"]
    _ALLOW_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"

print(
    f"[consultcastai] CORS mode: {'production' if _IS_PROD else 'local dev'}; "
    f"allowed_origins={_ALLOWED_ORIGINS}; localhost_regex={'on' if _ALLOW_ORIGIN_REGEX else 'off'}"
)

# Bump this string any time prompts.py changes and you need
# to confirm a restart actually picked up the new files, rather than
# guessing. Check the uvicorn startup log for this exact line.
print("[consultcastai] BUILD MARKER: assessment-removed-v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_ALLOW_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)

_coaching_state: dict[str, coaching.CoachingState] = {}
_start_time: dict[str, float] = {}


def _frontend_url() -> str:
    return os.environ.get("CONSULTCASTAI_FRONTEND_URL", "http://localhost:5500").rstrip("/")


def _issue_or_500(user: users.User) -> str:
    try:
        return users.issue_token(user)
    except RuntimeError as exc:
        print(f"[consultcastai] cannot issue token: {exc}")
        raise HTTPException(status_code=500, detail="Access control is not configured")


@app.post("/auth/signup")
def signup(req: SignupRequest, background_tasks: BackgroundTasks):
    email = users.normalize_email(req.email)
    if not users.is_valid_email(email):
        raise HTTPException(400, "Invalid email address")
    problem = users.password_problem(req.password)
    if problem:
        raise HTTPException(400, problem)
    if users.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")

    verification_token = secrets.token_urlsafe(32)
    try:
        user = users.create_user(email, users.hash_password(req.password), verification_token)
    except users.EmailTaken:
        raise HTTPException(409, "An account with this email already exists")

    # Soft requirement: sent after the response, and send_verification_email
    # swallows its own errors, so a failed email can never block signup.
    background_tasks.add_task(emailer.send_verification_email, user.email, verification_token)

    return {"token": _issue_or_500(user), "email": user.email, "email_verified": False}


@app.post("/auth/login")
def login(req: LoginRequest):
    user = users.get_user_by_email(req.email)
    if not user:
        users.burn_password_check(req.password)  # same response time whether or not the email exists
        raise HTTPException(401, "Incorrect email or password")
    if not users.verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return {"token": _issue_or_500(user), "email": user.email, "email_verified": user.email_verified}


@app.get("/auth/verify")
def verify_email(token: str):
    user = users.get_user_by_verification_token(token)
    if not user:
        return RedirectResponse(f"{_frontend_url()}/?verified=invalid")
    users.mark_verified(user.id)
    return RedirectResponse(f"{_frontend_url()}/?verified=1")


@app.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, background_tasks: BackgroundTasks):
    """Answers identically whether or not the email has an account, so this
    form can't be used to find out who's registered. The reset email (if any)
    goes out after the response."""
    generic = {"ok": True}
    email = users.normalize_email(req.email)
    if not users.is_valid_email(email):
        return generic
    user = users.get_user_by_email(email)
    if not user or users.reset_recently_requested(user):
        return generic
    token = users.create_reset_token(user.id)
    background_tasks.add_task(emailer.send_password_reset_email, user.email, token)
    return generic


@app.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    user = users.get_user_by_reset_token(req.token)
    if not user:
        raise HTTPException(400, "This reset link is invalid or has expired")
    problem = users.password_problem(req.password)
    if problem:
        raise HTTPException(400, problem)  # the token is only consumed on success, so they can retry

    users.reset_password(user.id, users.hash_password(req.password))
    fresh = users.get_user_by_id(user.id)  # re-read: token_version just changed
    # Signed in straight away with a token under the new version, every
    # older login for this account is now dead.
    return {"token": _issue_or_500(fresh), "email": fresh.email, "email_verified": True}


@app.get("/auth/me")
def me(user: auth.AuthUser = Depends(auth.verify_user)):
    """Lets the frontend ask "am I logged in?" on load. In local dev-bypass
    mode this succeeds as the dev user, so the login screen is skipped."""
    if auth.is_dev_bypass():
        return {"email": user.email, "email_verified": True, "dev_bypass": True}
    account = users.get_user_by_id(user.rep_id)
    if not account:
        raise HTTPException(401, "Account no longer exists, please log in again")
    return {"email": account.email, "email_verified": account.email_verified, "dev_bypass": False}


@app.get("/personas")
def list_personas():
    """Frontend catalog: personas + their active scenarios."""
    out = []
    for p in content.list_personas():
        scenarios = [s for s in content.list_active_scenarios() if s.persona_id == p.id]
        out.append({
            "id": p.id,
            "name": p.name,
            "role": p.role,
            "context": p.context,
            "traits": p.traits,
            "has_avatar": bool(p.avatar_id and p.voice_id),
            "industry": p.industry,
            "scenarios": [
                {
                    "id": s.id, "title": s.title,
                    "group": s.group, "group_label": content.GROUPS.get(s.group, s.group),
                    "product": s.product, "opener": s.opener, "chips": s.chips,
                    "briefing": s.briefing,
                }
                for s in scenarios
            ],
        })
    return out


@app.post("/sessions", response_model=SessionRecord)
def start_session(req: StartSessionRequest, user: auth.AuthUser = Depends(auth.verify_user)):
    persona = content.get_persona(req.persona_id)
    scenario = content.get_scenario(req.scenario_id)
    if not persona or not scenario:
        raise HTTPException(404, "Persona or scenario not found (or scenario deactivated)")

    call_direction = req.call_direction if req.call_direction in ("inbound", "outbound") else "outbound"

    # Outbound: the consultant placed the call, so they speak first, same as
    # a real one. Conversation starts empty; the persona's first reaction
    # comes through the normal /turn flow once the consultant actually says
    # something, not a pre-generated line.
    #
    # Inbound: the persona placed the call, so they open with a brief
    # greeting (see build_opener_prompt) generated live. scenario.opener is
    # also the safety-net fallback if that Claude call itself fails, so a
    # session can still start.
    conversation: list[ConversationTurn] = []
    if call_direction == "inbound":
        opener_prompt = build_opener_prompt(persona, scenario)
        try:
            opener = claude_client.get_opener(opener_prompt)
        except Exception as exc:
            print(f"[consultcastai] dynamic opener generation failed, falling back to static opener: {type(exc).__name__}: {exc}")
            opener = scenario.opener
        conversation = [ConversationTurn(role="assistant", content=opener)]

    session = SessionRecord(
        rep_id=user.rep_id,
        persona_name=persona.name,
        persona_role=persona.role,
        scenario_title=scenario.title,
        scenario_product=scenario.product,
        voice_tier=req.voice_tier,
        call_direction=call_direction,
        active_scenario_id=scenario.id,
        conversation=conversation,
    )
    store.save(session)
    _coaching_state[session.id] = coaching.CoachingState()
    _start_time[session.id] = time.time()

    return session


@app.post("/sessions/{session_id}/turn", response_model=TurnResponse)
def send_turn(session_id: str, req: TurnRequest, user: auth.AuthUser = Depends(auth.verify_user)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)

    persona = content.get_persona(_persona_id_for(session))
    scenario = content.get_scenario(session.active_scenario_id)
    if not persona or not scenario:
        raise HTTPException(409, "Persona or scenario no longer available")

    state = _coaching_state.setdefault(session_id, coaching.CoachingState())
    first_name = persona.name.split()[0]
    result = coaching.evaluate(req.message, state, first_name)
    _coaching_state[session_id] = result.state

    session.conversation.append(ConversationTurn(role="user", content=req.message))
    system_prompt = build_system_prompt(persona, scenario, session.call_direction)
    history = [{"role": t.role, "content": t.content} for t in session.conversation]
    try:
        reply = claude_client.get_persona_reply(system_prompt, history)
    except Exception as exc:
        session.conversation.pop()  # drop the user turn we just appended, nothing was saved yet
        print(f"[consultcastai] persona reply call failed for session {session.id}: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Persona reply temporarily unavailable")
    session.conversation.append(ConversationTurn(role="assistant", content=reply))

    session.pressure = result.state.pressure
    session.trust = result.state.trust
    session.specificity = result.state.specificity
    store.save(session)

    return TurnResponse(
        persona_reply=reply,
        pressure=result.state.pressure,
        trust=result.state.trust,
        specificity=result.state.specificity,
        coaching_note_kind=result.note_kind,
        coaching_note_text=result.note_text,
    )


@app.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(session_id: str, user: auth.AuthUser = Depends(auth.verify_user)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)

    persona = content.get_persona(_persona_id_for(session))
    scenario = content.get_scenario(session.active_scenario_id)

    started = _start_time.get(session_id, time.time())
    duration_sec = int(time.time() - started)
    session.duration_sec = duration_sec

    transcript_lines = [
        f"{'CONSULTANT' if t.role == 'user' else session.persona_name.upper()}: {t.content}"
        for t in session.conversation
    ]
    debrief_prompt = build_debrief_prompt(
        persona, scenario, transcript_lines,
        session.pressure, session.trust, session.specificity, duration_sec,
    )
    try:
        debrief = claude_client.get_debrief(debrief_prompt)
    except Exception as exc:
        print(f"[consultcastai] debrief call failed for session {session.id}: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Debrief temporarily unavailable")
    session.debrief = debrief
    session.status = "completed"
    store.save(session)

    return EndSessionResponse(session_id=session.id, debrief=debrief, duration_sec=duration_sec)


@app.post("/avatar/session-token", response_model=AvatarTokenResponse)
def avatar_session_token(req: AvatarTokenRequest, user: auth.AuthUser = Depends(auth.verify_user)):
    """Mints a short-lived Anam live-avatar session token. Claude still
    drives every reply through /turn; Anam only renders face + voice."""
    persona = content.get_persona(req.persona_id)
    if persona is None:
        raise HTTPException(404, "Persona not found")
    if not persona.avatar_id or not persona.voice_id:
        raise HTTPException(409, "Persona has no Anam avatar configured yet")
    try:
        token = anam_client.mint_session_token(
            persona.name, persona.avatar_id, persona.voice_id, persona.avatar_model
        )
    except Exception as exc:
        print(f"[consultcastai] anam session-token call failed: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Avatar session temporarily unavailable")
    return AvatarTokenResponse(session_token=token)


def _persona_id_for(session: SessionRecord) -> str:
    for pid, p in content.PERSONAS.items():
        if p.name == session.persona_name:
            return pid
    return ""
