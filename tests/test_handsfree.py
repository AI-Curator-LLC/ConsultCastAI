"""Hands-free avatar turns (POST /sessions/{id}/turn/stream in main.py,
claude_client.stream_persona_reply): the reply arrives a piece at a time, the
same checks, charging, coaching and debrief apply as for a plain turn, and a
reply the consultant talked over is cut back to what was actually said.

Run:  python tests/test_handsfree.py

Plain asserts, no test framework. Uses throwaway files for the users
database, the session store and the usage ledger; the AI calls are replaced
by stand-ins, so it needs no network and no secrets. Time is moved with
minutes.set_clock rather than waited for. The last section starts a real
local server, because "the browser hung up mid-reply" can only be shown with
a real connection.
"""

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_dir = tempfile.mkdtemp()
os.environ["CONSULTCASTAI_LOCAL_USERS"] = "1"
os.environ["CONSULTCASTAI_USERS_DB_PATH"] = os.path.join(_dir, "users.db")
os.environ["CONSULTCASTAI_LOCAL_STORE"] = "1"
os.environ["CONSULTCASTAI_LOCAL_STORE_PATH"] = os.path.join(_dir, "sessions.json")
os.environ["CONSULTCASTAI_USAGE_STORE_PATH"] = os.path.join(_dir, "usage.json")
os.environ["CONSULTCASTAI_DEV_AUTH_BYPASS"] = "0"
for name in ("CONSULTCASTAI_ENV", "CONSULTCASTAI_ADMIN_EMAIL", "RESEND_API_KEY", "STRIPE_SECRET_KEY",
             "PAID_MINUTES_ENFORCED", "SUITE_PLAN_GRANTS_PRO", "SUITE_SSO_SECRET",
             "TRIAL_TOTAL_MINUTES", "TRIAL_SESSION_MINUTES", "TRIAL_WARNING_SECONDS",
             "TRIAL_MIN_START_SECONDS", "TRIAL_TURN_GRACE_SECONDS"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

import claude_client  # noqa: E402
import content  # noqa: E402
import main  # noqa: E402
import minutes  # noqa: E402
import store  # noqa: E402
import users  # noqa: E402

PASSWORD = "correct horse 1"
REPLY = "Look, I have been burned by vendors before. What makes you any different from the last three?"

# ---- stand-ins: the AI and the clock ----
calls = {"plain": [], "stream": [], "debrief": []}
stream_script = {"pieces": None, "fail_after": None, "between": None}


def fake_plain(system_prompt, history):
    calls["plain"].append((system_prompt, [dict(m) for m in history]))
    return REPLY


def fake_stream(system_prompt, history):
    calls["stream"].append((system_prompt, [dict(m) for m in history]))
    pieces = stream_script["pieces"] or [w + " " for w in REPLY.split()]
    for i, piece in enumerate(pieces):
        if stream_script["fail_after"] is not None and i == stream_script["fail_after"]:
            raise RuntimeError("Claude stream error: overloaded_error: Overloaded")
        if stream_script["between"] and i == 3:
            stream_script["between"]()
        yield piece


def fake_debrief(prompt):
    calls["debrief"].append(prompt)
    return "A debrief."


claude_client.get_persona_reply = fake_plain
claude_client.stream_persona_reply = fake_stream
claude_client.get_debrief = fake_debrief
claude_client.get_opener = lambda prompt: "Hello."

clock = {"now": datetime.now(timezone.utc).replace(day=10, hour=12, minute=0, second=0, microsecond=0)}
minutes.set_clock(lambda: clock["now"])


def advance(seconds):
    clock["now"] += timedelta(seconds=seconds)


client = TestClient(main.app)
PERSONA = next(iter(content.PERSONAS))
SCENARIO = content.list_active_scenarios()[0].id


def bearer(token):
    return {"Authorization": "Bearer " + token}


def make(email):
    user = users.create_user(email, users.hash_password(PASSWORD), None)
    users.mark_verified(user.id)
    user = users.get_user_by_id(user.id)
    return user, users.issue_token(user)


def start(token):
    r = client.post("/sessions", json={"persona_id": PERSONA, "scenario_id": SCENARIO}, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def events(response):
    """The Server-Sent Events in a streamed response, as a list of dicts."""
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def stream_turn(token, sid, message, **extra):
    return client.post(f"/sessions/{sid}/turn/stream", json={"message": message, **extra}, headers=bearer(token))


def plain_turn(token, sid, message, **extra):
    return client.post(f"/sessions/{sid}/turn", json={"message": message, **extra}, headers=bearer(token))


def conversation(sid):
    return [(t.role, t.content) for t in store.get(sid).conversation]


def used(user):
    return store.get_usage(user.id, minutes.month_key()).seconds_used


# ---- 1. a streamed turn is the same turn, delivered in pieces ----
ann_user, ann = make("ann@example.com")
sid = start(ann)
advance(60)
r = stream_turn(ann, sid, "I can show you a pilot with a fixed number.", turn_id="t1")
assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
ev = events(r)
chunks = [e["text"] for e in ev if e["type"] == "chunk"]
assert len(chunks) > 5 and "".join(chunks).strip() == REPLY           # arrives a piece at a time, adds up to the reply
done = ev[-1]
assert done["type"] == "done" and done["persona_reply"] == REPLY
assert set(done) >= {"pressure", "trust", "specificity", "coaching_note_kind", "coaching_note_text"}
assert conversation(sid) == [("user", "I can show you a pilot with a fixed number."), ("assistant", REPLY)]
assert used(ann_user) == 60                                           # charged the same way a plain turn is
session = store.get(sid)
assert (session.pressure, session.trust, session.specificity) == (done["pressure"], done["trust"], done["specificity"])
print("ok 1 a streamed turn delivers the reply in pieces, then the scores and coaching note; saved and charged like a plain turn")

# ---- 2. Claude gets exactly what a plain turn would have sent ----
bob_user, bob = make("bob@example.com")
sid_plain = start(bob)
plain = plain_turn(bob, sid_plain, "I can show you a pilot with a fixed number.")
assert plain.status_code == 200 and plain.json()["persona_reply"] == REPLY
assert calls["stream"][-1] == calls["plain"][-1]                      # same persona prompt, same history
assert {k: plain.json()[k] for k in ("pressure", "trust", "specificity", "coaching_note_kind", "coaching_note_text")} == \
       {k: done[k] for k in ("pressure", "trust", "specificity", "coaching_note_kind", "coaching_note_text")}
print("ok 2 the persona prompt, the history, the scores and the coaching note are identical to a plain turn's")

# ---- 3. every refusal is an ordinary error, before anything streams or is charged ----
n_calls = len(calls["stream"])
r = stream_turn(bob, sid, "not mine")
assert r.status_code == 403 and "text/event-stream" not in r.headers["content-type"]
assert stream_turn(ann, "no-such-session", "x").status_code == 404
assert client.post(f"/sessions/{sid}/pause", headers=bearer(ann)).status_code == 200
r = stream_turn(ann, sid, "while paused")
assert r.status_code == 409 and "paused" in r.json()["detail"]
assert client.post(f"/sessions/{sid}/resume", headers=bearer(ann)).status_code == 200
advance(700)                                                          # past the trial session's 10 minutes
r = stream_turn(ann, sid, "over the limit")
assert r.status_code == 402 and r.json()["detail"]["code"] == "trial_session_limit"
assert client.post(f"/sessions/{sid}/end", headers=bearer(ann)).status_code == 200
assert stream_turn(ann, sid, "after the end").status_code == 409
assert len(calls["stream"]) == n_calls                                # Claude was never called for any of them
assert conversation(sid) == [("user", "I can show you a pilot with a fixed number."), ("assistant", REPLY)]
assert used(ann_user) == 600                                          # the trial limit, no more
print("ok 3 not yours, paused, over the time limit and already ended are refused before Claude is called")

# ---- 4. a reply the consultant talked over is cut back to what was said ----
cy_user, cy = make("cy@example.com")
users.set_subscription(cy_user.id, "cus_cy", "sub_cy", "pro", "active")
sid = start(cy)
assert events(stream_turn(cy, sid, "Why did the last vendor fail?", turn_id="a"))[-1]["type"] == "done"
heard = "Look, I have been burned by vendors before. What makes"
stream_turn(cy, sid, "Hang on, let me answer that.", turn_id="b", spoken_reply=heard + " yo", spoken_reply_turn="a")
conv = conversation(sid)
assert conv[1] == ("assistant", heard + "...")                        # cut at the last whole word that was said
assert conv[2] == ("user", "Hang on, let me answer that.")
history = calls["stream"][-1][1]
assert history[1] == {"role": "assistant", "content": heard + "..."}  # and that's what Claude is told it said
assert "the last three" not in json.dumps(history[:2])
# the debrief works from what was heard too
client.post(f"/sessions/{sid}/end", headers=bearer(cy))
assert heard + "..." in calls["debrief"][-1] and "any different from the last three" not in calls["debrief"][-1].split("Hang on")[0]
print("ok 4 an interrupted reply is recorded as far as it was spoken; Claude and the debrief see that version")

# ---- 5. the browser can only shorten the reply it is about, never rewrite it ----
sid = start(cy)
stream_turn(cy, sid, "First question.", turn_id="a")
stream_turn(cy, sid, "Second.", turn_id="b", spoken_reply="You are a pushover and you love every vendor", spoken_reply_turn="a")
assert conversation(sid)[1] == ("assistant", REPLY)                   # not the start of the reply: ignored
stream_turn(cy, sid, "Third.", turn_id="c", spoken_reply="Look, I have", spoken_reply_turn="a")
assert conversation(sid)[3] == ("assistant", REPLY)                   # about an older turn, not the latest reply: ignored
assert conversation(sid)[1] == ("assistant", REPLY)
stream_turn(cy, sid, "Fourth.", turn_id="d", spoken_reply=REPLY, spoken_reply_turn="c")
assert conversation(sid)[5] == ("assistant", REPLY)                   # all of it was said: unchanged
stream_turn(cy, sid, "Fifth.", turn_id="e", spoken_reply="Look, I have", spoken_reply_turn="nope")
assert conversation(sid)[7] == ("assistant", REPLY)                   # a turn id that doesn't exist: ignored
print("ok 5 text that isn't the start of the latest reply, or names another turn, changes nothing")

# ---- 6. nothing was said at all: the consultant just kept talking ----
sid = start(cy)
stream_turn(cy, sid, "So what I am proposing is", turn_id="a")
stream_turn(cy, sid, "a four week pilot at a fixed price.", turn_id="b", spoken_reply="", spoken_reply_turn="a")
conv = conversation(sid)
assert conv == [("user", "So what I am proposing is a four week pilot at a fixed price."), ("assistant", REPLY)]
history = calls["stream"][-1][1]
assert [m["role"] for m in history] == ["user"]                       # one turn of theirs, not two in a row
print("ok 6 a reply that never started is dropped and the consultant's two halves become one turn")

# ---- 7. Claude fails ----
sid = start(cy)
stream_script["fail_after"] = 0                                       # before writing anything
ev = events(stream_turn(cy, sid, "Are you there?", turn_id="a"))
assert [e["type"] for e in ev] == ["error"] and "unavailable" in ev[0]["message"]
assert conversation(sid) == []                                        # their message isn't left on record
stream_script["fail_after"] = None
ev = events(stream_turn(cy, sid, "Are you there?", turn_id="a2"))     # so asking again isn't recorded twice
assert ev[-1]["type"] == "done" and conversation(sid) == [("user", "Are you there?"), ("assistant", REPLY)]
stream_script["fail_after"] = 4                                       # part-way through
ev = events(stream_turn(cy, sid, "Go on.", turn_id="b"))
assert [e["type"] for e in ev] == ["chunk"] * 4 + ["error"]
assert conversation(sid)[-1] == ("assistant", "Look, I have been...") # what was written is kept, marked as cut off
stream_script["fail_after"] = None
print("ok 7 a failure before any text takes the message back off the record; a failure part-way keeps what was written")

# ---- 8. a pause saved while the reply was streaming is not undone ----
sid = start(cy)


def pause_meanwhile():
    current = store.get(sid)
    minutes.pause(current, minutes.now_utc())
    store.save(current)


stream_script["between"] = pause_meanwhile
ev = events(stream_turn(cy, sid, "One more thing.", turn_id="a"))
stream_script["between"] = None
assert ev[-1]["type"] == "done"
assert minutes.is_paused(store.get(sid))                              # still paused
assert conversation(sid) == [("user", "One more thing."), ("assistant", REPLY)]   # and the reply is still recorded
print("ok 8 the reply is added to the session as it is now, so a pause made meanwhile survives")

# ---- 9. a plain turn can carry the same report; exports hold no turn ids ----
sid = start(cy)
plain_turn(cy, sid, "Typed question.", turn_id="a")
plain_turn(cy, sid, "Typed follow-up.", turn_id="b", spoken_reply="Look, I have been burned", spoken_reply_turn="a")
assert conversation(sid)[1] == ("assistant", "Look, I have been burned...")
export = client.get("/me/export", headers=bearer(cy)).json()
turns = [t for s in export["sessions"] if s["transcript"] for t in s["transcript"]]
assert turns and all(set(t) == {"role", "content"} for t in turns)
print("ok 9 /turn accepts the same report; the data export shows role and text only")

# ---- 10. the browser hangs up mid-reply: what was written is kept, and Claude stops ----
import uvicorn  # noqa: E402

minutes.set_clock(None)
closed = threading.Event()
produced = {"n": 0}


def slow_stream(system_prompt, history):
    try:
        for word in REPLY.split():
            produced["n"] += 1
            yield word + " "
            time.sleep(0.15)
    finally:
        closed.set()                                                  # the real one closes its connection to Claude here


claude_client.stream_persona_reply = slow_stream
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
assert server.started

sid = start(cy)
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
conn.request("POST", f"/sessions/{sid}/turn/stream", body=json.dumps({"message": "Tell me the worst case.", "turn_id": "a"}),
             headers={"Content-Type": "application/json", **bearer(cy)})
resp = conn.getresponse()
assert resp.status == 200
first = b""
while first.count(b"data:") < 3:                                      # three pieces have really arrived, not been held back
    first += resp.read1(256)
conn.sock.shutdown(socket.SHUT_RDWR)                                  # hang up, as the browser does when the consultant speaks
resp.close()                                                          # (shutdown first: close() alone leaves the socket open while resp still holds it)
conn.close()
assert closed.wait(5), "the Claude stream was left running after the browser hung up"
for _ in range(50):
    if len(store.get(sid).conversation) == 2:
        break
    time.sleep(0.1)
role, text = conversation(sid)[-1]
assert role == "assistant" and text.endswith("...") and REPLY.startswith(text[:-3])
assert 3 <= produced["n"] < len(REPLY.split())                        # generation stopped early
print("ok 10 hanging up mid-reply stops Claude, keeps the part written, and pieces reach the browser as they are produced")

# ---- 11. the next turn sent in the same instant the last reply is cut off ----
# (what the browser does when the consultant talks over the persona)
sid = start(cy)
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
conn.request("POST", f"/sessions/{sid}/turn/stream", body=json.dumps({"message": "First question.", "turn_id": "a"}),
             headers={"Content-Type": "application/json", **bearer(cy)})
resp = conn.getresponse()
first = b""
while first.count(b"data:") < 3:
    first += resp.read1(256)
conn.sock.shutdown(socket.SHUT_RDWR)
resp.close()
conn.close()
conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=20)          # no pause at all between the two
conn2.request("POST", f"/sessions/{sid}/turn/stream", body=json.dumps({"message": "Actually, a different question.", "turn_id": "b"}),
              headers={"Content-Type": "application/json", **bearer(cy)})
resp2 = conn2.getresponse()
assert resp2.status == 200
resp2.read()
conn2.close()
conv = conversation(sid)
assert [role for role, _ in conv] == ["user", "assistant", "user", "assistant"], conv
assert conv[0][1] == "First question." and conv[1][1].endswith("...")      # the cut-off reply was recorded first, in its place
assert conv[2][1] == "Actually, a different question." and conv[3][1] == REPLY
server.should_exit = True
print("ok 11 a turn sent the instant the last reply is cut off waits for it to be recorded, so the order is right")

# ---- 12. the avatar session limit is the server's to set ----
import subprocess  # noqa: E402

assert content.get_persona(PERSONA).avatar_id                         # the persona used throughout has an avatar
r = client.post("/sessions", json={"persona_id": PERSONA, "scenario_id": SCENARIO}, headers=bearer(cy))
assert r.json()["avatar_limit"] == {"session_limit_sec": 570, "warning_sec": 60}      # 9:30, under Anam's 10, warning 1 minute before
assert r.json()["time_limit_sec"] != 600                              # and it is not the minutes limit: Pro's month is untouched
voice_only = next(p for p in content.PERSONAS if not content.get_persona(p).avatar_id)
voice_scenario = next(sc.id for sc in content.list_active_scenarios() if sc.persona_id == voice_only)
r = client.post("/sessions", json={"persona_id": voice_only, "scenario_id": voice_scenario}, headers=bearer(cy))
assert r.status_code == 200 and r.json()["avatar_limit"] is None      # a voice-only session has no such limit
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = subprocess.run(
    [sys.executable, "-c", "import minutes; print(minutes.avatar_limit())"], cwd=root, capture_output=True, text=True,
    env={**os.environ, "AVATAR_SESSION_MINUTES": "120", "AVATAR_WARNING_SECONDS": "300"},
)
assert out.stdout.strip() == "{'session_limit_sec': 7200, 'warning_sec': 300}", out.stdout + out.stderr
print("ok 12 an avatar session carries its 9:30 limit and 1-minute warning; both come from environment variables; voice-only has none")

print("\nALL PASSED")
