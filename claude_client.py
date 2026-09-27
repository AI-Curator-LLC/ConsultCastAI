"""
Claude API integration. Claude is the sole conversation engine, both for
in-character persona replies and the end-of-session debrief.

Dependency-free (stdlib urllib) so this has no supply-chain surface beyond
what's already required. API key is server-side only, never sent to the
browser.
"""

import os
import json
import urllib.request
import urllib.error

_KEY_ENV = "ANTHROPIC_API_KEY"
_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-6"  # keep in sync with whatever's current
_TIMEOUT = 30
_ANTHROPIC_VERSION = "2023-06-01"


def _require_key() -> str:
    api_key = (os.environ.get(_KEY_ENV) or "").strip()
    if not api_key:
        raise RuntimeError(
            f"{_KEY_ENV} not set. In production this comes from a secrets "
            "manager, never an env var checked into anything or a key file."
        )
    return api_key


def _call(system: str, messages: list[dict], max_tokens: int) -> str:
    api_key = _require_key()
    payload = json.dumps({
        "model": _MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }).encode("utf-8")
    request = urllib.request.Request(
        _API_URL,
        data=payload,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        print(f"[consultcastai] claude HTTP {e.code}: {body}")
        raise
    blocks = data.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if not text:
        raise RuntimeError("Claude response contained no text content")
    return text.strip()


def get_persona_reply(system_prompt: str, history: list[dict]) -> str:
    """history is the full conversation, oldest first, roles user/assistant.
    The opener (assistant's first line) is part of history already."""
    return _call(system_prompt, history, max_tokens=300)


def get_opener(opener_prompt: str) -> str:
    """Generates the actual first line of a session live, so the call
    genuinely starts at the beginning instead of a fixed pre-written line.
    No conversation history yet, opener_prompt (from
    prompts.build_opener_prompt) carries the full persona/situation context
    as the system prompt, paired with a minimal trigger message."""
    return _call(opener_prompt, [{"role": "user", "content": "Begin the call."}], max_tokens=150)


def get_debrief(debrief_prompt: str) -> str:
    return _call(
        "You are a precise, direct sales coach. Follow the requested format exactly.",
        [{"role": "user", "content": debrief_prompt}],
        max_tokens=600,
    )
