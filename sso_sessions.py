"""
Logins that came through the AI Curator Consulting Suite (single sign-on,
suite_sso.py). One row is one login on one browser.

The app's own login hands out a 30-day JWT, exactly as before. A login
through the suite is the same kind of JWT with two extra claims ("sso" and
a "jti" naming a row here), and it is this row, not the JWT's own expiry,
that decides whether the login is good: SSO_LIFETIME long, tied to the
suite sign-in it came from (`sid`), and renewed in the background. Once a
login has not been checked for SSO_RECHECK, the next request asks the
suite whether that sign-in is still good. Yes renews it; no ends it. A
suite that can't be reached is not a "no": the login simply runs to its
own expiry.

Which kind of JWT is honoured depends on the suite's switch, decided in
auth.verify_user:

  switch ON   only logins with a row here. The app's own JWTs are refused.
  switch OFF  the app's own JWTs, as always; a suite login still inside its
              7 days keeps working too, with no questions asked of the
              suite, so turning the switch off signs nobody out.

The table lives in the users database (users.py creates it with the others).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import suite_sso
import users

SSO_LIFETIME = timedelta(days=7)
SSO_RECHECK = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create(user_id: str, sid: str) -> str:
    """Start a login and return its id (the JWT's "jti"). Also clears out
    every expired login, so the table never needs a cleanup job."""
    jti = str(uuid.uuid4())
    now = _now()
    users._run("DELETE FROM sso_sessions WHERE expires_at < ?", (now.isoformat(),))
    users._run(
        "INSERT INTO sso_sessions (jti, user_id, sid, created_at, last_checked_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (jti, user_id, sid, now.isoformat(), now.isoformat(), (now + SSO_LIFETIME).isoformat()),
    )
    return jti


def alive(jti: str | None, user_id: str, ask_suite: bool) -> bool:
    """Is this suite login still good for this account. With ask_suite (the
    switch is on) it is rechecked with the suite once it has gone
    SSO_RECHECK without a check; without it (the switch is off) an
    unexpired login simply stands."""
    if not jti:
        return False
    row = users._run("SELECT * FROM sso_sessions WHERE jti = ?", (jti,), fetch_one=True)
    if row is None or str(row["user_id"]) != user_id:
        return False
    now = _now()
    if _parse(row["expires_at"]) <= now:
        users._run("DELETE FROM sso_sessions WHERE jti = ?", (jti,))
        return False
    if not ask_suite or now - _parse(row["last_checked_at"]) <= SSO_RECHECK:
        return True
    answer = suite_sso.check(row["sid"])
    if answer is None:
        return True  # couldn't ask: good until its own expiry
    if not answer.get("active"):
        if answer.get("reason") == "sso_off":
            return True  # the switch just went off: the login stands
        users._run("DELETE FROM sso_sessions WHERE jti = ?", (jti,))
        return False
    users._run(
        "UPDATE sso_sessions SET last_checked_at = ?, expires_at = ? WHERE jti = ?",
        (now.isoformat(), (now + SSO_LIFETIME).isoformat(), jti),
    )
    return True


def sid_for(jti: str | None) -> str | None:
    """The suite sign-in this login came from."""
    if not jti:
        return None
    row = users._run("SELECT sid FROM sso_sessions WHERE jti = ?", (jti,), fetch_one=True)
    return row["sid"] if row else None


def end(jti: str | None) -> None:
    """End one login (sign out). Unknown ids are ignored."""
    if jti:
        users._run("DELETE FROM sso_sessions WHERE jti = ?", (jti,))


def end_by_sid(sid: str) -> None:
    """The suite's back-channel sign-out: end every login that came from
    this suite sign-in."""
    if sid:
        users._run("DELETE FROM sso_sessions WHERE sid = ?", (sid,))
