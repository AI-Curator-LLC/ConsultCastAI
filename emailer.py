"""
Transactional email via Resend's REST API (one HTTP call, no SDK).

Every function here is best-effort: a failure is logged and swallowed, never
raised, so a broken email provider can't block signup or leak (through a
different response) whether an account exists on the forgot-password form.

Env vars:
- RESEND_API_KEY: unset means "log instead of send" (local dev), never an error.
- CONSULTCASTAI_EMAIL_FROM: defaults to Resend's shared test sender, which only
  delivers to your own Resend account's email. Switch to a verified domain
  (e.g. "ConsultCastAI <noreply@ai-curator.ai>") once it's verified in Resend.
- CONSULTCASTAI_BACKEND_URL: public base URL of this API (verification links
  hit it directly).
- CONSULTCASTAI_FRONTEND_URL: public base URL of the web app (password reset
  links open it, since setting a new password needs a form).
- CONSULTCASTAI_ADMIN_EMAIL: where send_admin_notification_email delivers
  "someone signed up" notices. Unset just skips sending (see users.py: this
  same env var also bootstraps that account to admin+approved on startup).
"""

import json
import os
import urllib.error
import urllib.request

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "ConsultCastAI <onboarding@resend.dev>"


def backend_url() -> str:
    return os.environ.get("CONSULTCASTAI_BACKEND_URL", "http://localhost:8081").rstrip("/")


def frontend_url() -> str:
    return os.environ.get("CONSULTCASTAI_FRONTEND_URL", "http://localhost:5500").rstrip("/")


def _send(to_email: str, subject: str, html: str, dev_log_link: str, what: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()

    if not api_key:
        if os.environ.get("CONSULTCASTAI_ENV") == "production":
            print(f"[consultcastai] RESEND_API_KEY is not set, skipping {what} email")
        else:
            # Local dev convenience only. Never log a live token in production.
            print(f"[consultcastai] RESEND_API_KEY not set, {what} link for {to_email}: {dev_log_link}")
        return

    body = json.dumps({
        "from": os.environ.get("CONSULTCASTAI_EMAIL_FROM", _DEFAULT_FROM),
        "to": [to_email],
        "subject": subject,
        "html": html,
    }).encode("utf-8")
    request = urllib.request.Request(
        _RESEND_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend sits behind Cloudflare, which can reject urllib's default UA.
            "User-Agent": "consultcastai/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        print(f"[consultcastai] {what} email failed for {to_email}: HTTP {exc.code} {detail}")
    except Exception as exc:
        print(f"[consultcastai] {what} email failed for {to_email}: {type(exc).__name__}: {exc}")


def send_verification_email(to_email: str, token: str) -> None:
    verify_url = f"{backend_url()}/auth/verify?token={token}"
    _send(
        to_email,
        "Verify your ConsultCastAI account",
        "<p>Welcome to ConsultCastAI.</p>"
        f'<p>Click to verify your email: <a href="{verify_url}">{verify_url}</a></p>'
        "<p>If you didn't create this account, you can ignore this email.</p>",
        dev_log_link=verify_url,
        what="verification",
    )


def send_delete_account_email(to_email: str, token: str) -> None:
    """Confirms an account deletion for someone who has no password of
    their own here (they sign in through the suite). Nothing is deleted
    until the link is opened and confirmed."""
    delete_url = f"{frontend_url()}/?delete_token={token}"
    _send(
        to_email,
        "Confirm deleting your ConsultCastAI account",
        "<p>We got a request to delete your ConsultCastAI account.</p>"
        f'<p>To go ahead, open this link and confirm: <a href="{delete_url}">{delete_url}</a></p>'
        "<p>This deletes your account, your practice sessions and your usage records, and cannot be undone. "
        "The link works once and expires in 1 hour. If you didn't ask for this, ignore this email and "
        "nothing will be deleted.</p>",
        dev_log_link=delete_url,
        what="account deletion",
    )


def send_password_reset_email(to_email: str, token: str) -> None:
    reset_url = f"{frontend_url()}/?reset_token={token}"
    _send(
        to_email,
        "Reset your ConsultCastAI password",
        "<p>We got a request to reset the password on your ConsultCastAI account.</p>"
        f'<p>Click to choose a new one: <a href="{reset_url}">{reset_url}</a></p>'
        "<p>This link works once and expires in 1 hour. If you didn't ask for this, "
        "you can ignore this email, your password won't change.</p>",
        dev_log_link=reset_url,
        what="password reset",
    )


def send_admin_notification_email(new_user_email: str) -> None:
    """Best-effort "someone signed up" notice. Purely informational now:
    the account approves itself when the email is verified, nothing is
    waiting on the admin. Silently does nothing if CONSULTCASTAI_ADMIN_EMAIL
    isn't set; the accounts view (GET /admin/accounts) always has the real,
    current list regardless of whether this ever sends."""
    admin_email = os.environ.get("CONSULTCASTAI_ADMIN_EMAIL", "").strip()
    if not admin_email:
        return
    _send(
        admin_email,
        "New ConsultCastAI signup",
        f"<p>{new_user_email} just signed up.</p>"
        "<p>No action needed: the account gets access automatically once they verify their email.</p>"
        "<p>To suspend it, log in and open Profile → Accounts.</p>",
        dev_log_link="(no link for this one, it's informational; accounts are listed under Profile -> Accounts)",
        what="admin signup notification",
    )


def send_team_invite_email(to_email: str, token: str) -> None:
    """The invited teammate never sees Checkout or pays individually — this
    link takes them straight to signup with the invite pre-filled, joining
    the team the moment they set a password (see main.py's /auth/signup)."""
    import urllib.parse
    invite_url = f"{frontend_url()}/?invite_token={urllib.parse.quote(token)}&invite_email={urllib.parse.quote(to_email)}"
    _send(
        to_email,
        "You've been invited to a ConsultCastAI team",
        "<p>You've been invited to join a team on ConsultCastAI.</p>"
        f'<p>Click to accept and create your account: <a href="{invite_url}">{invite_url}</a></p>'
        "<p>No payment needed — you're joining an already-subscribed team.</p>",
        dev_log_link=invite_url,
        what="team invite",
    )
