"""
Extra practice minutes bought as a one-time payment (a "top-up"), on top of
a paid plan's monthly minutes. See minutes.py for how they are used.

One row in topup_purchases is one paid Stripe Checkout. The row's key is
Stripe's own id for that Checkout, so crediting is a single INSERT that
either lands or is refused as a duplicate: a webhook that Stripe retries,
or that someone replays, credits nothing the second time. There is no
separate "credit the balance" step that could run twice or be lost half
way.

What was bought belongs to an owner: an account's id for a Pro account, a
team's id for a Team plan (the whole team draws on it). The balance is not
stored anywhere: it is everything bought (the sum of the rows here) minus
everything used (store.get_topup_used), so the two can never drift apart.

The table lives in the users database (users.py creates it with the
others), which is where exactly-once is a guarantee of the database itself
rather than of careful code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import store
import users


def record_purchase(payment_id: str, owner_id: str, owner_kind: str, seconds: int, bought_by: str | None) -> bool:
    """Credits one paid Checkout to its owner. True if this call credited
    it, False if that payment had already been credited."""
    try:
        users._run(
            "INSERT INTO topup_purchases (payment_id, owner_id, owner_kind, seconds, bought_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (payment_id, owner_id, owner_kind, int(seconds), bought_by, datetime.now(timezone.utc).isoformat()),
        )
    except Exception as exc:
        # sqlite3.IntegrityError / psycopg2.errors.UniqueViolation, matched
        # by name the way users.create_user does.
        if type(exc).__name__ in ("IntegrityError", "UniqueViolation"):
            return False
        raise
    return True


def purchased_seconds(owner_id: str) -> int:
    row = users._run(
        "SELECT COALESCE(SUM(seconds), 0) AS total FROM topup_purchases WHERE owner_id = ?",
        (owner_id,), fetch_one=True,
    )
    return int(row["total"] or 0) if row else 0


def balance_seconds(owner_id: str) -> int:
    """What is left of everything this owner has bought. Never below zero."""
    return max(0, purchased_seconds(owner_id) - store.get_topup_used(owner_id))


def delete_for_owner(owner_id: str) -> None:
    """Account deletion: nothing about the account is kept."""
    users._run("DELETE FROM topup_purchases WHERE owner_id = ?", (owner_id,))
