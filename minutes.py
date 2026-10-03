"""
Session-time accounting: how many minutes of practice each account has
actually used, measured on the server's own clock.

Two jobs:
- The trial cap. An approved account that has never had a subscription
  gets TRIAL_TOTAL_MINUTES of practice in total, at most
  TRIAL_SESSION_MINUTES per session (see is_trial/trial_status, enforced in
  auth.require_active_plan and main.py's /turn).
- A ledger for everyone else. Paid plans aren't capped on minutes yet, but
  their time is recorded the same way (per user, and per team for a team
  member) so a cap can be added later without having to backfill anything.

Time is derived from server timestamps on the session record, never from
anything the browser sends: a session's clock starts when the server
creates it, stops when the server is told to pause or end it, and every
/turn settles what has run so far into the ledger. Closing the tab,
clearing storage, or deleting the session afterwards doesn't give any of it
back (the ledger lives in the usage store, see store.add_seconds).

Limits are config, not scattered through the code.
"""

import os
from datetime import datetime, timezone

import store
import users

TRIAL_TOTAL_SEC = int(os.environ.get("TRIAL_TOTAL_MINUTES", "30")) * 60
TRIAL_SESSION_SEC = int(os.environ.get("TRIAL_SESSION_MINUTES", "10")) * 60
# How long before a trial session's limit the "time's nearly up" warning shows.
TRIAL_WARNING_SEC = int(os.environ.get("TRIAL_WARNING_SECONDS", "60"))
# Below this much remaining, a trial counts as used up: the counter reads in
# whole minutes, so anything under one would show "0 minutes left" while
# still letting a session start.
TRIAL_MIN_START_SEC = int(os.environ.get("TRIAL_MIN_START_SECONDS", "60"))
# The browser ends a trial session itself at the limit; this is the slack
# the server allows (clock skew, a turn already in flight) before it starts
# refusing turns on its own. Time past the limit is never charged either way.
TRIAL_TURN_GRACE_SEC = int(os.environ.get("TRIAL_TURN_GRACE_SECONDS", "10"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- trial ---------------------------------------------------------------

def is_trial(account: users.User | None) -> bool:
    """A trial account is an approved-but-never-subscribed individual one.
    Not an admin (uncapped), not a team member (rides on the team's plan),
    and not someone whose subscription lapsed or was canceled — that's a
    former customer, who gets the "subscribe" message, not a second trial."""
    if account is None or account.is_admin or account.team_id:
        return False
    return account.subscription_status is None and not account.stripe_subscription_id


def trial_status(account: users.User | None) -> dict | None:
    """What the frontend needs to show the counter and the upgrade prompt,
    or None for a non-trial account."""
    if not is_trial(account):
        return None
    used = min(TRIAL_TOTAL_SEC, store.get_total_seconds(account.id))
    remaining = TRIAL_TOTAL_SEC - used
    return {
        "total_sec": TRIAL_TOTAL_SEC,
        "used_sec": used,
        "remaining_sec": remaining,
        "session_limit_sec": TRIAL_SESSION_SEC,
        "warning_sec": TRIAL_WARNING_SEC,
        "exhausted": remaining < TRIAL_MIN_START_SEC,
    }


def session_time_limit(account: users.User | None) -> int | None:
    """The limit for a session this account is about to start: the per-
    session cap, or whatever's left of the trial if that's less. None for
    anyone who isn't on a trial."""
    status = trial_status(account)
    if status is None:
        return None
    return min(TRIAL_SESSION_SEC, status["remaining_sec"])


# --- per-session clock -----------------------------------------------------

def begin(session, now: datetime) -> None:
    session.run_since = now.isoformat()
    session.paused_at = None


def ensure_tracking(session, now: datetime) -> None:
    """A session created before this shipped has no clock at all. Start one
    from now rather than guessing at what it had already used."""
    if session.run_since is None and session.paused_at is None and session.ended_at is None:
        session.run_since = now.isoformat()


def is_paused(session) -> bool:
    return session.paused_at is not None and session.ended_at is None


def raw_seconds(session, now: datetime) -> float:
    """Time this session has actually been running: closed stretches plus
    the open one, pauses excluded."""
    total = float(session.run_sec or 0)
    started = _parse_utc(session.run_since)
    if started is not None:
        total += max(0.0, (now - started).total_seconds())
    return total


def billable_seconds(session, now: datetime) -> float:
    """raw_seconds, never past the session's own limit: a trial session that
    overruns (a slow request, a tab left open) is charged its limit and no
    more."""
    raw = raw_seconds(session, now)
    if session.time_limit_sec is not None:
        return min(raw, float(session.time_limit_sec))
    return raw


def over_limit(session, now: datetime) -> bool:
    if session.time_limit_sec is None:
        return False
    return raw_seconds(session, now) > session.time_limit_sec + TRIAL_TURN_GRACE_SEC


def _close_stretch(session, now: datetime) -> None:
    started = _parse_utc(session.run_since)
    if started is not None:
        session.run_sec = float(session.run_sec or 0) + max(0.0, (now - started).total_seconds())
    session.run_since = None


def pause(session, now: datetime) -> None:
    if session.ended_at or session.paused_at:
        return
    _close_stretch(session, now)
    session.paused_at = now.isoformat()


def resume(session, now: datetime) -> None:
    if session.ended_at or not session.paused_at:
        return
    session.paused_at = None
    session.run_since = now.isoformat()


def finish(session, now: datetime) -> None:
    if session.ended_at:
        return
    _close_stretch(session, now)
    session.paused_at = None
    session.ended_at = now.isoformat()


def settle(session, account: users.User | None, now: datetime) -> None:
    """Writes whatever this session has used since the last settle into the
    ledger. charged_sec on the session records how much has already been
    written, so calling this again (every turn, then pause, then end) only
    ever adds the difference. Whole seconds, rounded down, so it can't
    overcharge.

    account is None in local dev bypass (no real account to charge); the
    session's own clock is still kept so its duration is right.

    The caller saves the session afterwards."""
    billable = int(billable_seconds(session, now))
    delta = billable - int(session.charged_sec or 0)
    if delta <= 0:
        return
    if account is not None:
        month = now.strftime("%Y-%m")
        store.add_seconds(account.id, month, delta)        # per user
        if account.team_id:
            store.add_seconds(account.team_id, month, delta)  # per account, pooled across the team
    session.charged_sec = billable
