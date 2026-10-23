"""The unified client file in ConsultCastAI (frontend/client_practice.js).

Run:  python tests/test_client_file.py

0. frontend/clientfile.js is the suite's file, unchanged (when the aicsuite
   repo sits beside this one, or AICSUITE_DIR points at it).
1. Backend: POST /sessions/{id}/end also returns the final scores and what
   was practised (persona, scenario, when), the second End too; no
   transcript. GET /sessions/mine is unchanged.
2. The page: index.html loads clientfile.js and client_practice.js before
   its module, and endSession hands the debrief to CcaiClientFile.record.
3. Browser (Playwright, the two scripts on a stand-in page, the clients
   folder a stand-in): the Client picker lists the folder's clients (not
   the practice file); ?client=<id> preselects one and leaves the address;
   a session for a client goes into its file as ccai.sessions with
   lifecycle.lastPracticeSession, other sections kept; a session with no
   client goes into _practice.client.json; with no folder yet the debrief
   offers to choose it and then writes; a browser with no folder access
   says so.

Plain asserts, no test framework. The AI calls are stand-ins; nothing
leaves this machine."""

import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails = []


def check(label, cond, extra=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + (("  " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(label)


print("=== 0. clientfile.js matches the suite's ===")
mine = (ROOT / "frontend" / "clientfile.js").read_bytes()
cands = [os.environ.get("AICSUITE_DIR", ""), str(ROOT.parent / "aicsuite"), str(ROOT.parent / "aicsuite-ucf")]
suite = next((pathlib.Path(c, "web", "js", "clientfile.js") for c in cands if c and pathlib.Path(c, "web", "js", "clientfile.js").exists()), None)
if suite is None:
    print("[SKIP] the aicsuite repo is not beside this one; set AICSUITE_DIR to compare")
else:
    check(f"identical to {suite}", mine.replace(b"\r\n", b"\n") == suite.read_bytes().replace(b"\r\n", b"\n"))

# ---- 1. backend --------------------------------------------------------------
print("\n=== 1. /end returns the scores and what was practised ===")
_dir = tempfile.mkdtemp()
os.environ["CONSULTCASTAI_LOCAL_USERS"] = "1"
os.environ["CONSULTCASTAI_USERS_DB_PATH"] = os.path.join(_dir, "users.db")
os.environ["CONSULTCASTAI_LOCAL_STORE"] = "1"
os.environ["CONSULTCASTAI_LOCAL_STORE_PATH"] = os.path.join(_dir, "sessions.json")
os.environ["CONSULTCASTAI_USAGE_STORE_PATH"] = os.path.join(_dir, "usage.json")
os.environ["CONSULTCASTAI_DEV_AUTH_BYPASS"] = "0"
for name in ("CONSULTCASTAI_ENV", "CONSULTCASTAI_ADMIN_EMAIL", "RESEND_API_KEY", "STRIPE_SECRET_KEY", "PAID_MINUTES_ENFORCED"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

import claude_client  # noqa: E402
import content  # noqa: E402
import main  # noqa: E402
import users  # noqa: E402

claude_client.get_persona_reply = lambda system_prompt, history: "Go on."
claude_client.get_debrief = lambda prompt: "A debrief."
claude_client.get_opener = lambda prompt: "Hello."

client = TestClient(main.app)
user = users.create_user("rep@example.com", users.hash_password("correct horse 1"), None)
users.mark_verified(user.id)
token = users.issue_token(users.get_user_by_id(user.id))
H = {"Authorization": "Bearer " + token}
persona_id = next(iter(content.PERSONAS))
scenario = content.list_active_scenarios()[0]
r = client.post("/sessions", json={"persona_id": persona_id, "scenario_id": scenario.id}, headers=H)
check("a session starts", r.status_code == 200, r.text)
sid = r.json()["id"]
started = r.json()
client.post(f"/sessions/{sid}/turn", json={"message": "We cut costs by a third for a similar client."}, headers=H)
e1 = client.post(f"/sessions/{sid}/end", headers=H).json()
check("the scores come back as numbers", all(isinstance(e1.get(k), int) for k in ("pressure", "trust", "specificity")), e1)
check("with the persona, the scenario and when it started", e1.get("persona_name") == started["persona_name"]
      and e1.get("scenario_title") == scenario.title and str(e1.get("created_at", "")).startswith(started["created_at"][:10]), e1)
check("and no transcript", "conversation" not in e1 and "transcript" not in json.dumps(e1).lower())
e2 = client.post(f"/sessions/{sid}/end", headers=H).json()
check("ending twice returns the same summary", {k: e2.get(k) for k in ("debrief", "pressure", "trust", "specificity", "persona_name", "scenario_title", "created_at")}
      == {k: e1.get(k) for k in ("debrief", "pressure", "trust", "specificity", "persona_name", "scenario_title", "created_at")})
mine_list = client.get("/sessions/mine", headers=H).json()
check("Debrief History is unchanged", set(mine_list[0]) == {"id", "persona_name", "scenario_title", "created_at", "duration_sec", "debrief"}, mine_list[0])

# ---- 2. the page -------------------------------------------------------------
print("\n=== 2. index.html ===")
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
i_cf, i_cp, i_mod = html.find('src="clientfile.js'), html.find('src="client_practice.js'), html.find('<script type="module">')
check("clientfile.js, then client_practice.js, before the module", -1 < i_cf < i_cp < i_mod)
end_fn = html[html.find("window.endSession = async function"):html.find("window.restartSession")]
check("endSession hands the debrief to CcaiClientFile.record", "CcaiClientFile.record(res" in end_fn)

# ---- 3. browser ----------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[SKIP] Playwright not installed: browser checks skipped")
    print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
    sys.exit(1 if fails else 0)

print("\n=== 3. The Client picker and the client file ===")
HV_ID = "6f1c2a7e-3b9d-4c51-9a0e-2f4d8b6c1a35"
HARBOR = {"format": "aicsuite.client", "version": 1, "id": HV_ID, "updatedAt": "2026-10-20T10:00:00.000Z", "updatedBy": "cbai",
          "client": {"name": "Harbor & Vine Kitchen", "industry": "restaurant"}, "cbai": {"readiness": {"result": {"total_score": 58}}},
          "abai": {"roles": [{"id": "r1", "name": "Server"}]}, "stage": "adopt", "lifecycle": {"readinessDone": True}}
BLUE = {"format": "aicsuite.client", "version": 1, "id": "11111111-2222-3333-4444-555555555555", "updatedAt": "2026-10-21T10:00:00.000Z",
        "updatedBy": "abai", "client": {"name": "Bluebird Dental"}, "stage": "diagnose", "lifecycle": {}}
OLD_PRACTICE = {"format": "aicsuite.client", "version": 1, "id": "99999999-2222-3333-4444-555555555555", "updatedAt": "2026-10-01T10:00:00.000Z",
                "updatedBy": "ccai", "client": {"name": "Practice sessions"}, "ccai": {"sessions": []}, "stage": "diagnose", "lifecycle": {}}
FOLDER = """(files) => {
  class FFile {
    constructor(name, text) { this.kind = 'file'; this.name = name; this.text = text || ''; }
    async getFile() { return new File([this.text], this.name, { type: 'application/json' }); }
    async createWritable() { let b = ''; return { write: async s => { b += s; }, close: async () => { this.text = b; } }; }
  }
  class FDir {
    constructor(name) { this.kind = 'directory'; this.name = name; this.entries = new Map(); }
    async *values() { for (const e of this.entries.values()) yield e; }
    async getFileHandle(n, o) { const e = this.entries.get(n); if (e && e.kind === 'file') return e;
      if (!o || !o.create) { const er = new Error('nf'); er.name = 'NotFoundError'; throw er; }
      const f = new FFile(n); this.entries.set(n, f); return f; }
    async getDirectoryHandle(n, o) { throw new Error('nf'); }
    async removeEntry(n) { this.entries.delete(n); }
  }
  const dir = new FDir('Clients');
  for (const [n, v] of Object.entries(files)) dir.entries.set(n, new FFile(n, JSON.stringify(v, null, 2)));
  window.__dir = dir; window.__asks = 0;
  window.showDirectoryPicker = async () => { window.__asks++; return dir; };
}"""
DISK = "(n) => { const f = window.__dir.entries.get(n); return f ? JSON.parse(f.text) : null; }"
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>t</title></head><body>
<div id="setupBlock"><div class="field"><span class="field-label">Persona</span><select id="personaSelect"></select></div></div>
<div id="debriefScreen"><div class="debrief-body" id="debriefText"></div></div></body></html>"""
END = {"session_id": "s-1", "debrief": "You held your price well.", "duration_sec": 312, "pressure": 40, "trust": 65, "specificity": 70,
       "persona_name": "Dana Ruiz", "scenario_title": "Price objection", "created_at": "2026-10-22T15:04:05.123456"}
tmp = pathlib.Path(tempfile.mkdtemp())


def page(b, files=None, query="", folder=True):
    p = tmp / ("p%d.html" % len(list(tmp.iterdir())))
    p.write_text(PAGE, encoding="utf-8")
    ctx = b.new_context()
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    if folder:
        pg.add_init_script("(() => { const files = " + json.dumps(files or {}) + "; (" + FOLDER + ")(files); })()")
    else:
        pg.add_init_script("delete window.showDirectoryPicker; window.showDirectoryPicker = undefined;")
    pg.goto(p.as_uri() + query)
    pg.add_script_tag(path=str(ROOT / "frontend" / "clientfile.js"))
    pg.add_script_tag(path=str(ROOT / "frontend" / "client_practice.js"))
    pg.wait_for_timeout(300)
    return ctx, pg, errs


with sync_playwright() as p:
    b = p.chromium.launch()

    ctx, pg, errs = page(b, {"harbor-and-vine-kitchen.client.json": HARBOR, "bluebird-dental.client.json": BLUE,
                             "_practice.client.json": OLD_PRACTICE}, "?client=" + HV_ID + "&step=practice&invite=x")
    check("?client= is taken out of the address (other parameters stay)", "client=" not in pg.url and "step=" not in pg.url and "invite=x" in pg.url, pg.url)
    check("the Client field is first on the setup panel; it waits for the folder",
          pg.evaluate("() => document.getElementById('setupBlock').firstElementChild.classList.contains('ccai-client')")
          and pg.is_disabled("#ccaiClientSelect") and pg.inner_text("#ccaiFolderBtn") == "Choose clients folder")
    pg.click("#ccaiFolderBtn"); pg.wait_for_timeout(400)
    opts = pg.eval_on_selector_all("#ccaiClientSelect option", "os => os.map(o => o.textContent)")
    check("the folder's clients are listed, newest first, the practice file is not", opts == ["No client: practice only", "Bluebird Dental", "Harbor & Vine Kitchen"], opts)
    check("the client from the suite is preselected", pg.input_value("#ccaiClientSelect") == HV_ID and "this client" in pg.inner_text("#ccaiClientNote"))
    pg.evaluate("(r) => CcaiClientFile.record(r, {})", END); pg.wait_for_timeout(500)
    d = pg.evaluate(DISK, "harbor-and-vine-kitchen.client.json")
    s = (d.get("ccai") or {}).get("sessions", [{}])[0]
    check("the session goes into the client's file as ccai.sessions",
          s == {"id": "s-1", "persona": "Dana Ruiz", "scenario": "Price objection", "createdAt": "2026-10-22T15:04:05.123Z", "durationSec": 312,
                "scores": {"pressure": 40, "trust": 65, "specificity": 70}, "debrief": "You held your price well."}, s)
    check("lifecycle.lastPracticeSession is set; ClientBriefAI's and AdoptBriefAI's work, the stage and updatedBy ccai",
          d["lifecycle"].get("lastPracticeSession") == "2026-10-22T15:04:05.123Z" and d["lifecycle"].get("readinessDone") is True
          and d["cbai"]["readiness"]["result"]["total_score"] == 58 and d["abai"]["roles"][0]["name"] == "Server"
          and d["stage"] == "adopt" and d["updatedBy"] == "ccai", d["lifecycle"])
    check("the debrief says where it went", "Saved to Harbor & Vine Kitchen’s client file." in pg.inner_text("#ccaiFileNote"))
    pg.evaluate("(r) => CcaiClientFile.record(r, {})", dict(END, session_id="s-2")); pg.wait_for_timeout(500)
    pg.evaluate("(r) => CcaiClientFile.record(r, {})", dict(END, session_id="s-2")); pg.wait_for_timeout(500)
    d = pg.evaluate(DISK, "harbor-and-vine-kitchen.client.json")
    check("each session is added once (the same End again adds nothing)", [x["id"] for x in d["ccai"]["sessions"]] == ["s-1", "s-2"])
    pg.select_option("#ccaiClientSelect", "")
    pg.evaluate("(r) => CcaiClientFile.record(r, {})", dict(END, session_id="s-3")); pg.wait_for_timeout(500)
    pr = pg.evaluate(DISK, "_practice.client.json")
    check("no client: the session goes into _practice.client.json", [x["id"] for x in pr["ccai"]["sessions"]] == ["s-3"]
          and "practice file" in pg.inner_text("#ccaiFileNote"))
    check("the client's file is untouched by it", [x["id"] for x in pg.evaluate(DISK, "harbor-and-vine-kitchen.client.json")["ccai"]["sessions"]] == ["s-1", "s-2"])
    check("no page errors", errs == [], errs)
    ctx.close()

    ctx, pg, errs = page(b, {"bluebird-dental.client.json": BLUE})
    pg.evaluate("(r) => CcaiClientFile.record(r, { persona: 'From the page', scenario: 'Page scenario' })",
                {"session_id": "s-9", "debrief": "Short.", "duration_sec": 60}); pg.wait_for_timeout(300)
    check("no folder yet: the debrief offers to choose it", "Keep this debrief in your client files?" in pg.inner_text("#ccaiFileNote")
          and pg.evaluate(DISK, "_practice.client.json") is None)
    pg.click("#ccaiFileNote button"); pg.wait_for_timeout(600)
    pr = pg.evaluate(DISK, "_practice.client.json")
    check("chosen: written to the practice file, with the page's persona and scenario when the server sent none",
          pr and pr["ccai"]["sessions"][0]["persona"] == "From the page" and pr["ccai"]["sessions"][0]["scores"] == {"pressure": None, "trust": None, "specificity": None}, pr)
    check("and the picker now lists the folder", "Bluebird Dental" in pg.eval_on_selector_all("#ccaiClientSelect option", "os => os.map(o => o.textContent)"))
    check("no page errors", errs == [], errs)
    ctx.close()

    ctx, pg, errs = page(b, folder=False)
    check("no folder access: the picker says so", "cannot open your clients folder" in pg.inner_text("#ccaiClientNote") and pg.is_hidden("#ccaiFolderBtn"))
    pg.evaluate("(r) => CcaiClientFile.record(r, {})", END); pg.wait_for_timeout(300)
    check("...and the debrief says it stays in Debrief History", "Debrief History only" in pg.inner_text("#ccaiFileNote"))
    check("no page errors", errs == [], errs)
    ctx.close()
    b.close()

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
