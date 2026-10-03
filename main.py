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
import uuid
from datetime import datetime, timezone

import stripe
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from fastapi.responses import RedirectResponse

import auth
import claude_client
import anam_client
import content
import coaching
import disposable
import emailer
import minutes
import retention
import store
import teams
import users
from models import (
    SignupRequest,
    LoginRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SessionRecord,
    ConversationTurn,
    StartSessionRequest,
    StartSessionResponse,
    TurnRequest,
    TurnResponse,
    EndSessionResponse,
    AvatarTokenRequest,
    AvatarTokenResponse,
    UpdateProfileRequest,
    ChangePasswordRequest,
    CheckoutRequest,
    InviteRequest,
    DeleteAccountRequest,
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
BUILD_MARKER = "auto-approve-v1"
print(f"[consultcastai] BUILD MARKER: {BUILD_MARKER}")

# Never prints the key itself, just whether one's configured and which
# sender it'll use, so "is verification email even set up?" is answerable
# from the startup log alone instead of guessing from send-time behavior.
print(
    f"[consultcastai] Email: RESEND_API_KEY {'set' if os.environ.get('RESEND_API_KEY', '').strip() else 'NOT SET (verification/reset emails are skipped, links are logged instead)'}; "
    f"from={os.environ.get('CONSULTCASTAI_EMAIL_FROM', 'ConsultCastAI <onboarding@resend.dev> (default: only delivers to the Resend account owner)')}"
)

# Set at import time so a request never silently no-ops with a blank key; if
# it's unset, stripe.api_key ends up "" and Stripe's own client raises a
# clear auth error the first time something actually calls it, rather than
# this module crashing at startup the way `os.environ["STRIPE_SECRET_KEY"]`
# (a hard KeyError) would for every deploy that hasn't set up billing yet.
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
print(f"[consultcastai] Stripe: {'configured' if stripe.api_key else 'NOT SET (billing endpoints will fail closed)'}")

_STRIPE_PLAN_PRICE_IDS = {
    "pro_monthly": os.environ.get("STRIPE_PRICE_PRO_MONTHLY", "").strip(),
    "pro_annual": os.environ.get("STRIPE_PRICE_PRO_ANNUAL", "").strip(),
    "team_monthly": os.environ.get("STRIPE_PRICE_TEAM_MONTHLY", "").strip(),
    "team_annual": os.environ.get("STRIPE_PRICE_TEAM_ANNUAL", "").strip(),
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_ALLOW_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting on auth endpoints (login/signup brute-force, email-sending
# spam) — in-memory, no Redis needed at this scale (a single Render
# instance). Would stop being shared across instances if this ever scales
# out horizontally; not a concern right now.
#
# Render sits behind a reverse proxy: request.client.host is the proxy's
# own internal address for every request, not the real visitor's IP, unless
# X-Forwarded-For is read explicitly. Get this wrong and either everyone
# shares one "IP" (the limiter blocks every user at once after a handful of
# legitimate logins) or it silently limits nothing at all — this is the
# single most common way rate limiting looks fine locally and does nothing
# once deployed.
#
# Which header, though, matters just as much. X-Forwarded-For is a list each
# proxy appends to, and its first entry is whatever the client chose to
# send: a request with a made-up "X-Forwarded-For: 1.2.3.4" arrived here
# with that as the first entry (and as request.client.host, which uvicorn
# derives from the same header), so keying on it let anyone reset their own
# limit on every request. Checked against the live deploy, not assumed.
# CF-Connecting-IP is set by Cloudflare, which fronts every Render service,
# to the address that actually connected to it; a client can't supply its
# own (Cloudflare rejects the request outright). Local dev has no proxy and
# no such header, so it falls back to the socket address.
def get_real_ip(request: Request) -> str:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    return get_remote_address(request)

limiter = Limiter(key_func=get_real_ip)

# Accounts created per IP per rolling 24 hours (see users.record_signup).
# Counts accounts that were actually created, so it's separate from the
# request-rate limit on the signup route, which counts every attempt.
SIGNUPS_PER_IP_PER_DAY = int(os.environ.get("SIGNUPS_PER_IP_PER_DAY", "3"))
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def _start_retention_job() -> None:
    """Data retention cleanup (see retention.py): runs once immediately,
    then every 24 hours, on a background thread in this same process — a
    Render Cron Job can't reach this service's persistent disk (a disk
    mounts to one service only), so this has to live here instead."""
    retention.start_background_job()

_coaching_state: dict[str, coaching.CoachingState] = {}


def _account_for(rep_id: str) -> users.User | None:
    """The real account a session's time is charged to, or None in local dev
    bypass (whose synthetic "dev" user has no row; the session's clock is
    still kept, there's just no ledger to charge). Takes the session
    owner's id rather than the caller: an admin acting on someone else's
    session must not have that time land on the admin's own ledger."""
    if auth.is_dev_bypass():
        return None
    return users.get_user_by_id(rep_id)


def _frontend_url() -> str:
    return os.environ.get("CONSULTCASTAI_FRONTEND_URL", "http://localhost:5500").rstrip("/")


def _issue_or_500(user: users.User) -> str:
    try:
        return users.issue_token(user)
    except RuntimeError as exc:
        print(f"[consultcastai] cannot issue token: {exc}")
        raise HTTPException(status_code=500, detail="Access control is not configured")


@app.post("/auth/signup")
@limiter.limit("10/hour")  # every attempt, including ones that fail validation; accounts actually created are capped separately at SIGNUPS_PER_IP_PER_DAY below
def signup(req: SignupRequest, background_tasks: BackgroundTasks, request: Request):
    email = users.normalize_email(req.email)
    if not users.is_valid_email(email):
        raise HTTPException(400, "Invalid email address")
    # A verified email is what approves an account and what the one-per-
    # email trial hangs on, so a throwaway inbox is refused outright.
    if disposable.is_disposable(email):
        raise HTTPException(400, "Please use a permanent email address. Disposable email services aren't accepted.")
    problem = users.password_problem(req.password)
    if problem:
        raise HTTPException(400, problem)
    if users.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")

    # Accepting a team invite: validate it BEFORE creating the account, so a
    # bad/stale/reused token never leaves an orphaned user row behind.
    invite = None
    if req.invite_token:
        invite = teams.get_invite_by_token(req.invite_token)
        if not invite or invite.used or invite.email != email:
            raise HTTPException(400, "Invalid or mismatched invite")
        team = teams.get_team(invite.team_id)
        if not team:
            raise HTTPException(400, "This team no longer exists")
        # Re-checked here, not just at invite-creation time: several invites
        # can be outstanding at once, and accepting all of them shouldn't be
        # able to push a team past its own seat_limit.
        if users.count_team_members(team.id) >= team.seat_limit:
            raise HTTPException(400, f"This team is at its {team.seat_limit}-seat limit")

    # Accounts per IP per day. Checked last, right before creating one, so
    # only a signup that would otherwise have succeeded counts against it.
    # Joining a team by invite is exempt: a whole team signing up from one
    # office shares an address, and every one of them was invited by a
    # paying owner.
    client_ip = get_real_ip(request)
    if not invite and users.count_recent_signups(client_ip) >= SIGNUPS_PER_IP_PER_DAY:
        raise HTTPException(429, "Too many accounts have been created from this network today. Please try again tomorrow.")

    verification_token = secrets.token_urlsafe(32)
    try:
        user = users.create_user(email, users.hash_password(req.password), verification_token)
    except users.EmailTaken:
        raise HTTPException(409, "An account with this email already exists")
    if not invite:
        users.record_signup(client_ip)
    # This email's last account was suspended when it was deleted: the new
    # one starts suspended too (see users.remember_suspended_email).
    if users.email_was_suspended(email):
        users.set_suspended(user.id, True)

    if invite:
        # Joining an already-subscribed team: no Checkout, no separate
        # approval step, no admin notification (there's nothing to review).
        users.set_team(user.id, invite.team_id)
        users.set_approved(user.id, True)
        teams.mark_invite_used(invite.token)
        user = users.get_user_by_id(user.id)
    else:
        # Covers the bootstrap admin signing up for the first time (or
        # re-signing up after a deleted account) — the schema-migration half
        # of this safety net (users._ensure_schema) only ever reaches rows
        # that already existed before this shipped, not a fresh signup
        # afterward.
        if users.promote_if_admin_bootstrap(user.id, user.email):
            user = users.get_user_by_id(user.id)
        # Heads-up to whoever's set as CONSULTCASTAI_ADMIN_EMAIL, not a gate
        # on anything — skipped for an invite acceptance, which is already
        # approved and needs no review.
        background_tasks.add_task(emailer.send_admin_notification_email, user.email)

    # Email verification is independent of approval/billing either way, so
    # this always sends, and both swallow their own errors — a failed send
    # can never block signup.
    background_tasks.add_task(emailer.send_verification_email, user.email, verification_token)

    # The JWT still issues (keeps the session model consistent — a pending
    # account is logged in, just not able to use anything functional yet),
    # and "approved" rides along so the frontend can show the pending
    # screen immediately, without waiting for some other call to 403.
    return {"token": _issue_or_500(user), "email": user.email, "email_verified": False, "approved": user.approved}


@app.post("/auth/login")
@limiter.limit("5/minute")
def login(req: LoginRequest, request: Request):
    user = users.get_user_by_email(req.email)
    if not user:
        users.burn_password_check(req.password)  # same response time whether or not the email exists
        raise HTTPException(401, "Incorrect email or password")
    if not users.verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    # Login succeeds regardless of approval status — require_approved is
    # what actually blocks a pending account from doing anything, this just
    # needs to hand back enough for the frontend to show that clearly
    # rather than dropping a pending account into the main app.
    return {"token": _issue_or_500(user), "email": user.email, "email_verified": user.email_verified, "approved": user.approved}


@app.get("/auth/verify")
def verify_email(token: str):
    user = users.get_user_by_verification_token(token)
    if not user:
        return RedirectResponse(f"{_frontend_url()}/?verified=invalid")
    users.mark_verified(user.id)
    return RedirectResponse(f"{_frontend_url()}/?verified=1")


@app.post("/auth/forgot-password")
@limiter.limit("3/hour")
def forgot_password(req: ForgotPasswordRequest, background_tasks: BackgroundTasks, request: Request):
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


@app.post("/auth/resend-verification")
@limiter.limit("3/hour")
def resend_verification(background_tasks: BackgroundTasks, request: Request, user: auth.AuthUser = Depends(auth.verify_user)):
    """Re-sends the verification link, for when the original never arrived
    (see emailer.py's docstring on Resend's shared sender). Mints a fresh
    token rather than reusing the one from signup — simpler than depending
    on that one still being set, and it invalidates any earlier link, which
    is the right behavior for a "resend" anyway. Answers the same way
    whether or not the email actually went out; the frontend only shows
    this button pre-verification, so there's no dev_bypass or
    already-verified case for it to handle gracefully."""
    account = users.get_user_by_id(user.rep_id)
    if not account or account.email_verified:
        print(f"[consultcastai] resend-verification no-op for {user.email}: account={'missing' if not account else 'found'}, verified={account.email_verified if account else None}")
        return {"ok": True}
    token = users.create_verification_token(account.id)
    background_tasks.add_task(emailer.send_verification_email, account.email, token)
    return {"ok": True}


@app.get("/auth/me")
def me(user: auth.AuthUser = Depends(auth.verify_user)):
    """Lets the frontend ask "am I logged in?" on load. In local dev-bypass
    mode this succeeds as the dev user, so the login screen is skipped.
    Also backs the Profile panel (created_at/name/company), not just the
    login check, so it carries those even though the name suggests less."""
    if auth.is_dev_bypass():
        return {
            "email": user.email, "email_verified": True, "dev_bypass": True,
            "created_at": None, "name": None, "company": None,
            "is_admin": True, "approved": True, "suspended": False,
            "team_id": None, "is_team_owner": False,
            "trial": None,
        }
    account = users.get_user_by_id(user.rep_id)
    if not account:
        raise HTTPException(401, "Account no longer exists, please log in again")
    is_team_owner = False
    if account.team_id:
        team = teams.get_team(account.team_id)
        is_team_owner = bool(team and team.owner_user_id == account.id)
    return {
        "email": account.email, "email_verified": account.email_verified, "dev_bypass": False,
        "created_at": account.created_at, "name": account.name, "company": account.company,
        "is_admin": account.is_admin, "approved": account.approved,
        "suspended": account.suspended,
        "team_id": account.team_id, "is_team_owner": is_team_owner,
        # None unless this is a trial account; drives the "N of 30 trial
        # minutes left" counter and the upgrade prompt.
        "trial": minutes.trial_status(account),
    }


@app.get("/auth/pending")
def list_pending_requests(user: auth.AuthUser = Depends(auth.require_admin)):
    pending = users.list_pending()
    return [{"id": u.id, "email": u.email, "created_at": u.created_at} for u in pending]


@app.post("/auth/approve/{user_id}")
def approve_user(user_id: str, admin: auth.AuthUser = Depends(auth.require_admin)):
    """Approval is automatic on email verification; this is the override for
    when a verification email never arrives. An admin vouching for the
    address counts as verifying it, so it does exactly what clicking the
    link would have."""
    if not users.get_user_by_id(user_id):
        raise HTTPException(404, "Account not found")
    users.mark_verified(user_id)
    return {"message": "Approved"}


def _plan_label(u: users.User) -> str:
    if u.is_admin:
        return "Admin"
    if u.team_id:
        return "Team"
    if u.subscription_status:
        return f"Pro ({u.subscription_status})"
    return "Trial"


@app.get("/admin/accounts")
def list_accounts(admin: auth.AuthUser = Depends(auth.require_admin)):
    """Every account, newest first, for the admin accounts view: who has
    signed up, whether they've verified, and the suspend control."""
    return [
        {
            "id": u.id, "email": u.email, "created_at": u.created_at,
            "email_verified": u.email_verified, "approved": u.approved,
            "suspended": u.suspended, "is_admin": u.is_admin,
            "plan": _plan_label(u),
            "practice_minutes": store.get_total_seconds(u.id) // 60,
        }
        for u in users.list_all()
    ]


@app.post("/admin/accounts/{user_id}/suspend")
def suspend_account(user_id: str, admin: auth.AuthUser = Depends(auth.require_admin)):
    """Blocks the account from everything except logging in, exporting its
    data and deleting itself (auth.require_approved). Doesn't touch billing:
    a suspended subscriber keeps being charged until the subscription is
    canceled in Stripe."""
    target = users.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Account not found")
    if target.is_admin:
        raise HTTPException(400, "Admin accounts can't be suspended")
    users.set_suspended(user_id, True)
    return {"message": "Suspended"}


@app.post("/admin/accounts/{user_id}/unsuspend")
def unsuspend_account(user_id: str, admin: auth.AuthUser = Depends(auth.require_admin)):
    target = users.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Account not found")
    users.set_suspended(user_id, False)
    users.forget_suspended_email(target.email)  # in case this account inherited it from a deleted one
    return {"message": "Unsuspended"}


@app.patch("/auth/profile")
def update_profile(req: UpdateProfileRequest, user: auth.AuthUser = Depends(auth.require_approved)):
    users.update_profile(user.rep_id, req.name, req.company)
    return {"message": "Profile updated"}


@app.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, user: auth.AuthUser = Depends(auth.require_approved)):
    account = users.get_user_by_id(user.rep_id)
    if not account or not users.verify_password(req.current_password, account.password_hash):
        raise HTTPException(401, "Current password is incorrect")
    problem = users.password_problem(req.new_password)
    if problem:
        raise HTTPException(400, problem)
    users.update_password_hash(user.rep_id, users.hash_password(req.new_password))
    return {"message": "Password changed"}


@app.get("/version")
def version():
    """Which build is running, so "has the deploy landed?" can be answered
    from outside without reading the server log. Nothing but the marker."""
    return {"build": BUILD_MARKER}


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
            "trait_tags": p.trait_tags,
            "coaching_tips": p.coaching_tips,
            "difficulty": p.difficulty,
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


# --- Billing (Stripe subscriptions) -----------------------------------
# Real recurring billing (mode="subscription"), not a one-time payment:
# renews automatically until canceled through Stripe's own Customer Portal.
# Access is valid through current_period_end even after cancellation (see
# auth.require_active_plan) — standard SaaS behavior, no partial refund for
# time remaining.

@app.post("/billing/create-checkout-session")
def create_checkout_session(req: CheckoutRequest, user: auth.AuthUser = Depends(auth.verify_user)):
    """Deliberately Depends(auth.verify_user), not require_approved: a
    pending (unapproved, unsubscribed) account has to be able to reach
    checkout in the first place — subscribing is one of the two ways an
    account becomes approved (the other being manual admin approval), see
    the webhook below. Gating this on require_approved would make that
    path unreachable. (Approval is otherwise automatic on email
    verification; subscribing first still works and still approves.)"""
    if not stripe.api_key:
        raise HTTPException(500, "Billing is not configured")
    # Subscribing doesn't lift a suspension, so don't take the money.
    payer = None if auth.is_dev_bypass() else users.get_user_by_id(user.rep_id)
    if payer and payer.suspended:
        raise HTTPException(status_code=403, detail={
            "code": "account_suspended",
            "message": "This account has been suspended. Contact support if you think this is a mistake.",
        })
    price_id = _STRIPE_PLAN_PRICE_IDS.get(req.plan)
    if not price_id:
        # Also catches a real plan name whose specific STRIPE_PRICE_* env var
        # just isn't set yet, not only a genuinely unrecognized plan string —
        # both look identical from here, and both should refuse rather than
        # call Stripe with an empty price.
        raise HTTPException(400, "Unknown plan")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",  # real recurring billing, not a one-time payment
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{_frontend_url()}/?payment=success",
            cancel_url=f"{_frontend_url()}/?payment=cancelled",
            client_reference_id=user.rep_id,  # ties the subscription back to the account in the webhook
            metadata={"plan": req.plan},
        )
    except stripe.error.StripeError as exc:
        print(f"[consultcastai] checkout session creation failed for {user.email}: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Could not start checkout, please try again")

    return {"checkout_url": session.url}


@app.post("/billing/portal-session")
def create_portal_session(user: auth.AuthUser = Depends(auth.verify_user)):
    """Stripe's own hosted Customer Portal: cancel, update the payment
    method, view invoices. Deliberately NOT a custom in-app cancel flow —
    the portal already handles proration and other edge cases correctly.

    Team-aware: a team's Stripe customer lives on the teams row, not the
    owner's own user row, so an owner resolves through their team instead.
    A regular (non-owner) team member never paid anything and has no
    billing role at all — letting them into the portal would hand them the
    owner's payment method and a cancel button for the whole team, so they
    get a clear 403 instead."""
    if not stripe.api_key:
        raise HTTPException(500, "Billing is not configured")
    account = users.get_user_by_id(user.rep_id)
    if not account:
        raise HTTPException(400, "No billing account on file yet — subscribe first")

    if account.team_id:
        team = teams.get_team(account.team_id)
        if not team or team.owner_user_id != user.rep_id:
            raise HTTPException(403, "Only the team owner can manage billing")
        customer_id = team.stripe_customer_id
    else:
        customer_id = account.stripe_customer_id
    if not customer_id:
        raise HTTPException(400, "No billing account on file yet — subscribe first")

    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=_frontend_url(),
        )
    except stripe.error.StripeError as exc:
        print(f"[consultcastai] portal session creation failed for {user.email}: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Could not open the billing portal, please try again")

    return {"portal_url": portal.url}


@app.post("/team/invite")
def invite_teammate(req: InviteRequest, background_tasks: BackgroundTasks, user: auth.AuthUser = Depends(auth.verify_user)):
    """Owner-only — checked by actually owning the team (team.owner_user_id),
    a separate concept from the app's is_admin superuser flag."""
    account = users.get_user_by_id(user.rep_id)
    if not account or not account.team_id:
        raise HTTPException(403, "Only the team owner can invite")
    team = teams.get_team(account.team_id)
    if not team or team.owner_user_id != user.rep_id:
        raise HTTPException(403, "Only the team owner can invite")

    email = users.normalize_email(req.email)
    if not users.is_valid_email(email):
        raise HTTPException(400, "Invalid email address")
    if disposable.is_disposable(email):  # signup would refuse it anyway; say so now rather than send a dead invite
        raise HTTPException(400, "That's a disposable email address. Invite a permanent one.")
    if users.get_user_by_email(email):
        raise HTTPException(409, "That email already has an account")
    if users.count_team_members(team.id) >= team.seat_limit:
        raise HTTPException(400, f"Team is at its {team.seat_limit}-seat limit")

    invite_token = secrets.token_urlsafe(32)
    teams.create_invite(team.id, email, invite_token)
    # Same as every other email in this app: fired after the response, and
    # _send() itself never raises, so a failed send can't break the invite
    # (the token is already stored — sharing the link manually still works).
    background_tasks.add_task(emailer.send_team_invite_email, email, invite_token)
    return {"message": "Invite sent"}


@app.get("/team/mine")
def get_my_team(user: auth.AuthUser = Depends(auth.verify_user)):
    """Owner-only, matching the spec's Team management view being owner-only
    end to end — a regular member gets nothing to look at here either."""
    account = users.get_user_by_id(user.rep_id)
    if not account or not account.team_id:
        raise HTTPException(403, "Not on a team")
    team = teams.get_team(account.team_id)
    if not team or team.owner_user_id != user.rep_id:
        raise HTTPException(403, "Only the team owner can view this")

    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = store.get_team_usage(team.id, month_key)
    members = users.list_team_members(team.id)
    return {
        "seat_limit": team.seat_limit,
        "seats_used": len(members),
        "subscription_status": team.subscription_status,
        "current_period_end": team.current_period_end,
        "usage_this_month": usage.session_count,
        "monthly_cap": auth.TEAM_MONTHLY_SESSION_CAP,
        "members": [{"email": m.email, "created_at": m.created_at, "is_owner": m.id == team.owner_user_id} for m in members],
    }


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """No AuthUser dependency — Stripe calls this directly, proven
    authentic by the signature below, never by a bearer token. NEVER trust
    an unverified body: anyone could otherwise POST a fake "subscription
    active" event and grant themselves free access.

    Handles the subscription lifecycle, not just the initial purchase:
    checkout.session.completed (created), invoice.payment_succeeded
    (renewed — including recovering from past_due), customer.subscription.deleted
    (canceled, once the paid period actually ends, not immediately), and
    invoice.payment_failed (past_due). Dunning/retries themselves are
    Stripe's built-in smart retries, no custom code needed for that part."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        print("[consultcastai] STRIPE_WEBHOOK_SECRET is not set, refusing the webhook (fail closed, not open)")
        raise HTTPException(status_code=500, detail="Billing is not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        print(f"[consultcastai] webhook signature check failed: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    obj = event["data"]["object"]

    # A malformed/unexpected payload should never 500 back to Stripe (that
    # just triggers pointless retries) or silently grant access with
    # made-up data — every branch below logs and returns 200 instead.
    if event_type == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        plan = (obj.get("metadata") or {}).get("plan")
        subscription_id = obj.get("subscription")
        customer_id = obj.get("customer")
        if not user_id or not plan or not subscription_id:
            print(f"[consultcastai] webhook: checkout.session.completed missing user_id/plan/subscription (plan={plan!r}), ignoring")
            return {"received": True}
        plan_tier = plan.split("_")[0]  # "pro" or "team", from "*_monthly"/"*_annual"

        if plan_tier == "team":
            team_id = str(uuid.uuid4())
            teams.create_team(
                id=team_id,
                owner_user_id=user_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                subscription_status="active",
            )
            users.set_team(user_id, team_id)  # the owner's own account counts as one of the team's seats
            users.set_approved(user_id, True)
        else:
            users.set_subscription(
                user_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                plan_tier=plan_tier,
                status="active",
            )
            users.set_approved(user_id, True)  # subscribing satisfies the approval gate too

    elif event_type == "invoice.payment_succeeded":
        subscription_id = obj.get("subscription")
        lines = (obj.get("lines") or {}).get("data") or []
        period_end_ts = lines[0].get("period", {}).get("end") if lines else None
        if not subscription_id or not period_end_ts:
            print(f"[consultcastai] webhook: invoice.payment_succeeded missing subscription/period end, ignoring")
            return {"received": True}
        period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
        # Renewals fire for a team's subscription too — look up which table
        # this subscription id actually belongs to and update that one.
        if teams.get_team_by_subscription_id(subscription_id):
            teams.update_team_period_end(subscription_id, period_end)
        else:
            users.update_period_end(subscription_id, period_end)

    elif event_type == "customer.subscription.deleted":
        subscription_id = obj.get("id")
        if subscription_id:
            if teams.get_team_by_subscription_id(subscription_id):
                # Every member loses access at current_period_end, not just
                # the owner — they were never individually paying, they were
                # riding on this one subscription.
                teams.set_team_subscription_status(subscription_id, "canceled")
            else:
                users.set_subscription_status(subscription_id, "canceled")

    elif event_type == "invoice.payment_failed":
        subscription_id = obj.get("subscription")
        if subscription_id:
            if teams.get_team_by_subscription_id(subscription_id):
                teams.set_team_subscription_status(subscription_id, "past_due")
            else:
                users.set_subscription_status(subscription_id, "past_due")

    return {"received": True}


@app.post("/sessions", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest, user: auth.AuthUser = Depends(auth.require_active_plan)):
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

    # A trial session gets a time limit: the per-session cap, or whatever is
    # left of the trial if that's less. The clock starts here, after the
    # opener call above, so it runs from when the session actually exists
    # rather than while the opening line is still being generated.
    account = _account_for(user.rep_id)
    now = minutes.now_utc()
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
        time_limit_sec=minutes.session_time_limit(account),
    )
    minutes.begin(session, now)
    store.save(session)
    _coaching_state[session.id] = coaching.CoachingState()
    # Charged the instant a session actually exists, not speculatively before
    # (a request that 404s/500s above never touched this) — see
    # auth.require_active_plan for the cap this counts against. Incremented
    # under the team's own key for a team member, not their personal one —
    # has to match exactly what require_active_plan checked against, or the
    # pooled cap silently stops being pooled.
    if not auth.is_dev_bypass() and not user.is_admin:
        month_key = datetime.now(timezone.utc).strftime('%Y-%m')
        if account and account.team_id:
            store.increment_team_session_count(account.team_id, month_key)
        else:
            store.increment_session_count(user.rep_id, month_key)

    return StartSessionResponse(**session.model_dump(), trial=minutes.trial_status(account))


@app.post("/sessions/{session_id}/turn", response_model=TurnResponse)
def send_turn(session_id: str, req: TurnRequest, user: auth.AuthUser = Depends(auth.require_approved)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)

    # Checked before the Claude call, so a turn that isn't going to count
    # never costs anything either.
    if session.status == "completed":
        raise HTTPException(409, "This session has already ended")
    if minutes.is_paused(session):
        raise HTTPException(409, "This session is paused. Resume it to continue.")
    minutes.ensure_tracking(session, minutes.now_utc())
    if minutes.over_limit(session, minutes.now_utc()):
        raise HTTPException(status_code=402, detail={
            "code": "trial_session_limit",
            "message": "This trial session has reached its time limit.",
        })

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
    # Every turn settles the time used so far, so a session that's never
    # formally ended (tab closed, browser crashed) has still been charged
    # for everything up to its last exchange.
    minutes.settle(session, _account_for(session.rep_id), minutes.now_utc())
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
def end_session(session_id: str, user: auth.AuthUser = Depends(auth.require_approved)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)

    persona = content.get_persona(_persona_id_for(session))
    scenario = content.get_scenario(session.active_scenario_id)

    # Ending twice (a double click, a retry after the response was lost)
    # hands back the debrief already generated instead of paying for a new
    # one, and can't re-open the clock.
    if session.status == "completed" and session.debrief:
        return EndSessionResponse(
            session_id=session.id, debrief=session.debrief, duration_sec=session.duration_sec,
            trial=minutes.trial_status(_account_for(session.rep_id)),
        )

    # The server's own clock (see minutes.py), not an in-memory start time
    # that a restart or redeploy mid-session would have lost.
    now = minutes.now_utc()
    minutes.ensure_tracking(session, now)
    duration_sec = int(minutes.billable_seconds(session, now))

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
    # Closed at the moment End was asked for, not after the debrief came
    # back: the seconds spent generating it aren't practice time. Only
    # reached if the debrief succeeded, so a failed attempt leaves the
    # session open to be ended again.
    account = _account_for(session.rep_id)
    minutes.finish(session, now)
    minutes.settle(session, account, now)
    session.duration_sec = duration_sec
    session.debrief = debrief
    session.status = "completed"
    store.save(session)

    return EndSessionResponse(
        session_id=session.id, debrief=debrief, duration_sec=duration_sec,
        trial=minutes.trial_status(account),
    )


@app.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, user: auth.AuthUser = Depends(auth.require_approved)):
    """Stops the session's clock. The frontend calls this when the
    consultant pauses (or is auto-paused for being idle), and when they
    abandon a session without a debrief, so paused time isn't charged. A
    paused session refuses turns until it's resumed, so pausing can't be
    used to keep practicing off the clock."""
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)
    account = _account_for(session.rep_id)
    now = minutes.now_utc()
    minutes.ensure_tracking(session, now)
    minutes.pause(session, now)
    minutes.settle(session, account, now)
    store.save(session)
    return {"paused": minutes.is_paused(session), "trial": minutes.trial_status(account)}


@app.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, user: auth.AuthUser = Depends(auth.require_approved)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)
    if session.status == "completed":
        raise HTTPException(409, "This session has already ended")
    minutes.resume(session, minutes.now_utc())
    store.save(session)
    return {"paused": minutes.is_paused(session), "trial": minutes.trial_status(_account_for(session.rep_id))}


@app.get("/sessions/mine")
def list_my_sessions(user: auth.AuthUser = Depends(auth.require_approved)):
    """Debrief History: every completed session belonging to the calling
    user, newest first. Filtered server-side by rep_id, so this can never
    return anyone else's sessions regardless of what the client asks for."""
    sessions = store.list_for_rep(user.rep_id)
    completed = [s for s in sessions if s.status == "completed" and s.debrief]
    completed.sort(key=lambda s: s.created_at, reverse=True)
    return [
        {
            "id": s.id,
            "persona_name": s.persona_name,
            "scenario_title": s.scenario_title,
            "created_at": s.created_at,
            "duration_sec": s.duration_sec,
            "debrief": s.debrief,
        }
        for s in completed
    ]


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, user: auth.AuthUser = Depends(auth.verify_user)):
    """User-initiated deletion of one session — deliberately Depends(
    auth.verify_user) only, not require_approved/require_active_plan: a
    lapsed, canceled, or still-pending account must still be able to delete
    its own data (see the Data Retention, Deletion, and Export spec).
    Owner-only via auth.require_owner, the same ownership check /turn and
    /end already use: 404 for a session id that doesn't exist at all, 403
    for one that exists but isn't yours."""
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    auth.require_owner(session.rep_id, user)
    store.delete(session_id)
    return {"message": "Session deleted"}


@app.delete("/me/sessions")
def delete_my_sessions(user: auth.AuthUser = Depends(auth.verify_user)):
    """Deletes every one of the caller's own sessions. Never touches
    usage_records (see store.increment_session_count's docstring) — this is
    plain session deletion, not account deletion, and must not let anyone
    claw back part of their monthly cap by deleting sessions."""
    count = store.delete_for_rep(user.rep_id)
    return {"message": f"Deleted {count} session(s)"}


@app.get("/me/export")
def export_my_data(user: auth.AuthUser = Depends(auth.verify_user)):
    """Everything the caller owns: profile fields, plan status, and every
    one of their sessions (metadata + debrief, plus the transcript unless
    it's already been purged by the retention job). Filtered by rep_id the
    same way /sessions/mine is, so this can never return anyone else's
    data regardless of what the client asks for."""
    if auth.is_dev_bypass():
        account = None
    else:
        account = users.get_user_by_id(user.rep_id)
        if not account:
            raise HTTPException(401, "Account no longer exists, please log in again")

    sessions = store.list_for_rep(user.rep_id)
    sessions.sort(key=lambda s: s.created_at)

    def _session_dict(s: SessionRecord) -> dict:
        return {
            "id": s.id,
            "persona_name": s.persona_name,
            "persona_role": s.persona_role,
            "scenario_title": s.scenario_title,
            "scenario_product": s.scenario_product,
            "call_direction": s.call_direction,
            "status": s.status,
            "created_at": s.created_at,
            "duration_sec": s.duration_sec,
            "pressure": s.pressure,
            "trust": s.trust,
            "specificity": s.specificity,
            "debrief": s.debrief,
            "transcript_purged": s.transcript_purged,
            "transcript": None if s.transcript_purged else [t.model_dump() for t in s.conversation],
        }

    if account is None:
        profile = {"email": user.email, "name": None, "company": None, "created_at": None}
        plan = {"plan_tier": None, "subscription_status": None, "current_period_end": None, "team_id": None}
    else:
        profile = {
            "email": account.email, "name": account.name, "company": account.company,
            "created_at": account.created_at,
        }
        plan = {
            "plan_tier": account.plan_tier, "subscription_status": account.subscription_status,
            "current_period_end": account.current_period_end, "team_id": account.team_id,
        }

    # Whether a "this email has had a trial" record exists (kept as a hash,
    # and kept after account deletion), and when it was made.
    trial_record = users.get_trial_record(account.email) if account else None

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "plan": plan,
        "practice_seconds_total": store.get_total_seconds(user.rep_id),
        "trial": minutes.trial_status(account),
        "trial_record_created_at": trial_record.created_at if trial_record else None,
        "sessions": [_session_dict(s) for s in sessions],
    }


@app.post("/me/delete-account")
def delete_account(req: DeleteAccountRequest, user: auth.AuthUser = Depends(auth.verify_user)):
    """Deletes the account, all of its sessions, and its usage records,
    immediately and irreversibly. Requires the current password (same check
    as /auth/change-password). Refused while a subscription is genuinely
    active — deleting the account out from under a live Stripe subscription
    would keep billing them with no account left to use it. A team owner is
    held to the same rule via the team's own subscription; a regular
    (non-owner) team member is always allowed to leave, which simply frees
    their seat — they were never the one being billed. The Stripe customer
    record itself is deliberately NOT deleted: Stripe keeps billing history
    for tax purposes independent of this account existing."""
    if auth.is_dev_bypass():
        raise HTTPException(400, "Account deletion isn't available in local dev bypass mode")
    account = users.get_user_by_id(user.rep_id)
    if not account or not users.verify_password(req.password, account.password_hash):
        raise HTTPException(401, "Current password is incorrect")

    if account.team_id:
        team = teams.get_team(account.team_id)
        is_owner = bool(team and team.owner_user_id == account.id)
        # Same predicate main.py's other billing checks already use
        # (teams.team_subscription_active) rather than a separate
        # "status == canceled" check, so this stays consistent with every
        # other "is this subscription currently active" decision in the
        # app: refuse while it reads as active, allow once it doesn't.
        if is_owner and team and teams.team_subscription_active(team):
            raise HTTPException(400, "Cancel your team's subscription first via Manage subscription, then you can delete your account")
    elif users.subscription_active(account):
        raise HTTPException(400, "Cancel your subscription first via Manage subscription, then you can delete your account")

    # A trial is per email: before the ledger goes, make sure the fact that
    # this email used one is on record (it normally already is, from the
    # first charge; this covers trial time used before that record existed),
    # then cut the record's link to the account. What survives the deletion
    # is a keyed hash of the email and a date, see users.record_trial_used.
    if minutes.is_trial(account) and store.get_total_seconds(account.id) > 0:
        users.record_trial_used(account)
    users.detach_trial_record(account.id)
    # Same idea for a suspension: deleting the account isn't a way out of it.
    if account.suspended:
        users.remember_suspended_email(account.email)

    store.delete_for_rep(account.id)
    store.delete_usage_records_for_rep(account.id)
    users.delete_user(account.id)
    return {"message": "Account deleted"}


@app.post("/avatar/session-token", response_model=AvatarTokenResponse)
def avatar_session_token(req: AvatarTokenRequest, user: auth.AuthUser = Depends(auth.require_approved)):
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
