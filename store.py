"""
Session storage for ConsultCastAI.

Default mode: local JSON file, zero infra to get running. Set
CONSULTCASTAI_LOCAL_STORE=0 and provide CONSULTCASTAI_FIRESTORE_PROJECT to switch to
Firestore once this needs to run on real infrastructure (Cloud Run, multiple
devices, real reps). The public API (save/get/list_for_rep) doesn't change
either way, so nothing above this module needs to know which backend is live.

Local mode has no concurrency safety and no retention policy, fine for a
solo founder testing against himself, not fine for real customer data.
"""

import os
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from models import SessionRecord

_PROJECT_ID = os.environ.get("CONSULTCASTAI_FIRESTORE_PROJECT")
_COLLECTION = "sessions"
_USAGE_COLLECTION = "usage_records"

_LOCAL = os.environ.get("CONSULTCASTAI_LOCAL_STORE", "1") == "1"
_LOCAL_PATH = Path(os.environ.get("CONSULTCASTAI_LOCAL_STORE_PATH", "sessions_local.json"))
_LOCAL_LOCK = threading.Lock()
# Separate file (and lock) from sessions on purpose: a monthly usage counter
# is a different concern from session records, and this way an increment
# never has to read-modify-write the (much larger, ever-growing) sessions file.
_USAGE_LOCAL_PATH = Path(os.environ.get("CONSULTCASTAI_USAGE_STORE_PATH", "usage_local.json"))
_USAGE_LOCAL_LOCK = threading.Lock()

_client = None


@dataclass
class UsageRecord:
    user_id: str
    month: str  # "2026-09" format
    session_count: int = 0


def using_local_store() -> bool:
    return _LOCAL


# --- local file backend --------------------------------------------------

def _local_read_all() -> dict:
    if not _LOCAL_PATH.exists():
        return {}
    try:
        return json.loads(_LOCAL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"[consultcastai] {_LOCAL_PATH} was unreadable, starting a fresh local store.")
        return {}


def _local_write_all(data: dict) -> None:
    _LOCAL_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _usage_read_all() -> dict:
    if not _USAGE_LOCAL_PATH.exists():
        return {}
    try:
        return json.loads(_USAGE_LOCAL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"[consultcastai] {_USAGE_LOCAL_PATH} was unreadable, starting a fresh usage store.")
        return {}


def _usage_write_all(data: dict) -> None:
    _USAGE_LOCAL_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- Firestore backend (optional, production) -----------------------------

def _get_client():
    global _client
    if _client is None:
        from google.cloud import firestore
        _client = firestore.Client(project=_PROJECT_ID) if _PROJECT_ID else firestore.Client()
    return _client


# --- public API -------------------------------------------------------

def save(session: SessionRecord) -> None:
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
            data[session.id] = session.model_dump()
            _local_write_all(data)
        return
    _get_client().collection(_COLLECTION).document(session.id).set(session.model_dump())


def get(session_id: str) -> SessionRecord | None:
    if _LOCAL:
        with _LOCAL_LOCK:
            raw = _local_read_all().get(session_id)
        return SessionRecord(**raw) if raw else None
    doc = _get_client().collection(_COLLECTION).document(session_id).get()
    if not doc.exists:
        return None
    return SessionRecord(**doc.to_dict())


def list_for_rep(rep_id: str) -> list[SessionRecord]:
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
        return [SessionRecord(**r) for r in data.values() if r.get("rep_id") == rep_id]
    docs = _get_client().collection(_COLLECTION).where("rep_id", "==", rep_id).stream()
    return [SessionRecord(**d.to_dict()) for d in docs]


# --- usage metering (billing enforcement, see auth.require_active_plan) ---
# Same local-file-or-Firestore split as sessions above, and the same
# "fine for solo-founder scale, not a real concurrency guarantee" caveat
# from this module's docstring — a lost increment under a genuine race
# would very rarely let one paying rep sneak an extra session past the
# monthly cap, not a security issue, just not bank-grade accounting.

def _usage_key(user_id: str, month: str) -> str:
    return f"{user_id}:{month}"


def get_usage(user_id: str, month: str) -> UsageRecord:
    if _LOCAL:
        with _USAGE_LOCAL_LOCK:
            raw = _usage_read_all().get(_usage_key(user_id, month))
        return UsageRecord(user_id=user_id, month=month, session_count=(raw or {}).get("session_count", 0))
    doc = _get_client().collection(_USAGE_COLLECTION).document(_usage_key(user_id, month)).get()
    if not doc.exists:
        return UsageRecord(user_id=user_id, month=month)
    return UsageRecord(user_id=user_id, month=month, session_count=doc.to_dict().get("session_count", 0))


def increment_session_count(user_id: str, month: str) -> None:
    """Called once, right after a session is successfully created (see
    main.py's start_session) — never speculatively, so a request that fails
    before that point doesn't cost the rep part of their monthly cap."""
    key = _usage_key(user_id, month)
    if _LOCAL:
        with _USAGE_LOCAL_LOCK:
            data = _usage_read_all()
            rec = data.get(key, {"user_id": user_id, "month": month, "session_count": 0})
            rec["session_count"] = rec.get("session_count", 0) + 1
            data[key] = rec
            _usage_write_all(data)
        return
    from google.cloud import firestore
    _get_client().collection(_USAGE_COLLECTION).document(key).set(
        {"user_id": user_id, "month": month, "session_count": firestore.Increment(1)}, merge=True,
    )


# Team usage is the exact same storage shape as individual usage — pooling
# is achieved simply by keying the record on the team's id instead of a
# member's own id, so every member's sessions land in one shared counter
# rather than each seat getting its own separate cap.
def get_team_usage(team_id: str, month: str) -> UsageRecord:
    return get_usage(team_id, month)


def increment_team_session_count(team_id: str, month: str) -> None:
    increment_session_count(team_id, month)
