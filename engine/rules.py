"""
The rules engine. THE AI EXTRACTS, THE RULES DECIDE.

Each stage function takes (conn, state, rules) and returns:
    {"checks": [...], "data": {...}, "summary": "one line"}
A check is {"code", "level", "message", "action"?, "data"?} with level one of:
    pass < info < warn < review < hold < reject
'warn' and 'info' are notes and never change the decision. The final decision is the most severe
of review/hold/reject (NEEDS_REVIEW / HOLD / REJECT), or APPROVE if none was raised.
Every number that matters comes from data/rules.json, not from this file.
"""
from __future__ import annotations
import json, pathlib, re

from . import db as D
from .util import (desc_sim, fmt_inr, fmt_pct, fmt_qty, name_sim, normalize_invoice_number, normalize_po,
                   short, words_to_number)

RULES_PATH = D.DATA_DIR / "rules.json"
LEVELS = ["pass", "info", "warn", "review", "hold", "reject"]
RANK = {l: i for i, l in enumerate(LEVELS)}
DECISION_OF = {"review": "NEEDS_REVIEW", "hold": "HOLD", "reject": "REJECT"}
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

REQUIRED = {"vendor_name": ("vendor", "name"), "invoice_number": ("invoice", "number"),
            "invoice_date": ("invoice", "date"), "total_amount": ("totals", "total")}
CONF_FIELDS = [("vendor", "gstin"), ("vendor", "name"), ("invoice", "number"), ("invoice", "date"), ("totals", "total")]
CONF_IF_PRESENT = [("invoice", "po_reference"), ("bank", "account_number")]


def load_rules(path=None) -> dict:
    return json.loads(pathlib.Path(path or RULES_PATH).read_text())


def C(code, level, message, action=None, **data):
    return {"code": code, "level": level, "message": message, "action": action, "data": data}


def val(ex, *path):
    node = ex
    for p in path:
        node = node[p]
    return node["value"] if isinstance(node, dict) and "value" in node else node


def conf(ex, *path):
    node = ex
    for p in path:
        node = node[p]
    return node.get("confidence") if isinstance(node, dict) else None


def worst(checks) -> str:
    return max((c["level"] for c in checks), key=lambda l: RANK[l], default="pass")


def _summ(checks, ok_text):
    bad = [c for c in checks if RANK[c["level"]] >= RANK["warn"]]
    if not bad:
        return ok_text
    bad.sort(key=lambda c: -RANK[c["level"]])
    return bad[0]["message"] + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else "")


def _lines_sig(ex) -> str:
    return json.dumps(sorted(re.sub(r"\s+", " ", l["description"].lower()).strip() for l in ex["lines"]))


# =====================================================================================
# 3. VALIDATE: is this a complete, self-consistent invoice?
# =====================================================================================
def stage_validate(conn, state, rules):
    ex, ext = state["ex"], rules["extraction"]
    tol = ext.get("arithmetic_tolerance_inr", 1.0)
    floor = ext["min_field_confidence"]
    checks = []

    dt = ex["document"]["document_type"]
    if dt not in ext["accepted_document_types"]:
        pretty = dt.replace("_", " ")
        checks.append(C("DOC_NOT_INVOICE", "review", f"This document is a {pretty}, not a tax invoice",
                        action="A person should decide what to do with it; it is not routed for payment"))
        state["halt"] = "Not a tax invoice, so no payment checks apply"
        return {"checks": checks, "data": {"document_type": dt}, "summary": checks[0]["message"]}
    checks.append(C("DOC_TYPE", "pass", "Document is a tax invoice"))

    if ex["document"]["multiple_invoices_detected"]:
        checks.append(C("MULTI_INVOICE", "review", "The file contains more than one invoice; only the first was read",
                        action="Split the file and submit each invoice separately"))

    missing = [k for k, p in REQUIRED.items() if val(ex, *p) in (None, "")]
    if missing:
        checks.append(C("MISSING_FIELD", "hold", "Required information is missing: " + ", ".join(m.replace("_", " ") for m in missing),
                        action="Ask the vendor for a complete, corrected invoice", fields=missing))
    else:
        checks.append(C("REQUIRED_FIELDS", "pass", "Vendor, invoice number, date and total are all present"))

    cur = val(ex, "invoice", "currency")
    if cur and cur != rules["currency"]:
        checks.append(C("CURRENCY", "review", f"Invoice currency is {cur}; only {rules['currency']} is handled automatically"))

    low = []
    for p in CONF_FIELDS + [p for p in CONF_IF_PRESENT if val(ex, *p) is not None]:
        c = conf(ex, *p)
        if val(ex, *p) is not None and c is not None and c < floor:
            low.append(f"{'.'.join(p)} ({c:.2f})")
    if low:
        checks.append(C("LOW_CONFIDENCE", "review", f"The reader was unsure about: {', '.join(low)}",
                        action="A person should check these fields against the document", fields=low))
    else:
        checks.append(C("CONFIDENCE", "pass", f"All key fields read with confidence at or above {floor:.2f}"))

    # --- arithmetic (the model never does this; code does) ---
    total = val(ex, "totals", "total")
    treatment = ex["tax"]["treatment"]
    lines_sum = round(sum(l["line_amount"] for l in ex["lines"]), 2)
    derived = {"lines_sum": lines_sum}
    if total is not None and ex["lines"]:
        bad = []
        if treatment == "separate":
            sub = val(ex, "totals", "subtotal")
            base = sub if sub is not None else lines_sum
            tax_sum = round(sum(t["amount"] for t in ex["tax"]["tax_lines"] if t["amount"] is not None), 2)
            if sub is not None and abs(lines_sum - sub) > tol:
                bad.append(f"line items add up to {fmt_inr(lines_sum)} but the sub total says {fmt_inr(sub)}")
            if abs(base + tax_sum - total) > tol:
                bad.append(f"sub total {fmt_inr(base)} + tax {fmt_inr(tax_sum)} = {fmt_inr(base + tax_sum)} but the total says {fmt_inr(total)}")
            for t in ex["tax"]["tax_lines"]:
                if t["rate_pct"] is not None and t["amount"] is not None and abs(base * t["rate_pct"] / 100 - t["amount"]) > tol:
                    bad.append(f"{t['tax_type']} @ {t['rate_pct']:g}% should be {fmt_inr(base * t['rate_pct'] / 100)} but is {fmt_inr(t['amount'])}")
            derived.update(taxable=base, tax=tax_sum)
            ok = f"Line items add up to the sub total, and sub total + tax = {fmt_inr(total)}"
        else:
            if abs(lines_sum - total) > tol:
                bad.append(f"line items add up to {fmt_inr(lines_sum)} but the total says {fmt_inr(total)}")
            ok = f"Line items add up to the total ({fmt_inr(total)})"
        if bad:
            checks.append(C("ARITHMETIC", "hold", "The invoice's own numbers do not add up: " + "; ".join(bad),
                            action="Ask the vendor for a corrected invoice"))
        else:
            checks.append(C("ARITHMETIC", "pass", ok))

    # --- tax-inclusive pricing: backed out by code, never by the model ---
    if treatment == "inclusive" and total is not None:
        rate = next((t["rate_pct"] for t in ex["tax"]["tax_lines"] if t["rate_pct"] is not None), None)
        ttype = next((t["tax_type"] for t in ex["tax"]["tax_lines"]), "tax")
        if rate:
            taxable = round(total / (1 + rate / 100), 2)
            derived.update(taxable=taxable, tax=round(total - taxable, 2), inclusive_rate=rate)
            checks.append(C("TAX_INCLUSIVE", "pass", f"Prices include {ttype} @ {rate:g}%: taxable {fmt_inr(taxable)} + {ttype} {fmt_inr(total - taxable)} (calculated by the rules, not the AI)"))
        else:
            checks.append(C("TAX_RATE_UNKNOWN", "review", "Prices are tax-inclusive but no tax rate is stated, so the taxable value cannot be worked out"))

    # --- amount in words cross-check (catches misread digits) ---
    words = val(ex, "totals", "total_in_words")
    if words and total is not None:
        w = words_to_number(words)
        if w is None:
            checks.append(C("WORDS_UNPARSED", "info", "Amount in words could not be parsed; cross-check skipped"))
        elif abs(w - total) > tol:
            checks.append(C("WORDS_MISMATCH", "review", f"Total in figures is {fmt_inr(total)} but the amount in words says {fmt_inr(w)}",
                            action="A digit may have been misread; check the document"))
        else:
            checks.append(C("WORDS_MATCH", "pass", "Amount in words agrees with the total in figures"))
    state["derived"] = derived
    return {"checks": checks, "data": {"derived": derived}, "summary": _summ(checks, "Complete, consistent invoice")}


# =====================================================================================
# 4. VENDOR: who is this, really? GSTIN is trusted over the name.
# =====================================================================================
def _vendor_names(v):
    return [v["vendor_name"]] + [a for a in (v["aliases"] or "").split("|") if a]


def _best_vendor_by_name(name, vendors):
    best, score = None, 0.0
    for v in vendors:
        s = max((name_sim(name, n) for n in _vendor_names(v)), default=0.0)
        if s > score:
            best, score = v, s
    return best, score


def stage_vendor(conn, state, rules):
    ex, vm = state["ex"], rules["vendor_matching"]
    vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors")]
    by_gstin = {v["gstin"].upper(): v for v in vendors}
    gstin, name = val(ex, "vendor", "gstin"), val(ex, "vendor", "name")
    gstin = gstin.upper().replace(" ", "") if gstin else gstin
    checks, v = [], None

    if gstin and not GSTIN_RE.match(gstin):
        checks.append(C("GSTIN_FORMAT", "review", f"Vendor GSTIN '{gstin}' is not a valid GSTIN format",
                        action="Check the GSTIN against the document"))
    cand, cand_score = _best_vendor_by_name(name or "", vendors)

    if gstin and gstin in by_gstin:
        v = by_gstin[gstin]
        own = max(name_sim(name, n) for n in _vendor_names(v)) if name else 0.0
        if own >= vm["name_match_min"]:
            checks.append(C("VENDOR_MATCH", "pass", f"GSTIN {gstin} is {v['vendor_name']} ({v['vendor_id']}); name matches ({fmt_pct(own * 100, 0)})"))
        elif cand and cand["vendor_id"] != v["vendor_id"] and cand_score >= vm["lookalike_min"]:
            checks.append(C("VENDOR_IDENTITY_CONFLICT", "hold", f"GSTIN belongs to {v['vendor_name']} but the invoice name '{name}' resembles {cand['vendor_name']}",
                            action="Confirm who actually issued this invoice"))
        else:
            checks.append(C("VENDOR_NAME_MISMATCH", "review", f"GSTIN matches {v['vendor_name']} but the invoice name is '{name}'",
                            action="Confirm this is a trade name or a rename"))
    elif gstin:
        if cand and cand_score >= vm["lookalike_min"]:
            checks.append(C("VENDOR_LOOKALIKE", "reject", f"GSTIN {gstin} is not in the vendor master, but the name '{name}' closely resembles {cand['vendor_name']} ({fmt_pct(cand_score * 100, 0)}). Possible impersonation",
                            action="Do not pay. Alert procurement and verify with the real vendor", lookalike_of=cand["vendor_id"]))
        else:
            checks.append(C("VENDOR_UNKNOWN", "reject", f"GSTIN {gstin} ('{name}') is not an approved vendor",
                            action="Route to procurement for vendor onboarding before any payment"))
    else:
        if cand and cand_score >= vm["name_only_min"]:
            v = cand
            checks.append(C("VENDOR_BY_NAME_ONLY", "review", f"No GSTIN on the invoice; matched to {cand['vendor_name']} by name only ({fmt_pct(cand_score * 100, 0)})",
                            action="Confirm the vendor and ask for a GSTIN-bearing invoice"))
        else:
            checks.append(C("VENDOR_UNKNOWN", "reject", f"No GSTIN and no vendor matching '{name}'",
                            action="Route to procurement for vendor onboarding before any payment"))

    if v and v["status"] == "blocked":
        checks.append(C("VENDOR_BLOCKED", "reject", f"{v['vendor_name']} is blocked: {v['status_note'] or 'no reason recorded'}",
                        action="Do not pay. Procurement must clear the block first"))
    elif v:
        checks.append(C("VENDOR_ACTIVE", "pass", f"{v['vendor_name']} is an active, approved vendor"))
    state["vendor"] = v
    return {"checks": checks, "data": {"vendor_id": v and v["vendor_id"], "name_best_guess": cand and cand["vendor_id"]},
            "summary": _summ(checks, f"{v['vendor_name']} ({v['vendor_id']}), active" if v else "Vendor not identified")}


# =====================================================================================
# 5. VERIFY: payment details & compliance (the fraud and tax checks)
# =====================================================================================
def stage_verify(conn, state, rules):
    ex, v, checks = state["ex"], state.get("vendor"), []
    vv = rules["vendor_verification"]

    # bank account
    acct = val(ex, "bank", "account_number")
    notice = ex["bank"]["change_notice"]
    if v is None:
        checks.append(C("BANK_SKIPPED", "info", "Bank check skipped: vendor not identified"))
    elif not acct:
        checks.append(C("BANK_ABSENT", "info", "No bank details on the invoice; nothing to verify"))
    else:
        last4 = re.sub(r"\D", "", acct)[-4:]
        if last4 != v["bank_account_last4"]:
            extra = " The invoice itself announces a change of bank details." if notice["detected"] else ""
            checks.append(C("BANK_MISMATCH", "hold",
                            f"Bank account ends {last4}, but the vendor master has an account ending {v['bank_account_last4']}.{extra}",
                            action=vv["bank_account"]["required_action"], invoice_last4=last4, master_last4=v["bank_account_last4"]))
        elif notice["detected"]:
            checks.append(C("BANK_NOTICE_ODD", "review", "The invoice announces a bank change, but the account matches the vendor master",
                            action="Confirm with the vendor using the contact in the vendor master"))
        else:
            checks.append(C("BANK_MATCH", "pass", f"Bank account (ending {last4}) matches the vendor master"))

    # sender email domain
    email = val(ex, "vendor", "email")
    if v and email and v["contact_email"] and "@" in email:
        d_inv, d_master = email.lower().split("@")[-1], v["contact_email"].lower().split("@")[-1]
        if d_inv != d_master:
            checks.append(C("EMAIL_DOMAIN_MISMATCH", "review", f"Invoice email domain is {d_inv}; the vendor master has {d_master}",
                            action="Treat with suspicion; verify by phone using the vendor master contact"))
        else:
            checks.append(C("EMAIL_MATCH", "pass", f"Email domain ({d_inv}) matches the vendor master"))

    # buyer GSTIN
    bg = val(ex, "buyer", "gstin")
    if bg is None:
        checks.append(C("BUYER_GSTIN_ABSENT", "warn", "No buyer GSTIN on the invoice; input tax credit may be affected"))
    elif bg.upper().replace(" ", "") != rules["buyer"]["gstin"]:
        checks.append(C("BUYER_GSTIN_MISMATCH", "hold", f"Invoice is billed to GSTIN {bg}, not {rules['buyer']['name']} ({rules['buyer']['gstin']}); the tax credit would be at risk",
                        action="Ask the vendor to reissue the invoice to the correct GSTIN"))
    else:
        checks.append(C("BUYER_GSTIN_OK", "pass", f"Billed to the correct entity ({rules['buyer']['name']})"))

    # CGST+SGST vs IGST by state
    vg = (v["gstin"] if v else val(ex, "vendor", "gstin")) or ""
    types = {t["tax_type"] for t in ex["tax"]["tax_lines"]}
    if len(vg) >= 2 and types:
        intra = vg[:2] == rules["buyer"]["gstin"][:2]
        want = {"CGST", "SGST"} if intra else {"IGST"}
        got = types - {"CESS", "OTHER"}
        label = "same-state (CGST + SGST)" if intra else "inter-state (IGST)"
        if got != want:
            checks.append(C("TAX_TYPE_WRONG", "hold", f"This is a {label} supply but the invoice charges {' + '.join(sorted(got)) or 'no tax'}; the buyer cannot claim credit",
                            action="Ask the vendor for a corrected invoice with the right tax type"))
        else:
            checks.append(C("TAX_TYPE_OK", "pass", f"Tax type ({' + '.join(sorted(got))}) is correct for a {label} supply"))
    return {"checks": checks, "data": {}, "summary": _summ(checks, "Bank, email, buyer GSTIN and tax type all check out")}


# =====================================================================================
# 6. DUPLICATES: precise, not paranoid
# =====================================================================================
def stage_duplicates(conn, state, rules):
    ex, v, dd = state["ex"], state.get("vendor"), rules["duplicate_detection"]
    if v is None:
        return {"checks": [C("DUP_SKIPPED", "info", "Duplicate check skipped: vendor not identified")], "data": {}, "summary": "Skipped (vendor unknown)"}
    number, date, total = val(ex, "invoice", "number"), val(ex, "invoice", "date"), val(ex, "totals", "total")
    norm = normalize_invoice_number(number)
    sig = _lines_sig(ex)
    rows = conn.execute(
        "SELECT * FROM invoices WHERE vendor_id=? AND decision!='REJECT' AND COALESCE(human_decision,'')!='REJECT' "
        "AND (? IS NULL OR ABS(julianday(invoice_date)-julianday(?))<=?)", (v["vendor_id"], date, date, dd["lookback_days"])).fetchall()
    checks, ruled_out = [], []
    for p in rows:
        if norm and p["invoice_number_norm"] == norm:
            diffs = [x for x, same in [("date", p["invoice_date"] == date), ("total", abs(p["total_amount"] - (total or 0)) < 0.5)] if not same]
            checks.append(C("DUPLICATE_DEFINITE", "reject",
                            f"Duplicate: same vendor and same invoice number as {p['invoice_number_raw']} ({p['invoice_date']}, {fmt_inr(p['total_amount'])}, {p['decision']}). "
                            f"Both normalise to '{norm}'" + (f"; only the {' and '.join(diffs)} differs" if diffs else ""),
                            action="Do not pay twice. Reject and tell the vendor", duplicate_of=p["id"], normalised=norm))
            state["halt"] = "Duplicate invoices are rejected without further checks"
            break
        if total and abs(p["total_amount"] - total) <= dd["amount_match_pct"] / 100 * total:
            if p["invoice_date"] == date or p["lines_sig"] == sig:
                checks.append(C("DUPLICATE_PROBABLE", "review",
                                f"Probable duplicate of {p['invoice_number_raw']}: same vendor and amount ({fmt_inr(total)}) and the same {'date' if p['invoice_date'] == date else 'line items'}, but a different invoice number",
                                action="Confirm with the vendor whether this is a re-issue", duplicate_of=p["id"]))
                break
            ruled_out.append(p)
    if not checks:
        if ruled_out:
            p = ruled_out[0]
            checks.append(C("DUPLICATE_RULED_OUT", "pass",
                            f"Same amount as {p['invoice_number_raw']} ({fmt_inr(p['total_amount'])}) but a different number, date and line items, so treated as a separate billing"))
        else:
            checks.append(C("NO_DUPLICATE", "pass", f"No earlier invoice from this vendor with the same number (normalised '{norm}') or the same amount and date in the last {dd['lookback_days']} days"))
    return {"checks": checks, "data": {"normalised_number": norm, "compared_against": len(rows)}, "summary": _summ(checks, checks[0]["message"])}


# =====================================================================================
# 7. PO MATCH: find it (or infer it), then check status, vendor, date, balance
# =====================================================================================
def _allowed_overrun(remaining, rules):
    m = rules["matching"]
    return max(0.0, min(remaining * m["price_tolerance_pct"] / 100, m["price_tolerance_max_abs"]))


def _po_remaining(conn, po):
    used = D.po_consumed(conn, po["po_number"])
    return po["total"] - used, used


def _infer_po(conn, state, rules):
    ex, v, inf = state["ex"], state["vendor"], rules["po_inference"]
    total = val(ex, "totals", "total")
    cands = []
    for po in conn.execute("SELECT * FROM purchase_orders WHERE vendor_id=? AND status='open'", (v["vendor_id"],)):
        po = dict(po)
        remaining, _ = _po_remaining(conn, po)
        if remaining <= 0:
            continue
        ratio = total / remaining
        amount = 0.0 if total > remaining + _allowed_overrun(remaining, rules) else max(0.0, 1 - abs(1 - ratio))
        plines = [dict(r) for r in conn.execute("SELECT * FROM po_lines WHERE po_number=?", (po["po_number"],))]
        hsns = {p["hsn_sac"] for p in plines}
        n = max(len(ex["lines"]), 1)
        hsn = sum(1 for l in ex["lines"] if l["hsn_sac"] in hsns) / n
        desc = sum(max((desc_sim(l["description"], p["item_description"]) for p in plines), default=0) for l in ex["lines"]) / n
        w = inf["weights"]
        score = w["amount"] * amount + w["hsn"] * hsn + w["description"] * desc
        cands.append({"po": po["po_number"], "score": round(score, 3), "amount": round(amount, 2), "hsn": round(hsn, 2), "description": round(desc, 2)})
    cands.sort(key=lambda c: -c["score"])
    return cands


def stage_po_match(conn, state, rules):
    ex, v, checks = state["ex"], state.get("vendor"), []
    state["po"] = None
    if v is None:
        return {"checks": [C("PO_SKIPPED", "info", "PO matching skipped: vendor not identified")], "data": {}, "summary": "Skipped (vendor unknown)"}
    ref = val(ex, "invoice", "po_reference")
    others = ex["invoice"]["other_references"]
    inv_total, inv_date = val(ex, "totals", "total"), val(ex, "invoice", "date")
    po, method, score, data = None, None, None, {}

    if ref:
        by_norm = {normalize_po(r["po_number"]): dict(r) for r in conn.execute("SELECT * FROM purchase_orders")}
        po = by_norm.get(normalize_po(ref))
        if po is None:
            checks.append(C("PO_NOT_FOUND", "hold", f"The invoice cites PO '{ref}', which does not exist",
                            action="Ask the vendor or requester for the correct PO number"))
        else:
            method = "explicit"
            checks.append(C("PO_FOUND", "pass", f"PO reference '{ref}' matches {po['po_number']} ({po['description']})"))
            if po["vendor_id"] != v["vendor_id"]:
                checks.append(C("PO_VENDOR_MISMATCH", "hold", f"{po['po_number']} was issued to a different vendor ({po['vendor_id']}), not {v['vendor_id']}",
                                action="Check whether the vendor cited the wrong PO"))
    else:
        for o in others:
            checks.append(C("REFERENCE_NOT_PO", "info", f"The invoice's reference '{o}' is not a PO number"))
        if not rules["po_inference"]["enabled"]:
            checks.append(C("PO_MISSING", "hold", "No PO reference on the invoice", action="Ask the vendor or requester for the PO number"))
        else:
            cands = _infer_po(conn, state, rules)
            data["candidates"] = cands
            inf = rules["po_inference"]
            top = cands[0] if cands else None
            if not top or top["score"] < inf["min_score"]:
                checks.append(C("PO_MISSING", "hold", "No PO reference on the invoice, and no open PO for this vendor is a convincing match",
                                action="Ask the requester which PO this belongs to"))
            elif len(cands) > 1 and cands[1]["score"] >= inf["min_score"] and top["score"] - cands[1]["score"] < inf["ambiguity_margin"]:
                checks.append(C("PO_AMBIGUOUS", "review", f"No PO reference; {top['po']} ({top['score']:.0%}) and {cands[1]['po']} ({cands[1]['score']:.0%}) both fit",
                                action="A person must choose the PO"))
            else:
                po = dict(conn.execute("SELECT * FROM purchase_orders WHERE po_number=?", (top["po"],)).fetchone())
                method, score = "inferred", top["score"]
                runner = f"; next best {cands[1]['po']} scored {cands[1]['score']:.0%}" if len(cands) > 1 else ""
                checks.append(C("PO_INFERRED", "review",
                                f"No PO reference on the invoice. Best match is {po['po_number']} ({po['description']}) at {top['score']:.0%} confidence: amount fit {top['amount']:.0%}, HSN codes {top['hsn']:.0%}, description {top['description']:.0%}{runner}",
                                action="A person confirms the PO before payment. Inferred matches are never auto-approved", score=top["score"]))

    if po:
        if po["status"] != "open":
            checks.append(C("PO_CLOSED", "hold", f"{po['po_number']} is {po['status']}", action="The requester must reopen the PO or confirm the purchase"))
        if inv_date and inv_date < po["po_date"]:
            checks.append(C("INVOICE_BEFORE_PO", "review", f"Invoice date {inv_date} is before the PO date {po['po_date']}; the PO may have been raised after the purchase",
                            action="Check whether this purchase was approved in advance"))
        remaining, used = _po_remaining(conn, po)
        data.update(po_total=po["total"], already_invoiced=used, remaining_before=remaining, invoice_total=inv_total)
        if remaining <= 0:
            checks.append(C("PO_EXHAUSTED", "hold", f"{po['po_number']} has no balance left (PO {fmt_inr(po['total'])}, already invoiced {fmt_inr(used)})",
                            action="Ask the requester to confirm the extra spend or amend the PO"))
        else:
            excess = inv_total - remaining
            if excess <= 0:
                data["remaining_after"] = remaining - inv_total
                checks.append(C("PO_BALANCE_OK", "pass", f"{fmt_inr(inv_total)} is within the PO's remaining balance of {fmt_inr(remaining)} ({fmt_inr(remaining - inv_total)} left after this invoice)"))
            else:
                allowed = _allowed_overrun(remaining, rules)
                m = rules["matching"]
                p = excess / remaining * 100
                data.update(excess=excess, allowed=allowed)
                if excess <= allowed + 0.005:
                    checks.append(C("PO_WITHIN_TOLERANCE", "warn", f"Invoice is {fmt_inr(excess)} ({fmt_pct(p, 2)}) over the remaining balance of {fmt_inr(remaining)}, within the tolerance of {fmt_inr(allowed)} (lower of {m['price_tolerance_pct']:g}% or {fmt_inr(m['price_tolerance_max_abs'])})"))
                else:
                    checks.append(C("PO_BALANCE_EXCEEDED", "hold", f"Invoice total {fmt_inr(inv_total)} exceeds the PO's remaining balance of {fmt_inr(remaining)} by {fmt_inr(excess)} ({fmt_pct(p)}), above the tolerance of {fmt_inr(allowed)}",
                                    action="Ask the requester to confirm the extra amount or amend the PO, or ask the vendor for a corrected invoice"))
    state.update(po=po, po_method=method, po_score=score, po_remaining_after=data.get("remaining_after"))
    return {"checks": checks, "data": data, "summary": _summ(checks, f"{po['po_number']}: {checks[-1]['message']}" if po else "No PO matched")}


# =====================================================================================
# 8. LINES: quantities and prices against the PO, cumulative across invoices
# =====================================================================================
def _match_po_line(ln, plines, lm):
    same_hsn = [p for p in plines if ln["hsn_sac"] and p["hsn_sac"] == ln["hsn_sac"]]
    if len(same_hsn) == 1:
        return same_hsn[0]
    if len(same_hsn) > 1:
        return max(same_hsn, key=lambda p: desc_sim(ln["description"], p["item_description"]))
    best = max(plines, key=lambda p: desc_sim(ln["description"], p["item_description"]), default=None)
    return best if best and desc_sim(ln["description"], best["item_description"]) >= lm["min_similarity_without_hsn"] else None


def stage_lines(conn, state, rules):
    ex, po, lm = state["ex"], state.get("po"), rules["line_matching"]
    state["line_matches"] = []
    if not po:
        return {"checks": [C("LINES_SKIPPED", "info", "Line check skipped: no PO matched")], "data": {}, "summary": "Skipped (no PO)"}
    plines = [dict(r) for r in conn.execute("SELECT * FROM po_lines WHERE po_number=? ORDER BY line_no", (po["po_number"],))]
    rate = state.get("derived", {}).get("inclusive_rate")
    running, checks = {}, []
    for i, ln in enumerate(ex["lines"], 1):
        pl = _match_po_line(ln, plines, lm)
        state["line_matches"].append({"line_no": i, "po_line_no": pl["line_no"] if pl else None})
        label = f"Line {i} '{short(ln['description'], 38)}'"
        if pl is None:
            checks.append(C("UNORDERED_LINE", "warn", f"{label} ({fmt_inr(ln['line_amount'])}) is not on the PO"))
            continue
        qty = ln["quantity"]
        problems, notes = [], []
        if qty is not None:
            before = D.po_line_billed_qty(conn, po["po_number"], pl["line_no"]) + running.get(pl["line_no"], 0)
            cum = before + qty
            running[pl["line_no"]] = running.get(pl["line_no"], 0) + qty
            if cum > pl["quantity"] + 1e-9:
                problems.append(("QTY_OVER_PO", f"{label} bills {fmt_qty(qty)} {ln['uom'] or ''}; cumulative {fmt_qty(cum)} exceeds the {fmt_qty(pl['quantity'])} ordered on PO line {pl['line_no']}",
                                 "Confirm the extra quantity with the requester; a PO amendment may be needed"))
            else:
                notes.append(f"qty {fmt_qty(qty)} (cumulative {fmt_qty(cum)} of {fmt_qty(pl['quantity'])})")
        up = ln["unit_price"]
        if up is not None and pl["unit_price"] and ex["tax"]["treatment"] == "inclusive" and not rate:
            notes.append("rate not compared (tax-inclusive price, tax rate unknown)")
        elif up is not None and pl["unit_price"]:
            up_ex = up / (1 + rate / 100) if rate else up
            var = (up_ex - pl["unit_price"]) / pl["unit_price"] * 100
            if abs(var) > lm["price_tolerance_pct"]:
                problems.append(("PRICE_VARIANCE", f"{label} rate {fmt_inr(up_ex)} differs from the PO rate {fmt_inr(pl['unit_price'])} by {fmt_pct(var)} (tolerance {lm['price_tolerance_pct']:g}%)",
                                 "Ask the vendor to explain the price change"))
            else:
                notes.append(f"rate {fmt_inr(up_ex)} = PO {fmt_inr(pl['unit_price'])}" if abs(var) < 0.005 else f"rate {fmt_inr(up_ex)} vs PO {fmt_inr(pl['unit_price'])} ({fmt_pct(var)})")
        if problems:
            for code, msg, act in problems:
                checks.append(C(code, "hold", msg, action=act))
        else:
            checks.append(C("LINE_OK", "pass", f"{label} matches PO line {pl['line_no']}: " + ", ".join(notes)))
    return {"checks": checks, "data": {"matches": state["line_matches"]}, "summary": _summ(checks, f"All {len(ex['lines'])} lines match the PO")}


STAGE_FUNCS = {"validate": stage_validate, "vendor": stage_vendor, "verify": stage_verify,
               "duplicates": stage_duplicates, "po_match": stage_po_match, "lines": stage_lines}


# =====================================================================================
# 9. DECIDE: the most severe thing that happened wins, with reasons you can read
# =====================================================================================
def decide(state, all_checks):
    """all_checks: list of (stage, check). Returns (decision, summary, reasons, actions)."""
    blocking = [(s, c) for s, c in all_checks if c["level"] in DECISION_OF]
    notes = [(s, c) for s, c in all_checks if c["level"] == "warn"]
    top = max((RANK[c["level"]] for _, c in blocking), default=0)
    decision = DECISION_OF[LEVELS[top]] if blocking else "APPROVE"
    blocking.sort(key=lambda sc: -RANK[sc[1]["level"]])
    reasons = [{"stage": s, **{k: c[k] for k in ("code", "level", "message", "action")}} for s, c in blocking + notes]
    actions = []
    for _, c in blocking:
        if c.get("action") and c["action"] not in actions:
            actions.append(c["action"])
    total = val(state["ex"], "totals", "total")
    if decision == "APPROVE":
        po = state.get("po")
        left = state.get("po_remaining_after")
        summary = f"All checks passed: {fmt_inr(total)} matched to {po['po_number']}" + (f", {fmt_inr(left)} left on the PO" if left is not None else "") + (f" ({len(notes)} note{'s' if len(notes) != 1 else ''})" if notes else "")
    else:
        lead = blocking[0][1]["message"]
        summary = lead + (f" (+{len(blocking) - 1} more issue{'s' if len(blocking) > 2 else ''})" if len(blocking) > 1 else "")
    return decision, summary, reasons, actions
