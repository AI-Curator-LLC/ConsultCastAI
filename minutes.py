"""
Session-time accounting: how many minutes of practice each account has
actually used, measured on the server's own clock.

Three jobs:
- The trial cap. An approved account that has never had a subscription
  gets TRIAL_TOTAL_MINUTES of practice in total, at most
  TRIAL_SESSION_MINUTES per session (see is_trial/trial_status, enforced in
  auth.require_active_plan and main.py's /turn).
- The ledger. Everyone's time is recorded the same way, per calendar month
  (UTC): per user, and per team for a team member.
- Paid minutes. A Pro account gets PRO_MONTHLY_MINUTES a month; a Team
  plan gets TEAM_MONTHLY_MINUTES a month, shared by the team (one pool for
  the owner and every member, not an amount each). The month's use is what
  the ledger recorded that month for the pool. Extra minutes bought as a
  one-time payment (topups.py) are used only once the month's minutes are
  gone, and carry over from month to month. See paid_plan/paid_status,
  enforced in the same places as the trial. PAID_MINUTES_ENFORCED=0 turns
  the paid cap off: time is still recorded, nothing is refused.

Time is derived from server timestamps on the session record, never from
anything the browser sends: a session's clock starts when the server
creates it, stops when the server is told to pause or end it, and every
/turn settles what has run so far into the ledger. Closing the tab,
clearing storage, or deleting the session afterwards doesn't give any of it
back (the ledger lives in the usage store, see store.add_seconds).

Limits are config, not scattered through the code.
"""

import os
from datetime import datetime, timedelta, timezone

import store
import teams
import topups
import users

TRIAL_TOTAL_SEC = int(os.environ.get("TRIAL_TOTAL_MINUTES", "10")) * 60
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

# Avatar sessions, on every plan. Anam (the avatar service) closes a
# connection at its own plan's maximum session length, mid-sentence and
# without a debrief; this ends the session cleanly, with a warning and the
# debrief, before Anam can. So it is set a little UNDER the Anam plan's
# maximum, never at it: at the same figure the two race and Anam sometimes
# wins. Explorer's maximum is 10 minutes, so 9.5 here; after upgrading to
# Growth or Professional (2 hours) set it to 119. Decimals are fine: the
# value is minutes. It limits one sitting, not the month: minutes are still
# counted against the trial or the plan as usual. Voice-only sessions are
# not affected.
AVATAR_SESSION_SEC = int(round(float(os.environ.get("AVATAR_SESSION_MINUTES", "9.5")) * 60))
AVATAR_WARNING_SEC = int(os.environ.get("AVATAR_WARNING_SECONDS", "60"))


def avatar_limit() -> dict:
    """What the frontend needs to end an avatar session on time. Sent with
    the session (see main.start_session) rather than built into the page,
    so changing the environment variable is all it takes. The browser is
    what enforces it: the point is to hang up before Anam does, and a
    browser that ignored it would simply be cut off by Anam instead."""
    return {"session_limit_sec": AVATAR_SESSION_SEC, "warning_sec": AVATAR_WARNING_SEC}


# --- paid plans ---
# Minutes included each calendar month (UTC). Team's is one pool for the
# whole team, however many members it has.
PRO_INCLUDED_SEC = int(os.environ.get("PRO_MONTHLY_MINUTES", "120")) * 60
TEAM_INCLUDED_SEC = int(os.environ.get("TEAM_MONTHLY_MINUTES", "1000")) * 60
# Extra minutes are sold in blocks of this size. What a block costs is the
# Stripe Price named by STRIPE_PRICE_ID_TOPUP; TOPUP_PRICE_LABEL is only the
# words on the button and has to be kept in step with that Price by hand.
TOPUP_BLOCK_SEC = int(os.environ.get("TOPUP_BLOCK_MINUTES", "100")) * 60
TOPUP_PRICE_LABEL = os.environ.get("TOPUP_PRICE_LABEL", "").strip() or "$40"
# Same meanings as the trial's two settings above, for a paid session.
PAID_WARNING_SEC = int(os.environ.get("PAID_WARNING_SECONDS", "60"))
PAID_MIN_START_SEC = int(os.environ.get("PAID_MIN_START_SECONDS", "60"))
# How long an answer from the suite about the person's plan keeps counting.
# A sign-in through the suite lasts 7 days at most and is rechecked every
# few minutes while in use, so anything older than this is a sign-in that
# ended, not a plan that is still known to be there.
SUITE_PLAN_MAX_AGE = timedelta(days=int(os.environ.get("SUITE_PLAN_MAX_AGE_DAYS", "8")))


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def paid_minutes_enforced() -> bool:
    """The switch for the whole paid cap. Off: paid plans are recorded and
    never limited, with no counter on the page, exactly as before the cap
    existed. Extra minutes already bought stay on record untouched."""
    return _flag("PAID_MINUTES_ENFORCED", True)


def suite_plan_counts() -> bool:
    """Does the suite's own plan count as Pro here."""
    return _flag("SUITE_PLAN_GRANTS_PRO", True)


def topup_price_id() -> str:
    """The Stripe Price a block of extra minutes is sold at. Blank: extra
    minutes can't be bought (no button, the endpoint refuses)."""
    return os.environ.get("STRIPE_PRICE_ID_TOPUP", "").strip()


# The clock everything here reads. Replaceable so tests can move time (a
# month end, a long session) without waiting for it: set_clock(fn) with a
# function returning an aware UTC datetime, set_clock(None) to put the real
# one back.
_clock = None


def set_clock(fn) -> None:
    global _clock
    _clock = fn


def now_utc() -> datetime:
    return _clock() if _clock else datetime.now(timezone.utc)


def month_key(now: datetime | None = None) -> str:
    return (now or now_utc()).strftime("%Y-%m")


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
    if suite_pro(account):
        return False  # covered by the suite's plan: Pro, not a trial
    return account.subscription_status is None and not account.stripe_subscription_id


def trial_already_used(account: users.User) -> bool:
    """True if this email had its trial on an earlier account (see
    users.record_trial_used): a record exists and it isn't this account's
    own. The account that used the trial keeps counting against its own
    ledger as normal; only a later account on the same email is affected."""
    record = users.get_trial_record(account.email)
    return record is not None and record.user_id != account.id


def trial_status(account: users.User | None) -> dict | None:
    """What the frontend needs to show the counter and the upgrade prompt,
    or None for a non-trial account."""
    if not is_trial(account):
        return None
    previously_used = trial_already_used(account)
    # A trial is per email. An account on an email that has already had one
    # starts with none left, however much of it the earlier account used.
    used = TRIAL_TOTAL_SEC if previously_used else min(TRIAL_TOTAL_SEC, store.get_total_seconds(account.id))
    remaining = TRIAL_TOTAL_SEC - used
    return {
        "total_sec": TRIAL_TOTAL_SEC,
        "used_sec": used,
        "remaining_sec": remaining,
        "session_limit_sec": TRIAL_SESSION_SEC,
        "warning_sec": TRIAL_WARNING_SEC,
        "exhausted": remaining < TRIAL_MIN_START_SEC,
        "previously_used": previously_used,
    }


def session_time_limit(account: users.User | None) -> int | None:
    """The limit for a session this account is about to start. A trial: the
    per-session cap, or whatever's left of the trial if that's less. A paid
    plan: whatever its pool has left (this month's minutes plus any extra
    minutes); there is no per-session cap. None for anyone who isn't
    limited (an admin, local dev, or a paid plan with the cap switched
    off)."""
    status = trial_status(account)
    if status is not None:
        return min(TRIAL_SESSION_SEC, status["remaining_sec"])
    paid = paid_status(account)
    if paid is not None:
        return paid["remaining_sec"]
    return None


# --- paid plans ------------------------------------------------------------

def suite_pro(account: users.User | None) -> bool:
    """True when this account's access comes from the person's AI Curator
    Consulting Suite plan rather than a subscription of its own here: the
    suite said so (users.note_suite_plan), recently enough, for an address
    it has confirmed. Such an account is Pro, with Pro's minutes."""
    if account is None or not suite_plan_counts():
        return False
    if not account.suite_plan or not account.email_verified:
        return False
    checked = _parse_utc(account.suite_plan_checked_at)
    return checked is not None and now_utc() - checked <= SUITE_PLAN_MAX_AGE


def paid_plan(account: users.User | None) -> dict | None:
    """Which paid plan this account is on right now, and whose minutes it
    draws on, or None if it isn't on one (a trial, a lapsed subscription).
    Says nothing about admins or the on/off switch: see capped_plan.

    - a team member (the owner included): the team's plan, one pool for the
      whole team, kept under the team's id;
    - an account with its own active subscription: Pro, its own pool;
    - an account covered by the suite's plan: Pro, its own pool. A
      complimentary one draws on the monthly minutes the suite set for it."""
    if account is None:
        return None
    if account.team_id:
        team = teams.get_team(account.team_id)
        if not team or not teams.team_subscription_active(team):
            return None
        is_owner = team.owner_user_id == account.id
        return {"plan": "team", "pool_id": team.id, "pool_kind": "team", "included_sec": TEAM_INCLUDED_SEC,
                "via_suite": False, "is_team_member": not is_owner, "may_buy": is_owner}
    own = users.subscription_active(account)
    if own or suite_pro(account):
        # A complimentary suite account has its own monthly minutes, set at
        # the suite (users.note_suite_plan); a subscription of its own here
        # comes first and brings Pro's.
        included = PRO_INCLUDED_SEC
        if not own and account.suite_minutes:
            included = account.suite_minutes * 60
        return {"plan": "pro", "pool_id": account.id, "pool_kind": "user", "included_sec": included,
                "via_suite": not own, "is_team_member": False, "may_buy": True}
    return None


def capped_plan(account: users.User | None) -> dict | None:
    """paid_plan, for an account the cap actually applies to: None for an
    admin (never limited, as before) and for everyone while the switch is
    off."""
    if account is None or account.is_admin or not paid_minutes_enforced():
        return None
    return paid_plan(account)


def _next_month_start(now: datetime) -> datetime:
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)


def paid_status(account: users.User | None) -> dict | None:
    """What the frontend needs for a paid account's counter and its "minutes
    used" panel, the paid sibling of trial_status. None for anyone the paid
    cap doesn't apply to.

    included_remaining_sec is what is left of this month's minutes;
    topup_sec is the balance of extra minutes; remaining_sec is the two
    together, which is all a session can use."""
    plan = capped_plan(account)
    if plan is None:
        return None
    now = now_utc()
    included = plan["included_sec"]
    used = store.get_usage(plan["pool_id"], month_key(now)).seconds_used
    included_remaining = max(0, included - used)
    topup = topups.balance_seconds(plan["pool_id"])
    remaining = included_remaining + topup
    return {
        "plan": plan["plan"],
        "via_suite": plan["via_suite"],
        "included_sec": included,
        "used_sec": min(included, used),
        "included_remaining_sec": included_remaining,
        "topup_sec": topup,
        "remaining_sec": remaining,
        "warning_sec": PAID_WARNING_SEC,
        "exhausted": remaining < PAID_MIN_START_SEC,
        # The first day of next month (UTC), when the included minutes are back.
        "resets_on": _next_month_start(now).date().isoformat(),
        "is_team_member": plan["is_team_member"],
        # Whether this person is offered extra minutes: a Pro account or a
        # team's owner, and only once a price has been set up for them.
        "can_buy": plan["may_buy"] and bool(topup_price_id()),
        # Whether extra minutes are on sale at all (a team member can't buy
        # them, but is told to ask the owner only when the owner can).
        "topup_available": bool(topup_price_id()),
        "topup_block_min": TOPUP_BLOCK_SEC // 60,
        "topup_price_label": TOPUP_PRICE_LABEL,
    }


def pool_spent(session, account: users.User | None, now: datetime) -> bool:
    """True when the pool a paid session draws on has nothing left, counting
    what this session has run but not yet written to the ledger. A session's
    own limit (over_limit) already covers one person practicing alone; this
    is for a team, where a colleague's session can use up the shared
    minutes while this one is running. Same slack as over_limit."""
    status = paid_status(account)
    if status is None:
        return False
    unsettled = max(0.0, billable_seconds(session, now) - int(session.charged_sec or 0))
    return status["remaining_sec"] - unsettled < -TRIAL_TURN_GRACE_SEC


def plan_minutes() -> dict | None:
    """What each paid plan includes, for the page's wording, or None while
    the paid cap is switched off."""
    if not paid_minutes_enforced():
        return None
    return {"pro_min": PRO_INCLUDED_SEC // 60, "team_min": TEAM_INCLUDED_SEC // 60}


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
    """raw_seconds, never past the session's own limit: a session that
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
        # A paid plan uses this month's minutes first. Whatever part of this
        # charge doesn't fit in what was left of them comes out of the
        # pool's extra minutes, as far as those go. Worked out before the
        # charge is written below, from the month's figure as it stood.
        plan = capped_plan(account)
        if plan is not None:
            used_before = store.get_usage(plan["pool_id"], month).seconds_used
            beyond_included = delta - max(0, plan["included_sec"] - used_before)
            if beyond_included > 0:
                store.add_topup_used(plan["pool_id"], min(beyond_included, topups.balance_seconds(plan["pool_id"])))
        store.add_seconds(account.id, month, delta)        # per user
        if account.team_id:
            store.add_seconds(account.team_id, month, delta)  # per account, pooled across the team
        # First time this session is charged anything: if it's a trial, the
        # email has now used one. Kept past account deletion, see users.py.
        if int(session.charged_sec or 0) == 0 and is_trial(account):
            users.record_trial_used(account)
    session.charged_sec = billable
