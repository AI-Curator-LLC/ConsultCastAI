"""
Single sign-on with the AI Curator Consulting Suite: this app's side of the
conversation with the suite's server (aicsuite repo, sso.py and
docs/SSO_DESIGN.md). The same file is used by all three apps; only APP
differs.

THE SWITCH. Whether single sign-on is on is decided at the suite, by one
switch its administrator can flip with no deploy. enabled() asks the suite
and remembers the answer for STATUS_TTL seconds. Anything that goes wrong
(no secret set here, suite unreachable before any answer was ever heard)
means OFF, and off means this app's own sign-in works exactly as it always
has. So the worst a suite outage can do is send people back to the app's
own login page.

Settings, both set in Render, never in code:
  SUITE_SSO_SECRET   shared with the suite (its AICSUITE_SSO_SECRET_<APP>),
                     32+ characters. Blank: single sign-on stays off here.
  SUITE_BASE_URL     the suite's address. Defaults to the live suite.

Standard library only (urllib), so nothing new to install.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

APP = "consultcastai"

STATUS_TTL = 30          # seconds an answer to "is it on?" is reused
_TIMEOUT = 6             # seconds to wait for the suite

_lock = threading.Lock()
_status = {"enabled": False, "at": 0.0}
# After a session check could not reach the suite, don't make every request
# wait for another timeout: skip the check until this moment.
_RETRY_AFTER_FAILURE = 60
_skip_checks_until = 0.0


class SuiteError(Exception):
    """The suite refused, or could not be reached. `code` is the suite's
    error code when it gave one."""

    def __init__(self, code: str, status: int = 0):
        super().__init__(code)
        self.code = code
        self.status = status


def base_url() -> str:
    return (os.environ.get("SUITE_BASE_URL", "").strip() or "https://aicsuite.ai-curator.ai").rstrip("/")


def _secret() -> str:
    return os.environ.get("SUITE_SSO_SECRET", "").strip()


def configured() -> bool:
    return bool(_secret())


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["X-SSO-Secret"] = _secret()
    request = urllib.request.Request(base_url() + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = "suite_error"
        try:
            code = json.loads(exc.read().decode("utf-8"))["detail"]["code"]
        except Exception:
            pass
        raise SuiteError(code, exc.code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SuiteError("suite_unreachable") from exc


def enabled() -> bool:
    """Is single sign-on on. Cached for STATUS_TTL; when the suite can't be
    reached the last answer stands (and before any answer, off)."""
    if not configured():
        return False
    now = time.monotonic()
    with _lock:
        if _status["at"] and now - _status["at"] < STATUS_TTL:
            return _status["enabled"]
    try:
        answer = bool(_request("GET", "/api/sso/status").get("enabled"))
    except SuiteError as exc:
        print(f"[{APP}] suite sso status check failed ({exc.code}); keeping the last answer")
        with _lock:
            # don't ask again on every request while the suite is down
            _status["at"] = now
            return _status["enabled"]
    with _lock:
        _status["enabled"], _status["at"] = answer, now
    return answer


def forget_status() -> None:
    """Ask the suite afresh next time. For tests."""
    global _skip_checks_until
    with _lock:
        _status["enabled"], _status["at"] = False, 0.0
    _skip_checks_until = 0.0


def exchange(code: str, code_verifier: str) -> dict:
    """Trade the one-time code a callback page received for the person:
    {suite_user_id, email, name, lang, email_verified, sid, apps, is_admin}.
    Raises SuiteError."""
    return _request("POST", "/api/sso/token", {"app": APP, "code": code, "code_verifier": code_verifier})


def check(sid: str) -> dict | None:
    """Is the suite sign-in this session came from still good.
    {"active": True, ...person} or {"active": False, "reason": ...}; None
    when the suite couldn't be asked, which is not a "no"."""
    global _skip_checks_until
    if time.monotonic() < _skip_checks_until:
        return None
    try:
        return _request("POST", "/api/sso/session", {"app": APP, "sid": sid})
    except SuiteError as exc:
        print(f"[{APP}] suite sso session check failed ({exc.code})")
        _skip_checks_until = time.monotonic() + _RETRY_AFTER_FAILURE
        return None


def logout(sid: str) -> None:
    """The person signed out here: end the suite sign-in, which also tells
    the other apps. Best effort, never raises."""
    try:
        _request("POST", "/api/sso/logout", {"app": APP, "sid": sid})
    except SuiteError as exc:
        print(f"[{APP}] suite sso logout notice failed ({exc.code})")


def set_lang(sid: str, lang: str) -> None:
    """The person changed the language here: save it on the suite account.
    Best effort, never raises."""
    try:
        _request("POST", "/api/sso/lang", {"app": APP, "sid": sid, "lang": lang})
    except SuiteError as exc:
        print(f"[{APP}] suite sso language update failed ({exc.code})")


def secret_matches(given: str | None) -> bool:
    """Is this the suite calling (back-channel sign-out)."""
    import secrets
    expected = _secret()
    return bool(expected) and secrets.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))
