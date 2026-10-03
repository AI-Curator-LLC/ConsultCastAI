"""Single sign-on with the suite (suite_sso.py, sso_sessions.py, the /sso
routes in main.py, auth.verify_user), team access through the team owner,
and proof that nothing changes while the switch is off.

Run:  python tests/test_sso.py

Plain asserts, no test framework. Uses throwaway files for the users
database and the session store, with the suite replaced by a stand-in, so
it needs no network and no secrets.
"""

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
for name in ("CONSULTCASTAI_ENV", "CONSULTCASTAI_ADMIN_EMAIL", "RESEND_API_KEY", "STRIPE_SECRET_KEY"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import sso_sessions  # noqa: E402
import suite_sso  # noqa: E402
import teams  # noqa: E402
import users  # noqa: E402

PASSWORD = "correct horse 1"
SECRET = {"X-SSO-Secret": os.environ["SUITE_SSO_SECRET"]}


class FakeSuite:
    """Stands in for the suite's server: the switch, and what it answers."""

    def __init__(self):
        self.on = False
        self.reachable = True
        self.active = {}
        self.codes = {}
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((path, payload))
        if not self.reachable:
            raise suite_sso.SuiteError("suite_unreachable")
        if path == "/api/sso/status":
            return {"enabled": self.on, "signup": self.on}
        if path == "/api/sso/token":
            person = self.codes.pop(payload["code"], None)
            if person is None or payload["code_verifier"] != "verifier":
                raise suite_sso.SuiteError("sso_code_invalid", 400)
            self.active[person["sid"]] = person
            return person
        if path == "/api/sso/session":
            if not self.on:
                return {"active": False, "reason": "sso_off"}
            person = self.active.get(payload["sid"])
            return {"active": True, **person} if person else {"active": False, "reason": "signed_out"}
        if path == "/api/sso/logout":
            self.active.pop(payload["sid"], None)
            return {"ok": True}
        raise AssertionError(path)

    def switch(self, on):
        self.on = on
        suite_sso.forget_status()

    def person(self, email, sid, name="Ada Lovelace", verified=True):
        return {"suite_user_id": "suite-" + email.lower(), "email": email.lower(), "name": name, "lang": "en",
                "email_verified": verified, "sid": sid, "apps": None, "is_admin": False}


suite = FakeSuite()
suite_sso._request = suite.request
client = TestClient(main.app)


def bearer(token):
    return {"Authorization": "Bearer " + token}


def signup(email):
    r = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def sso_login(email, sid, invite_token=None, **person):
    suite.codes["code-" + sid] = suite.person(email, sid, **person)
    body = {"code": "code-" + sid, "code_verifier": "verifier"}
    if invite_token:
        body["invite_token"] = invite_token
    r = client.post("/sso/exchange", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def age_logins(delta):
    """Make every suite login look as if it was last checked `delta` ago."""
    for row in users._run("SELECT jti, last_checked_at FROM sso_sessions", fetch_all=True):
        users._run("UPDATE sso_sessions SET last_checked_at = ? WHERE jti = ?",
                   ((datetime.fromisoformat(row["last_checked_at"]) - delta).isoformat(), row["jti"]))


def me(token):
    return client.get("/auth/me", headers=bearer(token))


# ---- 1. switch off: the app behaves exactly as before ----
suite.switch(False)
assert client.get("/sso/status").json() == {"enabled": False, "suite": "https://aicsuite.ai-curator.ai", "app": "consultcastai"}
owner_jwt = signup("Owner@Acme-Consulting.com")
assert me(owner_jwt).json()["email"] == "owner@acme-consulting.com"
assert client.post("/auth/login", json={"email": "owner@acme-consulting.com", "password": PASSWORD}).status_code == 200
assert client.post("/sso/exchange", json={"code": "x", "code_verifier": "verifier"}).status_code == 409
print("ok 1  switch off: own signup, login and /auth/me work as before; exchange refused")

os.environ["SUITE_SSO_SECRET"] = ""
suite.switch(True)
before = len(suite.calls)
assert client.get("/sso/status").json()["enabled"] is False and me(owner_jwt).status_code == 200
assert len(suite.calls) == before
os.environ["SUITE_SSO_SECRET"] = SECRET["X-SSO-Secret"]
print("ok 2  no shared secret here: single sign-on stays off")

suite.switch(True)
suite.reachable = False
assert client.get("/sso/status").json()["enabled"] is False and me(owner_jwt).status_code == 200
suite.reachable = True
print("ok 3  suite unreachable: treated as off, own logins keep working")

# A team, as the Stripe webhook would have made it: the owner pays, members ride on it.
owner = users.get_user_by_email("owner@acme-consulting.com")
users.mark_verified(owner.id)
teams.create_team("team-1", owner.id, "cus_test", "sub_test", "active", seat_limit=3)
users.set_team(owner.id, "team-1")
member_jwt = signup("member@acme-consulting.com")
member = users.get_user_by_email("member@acme-consulting.com")
users.set_team(member.id, "team-1")
users.set_approved(member.id, True)
assert me(member_jwt).json()["team_id"] == "team-1"

# ---- 2. switch on ----
suite.switch(True)
assert client.get("/sso/status").json()["enabled"] is True
assert me(owner_jwt).status_code == 401                         # the app's own login is refused
r = client.post("/auth/login", json={"email": "owner@acme-consulting.com", "password": PASSWORD})
assert r.status_code == 403 and "moved to the AI Curator Consulting Suite" in r.json()["detail"]
assert client.post("/auth/signup", json={"email": "new@acme-consulting.com", "password": PASSWORD}).status_code == 403
assert users.get_user_by_email("new@acme-consulting.com") is None
assert client.post("/auth/forgot-password", json={"email": "owner@acme-consulting.com"}).status_code == 403
print("ok 4  switch on: own logins refused, own signup / login / password reset closed")

# an existing team member arrives through the suite: same account, still on the team
out = sso_login("MEMBER@acme-consulting.com", "sid-member-1")
linked = users.get_user_by_email("member@acme-consulting.com")
assert linked.id == member.id and linked.team_id == "team-1" and linked.password_hash == member.password_hash
assert out["email"] == "member@acme-consulting.com" and out["approved"] is True and out["token"] == out["api_token"]
info = me(out["token"]).json()
assert info["team_id"] == "team-1" and info["is_team_owner"] is False and info["email_verified"] is True
assert users.get_user_by_suite_id("suite-member@acme-consulting.com").id == member.id
print("ok 5  existing team member linked by email: still on the team, access through the owner")

own = sso_login("owner@acme-consulting.com", "sid-owner-1")
assert me(own["token"]).json()["is_team_owner"] is True
print("ok 6  the team owner is still the owner")

# a new teammate, invited by the owner: joins the team on arriving through the suite
teams.create_invite("team-1", "newbie@acme-consulting.com", "invite-abc")
new = sso_login("newbie@acme-consulting.com", "sid-newbie-1", invite_token="invite-abc", name="New Bie")
newbie = users.get_user_by_email("newbie@acme-consulting.com")
assert newbie.team_id == "team-1" and newbie.approved and newbie.email_verified and newbie.name == "New Bie"
assert teams.get_invite_by_token("invite-abc").used
assert me(new["token"]).json()["team_id"] == "team-1"
# the team is now full (3 seats): another invitation is ignored, the sign-in still works
teams.create_invite("team-1", "fourth@acme-consulting.com", "invite-full")
fourth = sso_login("fourth@acme-consulting.com", "sid-fourth-1", invite_token="invite-full")
assert users.get_user_by_email("fourth@acme-consulting.com").team_id is None
assert not teams.get_invite_by_token("invite-full").used and me(fourth["token"]).status_code == 200
# someone else's invitation can't be used
teams.create_invite("team-1", "other@acme-consulting.com", "invite-other")
sso_login("fourth@acme-consulting.com", "sid-fourth-2", invite_token="invite-other")
assert users.get_user_by_email("fourth@acme-consulting.com").team_id is None
print("ok 7  an invited teammate joins the team through the suite; seat limit and email match still enforced")

# a person with no account and no invitation gets a normal account (trial), approved because the suite verified the email
solo = sso_login("solo@acme-consulting.com", "sid-solo-1")
account = users.get_user_by_email("solo@acme-consulting.com")
assert account.approved and account.email_verified and account.team_id is None
assert me(solo["token"]).json()["trial"] is not None
unverified = sso_login("unv@acme-consulting.com", "sid-unv-1", verified=False)
assert unverified["approved"] is False
print("ok 8  new person: trial account, approved once the suite has verified the email")

suite.codes["once"] = suite.person("solo@acme-consulting.com", "sid-x")
assert client.post("/sso/exchange", json={"code": "once", "code_verifier": "wrong"}).status_code == 400
assert client.post("/sso/exchange", json={"code": "once", "code_verifier": "verifier"}).status_code == 400
print("ok 9  a bad or reused code is refused")

# ---- 3. seven-day logins, renewed through the suite ----
row = users._run("SELECT * FROM sso_sessions WHERE sid = ?", ("sid-member-1",), fetch_one=True)
lifetime = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
assert timedelta(days=6, hours=23) < lifetime <= timedelta(days=7)
calls = len(suite.calls)
assert me(out["token"]).status_code == 200
assert not [c for c in suite.calls[calls:] if c[0] == "/api/sso/session"]
age_logins(timedelta(minutes=11))
assert me(out["token"]).status_code == 200
assert ("/api/sso/session", {"app": "consultcastai", "sid": "sid-member-1"}) in suite.calls[calls:]
age_logins(timedelta(minutes=11))
suite.reachable = False
assert me(out["token"]).status_code == 200
suite.reachable = True
suite_sso._skip_checks_until = 0.0
age_logins(timedelta(minutes=11))
del suite.active["sid-member-1"]
assert me(out["token"]).status_code == 401
print("ok 10 7-day login, rechecked after 10 minutes; survives a suite outage; ends when the suite sign-in is gone")

# ---- 4. sign-out everywhere ----
t1 = sso_login("solo@acme-consulting.com", "sid-solo-2")["token"]
t2 = sso_login("solo@acme-consulting.com", "sid-solo-3")["token"]
assert client.post("/sso/backchannel-logout", json={"sids": ["sid-solo-2"]}).status_code == 403
assert client.post("/sso/backchannel-logout", json={"sids": ["sid-solo-2"]}, headers=SECRET).json() == {"ok": True}
assert me(t1).status_code == 401 and me(t2).status_code == 200
assert client.post("/auth/logout", headers=bearer(t2)).json() == {"ok": True}
assert "sid-solo-3" not in suite.active and me(t2).status_code == 401
assert client.post("/auth/logout").json() == {"ok": True}
print("ok 11 sign-out notices end the right logins; signing out here ends the suite sign-in too")

# ---- 5. switch off again: everyone is back where they were ----
keep = sso_login("member@acme-consulting.com", "sid-member-2")["token"]
suite.switch(False)
assert me(owner_jwt).json()["email"] == "owner@acme-consulting.com"        # the refused own login works again
assert me(member_jwt).json()["team_id"] == "team-1"
assert client.post("/auth/login", json={"email": "owner@acme-consulting.com", "password": PASSWORD}).status_code == 200
assert me(keep).status_code == 200                                         # a suite-born login keeps working
age_logins(timedelta(hours=2))
calls = len(suite.calls)
assert me(keep).status_code == 200
assert not [c for c in suite.calls[calls:] if c[0] != "/api/sso/status"]
print("ok 12 switch off: own logins and own login work again, nobody is signed out")

# a suite login belongs to one account: its row can't be used with another account's token
other_jti = sso_sessions.create(users.get_user_by_email("solo@acme-consulting.com").id, "sid-z")
assert sso_sessions.alive(other_jti, member.id, ask_suite=False) is False
print("ok 13 a login row only counts for its own account")

# ---- 6. deleting an account that has no password of its own here ----
suite.switch(True)
sent = []
import emailer  # noqa: E402
emailer.send_delete_account_email = lambda to, token: sent.append((to, token))
who = sso_login("leaver@acme-consulting.com", "sid-leaver-1")
leaver = users.get_user_by_email("leaver@acme-consulting.com")
teams.create_invite("team-1", "leaver@acme-consulting.com", "invite-leaver")
assert client.post("/me/delete-account/email-link").status_code == 401
assert client.post("/me/delete-account/email-link", headers=bearer(who["token"])).json() == {"ok": True}
assert sent and sent[-1][0] == "leaver@acme-consulting.com"
link_token = sent[-1][1]
assert users.get_user_by_email("leaver@acme-consulting.com") is not None       # nothing deleted yet
assert client.post("/me/delete-account/confirm", json={"token": "wrong"}).status_code == 400
assert client.post("/me/delete-account/confirm", json={"token": link_token}).json() == {"message": "Account deleted"}
assert users.get_user_by_email("leaver@acme-consulting.com") is None
assert me(who["token"]).status_code == 401
assert users._run("SELECT COUNT(*) AS n FROM sso_sessions WHERE user_id = ?", (leaver.id,), fetch_one=True)["n"] == 0
assert teams.get_invite_by_token("invite-leaver") is None
assert client.post("/me/delete-account/confirm", json={"token": link_token}).status_code == 400   # single use
# an expired link does nothing
late = sso_login("late@acme-consulting.com", "sid-late-1")
client.post("/me/delete-account/email-link", headers=bearer(late["token"]))
users._run("UPDATE users SET delete_token_expires = ? WHERE email = ?",
           ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), "late@acme-consulting.com"))
assert client.post("/me/delete-account/confirm", json={"token": sent[-1][1]}).status_code == 400
assert users.get_user_by_email("late@acme-consulting.com") is not None
# a team owner with a live subscription is refused before any email goes out
n = len(sent)
own2 = sso_login("owner@acme-consulting.com", "sid-owner-2")
assert client.post("/me/delete-account/email-link", headers=bearer(own2["token"])).status_code == 400
assert len(sent) == n
print("ok 14 delete by emailed link: needs the link, single use, expires, refused while subscribed")

# ---- 7. the suite's administrator deletes the person ----
body = {"suite_user_id": "suite-late@acme-consulting.com", "email": "late@acme-consulting.com"}
assert client.post("/sso/delete-account", json=body).status_code == 403
assert client.post("/sso/delete-account", json=body, headers=SECRET).json() == {"deleted": True}
assert users.get_user_by_email("late@acme-consulting.com") is None and me(late["token"]).status_code == 401
assert client.post("/sso/delete-account", json=body, headers=SECRET).json() == {"deleted": False}
print("ok 15 deleted on the suite's request; needs the shared secret")

assert set(client.get("/version").json()) == {"build", "retention_last_run", "retention_counts"}
print("ok 16 /version reports the retention job")

print("\nALL PASSED")
