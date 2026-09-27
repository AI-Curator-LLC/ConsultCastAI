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

from fastapi import Depends, Header, HTTPException

import store
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

    return AuthUser(rep_id=account.id, email=account.email, is_admin=account.is_admin)


def require_owner(session_rep_id: str, user: AuthUser) -> None:
    """A rep may only touch their own sessions; admins bypass."""
    if user.is_admin:
        return
    if session_rep_id != user.rep_id:
        raise HTTPException(status_code=403, detail="Not your session")


def require_approved(user: AuthUser = Depends(verify_user)) -> AuthUser:
    """Layered on top of verify_user for every functional (product)
    endpoint: a real, signed-in account whose signup hasn't been approved
    yet gets a specific 403 here, distinct from verify_user's 401s, so the
    frontend can show "pending approval" instead of "please log in again".
    Not applied to /auth/me, /auth/verify, /auth/resend-verification, or
    the password-reset endpoints — a pending account still needs those to
    work while it waits."""
    if _dev_bypass_enabled():
        return user  # nothing to approve in local dev bypass
    account = users.get_user_by_id(user.rep_id)
    if not account or not account.approved:
        raise HTTPException(status_code=403, detail="Your access request is still pending approval")
    return user


def require_admin(user: AuthUser = Depends(verify_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
