"""
Headless tests of the Streamlit app (Streamlit's AppTest harness). No browser, no API key, no network.

    python3 -m ui.test_ui
"""
from __future__ import annotations
import csv, json, os, pathlib, sys, tempfile, warnings

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AP_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ui_test.db")      # set before engine.db is imported
os.environ["EXTRACT_CACHE_DIR"] = os.path.join(tempfile.mkdtemp(), "cache")
os.environ["DB_MODE"] = "shared"                                                     # the single-DB tests below inspect one file
os.environ.pop("GEMINI_API_KEY", None)                                          # tests must never call the real API

from streamlit.testing.v1 import AppTest
from engine import db, pipeline
from extraction.extract import ExtractionError, extract_invoice as real_extract

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   <-- {detail}"))


def txt(at): return " ".join(str(m.value) for m in at.markdown)
def nav(at, page): at.sidebar.radio[0].set_value(page).run(); assert not at.exception, at.exception
def run_btn(at): return [b for b in at.button if b.label.strip("▶ ").strip() == "Run"][0]


def fresh(offline=False):
    db.reset_demo(db.connect(os.environ["AP_DB_PATH"]), clear_extraction_cache=False)     # every scenario starts clean
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    assert not at.exception, at.exception
    at.sidebar.slider[0].set_value(0.0).run()                                             # no demo pacing in tests
    if offline:
        at.sidebar.radio[1].set_value("Offline test data").run()
    return at


print("== pages load ==")
at = fresh()
ok("Run page loads", "Process an invoice" in txt(at))
nav(at, "Dashboard"); ok("Dashboard loads (empty state)", any("No runs yet" in str(i.value) for i in at.info))
nav(at, "Rules & assumptions"); ok("Rules page loads", "Adjust thresholds" in txt(at))

print("\n== the nine invoices through the UI (offline data), in the auto-advanced order ==")
at = fresh(offline=True)
ok("offline banner is shown", "Offline test data is ON" in txt(at))
order = []
for _ in range(9):
    order.append(at.selectbox[0].value[:2])
    run_btn(at).click().run(); assert not at.exception, at.exception
ok("Run auto-advances through 01..09", order == [f"0{i}" for i in range(1, 10)], order)
ok("dropdown stays on the last invoice after run 9 (no snap-back)", at.selectbox[0].value.startswith("09"))
ok("last run is repainted with all 9 step cards", sum(txt(at).count(f'class="ap-step {s}') for s in ("pass", "warn", "fail", "skipped")) == 9)
conn = db.connect(os.environ["AP_DB_PATH"])
got = [r["decision"] for r in conn.execute("SELECT decision FROM invoices ORDER BY id")]
want = [r["expected_decision"] for r in csv.DictReader(open(ROOT / "data" / "expected_results.csv"))]
ok("every decision matches the answer key", got == want, f"{got} vs {want}")
ok("every result is tagged as offline test data", {r[0] for r in conn.execute("SELECT extraction_source FROM runs")} == {"golden"})
at.selectbox[0].set_value("01_happy_sahyadri_steel.pdf").run()
ok("choosing an already-run invoice shows the duplicate warning", "Already processed in this session" in txt(at))

print("\n== dashboard and reviewer ==")
nav(at, "Dashboard")
t = txt(at)
ok("KPIs and review queue present", all(x in t for x in ("Invoices processed", "Needs review", "On hold", "Review queue (3)")))
inputs = [x for x in at.text_input if x.key and x.key.startswith("note")]
approve = [b for b in at.button if b.key and b.key.startswith("ap")]
approve[1].click().run()
ok("approving without a note is refused", any("add a short note" in str(e.value) for e in at.error))
at.text_input(key=inputs[1].key).set_value("Confirmed PO with Sandeep Rao").run()
at.button(key=approve[1].key).click().run(); assert not at.exception, at.exception
po = {p["po_number"]: p for p in db.po_status(db.connect(os.environ["AP_DB_PATH"]))}
ok("reviewer approval uses up PO-0104's balance", po["PO-2026-0104"]["remaining"] == 0, po["PO-2026-0104"]["remaining"])
flat = " ".join(str(r) for d in at.dataframe for r in d.value.values.tolist())
ok("history marks the reviewer decision", "by reviewer" in flat)
[c for c in at.checkbox if "wipe" in c.label][0].check().run()
[b for b in at.button if b.label == "Reset demo data"][0].click().run(); assert not at.exception, at.exception
ok("Reset demo data empties the history", db.connect(os.environ["AP_DB_PATH"]).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0)
nav(at, "Process invoice"); at.selectbox[0].set_value("03_split_brightline_part1.pdf").run()
ok("after a reset, choosing a different invoice sticks (no snap-back)", at.selectbox[0].value.startswith("03"))

print("\n== what-if rules ==")
at = fresh(offline=True)
at.selectbox[0].set_value("02_happy_tolerance_meridian.pdf").run(); run_btn(at).click().run()
ok("invoice 02 approved under default rules", "Approved" in txt(at))
nav(at, "Rules & assumptions"); at.number_input[0].set_value(0.5).run()
ok("Rules page reports the change", "1 rule(s) changed" in txt(at))
nav(at, "Process invoice"); ok("Run page warns that custom rules are active", "Custom rules are active" in txt(at))
at = fresh(offline=True)
nav(at, "Rules & assumptions"); at.number_input[0].set_value(0.5).run(); nav(at, "Process invoice")
at.selectbox[0].set_value("02_happy_tolerance_meridian.pdf").run(); run_btn(at).click().run()
ok("at 0.5% tolerance invoice 02 is put on hold", "On hold" in txt(at) and "tolerance" in txt(at))

print("\n== failures are visible and recoverable ==")
at = fresh(); calls = {"n": 0}
def flaky(path, **kw):
    calls["n"] += 1
    if calls["n"] == 1:
        raise ExtractionError("RATE_LIMIT", "Free-tier rate limit hit. Wait a minute and retry.")
    return real_extract(path, mode="golden")
pipeline.extract_invoice = flaky
run_btn(at).click().run(); t = txt(at)
ok("rate limit shows 'Run failed' with the code", "Run failed" in t and "RATE_LIMIT" in t)
ok("   Retry is offered and the 7 later steps show as skipped", any("Retry" in b.label for b in at.button) and t.count("ap-step skipped") == 7)
[b for b in at.button if "Retry" in b.label][0].click().run()
ok("   Retry recovers", "Approved" in txt(at) and calls["n"] == 2)
pipeline.extract_invoice = real_extract
at = fresh(); run_btn(at).click().run(); t = txt(at)
ok("no API key and nothing cached -> NO_API_KEY, no Retry offered", "NO_API_KEY" in t and not any("Retry" in b.label for b in at.button))


print("\n== a private sandbox per visitor, and saved AI reads ==")
os.environ["DB_MODE"] = "per_visitor"
a = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
a.sidebar.slider[0].set_value(0.0).run(); a.sidebar.radio[1].set_value("Offline test data").run()
run_btn(a).click().run()
b = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run(); nav(b, "Dashboard")
ok("a second visitor starts with an empty history", any("No runs yet" in str(i.value) for i in b.info))
nav(a, "Dashboard")
ok("the first visitor still sees their own run", "Invoices processed" in txt(a) and not any("No runs yet" in str(i.value) for i in a.info))
ok("the sandbox is announced in the sidebar", "Your own sandbox" in " ".join(str(m.value) for m in a.sidebar.markdown))
os.environ["DB_MODE"] = "shared"

import extraction.extract as extract
seed, f = tempfile.mkdtemp(), ROOT / "invoices" / "01_happy_sahyadri_steel.pdf"
key = extract._cache_key(f.read_bytes(), os.environ.get("GEMINI_MODEL", extract.DEFAULT_MODEL))
meta = {"source": "live", "model": extract.DEFAULT_MODEL, "cached": False, "latency_ms": 6000, "input_tokens": 1, "output_tokens": 1, "prompt_version": extract.PROMPT_VERSION}
(pathlib.Path(seed) / f"{key}.json").write_text(json.dumps({"data": json.loads((ROOT / "extraction/golden/01_happy_sahyadri_steel.json").read_text()), "meta": meta}))
os.environ["EXTRACT_SEED_DIR"], os.environ["EXTRACT_CACHE_DIR"] = seed, tempfile.mkdtemp()
at = fresh(); run_btn(at).click().run()
ok("a saved real result in seed_cache is used with no API key and no local cache", "Approved" in txt(at) and "Cached AI read" in txt(at))
at = fresh(); at.sidebar.checkbox[0].check().run(); run_btn(at).click().run()
ok("'Fresh AI read' ignores the saved result (and, with no key, fails visibly)", "NO_API_KEY" in txt(at))

print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:\n  - " + "\n  - ".join(FAIL))
sys.exit(1 if FAIL else 0)
