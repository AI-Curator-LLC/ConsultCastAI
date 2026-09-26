"""
Transactional email via Resend's REST API (one HTTP call, no SDK).

Email verification is a SOFT requirement: a failure here must never block
signup, so send_verification_email() swallows and logs every error rather
than raising. The user already has a working session token from
/auth/signup regardless of whether this email ever arrives.

Env vars:
- RESEND_API_KEY: unset means "log instead of send" (local dev), never an error.
- CONSULTCASTAI_EMAIL_FROM: defaults to Resend's shared test sender, which only
  delivers to your own Resend account's email. Switch to a verified domain
  (e.g. "ConsultCastAI <noreply@ai-curator.ai>") once it's verified in Resend.
- CONSULTCASTAI_BACKEND_URL: public base URL of this API, used to build the
  verification link.
"""

import json
import os
import urllib.error
import urllib.request

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "ConsultCastAI <onboarding@resend.dev>"


def backend_url() -> str:
    return os.environ.get("CONSULTCASTAI_BACKEND_URL", "http://localhost:8081").rstrip("/")


def send_verification_email(to_email: str, token: str) -> None:
    verify_url = f"{backend_url()}/auth/verify?token={token}"
    api_key = os.environ.get("RESEND_API_KEY", "").strip()

    if not api_key:
        if os.environ.get("CONSULTCASTAI_ENV") == "production":
            print("[consultcastai] RESEND_API_KEY is not set, skipping verification email")
        else:
            # Local dev convenience only. Never log a live token in production.
            print(f"[consultcastai] RESEND_API_KEY not set, verification link for {to_email}: {verify_url}")
        return

    body = json.dumps({
        "from": os.environ.get("CONSULTCASTAI_EMAIL_FROM", _DEFAULT_FROM),
        "to": [to_email],
        "subject": "Verify your ConsultCastAI account",
        "html": (
            "<p>Welcome to ConsultCastAI.</p>"
            f'<p>Click to verify your email: <a href="{verify_url}">{verify_url}</a></p>'
            "<p>If you didn't create this account, you can ignore this email.</p>"
        ),
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
        print(f"[consultcastai] verification email failed for {to_email}: HTTP {exc.code} {detail}")
    except Exception as exc:
        print(f"[consultcastai] verification email failed for {to_email}: {type(exc).__name__}: {exc}")
