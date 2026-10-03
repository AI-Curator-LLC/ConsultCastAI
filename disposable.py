"""
Disposable email detection for signup.

A throwaway inbox defeats everything a verified email is now relied on for:
approval is automatic once the email is verified, and the trial is one per
email. So an address at a known disposable service is refused before an
account is created (main.py's /auth/signup, and /team/invite so an owner
isn't left with an invite nobody can accept).

The list is disposable_email_domains.txt next to this file. It's a curated
set of well-known services, not an exhaustive one, and can be adjusted
without a deploy:
- CONSULTCASTAI_BLOCKED_EMAIL_DOMAINS: extra domains to refuse.
- CONSULTCASTAI_ALLOWED_EMAIL_DOMAINS: domains to let through even if listed.
Both comma-separated, read on every check so a change takes effect at once.
"""

import os
from pathlib import Path

_LIST_PATH = Path(__file__).with_name("disposable_email_domains.txt")


def _load() -> frozenset[str]:
    try:
        lines = _LIST_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        # Missing list: signup still works, it just isn't filtered. Loud in
        # the log rather than silently refusing (or silently allowing).
        print(f"[consultcastai] disposable email list unreadable ({type(exc).__name__}: {exc}), no domains blocked by it.")
        return frozenset()
    return frozenset(
        line.strip().lower() for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


_BUILT_IN = _load()


def _env_domains(name: str) -> set[str]:
    return {d.strip().lower().lstrip("@") for d in os.environ.get(name, "").split(",") if d.strip()}


def domain_count() -> int:
    return len(_BUILT_IN)


def is_disposable(email: str) -> bool:
    """True if the address is at a listed disposable domain or any subdomain
    of one (inbox.mailinator.com is caught by mailinator.com)."""
    domain = email.strip().lower().rpartition("@")[2]
    if not domain:
        return False
    blocked = _BUILT_IN | _env_domains("CONSULTCASTAI_BLOCKED_EMAIL_DOMAINS")
    allowed = _env_domains("CONSULTCASTAI_ALLOWED_EMAIL_DOMAINS")
    labels = domain.split(".")
    # The domain itself, then each parent: a.b.example.com, b.example.com, example.com
    candidates = [".".join(labels[i:]) for i in range(len(labels) - 1)]
    if any(c in allowed for c in candidates):
        return False
    return any(c in blocked for c in candidates)
