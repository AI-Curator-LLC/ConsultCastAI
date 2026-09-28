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
    # Atomic replace (write to a temp file, then rename over the real path)
    # so a crash or a concurrent reader mid-write never sees a truncated or
    # partially-written file — matters more now that the retention cleanup
    # job (retention.py) rewrites this file on a timer while /turn requests
    # are also saving to it under the same lock.
    tmp_path = _LOCAL_PATH.with_suffix(_LOCAL_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, _LOCAL_PATH)


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


def delete(session_id: str) -> bool:
    """Permanently removes one session record. Used by user-initiated
    deletion (DELETE /sessions/{id}, DELETE /me/sessions, account deletion)
    and by the retention cleanup job's age-based rules. Returns whether a
    record actually existed to delete."""
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
            existed = data.pop(session_id, None) is not None
            if existed:
                _local_write_all(data)
        return existed
    doc_ref = _get_client().collection(_COLLECTION).document(session_id)
    existed = doc_ref.get().exists
    if existed:
        doc_ref.delete()
    return existed


def delete_for_rep(rep_id: str) -> int:
    """Deletes every session belonging to one rep in one pass — used by
    DELETE /me/sessions, full account deletion, and the retention job's
    post-cancellation-grace rule. Returns how many were deleted."""
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
            to_remove = [sid for sid, rec in data.items() if rec.get("rep_id") == rep_id]
            for sid in to_remove:
                data.pop(sid, None)
            if to_remove:
                _local_write_all(data)
        return len(to_remove)
    docs = list(_get_client().collection(_COLLECTION).where("rep_id", "==", rep_id).stream())
    for d in docs:
        d.reference.delete()
    return len(docs)


def purge_transcript(session_id: str) -> bool:
    """Empties the conversation and flags transcript_purged, keeping the
    debrief/scores/metadata — the retention job's TRANSCRIPT_RETENTION_DAYS
    rule. Re-checks the record still has a debrief and isn't already purged
    right before writing, under the same lock as every other write, so a
    session that's still active (or already handled) by the time this
    actually runs is never touched, even if it looked eligible when the
    caller first scanned the store. Returns whether it actually purged
    anything."""
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
            rec = data.get(session_id)
            if not rec or not rec.get("debrief") or rec.get("transcript_purged"):
                return False
            rec["conversation"] = []
            rec["transcript_purged"] = True
            data[session_id] = rec
            _local_write_all(data)
        return True
    doc_ref = _get_client().collection(_COLLECTION).document(session_id)
    doc = doc_ref.get()
    if not doc.exists:
        return False
    rec = doc.to_dict()
    if not rec.get("debrief") or rec.get("transcript_purged"):
        return False
    doc_ref.update({"conversation": [], "transcript_purged": True})
    return True


def backfill_created_at(session_id: str, created_at_iso: str) -> bool:
    """Stamps created_at on a record that predates the field (see
    SessionRecord's docstring comment on created_at) without touching
    anything else. Re-checks the key is still genuinely absent right at
    write time, under the lock, so this can never clobber a real timestamp.
    Returns whether it actually wrote anything."""
    if _LOCAL:
        with _LOCAL_LOCK:
            data = _local_read_all()
            rec = data.get(session_id)
            if not rec or "created_at" in rec:
                return False
            rec["created_at"] = created_at_iso
            data[session_id] = rec
            _local_write_all(data)
        return True
    doc_ref = _get_client().collection(_COLLECTION).document(session_id)
    doc = doc_ref.get()
    if not doc.exists or "created_at" in doc.to_dict():
        return False
    doc_ref.update({"created_at": created_at_iso})
    return True


def list_all_raw() -> dict[str, dict]:
    """Every session record as a raw dict keyed by id — used only by the
    retention cleanup job (retention.py), which needs to see whether
    created_at is genuinely absent (a record that predates the field)
    rather than letting SessionRecord's default_factory silently paper
    over that with "now" the way get()/list_for_rep() would."""
    if _LOCAL:
        with _LOCAL_LOCK:
            return _local_read_all()
    return {d.id: d.to_dict() for d in _get_client().collection(_COLLECTION).stream()}


# --- usage metering (billing enforcement, see auth.require_active_plan) ---
# Same local-file-or-Firestore split as sessions above, and the same
# "fine for solo-founder scale, not a real concurrency guarantee" caveat
# from this module's docstring — a lost increment under a genuine race
# would very rarely let one paying rep sneak an extra session past the
# monthly cap, not a security issue, just not bank-grade accounting.

def _usage_key(user_id: str, month: str) -> str:
    return f"{user_id}:{month}"


def delete_usage_records_for_rep(user_id: str) -> int:
    """Removes every month's usage record for one rep. Called only from full
    account deletion (main.py's /me/delete-account) — never from plain
    session deletion, which must NOT touch usage_records (see
    increment_session_count's docstring: deleting sessions must not let
    someone claw back part of their monthly cap). The account itself is
    gone by the time this runs, so there's no cap left to protect."""
    prefix = f"{user_id}:"
    if _LOCAL:
        with _USAGE_LOCAL_LOCK:
            data = _usage_read_all()
            keys = [k for k in data if k.startswith(prefix)]
            for k in keys:
                data.pop(k, None)
            if keys:
                _usage_write_all(data)
        return len(keys)
    docs = list(_get_client().collection(_USAGE_COLLECTION).where("user_id", "==", user_id).stream())
    for d in docs:
        d.reference.delete()
    return len(docs)


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
