"""
Offline test suite for the rules engine. No API key, no network.

    python3 -m engine.test_engine

Part 1 runs the 9 test invoices in order and compares to data/expected_results.csv.
Part 2 runs edge-case scenarios on mutated copies of the golden extractions, including the
FALSE-ALARM cases that must NOT be flagged (a system that cries wolf gets ignored).
"""
from __future__ import annotations
import copy, csv, json, os, pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import db, pipeline, rules as R
from extraction.extract import ExtractionError

GOLD = ROOT / "extraction" / "golden"
PASSED, FAILED = [], []


def ok(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   <-- {detail}"))
    return cond


def G(stem):
    return copy.deepcopy(json.loads((GOLD / f"{stem}.json").read_text()))


def setv(ex, path, value, confidence=None):
    node = ex
    for p in path:
        node = node[p]
    node["value"] = value
    if confidence is not None:
        node["confidence"] = confidence


def run(conn, ex, name="test.pdf", rules=None):
    return pipeline.process(conn, None, extraction=ex, file_name=name, rules=rules)


def all_checks(conn, run_id):
    return [c for s in db.get_run(conn, run_id)["steps"] for c in s["checks"]]


def codes(conn, res, min_level="warn"):
    return {c["code"] for c in all_checks(conn, res["run_id"]) if R.RANK[c["level"]] >= R.RANK[min_level]}


def codes_all(conn, res):
    return {c["code"] for c in all_checks(conn, res["run_id"])}


def scenario(name, ex, expect, has=(), lacks=(), setup=None, pre=(), rules=None):
    """Run one scenario in a fresh DB. `pre` = golden stems to run first (to build history)."""
    conn = db.fresh_memory_db()
    for stem in pre:
        pipeline.process(conn, str(ROOT / "invoices" / f"{stem}.pdf"), mode="golden")
    if setup:
        setup(conn)
    res = run(conn, ex, rules=rules)
    c = codes_all(conn, res)
    good = res["decision"] == expect and all(h in c for h in has) and not any(l in c for l in lacks)
    ok(name, good, f"decision={res['decision']} (want {expect}); codes={sorted(c)}")
    return conn, res


# ============================================================ PART 1: the answer key
print("\n== PART 1: nine test invoices, in order, vs expected_results.csv ==")
conn = db.fresh_memory_db()
results = {}
for r in csv.DictReader(open(ROOT / "data" / "expected_results.csv")):
    res = pipeline.process(conn, str(ROOT / "invoices" / r["file"]), mode="golden")
    results[r["file"][:2]] = res
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (res["invoice_id"],)).fetchone()
    want_po = None if r["matched_po"] in ("-", "") else r["matched_po"].split(" ")[0]
    ok(f"{r['file'][:-4]}: {r['expected_decision']}", res["decision"] == r["expected_decision"] and inv["matched_po"] == want_po,
       f"got {res['decision']} / PO {inv['matched_po']}")

print("\n== PART 1b: the details that matter in the demo ==")
c = lambda k: codes_all(conn, results[k])
ok("run record has all 9 steps, in order", [s["stage"] for s in db.get_run(conn, results["01"]["run_id"])["steps"]] == [k for k, _ in pipeline.STAGES])
ok("02 approved with the tolerance NOTE (1.44% over) and the unordered courier line", {"PO_WITHIN_TOLERANCE", "UNORDERED_LINE"} <= c("02"))
ok("02 tolerance note says 1.44%", any("1.44%" in x["message"] for x in all_checks(conn, results["02"]["run_id"])))
bal = {p["po_number"]: p for p in db.po_status(conn)}
ok("PO-0102 balance after 03+04 is 1,18,000 (05 did not consume it)", abs(bal["PO-2026-0102"]["remaining"] - 118000) < 0.01, bal["PO-2026-0102"]["remaining"])
ok("05 held for balance (47,200 over) AND quantity (21 of 20 trips)", {"PO_BALANCE_EXCEEDED", "QTY_OVER_PO"} <= c("05"))
ok("05 reason cites exactly 47,200", any("₹47,200" in x["message"] for x in all_checks(conn, results["05"]["run_id"])))
ok("04 NOT flagged as duplicate of 03 (same total, same PO, different number/date/lines)", "DUPLICATE_RULED_OUT" in c("04") and "DUPLICATE_PROBABLE" not in c("04"))
ok("07 caught as duplicate of 06 via normalised number '42'", "DUPLICATE_DEFINITE" in c("07"))
ok("07 stopped after the duplicate (PO + line checks skipped)", [s["status"] for s in db.get_run(conn, results["07"]["run_id"])["steps"]][6:8] == ["skipped", "skipped"])
ok("08 PO inferred as PO-0104, not the decoy PO-0105", conn.execute("select matched_po from invoices where id=?", (results["08"]["invoice_id"],)).fetchone()[0] == "PO-2026-0104")
inf = [x for x in all_checks(conn, results["08"]["run_id"]) if x["code"] == "PO_INFERRED"][0]
ok("08 inference score is high but it is still NOT auto-approved", inf["data"]["score"] > 0.8 and results["08"]["decision"] == "NEEDS_REVIEW")
ok("08 tax backed out by code: taxable 1,60,000 and IGST 28,800", any("₹1,60,000" in x["message"] and "₹28,800" in x["message"] for x in all_checks(conn, results["08"]["run_id"])))
ok("08 inclusive prices compared ex-tax (3304 -> 2800, no false price alarm)", "PRICE_VARIANCE" not in c("08"))
ok("09 bank mismatch cites both account endings (8820 vs 4417)", any("8820" in x["message"] and "4417" in x["message"] for x in all_checks(conn, results["09"]["run_id"])))
ok("09 also flags the lookalike email domain", "EMAIL_DOMAIN_MISMATCH" in c("09"))
ok("09 has the phone-verification action", any("phone" in a for a in json.loads(conn.execute("select actions_json from invoices where id=?", (results["09"]["invoice_id"],)).fetchone()[0])))
ok("08 reached only after other invoices: 01,03,04,06 consumed; 09/05/07/08 did not", abs(bal["PO-2026-0101"]["remaining"]) < 0.01 and bal["PO-2026-0108"]["invoiced"] == 0 and bal["PO-2026-0104"]["invoiced"] == 0)

# ============================================================ PART 2: edge cases
print("\n== PART 2a: document & field problems ==")
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "number"), None)
scenario("missing invoice number -> HOLD", ex, "HOLD", has=["MISSING_FIELD"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("totals", "total"), 472000.0, 0.6)
scenario("low-confidence total -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["LOW_CONFIDENCE"])
ex = G("01_happy_sahyadri_steel"); ex["lines"][0]["line_amount"] = 300000.0
scenario("line items that don't add up -> HOLD", ex, "HOLD", has=["ARITHMETIC"])
ex = G("01_happy_sahyadri_steel"); ex["totals"]["total_in_words"] = "Rupees Four Lakh Seventeen Thousand Only"
scenario("amount in words disagrees with figures -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["WORDS_MISMATCH"])
ex = G("01_happy_sahyadri_steel"); ex["document"]["document_type"] = "proforma_invoice"
conn_, res_ = scenario("proforma invoice -> NEEDS_REVIEW, nothing else runs", ex, "NEEDS_REVIEW", has=["DOC_NOT_INVOICE"])
ok("   ...and the vendor/PO steps are skipped", [s["status"] for s in db.get_run(conn_, res_["run_id"])["steps"]][3:8] == ["skipped"] * 5)
ex = G("01_happy_sahyadri_steel"); ex["document"]["document_type"] = "credit_note"
scenario("credit note is never auto-paid -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["DOC_NOT_INVOICE"])
ex = G("01_happy_sahyadri_steel"); ex["document"]["multiple_invoices_detected"] = True
scenario("multiple invoices in one file -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["MULTI_INVOICE"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "currency"), "USD")
scenario("non-INR currency -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["CURRENCY"])
ex = G("08_scanned_orbit_no_po"); ex["tax"]["tax_lines"][0]["rate_pct"] = None
scenario("tax-inclusive with no rate -> NEEDS_REVIEW and NO false price alarm", ex, "NEEDS_REVIEW", has=["TAX_RATE_UNKNOWN"], lacks=["PRICE_VARIANCE"])

print("\n== PART 2b: vendor identity & fraud ==")
ex = G("01_happy_sahyadri_steel"); setv(ex, ("vendor", "name"), "Sahyadri Steels Pvt Ltd"); setv(ex, ("vendor", "gstin"), "27AAHFS5521K1ZZ")
scenario("LOOKALIKE vendor (similar name, unknown GSTIN) -> REJECT", ex, "REJECT", has=["VENDOR_LOOKALIKE"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("vendor", "name"), "Brightline Logistics LLP")
scenario("GSTIN is vendor A but name is vendor B -> HOLD", ex, "HOLD", has=["VENDOR_IDENTITY_CONFLICT"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("vendor", "name"), "Quantum Fabricators"); setv(ex, ("vendor", "gstin"), "27AAACQ1234F1Z5")
scenario("unknown vendor -> REJECT, PO matching skipped", ex, "REJECT", has=["VENDOR_UNKNOWN", "PO_SKIPPED"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("vendor", "gstin"), None)
scenario("no GSTIN but exact name match -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["VENDOR_BY_NAME_ONLY"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("vendor", "gstin"), "27AAHFS55")
scenario("malformed GSTIN is flagged (and, being unknown, the invoice is rejected)", ex, "REJECT", has=["GSTIN_FORMAT"])

# blocked vendor + closed PO (Zenith, V006)
ex = G("01_happy_sahyadri_steel")
setv(ex, ("vendor", "name"), "Zenith Industrial Tools"); setv(ex, ("vendor", "gstin"), "24AAECZ3318H1Z1"); setv(ex, ("vendor", "email"), "ar@zenithtools.in")
setv(ex, ("invoice", "po_reference"), "PO-2026-0107"); setv(ex, ("bank", "account_number"), "55550006084")
ex["lines"] = [{"description": "Carbide turning inserts CNMG 120408", "hsn_sac": "8209", "quantity": 200.0, "uom": "Nos", "unit_price": 425.0, "line_amount": 85000.0, "confidence": 0.97}]
setv(ex, ("totals", "subtotal"), 85000.0); setv(ex, ("totals", "total"), 100300.0); ex["totals"]["total_in_words"] = "Rupees One Lakh Three Hundred Only"
ex["tax"]["tax_lines"] = [{"tax_type": "IGST", "rate_pct": 18.0, "amount": 15300.0}]
scenario("blocked vendor AND closed PO -> REJECT with both reasons", ex, "REJECT", has=["VENDOR_BLOCKED", "PO_CLOSED"], lacks=["TAX_TYPE_WRONG", "BANK_MISMATCH"])

ex = G("01_happy_sahyadri_steel"); setv(ex, ("buyer", "gstin"), "27AAKCK4821M1Z9")
scenario("wrong buyer GSTIN -> HOLD", ex, "HOLD", has=["BUYER_GSTIN_MISMATCH"])
ex = G("01_happy_sahyadri_steel"); ex["tax"]["tax_lines"] = [{"tax_type": "IGST", "rate_pct": 18.0, "amount": 72000.0}]
scenario("same-state supply charged IGST -> HOLD", ex, "HOLD", has=["TAX_TYPE_WRONG"])
ex = G("08_scanned_orbit_no_po"); ex["tax"]["tax_lines"][0]["tax_type"] = "CGST"
scenario("inter-state supply charged CGST -> HOLD", ex, "HOLD", has=["TAX_TYPE_WRONG"])
ex = G("09_bank_change_sahyadri"); setv(ex, ("vendor", "email"), "accounts@sahyadristeel.in")
conn_, res_ = scenario("bank mismatch ALONE (email fine) is still HOLD", ex, "HOLD", has=["BANK_MISMATCH"], lacks=["EMAIL_DOMAIN_MISMATCH"])
ex = G("09_bank_change_sahyadri"); setv(ex, ("bank", "account_number"), "60198423334417"); setv(ex, ("vendor", "email"), "accounts@sahyadristeel.in")
ex["bank"]["change_notice"] = {"detected": False, "evidence": None}
scenario("same invoice with the REAL bank details -> APPROVE (the check isn't paranoid)", ex, "APPROVE", lacks=["BANK_MISMATCH"])

print("\n== PART 2c: duplicates - precise, not paranoid ==")
ex = G("02_happy_tolerance_meridian"); setv(ex, ("invoice", "number"), "INV-0042")
scenario("same invoice number from a DIFFERENT vendor is NOT a duplicate", ex, "APPROVE", pre=["06_dup_deccan_original"], has=["NO_DUPLICATE"], lacks=["DUPLICATE_DEFINITE", "DUPLICATE_PROBABLE"])
ex = G("03_split_brightline_part1"); setv(ex, ("invoice", "number"), "BL/INV/2026/0999")
scenario("same amount+date+lines, new number -> PROBABLE duplicate, NEEDS_REVIEW", ex, "NEEDS_REVIEW", pre=["03_split_brightline_part1"], has=["DUPLICATE_PROBABLE"])
ex = G("04_split_brightline_part2"); setv(ex, ("invoice", "number"), "BL/INV/2026/0811"); setv(ex, ("invoice", "date"), "2026-12-20")
scenario("same number reused 106 days later is outside the 90-day window -> not a duplicate", ex, "APPROVE", pre=["03_split_brightline_part1"], has=["NO_DUPLICATE"], lacks=["DUPLICATE_DEFINITE"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "number"), "SST/26-27/0400"); setv(ex, ("invoice", "date"), "2026-09-20")
scenario("PO already fully invoiced -> HOLD", ex, "HOLD", pre=["01_happy_sahyadri_steel"], has=["PO_EXHAUSTED"])

print("\n== PART 2d: PO matching ==")
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "po_reference"), "PO-2026-9999")
scenario("cited PO does not exist -> HOLD (no guessing)", ex, "HOLD", has=["PO_NOT_FOUND"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "po_reference"), "PO-2026-0103")
scenario("cited PO belongs to another vendor -> HOLD", ex, "HOLD", has=["PO_VENDOR_MISMATCH"])
ex = G("01_happy_sahyadri_steel"); setv(ex, ("invoice", "date"), "2026-08-01")
scenario("invoice dated before the PO -> NEEDS_REVIEW", ex, "NEEDS_REVIEW", has=["INVOICE_BEFORE_PO"])
ex = G("08_scanned_orbit_no_po")
def second_similar_po(conn):
    conn.execute("INSERT INTO purchase_orders SELECT 'PO-2026-0199',vendor_id,po_date,description,currency,subtotal,tax_type,tax_rate_pct,tax_amount,total,status,requester,cost_center FROM purchase_orders WHERE po_number='PO-2026-0104'")
    conn.execute("INSERT INTO po_lines SELECT 'PO-2026-0199',line_no,item_description,hsn_sac,quantity,uom,unit_price,line_amount FROM po_lines WHERE po_number='PO-2026-0104'")
conn_, res_ = scenario("two equally good POs -> NEEDS_REVIEW, refuses to guess", ex, "NEEDS_REVIEW", setup=second_similar_po, has=["PO_AMBIGUOUS"], lacks=["PO_INFERRED"])
ok("   ...and no PO is attached to the invoice", conn_.execute("select matched_po from invoices where id=?", (res_["invoice_id"],)).fetchone()[0] is None)
ex = G("08_scanned_orbit_no_po"); ex["lines"] = [{"description": "Consulting services", "hsn_sac": "9983", "quantity": 1.0, "uom": "Job", "unit_price": 999000.0, "line_amount": 999000.0, "confidence": 0.9}]
setv(ex, ("totals", "total"), 999000.0); ex["totals"]["total_in_words"] = None
scenario("no PO ref and nothing plausible to infer -> HOLD", ex, "HOLD", has=["PO_MISSING"])

print("\n== PART 2e: line items ==")
ex = G("09_bank_change_sahyadri"); setv(ex, ("bank", "account_number"), "60198423334417"); setv(ex, ("vendor", "email"), "accounts@sahyadristeel.in")
ex["bank"]["change_notice"] = {"detected": False, "evidence": None}
ex["lines"][0].update(quantity=12.5, unit_price=20000.0, line_amount=250000.0)
scenario("OFFSETTING variances (qty up, price down, same total) -> HOLD", ex, "HOLD", has=["QTY_OVER_PO", "PRICE_VARIANCE"], lacks=["PO_BALANCE_EXCEEDED"])
ex = G("02_happy_tolerance_meridian"); ex["lines"][0]["unit_price"] = 300.0
ex["lines"][0]["line_amount"] = 60000.0; setv(ex, ("totals", "subtotal"), 115600.0)
ex["tax"]["tax_lines"] = [{"tax_type": "CGST", "rate_pct": 9.0, "amount": 10404.0}, {"tax_type": "SGST", "rate_pct": 9.0, "amount": 10404.0}]
setv(ex, ("totals", "total"), 136408.0); ex["totals"]["total_in_words"] = None
scenario("unit price 5% above PO rate -> HOLD (even before total tolerance bites)", ex, "HOLD", has=["PRICE_VARIANCE"])

print("\n== PART 2f: it is configurable, and it is auditable ==")
tight = R.load_rules(); tight["matching"]["price_tolerance_pct"] = 0.5
scenario("tolerance 2% -> 0.5% turns invoice 02 from APPROVE into HOLD", G("02_happy_tolerance_meridian"), "HOLD", has=["PO_BALANCE_EXCEEDED"], rules=tight)
ex = G("02_happy_tolerance_meridian"); ex["lines"][3].update(unit_price=5600.0, line_amount=5600.0)
setv(ex, ("totals", "subtotal"), 116600.0); setv(ex, ("totals", "total"), 137588.0); ex["totals"]["total_in_words"] = None
ex["tax"]["tax_lines"] = [{"tax_type": "CGST", "rate_pct": 9.0, "amount": 10494.0}, {"tax_type": "SGST", "rate_pct": 9.0, "amount": 10494.0}]
scenario("5% over (Rs 6,608): above 2% yet under Rs 10,000 -> HOLD (tolerance is the LOWER of the two)", ex, "HOLD", has=["PO_BALANCE_EXCEEDED"])
loose = R.load_rules(); loose["matching"]["price_tolerance_pct"] = 50.0; loose["matching"]["price_tolerance_max_abs"] = 100000
conn_, res_ = scenario("tolerance 50% / 1,00,000 lets invoice 05's balance overrun through", G("05_split_brightline_part3"), "HOLD",
                       pre=["03_split_brightline_part1", "04_split_brightline_part2"], rules=loose, lacks=["PO_BALANCE_EXCEEDED"], has=["QTY_OVER_PO"])

print("\n== PART 2g: human review, failures, reset ==")
conn = db.fresh_memory_db()
res = pipeline.process(conn, str(ROOT / "invoices/08_scanned_orbit_no_po.pdf"), mode="golden")
before = {p["po_number"]: p["remaining"] for p in db.po_status(conn)}["PO-2026-0104"]
db.apply_human_decision(conn, res["invoice_id"], "approve", "Confirmed PO with Sandeep Rao")
after = {p["po_number"]: p["remaining"] for p in db.po_status(conn)}["PO-2026-0104"]
ok("reviewer approving 08 consumes PO-0104's balance", before == 188800 and after == 0, f"{before} -> {after}")
ok("   ...and the audit trail gets a human_review step", db.get_run(conn, res["run_id"])["steps"][-1]["stage"] == "human_review")
pipeline.process(conn, str(ROOT / "invoices/06_dup_deccan_original.pdf"), mode="golden")
rej = pipeline.process(conn, str(ROOT / "invoices/07_dup_deccan_resubmit.pdf"), mode="golden")
ok("   (setup) 07 was rejected as a duplicate", rej["decision"] == "REJECT")
try:
    db.apply_human_decision(conn, rej["invoice_id"], "approve"); blocked = False
except ValueError:
    blocked = True
ok("a REJECTED invoice cannot be approved by a reviewer", blocked)

# extraction failure: visible, retryable, leaves no invoice behind
conn = db.fresh_memory_db()
real = pipeline.extract_invoice
def boom(*a, **k): raise ExtractionError("RATE_LIMIT", "Free-tier rate limit hit. Wait a minute and retry.")
pipeline.extract_invoice = boom
res = pipeline.process(conn, str(ROOT / "invoices/01_happy_sahyadri_steel.pdf"))
pipeline.extract_invoice = real
run_ = db.get_run(conn, res["run_id"])
ok("extraction failure -> run 'failed', code RATE_LIMIT, retryable", res["status"] == "failed" and res["error_code"] == "RATE_LIMIT" and res["retryable"] and run_["status"] == "failed")
ok("   ...later steps skipped, no invoice row created", [s["status"] for s in run_["steps"]][2:] == ["skipped"] * 7 and conn.execute("select count(*) from invoices").fetchone()[0] == 0)
res = pipeline.process(conn, str(ROOT / "invoices/does_not_exist.pdf"))
ok("missing file -> failed with FILE_NOT_FOUND", res["status"] == "failed" and res["error_code"] == "FILE_NOT_FOUND")
bad = G("01_happy_sahyadri_steel"); del bad["vendor"]
res = run(conn, bad)
ok("a malformed extraction can't crash the run: it fails visibly (INTERNAL_ERROR)", res["status"] == "failed" and res["error_code"] == "INTERNAL_ERROR", res)

# reset on a real file database
tmp = os.path.join(tempfile.mkdtemp(), "t.db")
fconn = db.connect(tmp); db.reset_demo(fconn)
pipeline.process(fconn, str(ROOT / "invoices/01_happy_sahyadri_steel.pdf"), mode="golden")
n1 = fconn.execute("select count(*) from invoices").fetchone()[0]
db.reset_demo(fconn)
n2, nv = fconn.execute("select count(*) from invoices").fetchone()[0], fconn.execute("select count(*) from vendors").fetchone()[0]
ok("reset wipes history and reloads master data", n1 == 1 and n2 == 0 and nv == 6, (n1, n2, nv))
res = pipeline.process(fconn, str(ROOT / "invoices/01_happy_sahyadri_steel.pdf"), mode="golden")
ok("after reset, invoice 01 approves again (balances restored)", res["decision"] == "APPROVE")
events = list(pipeline.process_iter(fconn, str(ROOT / "invoices/02_happy_tolerance_meridian.pdf"), mode="golden"))
kinds = [e["type"] for e in events]
pairs = all(kinds[1 + 2 * i] == "step_start" and kinds[2 + 2 * i] == "step_end" and events[1 + 2 * i]["stage"] == events[2 + 2 * i]["stage"] for i in range(9))
ok("live-view event stream: run_start, then a start/end pair per step (9), then done", kinds[0] == "run_start" and kinds[-1] == "done" and len(events) == 20 and pairs, kinds)

print(f"\n{'=' * 60}\n{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    print("FAILED:\n  - " + "\n  - ".join(FAILED))
sys.exit(1 if FAILED else 0)
