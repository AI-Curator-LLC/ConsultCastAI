"""
Disposable email detection for signup.

A throwaway inbox defeats everything a verified email is now relied on for:
approval is automatic once the email is verified, and the trial is one per
email. So an address at a known disposable service is refused before an
account is created (main.py's /auth/signup, and /team/invite so an owner
isn't left with an invite nobody can accept).

Two files next to this one, both one domain per line:
- disposable_email_domains.txt: the community-maintained blocklist from
  github.com/disposable-email-domains/disposable-email-domains
  (disposable_email_blocklist.conf, public domain / CC0), copied unmodified.
  These services add domains constantly, so it goes stale: to refresh it,
  replace the file with the current upstream one and redeploy.
- disposable_email_domains_extra.txt: this project's own additions.

And two environment variables, for changes that shouldn't wait for a deploy:
- CONSULTCASTAI_BLOCKED_EMAIL_DOMAINS: extra domains to refuse.
- CONSULTCASTAI_ALLOWED_EMAIL_DOMAINS: domains to let through even if listed.
Both comma-separated, read on every check so a change takes effect at once.
"""

import os
from pathlib import Path

_LIST_PATHS = [
    Path(__file__).with_name("disposable_email_domains.txt"),
    Path(__file__).with_name("disposable_email_domains_extra.txt"),
]


def _load() -> frozenset[str]:
    domains: set[str] = set()
    for path in _LIST_PATHS:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            # A missing list: signup still works, it just isn't filtered by
            # that file. Loud in the log rather than silently refusing (or
            # silently allowing).
            print(f"[consultcastai] disposable email list {path.name} unreadable ({type(exc).__name__}: {exc}), nothing blocked by it.")
            continue
        domains.update(
            line.strip().lower() for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
    return frozenset(domains)


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
