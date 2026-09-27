"""
User accounts: signup, login, email verification, and the JWTs that prove
identity afterward.

Separate from store.py on purpose: store.py holds practice-session data,
this holds accounts, a different concern with a much higher bar for
correctness (a wrong pressure score is annoying, wrong password handling
is a real security problem).

Same dual-mode shape as store.py's local/production split:
- CONSULTCASTAI_LOCAL_USERS=1 (default): a local SQLite file, zero infra.
  On Render this can live on the same persistent disk as the session
  store (CONSULTCASTAI_USERS_DB_PATH=/data/users.db).
- CONSULTCASTAI_LOCAL_USERS=0: Postgres via DATABASE_URL (Render injects
  it when a database is linked). Needs psycopg2-binary installed, see the
  comment in requirements.txt.

The public functions are identical either way, nothing above this module
needs to know which backend is live.
"""

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt

_LOCAL = os.environ.get("CONSULTCASTAI_LOCAL_USERS", "1") == "1"
_LOCAL_PATH = Path(os.environ.get("CONSULTCASTAI_USERS_DB_PATH", "users_local.db"))
_DATABASE_URL = os.environ.get("DATABASE_URL")

_JWT_SECRET_ENV = "CONSULTCASTAI_JWT_SECRET"
_JWT_TTL = timedelta(days=30)
_DEV_JWT_SECRET = "dev-only-insecure-secret-never-used-in-production"

RESET_TTL = timedelta(hours=1)
# Minimum gap between reset emails to the same account. Not a general rate
# limiter, just stops the forgot-password form being used to spam one inbox.
RESET_COOLDOWN = timedelta(seconds=60)

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; newer bcrypt raises past it instead of truncating


class EmailTaken(Exception):
    pass


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    email_verified: bool
    verification_token: str | None
    is_admin: bool
    created_at: str
    # Bumped on password reset. Tokens carry the version they were issued
    # under, so a reset instantly invalidates every existing login (a
    # stateless JWT would otherwise stay valid for its full 30 days).
    token_version: int = 0
    reset_token_hash: str | None = None
    reset_token_expires: str | None = None  # ISO-8601 UTC
    # Profile display only, never fed to prompts or shown to the persona.
    name: str | None = None
    company: str | None = None
    # Access gate: a signed-up account can't use any functional endpoint
    # until an admin approves it (see require_approved in auth.py). New
    # rows default to False; existing/admin rows are flipped to True by
    # the migration in _ensure_schema below.
    approved: bool = False
    # Billing: real recurring Stripe subscriptions (see require_active_plan
    # in auth.py). plan_tier is a display/bookkeeping label ("pro", later
    # "team"); subscription_status + current_period_end are what actually
    # gate access. All None until the Stripe webhook sets them.
    plan_tier: str | None = None
    subscription_status: str | None = None  # "active" | "canceled" | "past_due"
    current_period_end: str | None = None   # ISO-8601 UTC — access valid through this, even after cancellation
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None


# --- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


_dummy_hash: str | None = None


def burn_password_check(password: str) -> None:
    """Run a real bcrypt comparison against a throwaway hash. Called when a
    login email doesn't exist so that path takes as long as a real one,
    otherwise response time alone reveals which emails have accounts."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    verify_password(password, _dummy_hash)


def password_problem(password: str) -> str | None:
    """Returns a human-readable reason the password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes"
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


# --- JWTs ------------------------------------------------------------------

def _jwt_secret() -> str:
    secret = os.environ.get(_JWT_SECRET_ENV, "").strip()
    if secret:
        if os.environ.get("CONSULTCASTAI_ENV") == "production" and len(secret) < 32:
            raise RuntimeError(f"{_JWT_SECRET_ENV} is too short, use a long random string (32+ characters)")
        return secret
    if os.environ.get("CONSULTCASTAI_ENV") == "production":
        raise RuntimeError(f"{_JWT_SECRET_ENV} is not set")
    return _DEV_JWT_SECRET  # local dev only, never reachable in production


def issue_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "tv": user.token_version,
        "exp": datetime.now(timezone.utc) + _JWT_TTL,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


# --- storage ---------------------------------------------------------------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    verification_token TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 0,
    reset_token_hash TEXT,
    reset_token_expires TEXT,
    name TEXT,
    company TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    plan_tier TEXT,
    subscription_status TEXT,
    current_period_end TEXT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT
)
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_token TEXT,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    token_version INTEGER NOT NULL DEFAULT 0,
    reset_token_hash TEXT,
    reset_token_expires TEXT,
    name TEXT,
    company TEXT,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    plan_tier TEXT,
    subscription_status TEXT,
    current_period_end TIMESTAMPTZ,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT
)
"""

# CREATE TABLE IF NOT EXISTS won't touch a table that already exists, so
# databases created before password reset shipped (e.g. the live one) get
# these columns added here. Existing rows take the defaults, and old JWTs
# without a "tv" claim are treated as version 0, so nobody is logged out.
_ADDED_COLUMNS = [
    ("token_version", "INTEGER NOT NULL DEFAULT 0"),
    ("reset_token_hash", "TEXT"),
    ("reset_token_expires", "TEXT"),
    ("name", "TEXT"),
    ("company", "TEXT"),
    ("approved", "BOOLEAN NOT NULL DEFAULT FALSE"),  # modern SQLite (3.23+) and Postgres both accept TRUE/FALSE literals
    ("plan_tier", "TEXT"),
    ("subscription_status", "TEXT"),
    ("current_period_end", "TEXT"),
    ("stripe_customer_id", "TEXT"),
    ("stripe_subscription_id", "TEXT"),
]

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
    # Queries are written with "?" placeholders (sqlite style), Postgres wants "%s".
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
            if _LOCAL:
                cur.execute("PRAGMA table_info(users)")
                existing = {row["name"] for row in cur.fetchall()}
                for name, ddl in _ADDED_COLUMNS:
                    if name not in existing:
                        cur.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
            else:
                for name, ddl in _ADDED_COLUMNS:
                    cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {ddl}")

            # --- Access-approval migration safety net ---------------------
            # `approved` defaults to FALSE for every row, including ones
            # that already existed before this column did. Left alone,
            # that locks every existing account (including yours) out the
            # moment this ships — same class of mistake as the missing
            # CONSULTCASTAI_JWT_SECRET incident. Two idempotent fixes, run
            # every time this function runs, not just once:
            #
            # 1. Any account already flagged is_admin gets auto-approved.
            cur.execute(_sql("UPDATE users SET approved = ? WHERE is_admin = ?"), (True, True))
            # 2. There was never a UI to set is_admin in the first place
            #    (see README), so #1 alone likely approves nobody on this
            #    app's actual existing database. CONSULTCASTAI_ADMIN_EMAIL,
            #    if set, promotes that one account to admin+approved by
            #    email regardless of its current is_admin value — set this
            #    to your own login email BEFORE this migration first runs
            #    against a real database, or you lock yourself out.
            bootstrap_email = os.environ.get("CONSULTCASTAI_ADMIN_EMAIL", "").strip()
            if bootstrap_email:
                cur.execute(
                    _sql("UPDATE users SET is_admin = ?, approved = ? WHERE email = ?"),
                    (True, True, normalize_email(bootstrap_email)),
                )
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


def _row_to_user(row) -> User | None:
    if row is None:
        return None
    return User(
        id=str(row["id"]),
        email=row["email"],
        password_hash=row["password_hash"],
        email_verified=bool(row["email_verified"]),
        verification_token=row["verification_token"],
        is_admin=bool(row["is_admin"]),
        created_at=str(row["created_at"]),
        token_version=int(row["token_version"] or 0),
        reset_token_hash=row["reset_token_hash"],
        reset_token_expires=row["reset_token_expires"],
        name=row["name"],
        company=row["company"],
        approved=bool(row["approved"]),
        plan_tier=row["plan_tier"],
        subscription_status=row["subscription_status"],
        current_period_end=row["current_period_end"],
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
    )


def create_user(email: str, password_hash: str, verification_token: str, is_admin: bool = False) -> User:
    user_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        _run(
            "INSERT INTO users (id, email, password_hash, email_verified, verification_token, is_admin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, normalize_email(email), password_hash, False, verification_token, is_admin, created_at),
        )
    except Exception as exc:
        # sqlite3.IntegrityError / psycopg2.errors.UniqueViolation, matched by
        # name so this module doesn't have to import psycopg2 in local mode.
        if type(exc).__name__ in ("IntegrityError", "UniqueViolation"):
            raise EmailTaken(email) from exc
        raise
    return get_user_by_id(user_id)


def get_user_by_email(email: str) -> User | None:
    return _row_to_user(_run("SELECT * FROM users WHERE email = ?", (normalize_email(email),), fetch_one=True))


def get_user_by_id(user_id: str) -> User | None:
    return _row_to_user(_run("SELECT * FROM users WHERE id = ?", (user_id,), fetch_one=True))


def get_user_by_verification_token(token: str) -> User | None:
    if not token:
        return None
    return _row_to_user(_run("SELECT * FROM users WHERE verification_token = ?", (token,), fetch_one=True))


def mark_verified(user_id: str) -> None:
    # Clearing the token makes each link single-use.
    _run("UPDATE users SET email_verified = ?, verification_token = NULL WHERE id = ?", (True, user_id))


def create_verification_token(user_id: str) -> str:
    """Issues a fresh verification token (replacing any earlier one, so an
    old copy of the link stops working) and returns the raw value to email.
    Used for the initial signup email and every "resend" after it, so a
    resend never depends on the original token still being present."""
    token = secrets.token_urlsafe(32)
    _run("UPDATE users SET verification_token = ? WHERE id = ?", (token, user_id))
    return token


# --- password reset --------------------------------------------------------

def _hash_reset_token(token: str) -> str:
    # Only this hash is stored, so a leaked database doesn't hand out usable
    # reset links. The token itself is 256 bits of randomness, so a fast
    # unsalted hash is fine here (unlike a human-chosen password).
    return hashlib.sha256(token.encode()).hexdigest()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reset_recently_requested(user: User) -> bool:
    """True if a reset was issued for this account within RESET_COOLDOWN.
    The issue time is derived from the expiry (issued = expires - TTL), so
    no extra column is needed."""
    expires = _parse_utc(user.reset_token_expires)
    if not expires:
        return False
    issued = expires - RESET_TTL
    return datetime.now(timezone.utc) - issued < RESET_COOLDOWN


def create_reset_token(user_id: str) -> str:
    """Issues a fresh single-use reset token (replacing any earlier one) and
    returns the raw token, which exists only in the emailed link."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + RESET_TTL).isoformat()
    _run(
        "UPDATE users SET reset_token_hash = ?, reset_token_expires = ? WHERE id = ?",
        (_hash_reset_token(token), expires, user_id),
    )
    return token


def get_user_by_reset_token(token: str) -> User | None:
    """The account a still-valid reset token belongs to, else None
    (unknown, already used, or expired)."""
    if not token:
        return None
    user = _row_to_user(_run("SELECT * FROM users WHERE reset_token_hash = ?", (_hash_reset_token(token),), fetch_one=True))
    if not user:
        return None
    expires = _parse_utc(user.reset_token_expires)
    if not expires or expires < datetime.now(timezone.utc):
        return None
    return user


def reset_password(user_id: str, new_password_hash: str) -> None:
    """Sets the new password, consumes the reset token, and bumps
    token_version so every existing login token stops working. Also marks the
    email verified: having received the reset link proves control of the inbox."""
    _run(
        "UPDATE users SET password_hash = ?, reset_token_hash = NULL, reset_token_expires = NULL, "
        "token_version = token_version + 1, email_verified = ?, verification_token = NULL WHERE id = ?",
        (new_password_hash, True, user_id),
    )


# --- profile -----------------------------------------------------------

def update_profile(user_id: str, name: str | None, company: str | None) -> None:
    _run("UPDATE users SET name = ?, company = ? WHERE id = ?", (name, company, user_id))


def update_password_hash(user_id: str, new_hash: str) -> None:
    """Sets a new password directly, called only after the caller has already
    verified the current password (see /auth/change-password). Deliberately
    doesn't touch token_version, unlike reset_password() above: the caller
    proved they know the current password within an already-valid session,
    so there's no reason to log that session itself out. A forgotten-password
    reset (which by definition wasn't authenticated first) still gets the
    full token_version bump via reset_password()."""
    _run("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))


# --- access approval ---------------------------------------------------

def set_approved(user_id: str, approved: bool) -> None:
    _run("UPDATE users SET approved = ? WHERE id = ?", (approved, user_id))


def promote_if_admin_bootstrap(user_id: str, email: str) -> bool:
    """If CONSULTCASTAI_ADMIN_EMAIL matches this email, promotes this
    account to admin+approved right away. This is the OTHER half of the
    migration safety net in _ensure_schema: that one only reaches rows
    that already existed the moment the schema migration ran (once, at
    process start) — it can't retroactively catch a signup that happens
    afterward. Called right after signup so the bootstrap admin's very
    first signup works immediately, no separate approval step, whether
    their account already existed before this shipped or not. Returns
    whether it applied, purely so the caller can reflect it in the
    response without a second DB round trip."""
    bootstrap_email = os.environ.get("CONSULTCASTAI_ADMIN_EMAIL", "").strip()
    if not bootstrap_email or normalize_email(email) != normalize_email(bootstrap_email):
        return False
    _run("UPDATE users SET is_admin = ?, approved = ? WHERE id = ?", (True, True, user_id))
    return True


def list_pending() -> list[User]:
    """Accounts still waiting on admin approval, oldest request first (so
    whoever's been waiting longest shows at the top)."""
    rows = _run("SELECT * FROM users WHERE approved = ? ORDER BY created_at ASC", (False,), fetch_all=True)
    return [_row_to_user(r) for r in rows]


# --- billing (Stripe subscriptions) ---------------------------------------
# Real recurring billing: renews automatically until canceled through
# Stripe's own Customer Portal (see /billing/portal-session in main.py).
# Every function here is called only from main.py's signature-verified
# /billing/webhook — never from a route a browser can hit directly, there's
# no "trust me, I'm subscribed" path into any of this.

def set_subscription(user_id: str, stripe_customer_id: str, stripe_subscription_id: str, plan_tier: str, status: str) -> None:
    """checkout.session.completed: the initial subscription was just created."""
    _run(
        "UPDATE users SET stripe_customer_id = ?, stripe_subscription_id = ?, plan_tier = ?, subscription_status = ? WHERE id = ?",
        (stripe_customer_id, stripe_subscription_id, plan_tier, status, user_id),
    )


def update_period_end(stripe_subscription_id: str, period_end: datetime) -> None:
    """invoice.payment_succeeded: a renewal (or the initial invoice) went
    through. Also sets status back to "active" — the normal way a
    previously past_due subscription recovers is a retried invoice
    succeeding, and Stripe fires this same event for that."""
    _run(
        "UPDATE users SET current_period_end = ?, subscription_status = ? WHERE stripe_subscription_id = ?",
        (period_end.isoformat(), "active", stripe_subscription_id),
    )


def set_subscription_status(stripe_subscription_id: str, status: str) -> None:
    """customer.subscription.deleted (-> "canceled") or invoice.payment_failed
    (-> "past_due"). Looked up by subscription id, not user id — these
    webhook events don't carry client_reference_id, only the subscription/
    customer that already exists from checkout.session.completed."""
    _run("UPDATE users SET subscription_status = ? WHERE stripe_subscription_id = ?", (status, stripe_subscription_id))


def get_user_by_stripe_subscription_id(stripe_subscription_id: str) -> User | None:
    return _row_to_user(_run("SELECT * FROM users WHERE stripe_subscription_id = ?", (stripe_subscription_id,), fetch_one=True))


def subscription_active(user: User) -> bool:
    """True if this account has a currently-active subscription. Canceling
    doesn't flip this immediately — customer.subscription.deleted (status
    -> "canceled") only fires once the already-paid-for period actually
    ends, so current_period_end naturally covers "access continues through
    what they already paid for" without this needing to know why access
    might still be valid."""
    if user.subscription_status != "active":
        return False
    if not user.current_period_end:
        return True
    end = _parse_utc(user.current_period_end)
    return bool(end and end >= datetime.now(timezone.utc))
