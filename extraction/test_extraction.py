"""
Two modes (run from the kit root):

  python -m extraction.test_extraction --validate-goldens
      Offline. Checks every golden file against the schema, checks its arithmetic, and (for
      digital PDFs) confirms the golden values really appear in the PDF text.

  python -m extraction.test_extraction            # needs GEMINI_API_KEY
  python -m extraction.test_extraction 08 09      # only files whose name starts with 08 or 09
      Live. Runs the real extractor on each invoice and diffs it against the golden answer.
      Use --no-cache to force fresh model calls.
"""
import json, pathlib, re, subprocess, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from extraction.schema import validate, field_confidences
from extraction.extract import extract_invoice, ExtractionError

GOLD = ROOT / "extraction" / "golden"
INV = ROOT / "invoices"
FLOOR = json.loads((ROOT / "data" / "rules.json").read_text())["extraction"]["min_field_confidence"]


def v(d, *path):
    for p in path:
        d = d[p]
    return d["value"] if isinstance(d, dict) and "value" in d else d


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def close(a, b, tol=1.0):
    return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) <= tol)


def compare(got, exp):
    """Return list of mismatches between an extraction and the golden answer."""
    bad = []
    def chk(name, a, b, eq=lambda x, y: norm(x) == norm(y)):
        if not eq(a, b):
            bad.append(f"{name}: got {a!r}, expected {b!r}")
    chk("document_type", got["document"]["document_type"], exp["document"]["document_type"])
    chk("vendor.gstin", v(got, "vendor", "gstin"), v(exp, "vendor", "gstin"))
    chk("vendor.name", v(got, "vendor", "name"), v(exp, "vendor", "name"), lambda x, y: norm(x).lower() == norm(y).lower())
    chk("vendor.email", v(got, "vendor", "email"), v(exp, "vendor", "email"), lambda x, y: norm(x).lower() == norm(y).lower())
    chk("buyer.gstin", v(got, "buyer", "gstin"), v(exp, "buyer", "gstin"))
    chk("invoice.number", v(got, "invoice", "number"), v(exp, "invoice", "number"))
    chk("invoice.date", v(got, "invoice", "date"), v(exp, "invoice", "date"))
    chk("invoice.po_reference", v(got, "invoice", "po_reference"), v(exp, "invoice", "po_reference"))
    chk("totals.total", v(got, "totals", "total"), v(exp, "totals", "total"), close)
    chk("totals.subtotal", v(got, "totals", "subtotal"), v(exp, "totals", "subtotal"), close)
    chk("tax.treatment", got["tax"]["treatment"], exp["tax"]["treatment"])
    chk("bank.account_number", v(got, "bank", "account_number"), v(exp, "bank", "account_number"))
    chk("bank.change_notice", got["bank"]["change_notice"]["detected"], exp["bank"]["change_notice"]["detected"])
    chk("lines.count", len(got["lines"]), len(exp["lines"]))
    for i, (g, e) in enumerate(zip(got["lines"], exp["lines"])):
        chk(f"lines[{i}].line_amount", g["line_amount"], e["line_amount"], close)
    return bad


def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout


def validate_goldens():
    ok = True
    for gf in sorted(GOLD.glob("*.json")):
        g = json.loads(gf.read_text())
        issues = validate(g)
        note = ""
        lines_sum = sum(l["line_amount"] for l in g["lines"])
        tot = v(g, "totals", "total")
        if g["tax"]["treatment"] == "separate":
            tax_sum = sum(t["amount"] for t in g["tax"]["tax_lines"])
            if not close(lines_sum, v(g, "totals", "subtotal")) or not close(lines_sum + tax_sum, tot):
                issues.append(f"arithmetic: lines {lines_sum} + tax {tax_sum} != total {tot}")
        elif not close(lines_sum, tot):
            issues.append(f"arithmetic: inclusive lines {lines_sum} != total {tot}")
        txt = pdf_text(INV / (gf.stem + ".pdf"))
        if len(txt.strip()) > 50:  # digital PDF: values must appear in the real document text
            flat = txt.replace(" ", "")
            for label, needle in [("invoice number", v(g, "invoice", "number")),
                                  ("vendor gstin", v(g, "vendor", "gstin")),
                                  ("bank account", v(g, "bank", "account_number")),
                                  ("po reference", v(g, "invoice", "po_reference"))]:
                if needle and needle.replace(" ", "") not in flat:
                    issues.append(f"{label} {needle!r} not found in PDF text")
            digits = re.sub(r"[^\d.]", "", txt)
            if re.sub(r"[^\d.]", "", f"{tot:.2f}") not in digits:
                issues.append(f"total {tot} not found in PDF text")
            note = "text cross-check passed"
        else:
            note = "scanned (no text layer): PDF cross-check skipped"
        status = "OK  " if not issues else "FAIL"
        ok &= not issues
        print(f"{status} {gf.stem:34s} {'; '.join(issues) if issues else note}")
    print("\nAll goldens valid." if ok else "\nSome goldens failed.")
    return ok


def live(prefixes, use_cache):
    files = sorted(p for p in INV.glob("*") if p.suffix.lower() in {".pdf", ".jpg"})
    if prefixes:
        files = [f for f in files if any(f.name.startswith(p) for p in prefixes)]
    passed = 0
    for f in files:
        gold = GOLD / (f.stem + ".json")
        if not gold.exists():
            continue
        t0 = time.time()
        try:
            r = extract_invoice(str(f), use_cache=use_cache)
        except ExtractionError as e:
            print(f"ERR  {f.name:34s} {e.code}: {e.message}")
            continue
        bad = compare(r["data"], json.loads(gold.read_text()))
        low = {k: c for k, c in field_confidences(r["data"]).items() if c < FLOOR}
        tag = "PASS" if not bad else "DIFF"
        passed += not bad
        print(f"{tag} {f.name:34s} {r['meta']['latency_ms']:>5} ms  cached={r['meta']['cached']}  low-confidence fields={len(low)}")
        for b in bad:
            print(f"       - {b}")
        if len(files) > 1:
            time.sleep(1.5)  # be gentle with free-tier rate limits
    print(f"\n{passed}/{len(files)} matched the golden answers exactly on all checked fields.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--validate-goldens" in args:
        sys.exit(0 if validate_goldens() else 1)
    live([a for a in args if not a.startswith("--")], use_cache="--no-cache" not in args)
