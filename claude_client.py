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


def _stream(system: str, messages: list[dict], max_tokens: int):
    """Same request as _call with "stream": true, yielding each piece of text
    as it arrives instead of returning the whole reply at the end.

    The response is Server-Sent Events: one "data: {json}" line per event.
    Only two kinds matter here. content_block_delta with a text_delta carries
    the next piece of text; error (an overload part-way through, say) arrives
    in the body of what is already an HTTP 200, so it has to be raised from
    here or it would pass for a reply that just stopped. Everything else
    (message_start, ping, content_block_start/stop, message_delta,
    message_stop) is bookkeeping and is skipped; the stream ending is what
    ends the reply.

    Closing the generator early closes the connection, which stops the
    generation: that's how an interrupted reply stops costing anything."""
    api_key = _require_key()
    payload = json.dumps({
        "model": _MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "stream": True,
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
        response = urllib.request.urlopen(request, timeout=_TIMEOUT)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        print(f"[consultcastai] claude HTTP {e.code}: {body}")
        raise
    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue  # "event: ..." lines and the blank separators; the JSON carries its own type
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
            elif kind == "error":
                error = event.get("error") or {}
                raise RuntimeError(f"Claude stream error: {error.get('type')}: {error.get('message')}")
    finally:
        response.close()


def stream_persona_reply(system_prompt: str, history: list[dict]):
    """get_persona_reply, a piece at a time: same prompt, same history, same
    length limit. Used for avatar sessions, where the reply is spoken as it
    is generated rather than after it's complete."""
    return _stream(system_prompt, history, max_tokens=300)


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
