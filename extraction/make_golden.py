"""Builds golden/*.json: what a perfect extraction of each test invoice looks like.
Derived from the same data that generated the PDFs (gen_invoices.INVOICES), so they cannot drift.
Run from the kit root:  python -m extraction.make_golden
"""
import datetime as dt, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import gen_invoices as gi
from extraction.schema import validate

OUT = pathlib.Path(__file__).parent / "golden"
INVNO = {"Invoice No:", "Invoice No.", "Invoice #"}
DATE = {"Invoice Date:", "Date:", "Date", "Invoice Date", "Inv. Date"}
PO = {"PO No:", "Your Order Ref", "PO#", "PO Ref:", "Ref PO"}
DUE = {"Due Date:"}
FMTS = ["%d-%b-%Y", "%d/%m/%Y", "%d %b %Y"]


def iso(s):
    for f in FMTS:
        try:
            return dt.datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(s)


def W(value, evidence=None, conf=0.98):
    return {"value": value, "confidence": conf if value is not None else 0.95,
            "evidence": evidence if value is not None else None}


EXTRA = {  # per-file facts that can't be derived from the generator data
    "07_dup_deccan_resubmit": dict(po_label="Ref PO"),
    "08_scanned_orbit_no_po": dict(
        scan=True, other_refs=["Your Ref: Email dt. 04-Sep-2026 (Mr. Sandeep)"],
        annotations=["RECEIVED 26 SEP 2026 - KCPL (rubber stamp)", "fwd to Sandeep - IT (handwritten)"]),
    "09_bank_change_sahyadri": dict(
        notice="IMPORTANT: Our bank details have changed with effect from 01-Sep-2026."),
}


def build(inv):
    stem = inv["file"][:-4]
    x = EXTRA.get(stem, {})
    meta = {k: v for k, v in inv["meta"]}
    v = inv["vendor"]
    number = next(val for k, val in meta.items() if k in INVNO)
    date = iso(next(val for k, val in meta.items() if k in DATE))
    po_label = next((k for k in meta if k in PO), None)
    po = meta.get(po_label) if po_label else None
    due = next((iso(val) for k, val in meta.items() if k in DUE), None)

    lines = [{"description": l["desc"], "hsn_sac": l["hsn"], "quantity": l["qty"], "uom": l["uom"],
              "unit_price": float(l["rate"]), "line_amount": float(l["qty"] * l["rate"]), "confidence": 0.97}
             for l in inv["lines"]]
    sub = float(sum(l["qty"] * l["rate"] for l in inv["lines"]))
    mode = inv["tax_mode"]
    if mode == "split":
        cg = round(sub * 0.09, 2)
        tax = {"treatment": "separate", "inclusive_statement": None,
               "tax_lines": [{"tax_type": "CGST", "rate_pct": 9.0, "amount": cg},
                             {"tax_type": "SGST", "rate_pct": 9.0, "amount": cg}]}
        total, subtotal = sub + 2 * cg, W(sub, f"Sub Total {gi.inr(sub)}")
    elif mode == "igst":
        ig = round(sub * 0.18, 2)
        tax = {"treatment": "separate", "inclusive_statement": None,
               "tax_lines": [{"tax_type": "IGST", "rate_pct": 18.0, "amount": ig}]}
        total, subtotal = sub + ig, W(sub, f"Taxable Value {gi.inr(sub)}")
    else:  # inclusive
        tax = {"treatment": "inclusive", "inclusive_statement": "All amounts are inclusive of IGST @ 18%",
               "tax_lines": [{"tax_type": "IGST", "rate_pct": 18.0, "amount": None}]}
        total, subtotal = sub, W(None)

    acct, ifsc = re.search(r"A/c No:\s*(\d+)\s+IFSC:\s*(\w+)", inv["bank"][1]).groups()
    scan = x.get("scan", False)
    return {
        "document": {"document_type": "tax_invoice", "document_quality": "scan_clear" if scan else "digital",
                     "multiple_invoices_detected": False, "annotations": x.get("annotations", [])},
        "vendor": {"name": W(v["name"], v["name"]), "gstin": W(v["gstin"], f"GSTIN: {v['gstin']}"),
                   "email": W(v["email"], v["email"]), "address": ", ".join(v["addr"])},
        "buyer": {"name": W("Kestrel Components Pvt Ltd", "Kestrel Components Pvt Ltd"),
                  "gstin": W("27AAKCK4821M1Z3", "GSTIN: 27AAKCK4821M1Z3")},
        "invoice": {"number": W(number, number), "date": W(date, next(val for k, val in meta.items() if k in DATE)),
                    "due_date": due, "po_reference": W(po, f"{po_label}: {po}" if po else None),
                    "other_references": x.get("other_refs", []), "currency": W("INR", "₹")},
        "lines": lines, "tax": tax,
        "totals": {"subtotal": subtotal, "total": W(total, gi.inr(total, sym=True)),
                   "total_in_words": gi.words(total)},
        "bank": {"bank_name": W(inv["bank"][0], inv["bank"][0]), "account_number": W(acct, f"A/c No: {acct}"),
                 "ifsc": W(ifsc, f"IFSC: {ifsc}"),
                 "change_notice": {"detected": "notice" in x, "evidence": x.get("notice")}},
    }


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for inv in gi.INVOICES:
        g = build(inv)
        bad = validate(g)
        assert not bad, (inv["file"], bad)
        (OUT / (inv["file"][:-4] + ".json")).write_text(json.dumps(g, indent=2, ensure_ascii=False))
        print(f"{inv['file'][:-4]:34s} total={g['totals']['total']['value']:>11,.2f} "
              f"tax={g['tax']['treatment']:9s} po={g['invoice']['po_reference']['value']}")
