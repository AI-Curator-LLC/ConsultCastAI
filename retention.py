"""
Data retention: purges old transcripts, deletes expired session records,
and clears out sessions left over from lapsed subscriptions. Runs as a
background thread started at app startup (see main.py), not a Render Cron
Job — a Cron Job runs as its own separate service/container and can't reach
a Render Disk, which mounts to exactly one service (the web service that
already holds this data). A single Render instance means this can't
double-run; run_once() is written to be idempotent anyway (safe to call
again on a record it already handled).

Retention periods are config, not scattered through this file:
- TRANSCRIPT_RETENTION_DAYS: full conversation transcripts are purged this
  many days after a session was created, whether or not it has a debrief.
- DEBRIEF_RETENTION_DAYS: past this, on top of TRANSCRIPT_RETENTION_DAYS,
  the whole record (debrief, scores, metadata) is deleted.
- POST_CANCEL_GRACE_DAYS: how long a canceled account's (or team's) sessions
  are kept past current_period_end before being deleted outright.

Every run logs only counts, never session content or message text.
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import store
import teams
import users

TRANSCRIPT_RETENTION_DAYS = int(os.environ.get("TRANSCRIPT_RETENTION_DAYS", "90"))
DEBRIEF_RETENTION_DAYS = int(os.environ.get("DEBRIEF_RETENTION_DAYS", "365"))
POST_CANCEL_GRACE_DAYS = int(os.environ.get("POST_CANCEL_GRACE_DAYS", "90"))

_RUN_INTERVAL_SEC = 24 * 60 * 60


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _rep_is_past_cancellation_grace(rep_id: str, now: datetime) -> bool:
    """True if this rep's (or their team's) subscription has been canceled
    and current_period_end is more than POST_CANCEL_GRACE_DAYS in the past.
    A team member rides on the team's own status/period_end, never their
    own users row (which has no billing fields set at all for a member)."""
    account = users.get_user_by_id(rep_id)
    if not account:
        return False  # already deleted (account deletion wipes its own sessions immediately) — nothing to do
    if account.team_id:
        team = teams.get_team(account.team_id)
        if not team:
            return False
        status, period_end = team.subscription_status, team.current_period_end
    else:
        status, period_end = account.subscription_status, account.current_period_end
    if status != "canceled":
        return False
    end = _parse_utc(period_end)
    if not end:
        return False
    return now - end > timedelta(days=POST_CANCEL_GRACE_DAYS)


def _delete_lapsed_subscription_sessions(raw: dict, now: datetime) -> int:
    deleted = 0
    checked_rep_ids: set[str] = set()
    for rec in raw.values():
        rep_id = rec.get("rep_id")
        if not rep_id or rep_id in checked_rep_ids:
            continue
        checked_rep_ids.add(rep_id)
        if _rep_is_past_cancellation_grace(rep_id, now):
            deleted += store.delete_for_rep(rep_id)
    return deleted


def run_once() -> dict:
    """One pass of the cleanup job. Returns counts for logging — never
    returns or logs session content or message text."""
    now = datetime.now(timezone.utc)
    raw = store.list_all_raw()

    backfilled = 0
    transcripts_purged = 0
    sessions_deleted = 0

    for session_id, rec in raw.items():
        created_raw = rec.get("created_at")
        created = _parse_utc(created_raw) if created_raw else None
        if created is None:
            # Predates the created_at field entirely: stamp with "now" and
            # skip this run — never treat a missing date as "very old" and
            # delete or purge it out from under someone.
            if store.backfill_created_at(session_id, now.isoformat()):
                backfilled += 1
            continue

        has_debrief = bool(rec.get("debrief"))

        if not has_debrief:
            # Abandoned session (never finished, no debrief) — nothing
            # worth keeping past the transcript window.
            if created < _cutoff(now, TRANSCRIPT_RETENTION_DAYS):
                if store.delete(session_id):
                    sessions_deleted += 1
            continue

        if created < _cutoff(now, DEBRIEF_RETENTION_DAYS):
            if store.delete(session_id):
                sessions_deleted += 1
        elif created < _cutoff(now, TRANSCRIPT_RETENTION_DAYS):
            if store.purge_transcript(session_id):
                transcripts_purged += 1

    lapsed_deleted = _delete_lapsed_subscription_sessions(raw, now)

    counts = {
        "created_at_backfilled": backfilled,
        "transcripts_purged": transcripts_purged,
        "sessions_deleted_age_based": sessions_deleted,
        "sessions_deleted_lapsed_subscription": lapsed_deleted,
    }
    print(f"[consultcastai] retention cleanup: {counts}")
    return counts


def _loop() -> None:
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"[consultcastai] retention cleanup failed: {type(exc).__name__}: {exc}")
        time.sleep(_RUN_INTERVAL_SEC)


def start_background_job() -> None:
    """Runs once immediately, then every 24 hours, on a daemon thread so it
    never blocks app startup or keeps the process alive on its own."""
    threading.Thread(target=_loop, daemon=True, name="retention-cleanup").start()
