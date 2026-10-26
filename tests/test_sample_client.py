"""The suite's sample client in ConsultCastAI (frontend/client_practice.js, the
suite's docs/SPEC_First_Run_Onboarding.md decision 3): a client file marked
`sample: true` is marked "(sample)" in the Client picker, and the note under
it says it is not a real business; a real client is shown as before.
The page and the clients folder are stand-ins, as in test_client_file.py.

Run:  python tests/test_sample_client.py"""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
# the same page and folder stand-ins (and the clientfile.js drift check)
exec(HERE.joinpath("test_client_file.py").read_text(encoding="utf-8").split("\nwith sync_playwright() as p:")[0])

SAMPLE = json.loads(json.dumps(HARBOR))
SAMPLE.update({"id": "00000000-5a3b-4e1e-8000-5a3b1e000001", "sample": True, "updatedAt": "2026-10-25T10:00:00.000Z"})
SAMPLE["client"] = dict(HARBOR["client"], name="Sample: Harbor & Vine Kitchen")

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg, errs = page(b, {"sample-harbor-and-vine-kitchen.client.json": SAMPLE, "harbor-and-vine-kitchen.client.json": HARBOR})
    pg.click("#ccaiFolderBtn"); pg.wait_for_timeout(400)
    opts = pg.eval_on_selector_all("#ccaiClientSelect option", "os => os.map(o => o.textContent)")
    check("the picker marks the sample, the real client as before",
          "Sample: Harbor & Vine Kitchen (sample)" in opts and "Harbor & Vine Kitchen" in opts, opts)
    pg.select_option("#ccaiClientSelect", SAMPLE["id"]); pg.wait_for_timeout(200)
    check("the note says the sample is not a real business", "not a real business" in pg.inner_text("#ccaiClientNote"), pg.inner_text("#ccaiClientNote"))
    pg.select_option("#ccaiClientSelect", HV_ID); pg.wait_for_timeout(200)
    check("a real client's note as before", "this client" in pg.inner_text("#ccaiClientNote"), pg.inner_text("#ccaiClientNote"))
    check("no page errors", not errs, errs)
    ctx.close()
    b.close()

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
