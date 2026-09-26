"""
Authentication: real user accounts (email + password) issuing signed JWTs.

Accounts live in users.py (signup/login/verification) and prove identity
via a stateless bearer token, so there's no server-side session table: the
signed token itself is the proof, checked here on every request.

- The Authorization: Bearer <jwt> header is decoded and verified against
  CONSULTCASTAI_JWT_SECRET (see users.decode_token).
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

from fastapi import Header, HTTPException

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

    return AuthUser(rep_id=payload["sub"], email=payload["email"], is_admin=payload.get("is_admin", False))


def require_owner(session_rep_id: str, user: AuthUser) -> None:
    """A rep may only touch their own sessions; admins bypass."""
    if user.is_admin:
        return
    if session_rep_id != user.rep_id:
        raise HTTPException(status_code=403, detail="Not your session")
