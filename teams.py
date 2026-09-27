"""
Team-tier subscriptions: one Stripe subscription and one pooled monthly
usage cap shared across up to `seat_limit` member accounts (see
users.team_id), with one owner who invites the others.

Same physical database as users.py (same CONSULTCASTAI_LOCAL_USERS /
CONSULTCASTAI_USERS_DB_PATH / DATABASE_URL env vars) — a team's owner_user_id
and a member's team_id are foreign keys into that same table, so this can't
live anywhere else. Kept as its own module purely for call-site clarity
(teams.get_team(...) reads better than folding this into users.py's already
large surface), duplicating users.py's small dual-mode connection pattern
rather than importing its private helpers across module boundaries.
"""

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LOCAL = os.environ.get("CONSULTCASTAI_LOCAL_USERS", "1") == "1"
_LOCAL_PATH = Path(os.environ.get("CONSULTCASTAI_USERS_DB_PATH", "users_local.db"))
_DATABASE_URL = os.environ.get("DATABASE_URL")

DEFAULT_SEAT_LIMIT = 5


@dataclass
class Team:
    id: str
    owner_user_id: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    subscription_status: str | None  # "active" | "canceled" | "past_due", same values as an individual account
    current_period_end: str | None   # ISO-8601 UTC
    seat_limit: int
    created_at: str


@dataclass
class TeamInvite:
    token: str
    team_id: str
    email: str
    used: bool
    created_at: str


# --- storage ---------------------------------------------------------------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    subscription_status TEXT,
    current_period_end TEXT,
    seat_limit INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL
)
"""

_SQLITE_INVITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_invites (
    token TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    email TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    subscription_status TEXT,
    current_period_end TIMESTAMPTZ,
    seat_limit INTEGER NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_POSTGRES_INVITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_invites (
    token TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    email TEXT NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_schema_ready = False
_schema_lock = threading.Lock()


def _connect():
    if _LOCAL:
        conn = sqlite3.connect(_LOCAL_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    if not _DATABASE_URL:
        raise RuntimeError("CONSULTCASTAI_LOCAL_USERS=0 but DATABASE_URL is not set")
    import psycopg2
    import psycopg2.extras
    return psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _sql(query: str) -> str:
    return query if _LOCAL else query.replace("?", "%s")


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(_SQLITE_SCHEMA if _LOCAL else _POSTGRES_SCHEMA)
            cur.execute(_SQLITE_INVITES_SCHEMA if _LOCAL else _POSTGRES_INVITES_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _schema_ready = True


def _run(query: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False):
    _ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_sql(query), params)
        row = cur.fetchone() if fetch_one else cur.fetchall() if fetch_all else None
        conn.commit()
        return row
    finally:
        conn.close()


def _row_to_team(row) -> Team | None:
    if row is None:
        return None
    return Team(
        id=str(row["id"]),
        owner_user_id=row["owner_user_id"],
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        subscription_status=row["subscription_status"],
        current_period_end=row["current_period_end"],
        seat_limit=int(row["seat_limit"] or DEFAULT_SEAT_LIMIT),
        created_at=str(row["created_at"]),
    )


def _row_to_invite(row) -> TeamInvite | None:
    if row is None:
        return None
    return TeamInvite(
        token=row["token"],
        team_id=row["team_id"],
        email=row["email"],
        used=bool(row["used"]),
        created_at=str(row["created_at"]),
    )


def _parse_utc(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- teams -------------------------------------------------------------

def create_team(
    id: str,
    owner_user_id: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    subscription_status: str,
    seat_limit: int = DEFAULT_SEAT_LIMIT,
) -> None:
    """Called only from main.py's signature-verified /billing/webhook, on
    the initial checkout.session.completed for a "team" plan — never from a
    route a browser can hit directly."""
    created_at = datetime.now(timezone.utc).isoformat()
    _run(
        "INSERT INTO teams (id, owner_user_id, stripe_customer_id, stripe_subscription_id, "
        "subscription_status, seat_limit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (id, owner_user_id, stripe_customer_id, stripe_subscription_id, subscription_status, seat_limit, created_at),
    )


def get_team(team_id: str) -> Team | None:
    return _row_to_team(_run("SELECT * FROM teams WHERE id = ?", (team_id,), fetch_one=True))


def get_team_by_subscription_id(stripe_subscription_id: str) -> Team | None:
    """Used by the webhook's renewal/cancellation/failure events, which
    carry a subscription id, not a team id — see main.py's /billing/webhook,
    where this determines whether a given event belongs to a team or an
    individual account."""
    if not stripe_subscription_id:
        return None
    return _row_to_team(_run("SELECT * FROM teams WHERE stripe_subscription_id = ?", (stripe_subscription_id,), fetch_one=True))


def update_team_period_end(stripe_subscription_id: str, period_end: datetime) -> None:
    """invoice.payment_succeeded for a team's subscription: extends access
    and, same as the individual-account equivalent, resets status to
    "active" — the normal way a past_due team subscription recovers."""
    _run(
        "UPDATE teams SET current_period_end = ?, subscription_status = ? WHERE stripe_subscription_id = ?",
        (period_end.isoformat(), "active", stripe_subscription_id),
    )


def set_team_subscription_status(stripe_subscription_id: str, status: str) -> None:
    _run("UPDATE teams SET subscription_status = ? WHERE stripe_subscription_id = ?", (status, stripe_subscription_id))


def team_subscription_active(team: Team) -> bool:
    """Same logic as users.subscription_active, for a team's subscription."""
    if team.subscription_status != "active":
        return False
    if not team.current_period_end:
        return True
    end = _parse_utc(team.current_period_end)
    return bool(end and end >= datetime.now(timezone.utc))


# --- invites -------------------------------------------------------------

def create_invite(team_id: str, email: str, token: str) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    _run(
        "INSERT INTO team_invites (token, team_id, email, used, created_at) VALUES (?, ?, ?, ?, ?)",
        (token, team_id, email, False, created_at),
    )


def get_invite_by_token(token: str) -> TeamInvite | None:
    if not token:
        return None
    return _row_to_invite(_run("SELECT * FROM team_invites WHERE token = ?", (token,), fetch_one=True))


def mark_invite_used(token: str) -> None:
    _run("UPDATE team_invites SET used = ? WHERE token = ?", (True, token))
