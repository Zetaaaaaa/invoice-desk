"""
Runs one invoice through the stages, writing every step to the database as it happens.

    for event in process_iter(conn, "invoices/01_happy_sahyadri_steel.pdf"):
        ...   # event["type"] in: run_start, step_start, step_end, done

The Streamlit live-run view consumes this generator and redraws on every event.
`process()` just runs it to the end and returns the final 'done' event.
"""
from __future__ import annotations
import hashlib, json, os, time, traceback

from . import db as D
from . import rules as R
from .util import fmt_inr, normalize_invoice_number
from extraction.extract import ExtractionError, extract_invoice

STAGES = [("intake", "Receive file"), ("extract", "Read invoice (AI)"), ("validate", "Validate document"),
          ("vendor", "Identify vendor"), ("verify", "Verify payment & compliance"),
          ("duplicates", "Check for duplicates"), ("po_match", "Match purchase order"),
          ("lines", "Check line items"), ("decision", "Decide")]
RETRYABLE = {"RATE_LIMIT", "API_ERROR", "BAD_JSON", "SCHEMA_INVALID"}
STATUS_OF_LEVEL = {"pass": "pass", "info": "pass", "warn": "warn", "review": "warn", "hold": "fail", "reject": "fail"}


def _stage_intake(path, file_name):
    if path is None:
        return {"checks": [], "data": {"file_name": file_name, "source": "pre-extracted data"}, "summary": f"{file_name} (pre-extracted data)"}
    if not os.path.exists(path):
        raise ExtractionError("FILE_NOT_FOUND", f"File not found: {path}")
    raw = open(path, "rb").read()
    data = {"file_name": file_name, "size_kb": round(len(raw) / 1024, 1), "sha256": hashlib.sha256(raw).hexdigest()[:12]}
    return {"checks": [], "data": data, "summary": f"{file_name}, {data['size_kb']} KB, fingerprint {data['sha256']}"}


def _stage_extract(state, path, extraction, extraction_meta, mode, use_cache):
    if extraction is not None:
        ex, meta = extraction, extraction_meta or {"source": "injected", "cached": False}
    else:
        r = extract_invoice(path, use_cache=use_cache, mode=mode)
        ex, meta = r["data"], r["meta"]
    state["ex"], state["extraction_meta"] = ex, meta
    src = "cached" if meta.get("cached") else meta.get("source", "live")
    state["source"] = src
    total = R.val(ex, "totals", "total")
    how = {"live": f"{meta.get('model')} in {meta.get('latency_ms', 0) / 1000:.1f}s", "cached": "cached result",
           "golden": "OFFLINE TEST DATA (not read by the model)", "injected": "pre-extracted data"}.get(src, src)
    summary = f"Read {len(ex['lines'])} line item{'s' if len(ex['lines']) != 1 else ''}, total {fmt_inr(total)}, via {how}"
    preview = {"vendor": R.val(ex, "vendor", "name"), "invoice_number": R.val(ex, "invoice", "number"),
               "invoice_date": R.val(ex, "invoice", "date"), "po_reference": R.val(ex, "invoice", "po_reference"),
               "total": total, "tax_treatment": ex["tax"]["treatment"], "document_quality": ex["document"]["document_quality"]}
    return {"checks": [], "data": {"meta": meta, "source": src, "preview": preview, "extraction": ex}, "summary": summary}


def _stage_decide(conn, state, run_id, file_name, all_checks):
    ex, v, po = state["ex"], state.get("vendor"), state.get("po")
    decision, summary, reasons, actions = R.decide(state, all_checks)
    number = R.val(ex, "invoice", "number")
    cur = conn.execute(
        "INSERT INTO invoices(run_id,file_name,vendor_id,invoice_number_raw,invoice_number_norm,invoice_date,total_amount,"
        "matched_po,po_match_method,po_match_score,decision,summary,reasons_json,actions_json,extraction_json,lines_sig,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, file_name, v and v["vendor_id"], number, normalize_invoice_number(number), R.val(ex, "invoice", "date"),
         R.val(ex, "totals", "total") or 0, po and po["po_number"], state.get("po_method"), state.get("po_score"), decision,
         summary, json.dumps(reasons), json.dumps(actions), json.dumps(ex), R._lines_sig(ex), D.now()))
    inv_id = cur.lastrowid
    matches = {m["line_no"]: m["po_line_no"] for m in state.get("line_matches", [])}
    for i, l in enumerate(ex["lines"], 1):
        conn.execute("INSERT INTO invoice_lines VALUES(?,?,?,?,?,?,?,?,?)",
                     (inv_id, i, l["description"], l["hsn_sac"], l["quantity"], l["uom"], l["unit_price"], l["line_amount"], matches.get(i)))
    conn.commit()
    state["invoice_id"] = inv_id
    status = {"APPROVE": "pass", "NEEDS_REVIEW": "warn"}.get(decision, "fail")
    return {"checks": [], "data": {"decision": decision, "summary": summary, "reasons": reasons, "actions": actions, "invoice_id": inv_id},
            "summary": f"{decision}: {summary}", "status": status, "decision": decision, "final_summary": summary}


def process_iter(conn, path=None, *, file_name=None, extraction=None, extraction_meta=None, rules=None, mode=None, use_cache=True):
    rules = rules or R.load_rules()
    file_name = file_name or (os.path.basename(path) if path else "pre-extracted.json")
    run_id = D.create_run(conn, file_name)
    state, all_checks, failure = {"halt": None, "ex": None}, [], None
    yield {"type": "run_start", "run_id": run_id, "file_name": file_name, "stages": STAGES}
    result = {}

    for seq, (key, title) in enumerate(STAGES, 1):
        skip_reason = None
        if failure:
            skip_reason = "Skipped: an earlier step failed"
        elif state["halt"] and key != "decision":
            skip_reason = f"Skipped: {state['halt']}"
        if skip_reason:
            t = D.now()
            D.add_step(conn, run_id, seq, key, title, "skipped", skip_reason, [], {}, t, t, 0)
            yield {"type": "step_end", "run_id": run_id, "seq": seq, "stage": key, "title": title, "status": "skipped",
                   "summary": skip_reason, "checks": [], "data": {}, "duration_ms": 0}
            continue

        yield {"type": "step_start", "run_id": run_id, "seq": seq, "stage": key, "title": title}
        started, t0 = D.now(), time.time()
        try:
            if key == "intake":
                out = _stage_intake(path, file_name)
            elif key == "extract":
                out = _stage_extract(state, path, extraction, extraction_meta, mode, use_cache)
            elif key == "decision":
                out = _stage_decide(conn, state, run_id, file_name, all_checks)
                result = out
            else:
                out = R.STAGE_FUNCS[key](conn, state, rules)
                all_checks += [(key, c) for c in out["checks"]]
            status = out.get("status") or STATUS_OF_LEVEL[R.worst(out["checks"])]
        except ExtractionError as e:
            failure = {"code": e.code, "message": e.message}
            out, status = {"checks": [], "data": {"error_code": e.code, "retryable": e.code in RETRYABLE}, "summary": f"{e.code}: {e.message}"}, "fail"
        except Exception as e:  # a bug in a rule must never crash the run view
            failure = {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"}
            out, status = {"checks": [], "data": {"traceback": traceback.format_exc(limit=4)}, "summary": f"INTERNAL_ERROR: {type(e).__name__}: {e}"}, "fail"

        ms = int((time.time() - t0) * 1000)
        D.add_step(conn, run_id, seq, key, title, status, out["summary"], out["checks"],
                   {k: v for k, v in out["data"].items() if k != "extraction"} if key == "extract" else out["data"],
                   started, D.now(), ms)
        yield {"type": "step_end", "run_id": run_id, "seq": seq, "stage": key, "title": title, "status": status,
               "summary": out["summary"], "checks": out["checks"], "data": out["data"], "duration_ms": ms}

    if failure:
        D.finish_run(conn, run_id, "failed", None, f"{failure['code']}: {failure['message']}", state.get("source"), failure["code"], failure["message"])
        yield {"type": "done", "run_id": run_id, "status": "failed", "decision": None, "summary": failure["message"],
               "error_code": failure["code"], "retryable": failure["code"] in RETRYABLE, "invoice_id": None}
    else:
        D.finish_run(conn, run_id, "completed", result["decision"], result["final_summary"], state.get("source"), invoice_id=state["invoice_id"])
        yield {"type": "done", "run_id": run_id, "status": "completed", "decision": result["decision"], "summary": result["final_summary"],
               "invoice_id": state["invoice_id"], "source": state.get("source"), "error_code": None, "retryable": False}


def process(conn, path=None, **kw) -> dict:
    last = None
    for ev in process_iter(conn, path, **kw):
        last = ev
    return last
