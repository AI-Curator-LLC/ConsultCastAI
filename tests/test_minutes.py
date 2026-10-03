"""Paid practice minutes (minutes.py, topups.py, the /billing/topup-checkout-session
route and the webhook in main.py, auth.require_active_plan): Pro's and Team's
monthly minutes, extra minutes bought as a one-time payment, the on/off
switch, and proof that the trial is unchanged.

Run:  python tests/test_minutes.py

Plain asserts, no test framework. Uses throwaway files for the users
database, the session store and the usage ledger; the suite, the AI calls
and Stripe are all replaced by stand-ins, so it needs no network and no
secrets. Time is moved with minutes.set_clock rather than waited for.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_dir = tempfile.mkdtemp()
os.environ["CONSULTCASTAI_LOCAL_USERS"] = "1"
os.environ["CONSULTCASTAI_USERS_DB_PATH"] = os.path.join(_dir, "users.db")
os.environ["CONSULTCASTAI_LOCAL_STORE"] = "1"
os.environ["CONSULTCASTAI_LOCAL_STORE_PATH"] = os.path.join(_dir, "sessions.json")
os.environ["CONSULTCASTAI_USAGE_STORE_PATH"] = os.path.join(_dir, "usage.json")
os.environ["CONSULTCASTAI_DEV_AUTH_BYPASS"] = "0"
os.environ["SUITE_SSO_SECRET"] = "consultcastai-shared-secret-0123456789abcdef"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_only"
for name in ("CONSULTCASTAI_ENV", "CONSULTCASTAI_ADMIN_EMAIL", "RESEND_API_KEY", "STRIPE_SECRET_KEY",
             "STRIPE_PRICE_ID_TOPUP", "PAID_MINUTES_ENFORCED", "SUITE_PLAN_GRANTS_PRO", "SUITE_PLAN_MAX_AGE_DAYS",
             "PRO_MONTHLY_MINUTES", "TEAM_MONTHLY_MINUTES", "TOPUP_BLOCK_MINUTES", "TOPUP_PRICE_LABEL",
             "PAID_WARNING_SECONDS", "PAID_MIN_START_SECONDS",
             "TRIAL_TOTAL_MINUTES", "TRIAL_SESSION_MINUTES", "TRIAL_WARNING_SECONDS",
             "TRIAL_MIN_START_SECONDS", "TRIAL_TURN_GRACE_SECONDS"):
    os.environ.pop(name, None)

import stripe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import claude_client  # noqa: E402
import content  # noqa: E402
import main  # noqa: E402
import minutes  # noqa: E402
import store  # noqa: E402
import suite_sso  # noqa: E402
import teams  # noqa: E402
import topups  # noqa: E402
import users  # noqa: E402

PASSWORD = "correct horse 1"
MIN = 60

# ---- stand-ins: the AI, the clock, the suite, Stripe ----
claude_client.get_persona_reply = lambda system_prompt, history: "Go on."
claude_client.get_debrief = lambda prompt: "A debrief."
claude_client.get_opener = lambda prompt: "Hello."

# Starts on the 10th of the real current month, so a suite answer stamped
# with the real time is never "in the future" by more than a few weeks, and
# only ever moves forward.
_real = datetime.now(timezone.utc)
clock = {"now": _real.replace(day=10, hour=12, minute=0, second=0, microsecond=0)}
minutes.set_clock(lambda: clock["now"])


def advance(seconds):
    clock["now"] += timedelta(seconds=seconds)


def next_month():
    """Move to the 2nd of the next calendar month."""
    now = clock["now"]
    clock["now"] = datetime(now.year + (now.month == 12), now.month % 12 + 1, 2, 9, 0, tzinfo=timezone.utc)


class FakeSuite:
    def __init__(self):
        self.on = False
        self.codes = {}
        self.active = {}

    def request(self, method, path, payload=None):
        if path == "/api/sso/status":
            return {"enabled": self.on}
        if path == "/api/sso/token":
            person = self.codes.pop(payload["code"])
            self.active[person["sid"]] = person
            return person
        if path == "/api/sso/session":
            person = self.active.get(payload["sid"])
            return {"active": True, **person} if person else {"active": False, "reason": "signed_out"}
        return {"ok": True}

    def switch(self, on):
        self.on = on
        suite_sso.forget_status()


suite = FakeSuite()
suite_sso._request = suite.request

# Stripe: no call ever leaves this process. Checkout creation is recorded
# and answered with a made-up page; the webhook's signature check is
# replaced by "the body is the event".
checkouts = []


class _FakeCheckout:
    def __init__(self, n):
        self.id = f"cs_test_{n}"
        self.url = f"https://checkout.stripe.test/{self.id}"


def _fake_create(**params):
    checkouts.append(params)
    return _FakeCheckout(len(checkouts))


stripe.checkout.Session.create = _fake_create
stripe.Webhook.construct_event = lambda payload, sig, secret: json.loads(payload)
main.stripe.api_key = "sk_test_stand_in"

client = TestClient(main.app)
PERSONA = next(iter(content.PERSONAS))
SCENARIO = content.list_active_scenarios()[0].id


def bearer(token):
    return {"Authorization": "Bearer " + token}


def make(email, verified=True):
    """An account made the way signup makes one, minus the HTTP (signup is
    capped per IP). Returns (account, login token)."""
    user = users.create_user(email, users.hash_password(PASSWORD), None)
    if verified:
        users.mark_verified(user.id)
    user = users.get_user_by_id(user.id)
    return user, users.issue_token(user)


def subscribe(user, sub_id):
    users.set_subscription(user.id, "cus_" + sub_id, sub_id, "pro", "active")


def me(token):
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def start(token):
    return client.post("/sessions", json={"persona_id": PERSONA, "scenario_id": SCENARIO}, headers=bearer(token))


def turn(token, sid):
    return client.post(f"/sessions/{sid}/turn", json={"message": "x"}, headers=bearer(token))


def end(token, sid):
    r = client.post(f"/sessions/{sid}/end", headers=bearer(token))
    assert r.status_code == 200 and r.json()["debrief"] == "A debrief.", r.text
    return r.json()


def practice(token, seconds):
    """One whole session of `seconds`: start, one exchange at the end, end."""
    r = start(token)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    advance(seconds)
    assert turn(token, sid).status_code == 200
    return end(token, sid)


def webhook(event_type, obj):
    r = client.post("/billing/webhook", content=json.dumps({"type": event_type, "data": {"object": obj}}),
                    headers={"stripe-signature": "stand-in"})
    assert r.status_code == 200 and r.json() == {"received": True}, r.text


def paid_checkout(params, payment_id, payment_status="paid"):
    """The Checkout object Stripe would send for a checkout made with these params."""
    return {"id": payment_id, "object": "checkout.session", "mode": params["mode"], "payment_status": payment_status,
            "client_reference_id": params["client_reference_id"], "metadata": params["metadata"],
            "customer": params.get("customer"), "subscription": None}


def month_used(owner_id):
    return store.get_usage(owner_id, minutes.month_key()).seconds_used


# ---- 1. the trial is exactly as it was ----
trial_user, trial = make("trial@example.com")
info = me(trial)
assert info["paid_minutes"] is None
assert info["trial"] == {"total_sec": 600, "used_sec": 0, "remaining_sec": 600, "session_limit_sec": 600,
                         "warning_sec": 60, "exhausted": False, "previously_used": False}
r = start(trial)
assert r.status_code == 200 and r.json()["time_limit_sec"] == 600 and r.json()["paid_minutes"] is None
assert r.json()["trial"]["remaining_sec"] == 600
sid = r.json()["id"]
advance(9 * MIN)
assert turn(trial, sid).status_code == 200
advance(MIN + 5)                                    # 5 seconds past the limit: inside the server's slack
assert turn(trial, sid).status_code == 200
advance(20)                                         # past the slack
r = turn(trial, sid)
assert r.status_code == 402 and r.json()["detail"]["code"] == "trial_session_limit"
out = end(trial, sid)
assert out["trial"]["used_sec"] == 600 and out["trial"]["exhausted"] is True and out["paid_minutes"] is None
assert store.get_total_seconds(trial_user.id) == 600           # time past the limit is never charged
r = start(trial)
assert r.status_code == 402 and r.json()["detail"]["code"] == "trial_exhausted"
assert store.get_topup_used(trial_user.id) == 0 and users.get_trial_record("trial@example.com") is not None
r = client.post("/billing/topup-checkout-session", headers=bearer(trial))
assert r.status_code == 400                         # extra minutes are not for a trial (and no price is set yet)
print("ok 1  trial unchanged: 10 minutes, same status object, same refusals, never charged past the limit")

# ---- 2. Pro: 120 minutes a month, counted down, refused at zero ----
pro_user, pro = make("pro@example.com")
subscribe(pro_user, "sub_pro")
info = me(pro)
assert info["trial"] is None and info["plan_minutes"] == {"pro_min": 120, "team_min": 1000}
paid = info["paid_minutes"]
assert paid["plan"] == "pro" and paid["via_suite"] is False and paid["is_team_member"] is False
assert paid["included_sec"] == 7200 and paid["remaining_sec"] == 7200 and paid["topup_sec"] == 0
assert paid["exhausted"] is False and paid["warning_sec"] == 60 and paid["can_buy"] is False
now = clock["now"]
assert paid["resets_on"] == datetime(now.year + (now.month == 12), now.month % 12 + 1, 1).date().isoformat()
r = start(pro)
assert r.status_code == 200 and r.json()["time_limit_sec"] == 7200 and r.json()["trial"] is None
sid = r.json()["id"]
advance(36 * MIN)
assert turn(pro, sid).status_code == 200
out = end(pro, sid)
assert out["paid_minutes"]["remaining_sec"] == 84 * MIN and out["paid_minutes"]["used_sec"] == 36 * MIN
assert me(pro)["paid_minutes"]["included_remaining_sec"] == 84 * MIN
r = start(pro)
assert r.json()["time_limit_sec"] == 84 * MIN       # no per-session cap: the limit is what the month has left
sid = r.json()["id"]
advance(84 * MIN + 5)
assert turn(pro, sid).status_code == 200            # inside the slack
advance(30)
r = turn(pro, sid)
assert r.status_code == 402 and r.json()["detail"]["code"] == "minutes_session_limit"
out = end(pro, sid)                                 # the debrief is still produced
assert out["paid_minutes"]["remaining_sec"] == 0 and out["paid_minutes"]["exhausted"] is True
assert month_used(pro_user.id) == 7200              # exactly the allowance: time past the limit is not charged
r = start(pro)
detail = r.json()["detail"]
assert r.status_code == 402 and detail["code"] == "minutes_exhausted"
assert "this month's 120 minutes" in detail["message"] and "reset on" in detail["message"]
assert "add" not in detail["message"].lower()       # no price set up: nothing is offered
print("ok 2  Pro: 120 minutes a month, counts down, session limit is what is left, refused at zero")

# under a minute left counts as used up
low_user, low = make("low@example.com")
subscribe(low_user, "sub_low")
store.add_seconds(low_user.id, minutes.month_key(), 7200 - 45)
assert me(low)["paid_minutes"]["exhausted"] is True and start(low).status_code == 402
print("ok 2b under a minute left: a new session cannot be started")

# ---- 3. a new month restores the allowance ----
next_month()
paid = me(pro)["paid_minutes"]
assert paid["remaining_sec"] == 7200 and paid["used_sec"] == 0 and paid["exhausted"] is False
out = practice(pro, 10 * MIN)
assert out["paid_minutes"]["remaining_sec"] == 110 * MIN
assert store.get_total_seconds(pro_user.id) == 7200 + 600      # last month's figure is still on record
print("ok 3  month rollover: the allowance is back, earlier months stay on record")

# ---- 4. Team: 1,000 minutes a month, one pool for the whole team ----
owner_user, owner = make("owner@acme-consulting.com")
teams.create_team("team-1", owner_user.id, "cus_team", "sub_team", "active", seat_limit=5)
users.set_team(owner_user.id, "team-1")
ann_user, ann = make("ann@acme-consulting.com")
bob_user, bob = make("bob@acme-consulting.com")
users.set_team(ann_user.id, "team-1")
users.set_team(bob_user.id, "team-1")
paid = me(owner)["paid_minutes"]
assert paid["plan"] == "team" and paid["included_sec"] == 60000 and paid["is_team_member"] is False
assert me(ann)["paid_minutes"]["is_team_member"] is True
practice(ann, 600 * MIN)
assert month_used("team-1") == 600 * MIN and month_used(ann_user.id) == 600 * MIN
for token in (owner, ann, bob):                    # everyone sees the same pool
    assert me(token)["paid_minutes"]["remaining_sec"] == 400 * MIN
r = start(bob)
assert r.json()["time_limit_sec"] == 400 * MIN
bob_sid = r.json()["id"]
advance(399 * MIN + 30)
assert turn(bob, bob_sid).status_code == 200
end(bob, bob_sid)
assert me(owner)["paid_minutes"]["exhausted"] is True
r = start(ann)
assert r.status_code == 402 and r.json()["detail"]["code"] == "minutes_exhausted"
assert r.json()["detail"]["message"].startswith("Your team has used this month's 1,000 minutes. They reset on ")
assert "team owner" not in r.json()["detail"]["message"]          # nothing is on sale yet, so nobody is sent to ask
os.environ["STRIPE_PRICE_ID_TOPUP"] = "price_topup_test"
assert start(ann).json()["detail"]["message"].endswith("Ask your team owner to add minutes.")
assert start(owner).json()["detail"]["message"].endswith("You can add 100 minutes for $40.")
assert me(ann)["paid_minutes"]["topup_available"] is True and me(ann)["paid_minutes"]["can_buy"] is False
os.environ.pop("STRIPE_PRICE_ID_TOPUP")
print("ok 4  Team: 1,000 minutes shared by the owner and two members; one pool, refused for all at zero")

# two members practicing at the same time cannot spend the pool twice
next_month()
a_sid = start(ann).json()["id"]
b_sid = start(bob).json()["id"]
advance(500 * MIN)
assert turn(ann, a_sid).status_code == 200 and turn(bob, b_sid).status_code == 200
assert month_used("team-1") == 1000 * MIN
advance(MIN)
r = turn(ann, a_sid)
assert r.status_code == 402 and r.json()["detail"]["code"] == "minutes_session_limit"
end(ann, a_sid)
end(bob, b_sid)
print("ok 4b two members at once: turns are refused once the shared pool is gone")

# ---- 5. buying extra minutes ----
# no price set up: no button, the endpoint says so, everything else works
assert me(pro)["paid_minutes"]["can_buy"] is False and me(owner)["paid_minutes"]["can_buy"] is False
r = client.post("/billing/topup-checkout-session", headers=bearer(pro))
assert r.status_code == 400 and r.json()["detail"]["code"] == "topup_unavailable" and not checkouts
assert start(pro).status_code == 200
print("ok 5  no price configured: buying is not offered and the endpoint refuses clearly")

os.environ["STRIPE_PRICE_ID_TOPUP"] = "price_topup_test"
assert me(pro)["paid_minutes"]["can_buy"] is True and me(owner)["paid_minutes"]["can_buy"] is True
assert me(ann)["paid_minutes"]["can_buy"] is False
assert me(pro)["paid_minutes"]["topup_block_min"] == 100 and me(pro)["paid_minutes"]["topup_price_label"] == "$40"
assert client.post("/billing/topup-checkout-session").status_code == 401
r = client.post("/billing/topup-checkout-session", headers=bearer(ann))
assert r.status_code == 403 and r.json()["detail"]["message"] == "Ask your team owner to add minutes." and not checkouts
r = client.post("/billing/topup-checkout-session", headers=bearer(trial))
assert r.status_code == 400 and r.json()["detail"]["code"] == "topup_needs_plan" and not checkouts
r = client.post("/billing/topup-checkout-session", headers=bearer(owner))
assert r.status_code == 200 and r.json()["checkout_url"].startswith("https://checkout.stripe.test/")
team_checkout = checkouts[-1]
assert team_checkout["mode"] == "payment" and team_checkout["line_items"] == [{"price": "price_topup_test", "quantity": 1}]
assert team_checkout["metadata"] == {"kind": "topup", "owner_kind": "team", "owner_id": "team-1", "topup_minutes": "100"}
assert team_checkout["customer"] == "cus_team" and team_checkout["success_url"].endswith("/?payment=topup_success")
assert client.post("/billing/topup-checkout-session", headers=bearer(pro)).status_code == 200
pro_checkout = checkouts[-1]
assert pro_checkout["metadata"]["owner_kind"] == "user" and pro_checkout["metadata"]["owner_id"] == pro_user.id
assert topups.balance_seconds("team-1") == 0 and topups.balance_seconds(pro_user.id) == 0   # nothing until Stripe confirms
print("ok 6  a team member cannot buy, the owner and a Pro account can; nothing is credited by asking")

# the webhook credits the block once, however many times it arrives
event = paid_checkout(team_checkout, "cs_team_1")
webhook("checkout.session.completed", event)
assert topups.balance_seconds("team-1") == 6000
webhook("checkout.session.completed", event)                       # Stripe retries
webhook("checkout.session.async_payment_succeeded", event)         # a second event about the same payment
assert topups.balance_seconds("team-1") == 6000
for token in (owner, ann, bob):                                    # credited to the team, seen by all of it
    assert me(token)["paid_minutes"]["topup_sec"] == 6000
assert topups.balance_seconds(owner_user.id) == 0
# an unpaid checkout credits nothing until the payment itself is confirmed
webhook("checkout.session.completed", paid_checkout(pro_checkout, "cs_pro_1", payment_status="unpaid"))
assert topups.balance_seconds(pro_user.id) == 0
webhook("checkout.session.async_payment_succeeded", paid_checkout(pro_checkout, "cs_pro_1"))
assert topups.balance_seconds(pro_user.id) == 6000
# a second, separate payment is a second block; a made-up payload is ignored
webhook("checkout.session.completed", paid_checkout(team_checkout, "cs_team_2"))
assert topups.balance_seconds("team-1") == 12000
webhook("checkout.session.completed", {"id": "cs_bad", "payment_status": "paid", "metadata": {"kind": "topup", "owner_kind": "user"}})
webhook("checkout.session.completed", {"id": "cs_bad2", "payment_status": "paid",
                                       "metadata": {"kind": "topup", "owner_kind": "user", "owner_id": pro_user.id, "topup_minutes": "999999"}})
assert topups.balance_seconds(pro_user.id) == 6000
# the real library's own object type (not a dict) is handled too
stripe.Webhook.construct_event = lambda payload, sig, secret: stripe.Event.construct_from(json.loads(payload), "sk_test")
webhook("checkout.session.completed", paid_checkout(pro_checkout, "cs_pro_1"))
assert topups.balance_seconds(pro_user.id) == 6000
sub_user, sub_token = make("newsub@example.com")
webhook("checkout.session.completed", {"id": "cs_sub", "object": "checkout.session", "mode": "subscription", "client_reference_id": sub_user.id,
                                       "metadata": {"plan": "pro_monthly"}, "subscription": "sub_new", "customer": "cus_new"})
assert users.subscription_active(users.get_user_by_id(sub_user.id)) and me(sub_token)["paid_minutes"]["plan"] == "pro"
stripe.Webhook.construct_event = lambda payload, sig, secret: json.loads(payload)
print("ok 7  top-up credited exactly once for a repeated or replayed webhook; subscriptions still handled")

# ---- 6. extra minutes are used only after the month's own, and outlast the month ----
next_month()
paid = me(pro)["paid_minutes"]
assert paid["included_remaining_sec"] == 7200 and paid["topup_sec"] == 6000 and paid["remaining_sec"] == 13200
r = start(pro)
assert r.json()["time_limit_sec"] == 13200
sid = r.json()["id"]
advance(60 * MIN)
assert turn(pro, sid).status_code == 200
paid = me(pro)["paid_minutes"]
assert paid["included_remaining_sec"] == 3600 and paid["topup_sec"] == 6000        # untouched while the month has minutes
advance(70 * MIN)
assert turn(pro, sid).status_code == 200
out = end(pro, sid)["paid_minutes"]
assert out["included_remaining_sec"] == 0 and out["topup_sec"] == 6000 - 10 * MIN and out["exhausted"] is False
assert store.get_topup_used(pro_user.id) == 10 * MIN and month_used(pro_user.id) == 130 * MIN
assert practice(pro, 5 * MIN)["paid_minutes"]["topup_sec"] == 6000 - 15 * MIN      # the month is used: straight from the extra
print("ok 8  extra minutes are used only once the month's minutes are gone")

next_month()
paid = me(pro)["paid_minutes"]
assert paid["included_remaining_sec"] == 7200 and paid["topup_sec"] == 6000 - 15 * MIN
assert store.get_total_seconds(pro_user.id) == 7200 + 600 + 135 * MIN              # the bought-minutes record adds nothing to totals
print("ok 9  extra minutes survive the end of the month")

# only usable while the subscription is active; the balance stays on record
users.set_subscription_status("sub_pro", "canceled")
assert me(pro)["paid_minutes"] is None and start(pro).status_code == 402
assert client.post("/billing/topup-checkout-session", headers=bearer(pro)).status_code == 400
assert topups.balance_seconds(pro_user.id) == 6000 - 15 * MIN
users.set_subscription_status("sub_pro", "active")
assert me(pro)["paid_minutes"]["topup_sec"] == 6000 - 15 * MIN
print("ok 10 extra minutes need an active subscription; the balance is kept either way")

# ---- 7. the switch: off means paid plans are recorded and never capped ----
store.add_seconds(low_user.id, minutes.month_key(), 7200)          # this month's minutes all used
assert start(low).status_code == 402
before_topup = store.get_topup_used(pro_user.id)
store.add_seconds(pro_user.id, minutes.month_key(), 7200)          # Pro's month used too: only extra minutes left
os.environ["PAID_MINUTES_ENFORCED"] = "0"
info = me(low)
assert info["paid_minutes"] is None and info["plan_minutes"] is None and info["trial"] is None
r = start(low)
assert r.status_code == 200 and r.json()["time_limit_sec"] is None and r.json()["paid_minutes"] is None
sid = r.json()["id"]
advance(300 * MIN)
assert turn(low, sid).status_code == 200
assert end(low, sid)["paid_minutes"] is None
assert month_used(low_user.id) == 7200 + 300 * MIN                 # still recorded
practice(pro, 20 * MIN)
assert store.get_topup_used(pro_user.id) == before_topup           # bought minutes are not touched while it is off
assert client.post("/billing/topup-checkout-session", headers=bearer(pro)).status_code == 400
assert me(trial)["trial"]["exhausted"] is True                     # the trial is not affected by the switch
os.environ["PAID_MINUTES_ENFORCED"] = "1"
assert start(low).status_code == 402 and me(low)["paid_minutes"]["exhausted"] is True
print("ok 11 switch off: paid plans uncapped and uncounted on the page, still recorded; back on, capped again")

# ---- 8. an admin stays exempt ----
admin_user, admin = make("admin@example.com")
users._run("UPDATE users SET is_admin = ? WHERE id = ?", (True, admin_user.id))
admin = users.issue_token(users.get_user_by_id(admin_user.id))
subscribe(admin_user, "sub_admin")
assert me(admin)["paid_minutes"] is None and me(admin)["trial"] is None
assert start(admin).json()["time_limit_sec"] is None
print("ok 12 an admin account is not capped, as before")

# ---- 9. old ledger data keeps working ----
legacy_user, legacy = make("legacy@example.com")
subscribe(legacy_user, "sub_legacy")
with open(os.environ["CONSULTCASTAI_USAGE_STORE_PATH"], encoding="utf-8") as f:
    data = json.load(f)
data[f"{legacy_user.id}:2026-01"] = {"user_id": legacy_user.id, "month": "2026-01", "session_count": 4}                   # before minutes were recorded
data[f"{legacy_user.id}:2026-02"] = {"user_id": legacy_user.id, "month": "2026-02", "session_count": 2, "seconds_used": 900}
with open(os.environ["CONSULTCASTAI_USAGE_STORE_PATH"], "w", encoding="utf-8") as f:
    json.dump(data, f)
assert store.get_total_seconds(legacy_user.id) == 900 and store.get_usage(legacy_user.id, "2026-01").session_count == 4
assert store.get_topup_used(legacy_user.id) == 0 and me(legacy)["paid_minutes"]["remaining_sec"] == 7200
print("ok 13 records written before this change read exactly as before")

# ---- 10. Pro through the suite's plan ----
suite.switch(True)


def sso_login(email, sid, apps, verified=True):
    suite.codes["code-" + sid] = {"suite_user_id": "suite-" + email, "email": email, "name": "Sam Suite", "lang": "en",
                                  "email_verified": verified, "sid": sid, "apps": apps, "is_admin": False}
    r = client.post("/sso/exchange", json={"code": "code-" + sid, "code_verifier": "v"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


ALL = ["consultcastai", "clientbriefai", "adoptbriefai", "suite"]
sam = sso_login("sam@example.com", "sid-sam-1", ALL)
info = me(sam)
assert info["trial"] is None
paid = info["paid_minutes"]
assert paid["plan"] == "pro" and paid["via_suite"] is True and paid["included_sec"] == 7200 and paid["remaining_sec"] == 7200
assert practice(sam, 30 * MIN)["paid_minutes"]["remaining_sec"] == 90 * MIN
assert users.get_trial_record("sam@example.com") is None          # that was not trial time
assert client.post("/billing/topup-checkout-session", headers=bearer(sam)).status_code == 200
assert checkouts[-1]["metadata"]["owner_kind"] == "user" and "customer" not in checkouts[-1]
sam = sso_login("sam@example.com", "sid-sam-2", None)             # the suite could not check: the last answer stands
assert me(sam)["paid_minutes"]["via_suite"] is True
sam = sso_login("sam@example.com", "sid-sam-3", ["clientbriefai"])  # the plan is gone
info = me(sam)
assert info["paid_minutes"] is None and info["trial"] is not None and info["trial"]["exhausted"] is True
sam = sso_login("sam@example.com", "sid-sam-4", ALL)
assert me(sam)["paid_minutes"] is not None
advance(9 * 86400)                                                # no word from the suite for 9 days
assert me(sam)["paid_minutes"] is None
sam = sso_login("sam@example.com", "sid-sam-5", ALL)
assert me(sam)["paid_minutes"] is not None
os.environ["SUITE_PLAN_GRANTS_PRO"] = "0"
assert me(sam)["paid_minutes"] is None and me(sam)["trial"] is not None
os.environ.pop("SUITE_PLAN_GRANTS_PRO")
guest = sso_login("guest@example.com", "sid-guest-1", ALL, verified=False)        # an address the suite has not confirmed
assert users.get_user_by_email("guest@example.com").suite_plan is False
plain = sso_login("plain@example.com", "sid-plain-1", ["consultcastai"])          # no Suite plan: an ordinary trial
assert me(plain)["trial"]["remaining_sec"] == 600 and me(plain)["paid_minutes"] is None
suite.switch(False)
print("ok 14 a person on the suite's plan is Pro here with Pro's 120 minutes; lost when the plan or the answer lapses")

# ---- 11. deleting an account removes what it bought ----
users.set_subscription_status("sub_pro", "canceled")
r = client.post("/me/delete-account", json={"password": PASSWORD}, headers=bearer(pro))
assert r.status_code == 200, r.text
assert topups.purchased_seconds(pro_user.id) == 0 and store.get_topup_used(pro_user.id) == 0
assert topups.balance_seconds("team-1") == 12000                  # somebody else's is untouched
print("ok 15 account deletion removes its purchases and its ledger")

minutes.set_clock(None)
print("\nALL PASSED")
