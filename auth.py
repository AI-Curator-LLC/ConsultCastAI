"""
Authentication: real user accounts (email + password) issuing signed JWTs.

Accounts live in users.py (signup/login/verification/password reset) and
prove identity via a signed bearer token, so there's no server-side session
table. The token is also checked against its account on every request
(one primary-key lookup) so a password reset can revoke all existing logins.

- The Authorization: Bearer <jwt> header is decoded and verified against
  CONSULTCASTAI_JWT_SECRET (see users.decode_token), then matched to the
  live account's token_version.
- If that secret is missing in production, auth FAILS CLOSED (500), never
  admits everyone.
- A local-dev bypass exists for zero-setup local testing. It is DELIBERATELY
  independent of which storage backend is active (CONSULTCASTAI_DEV_AUTH_BYPASS,
  see _dev_bypass_enabled below): storage backend and "should auth be
  enforced" are separate questions. Defaults to matching local-store mode
  when unset, so the zero-setup local dev experience is unchanged unless
  you opt in.

Every route depends on AuthUser, not on how it was produced, so swapping
auth mechanisms again later only touches verify_user's body.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException

import minutes
import store
import teams
import sso_sessions
import suite_sso
import users

_DEV_AUTH_BYPASS_ENV = "CONSULTCASTAI_DEV_AUTH_BYPASS"  # "1"/"0", overrides the storage-based default


@dataclass
class AuthUser:
    rep_id: str
    email: str
    is_admin: bool


def _dev_bypass_enabled() -> bool:
    """Explicit CONSULTCASTAI_DEV_AUTH_BYPASS wins if set ("1" or "0").
    Otherwise falls back to matching local-store mode, preserving the
    original zero-setup local dev behavior for anyone who's never heard of
    this flag."""
    raw = os.environ.get(_DEV_AUTH_BYPASS_ENV)
    if raw is not None:
        return raw == "1"
    return store.using_local_store()


def is_dev_bypass() -> bool:
    return _dev_bypass_enabled()


def verify_user(authorization: str | None = Header(default=None)) -> AuthUser:
    """FastAPI dependency: returns the verified caller, or raises 401/500."""
    if _dev_bypass_enabled():
        return AuthUser(rep_id="dev", email="dev@localhost", is_admin=True)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    try:
        payload = users.decode_token(token)
    except RuntimeError as exc:
        print(f"[consultcastai] auth FAIL-CLOSED: {exc} -> refusing with 500.")
        raise HTTPException(status_code=500, detail="Access control is not configured")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")

    # A signature-valid token still has to match the live account: this is
    # what makes a password reset (which bumps token_version) log out every
    # existing session, and makes a deleted account or a revoked admin flag
    # take effect immediately instead of after the token's 30 days. Tokens
    # issued before token_version existed carry no "tv" and count as 0.
    account = users.get_user_by_id(payload["sub"])
    if not account or account.token_version != payload.get("tv", 0):
        raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")

    # Single sign-on (sso_sessions.py). While the suite's switch is on, only
    # a login that came through the suite counts, and it is rechecked with
    # the suite now and then. While it is off the app's own logins work as
    # always, and a suite login still inside its 7 days keeps working too.
    sso_on = suite_sso.enabled()
    if payload.get("sso"):
        if not sso_sessions.alive(payload.get("jti"), account.id, ask_suite=sso_on):
            raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")
    elif sso_on:
        raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")

    return AuthUser(rep_id=account.id, email=account.email, is_admin=account.is_admin)


def require_owner(session_rep_id: str, user: AuthUser) -> None:
    """A rep may only touch their own sessions; admins bypass."""
    if user.is_admin:
        return
    if session_rep_id != user.rep_id:
        raise HTTPException(status_code=403, detail="Not your session")


def require_approved(user: AuthUser = Depends(verify_user)) -> AuthUser:
    """Layered on top of verify_user for every functional (product)
    endpoint. Two ways a real, signed-in account is refused here, each with
    its own code so the frontend can show the right screen instead of
    "please log in again":

    - verification_required: not approved yet. Approval is automatic, set
      when the email is verified (users.mark_verified), so this means "click
      the link we sent you", not "wait for someone".
    - account_suspended: an admin suspended it. Checked first, and separate
      from approval, so verifying or subscribing can't lift it.

    Not applied to /auth/me, /auth/verify, /auth/resend-verification, the
    password-reset endpoints, or export/delete: an unverified or suspended
    account still needs those."""
    if _dev_bypass_enabled():
        return user  # nothing to approve in local dev bypass
    account = users.get_user_by_id(user.rep_id)
    if account and account.suspended:
        raise HTTPException(status_code=403, detail={
            "code": "account_suspended",
            "message": "This account has been suspended. Contact support if you think this is a mistake.",
        })
    if not account or not account.approved:
        raise HTTPException(status_code=403, detail={
            "code": "verification_required",
            "message": "Verify your email to start using ConsultCastAI.",
        })
    return user


def require_admin(user: AuthUser = Depends(verify_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_verified_for_first_session(account: users.User) -> None:
    """Email has to be verified before an account's first session. Checked
    against whether it has any session yet rather than applied flatly, so an
    account that was already practicing before this shipped isn't locked out
    by it. 403, not 402: nothing to pay for, just a link to click."""
    if account.email_verified:
        return
    if store.list_for_rep(account.id):
        return
    raise HTTPException(status_code=403, detail={
        "code": "email_unverified",
        "message": "Verify your email before your first session. Check your inbox for the link, or use \"resend email\" to get a new one.",
    })


def require_active_plan(user: AuthUser = Depends(require_approved)) -> AuthUser:
    """Stacked on top of require_approved (chained via Depends above, so a
    still-pending account gets that 403 first) for the one action that
    actually costs money per use: starting a new practice session. Not
    applied anywhere else — continuing an already-started session (/turn,
    /end) doesn't cost anything further per the pricing model.

    Admins bypass entirely (no subscription to check), matching
    require_owner's existing admin-bypass convention above — otherwise the
    moment this shipped, every admin account (including whichever one
    bootstrapped the approval system) would itself fail this check, having
    no subscription at all, the same class of self-lockout mistake flagged
    on earlier features.

    Four kinds of account get through:
    - a team member, riding entirely on the team's subscription (their own
      account has none), checked via account.team_id;
    - an individual with an active subscription;
    - an individual whose AI Curator Consulting Suite plan covers this app
      (minutes.suite_pro), treated as Pro;
    - a trial: approved, never subscribed (see minutes.is_trial), with trial
      minutes left. A former subscriber is not a trial and gets the
      "subscribe" message instead.

    A paid account also needs minutes left: this month's, or extra minutes
    it has bought (minutes.paid_status; switched off, that check is
    skipped and a paid plan is not limited at all). Minutes are the only
    limit: the number of sessions started in a month is still recorded
    (main.start_session) but nothing is refused because of it."""
    if _dev_bypass_enabled() or user.is_admin:
        return user
    account = users.get_user_by_id(user.rep_id)
    if not account:
        raise HTTPException(status_code=402, detail="Your subscription isn't active. Please subscribe or update your billing.")

    _require_verified_for_first_session(account)

    if account.team_id:
        team = teams.get_team(account.team_id)
        if not team or not teams.team_subscription_active(team):
            raise HTTPException(status_code=402, detail="Your team's subscription isn't active. Contact your team owner.")
    elif users.subscription_active(account) or minutes.suite_pro(account):
        pass  # Pro, by its own subscription or through the suite's plan
    elif minutes.is_trial(account):
        status = minutes.trial_status(account)
        if status["exhausted"]:
            message = (
                "This email has already used its free trial. Upgrade to Pro to keep practicing."
                if status["previously_used"] else
                f"You've used your {minutes.TRIAL_TOTAL_SEC // 60} trial minutes. Upgrade to Pro to keep practicing."
            )
            raise HTTPException(status_code=402, detail={"code": "trial_exhausted", "message": message})
        return user  # the trial has its own minutes, checked just above
    else:
        raise HTTPException(status_code=402, detail="Your subscription isn't active. Please subscribe or update your billing.")

    paid = minutes.paid_status(account)
    if paid and paid["exhausted"]:
        raise HTTPException(status_code=402, detail={"code": "minutes_exhausted", "message": minutes_exhausted_message(paid)})
    return user


def minutes_exhausted_message(paid: dict) -> str:
    """The sentence a paid account gets when its minutes are gone: whose
    minutes, when they come back, and what can be done about it now."""
    resets = datetime.fromisoformat(paid["resets_on"])
    when = f"{resets:%B} {resets.day}"
    included = f"{paid['included_sec'] // 60:,}"
    if paid["plan"] == "team":
        message = f"Your team has used this month's {included} minutes. They reset on {when}."
    else:
        message = f"You've used this month's {included} minutes. They reset on {when}."
    if paid["can_buy"]:
        message += f" You can add {paid['topup_block_min']} minutes for {paid['topup_price_label']}."
    elif paid["is_team_member"] and paid["topup_available"]:
        message += " Ask your team owner to add minutes."
    return message
