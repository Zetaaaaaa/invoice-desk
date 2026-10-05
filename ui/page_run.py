"""Page 1: process an invoice, with a live view of every stage as it runs."""
from __future__ import annotations
import csv, pathlib, time

import pandas as pd
import streamlit as st

from engine import db, pipeline
from ui import components as comp
from ui.theme import banner, esc

ROOT = pathlib.Path(__file__).resolve().parent.parent

CASES = [
    ("01_happy_sahyadri_steel.pdf", "Happy path: exact match", "A clean PDF that cites its PO, with amounts matching to the rupee."),
    ("02_happy_tolerance_meridian.pdf", "Happy path: small overrun within tolerance", "Slightly over the PO (1.44%), plus a courier line that is not on the PO. Approved with notes."),
    ("03_split_brightline_part1.pdf", "Split PO (1 of 3)", "One PO is billed across three invoices. The vendor writes the PO as '2026-0102', so number matching must be tolerant."),
    ("04_split_brightline_part2.pdf", "Split PO (2 of 3)", "Same amount as part 1. A naive duplicate rule would reject it; this one should not."),
    ("05_split_brightline_part3.pdf", "Split PO (3 of 3): exceeds the PO", "Pushes the running total over the PO and bills 21 trips against 20 ordered."),
    ("06_dup_deccan_original.pdf", "Duplicate: the original", "A normal invoice that should be approved."),
    ("07_dup_deccan_resubmit.pdf", "Duplicate: re-sent as '42' in a new layout", "Same bill, different number format, date and layout. An exact-match check would miss it."),
    ("08_scanned_orbit_no_po.pdf", "Scanned, no PO number, tax embedded", "An image-only scan with a stamp and handwriting. No PO is cited and prices include tax."),
    ("09_bank_change_sahyadri.pdf", "Bank details changed", "Everything matches the PO, but the bank account and email domain differ from the vendor master."),
]
ORDER_NOTE = "Order matters: the process remembers history. Run 03 → 04 → 05 and 06 → 07 in sequence."


def _set_retry():
    st.session_state["do_retry"] = True


def _runs_done(conn) -> set:
    return {r[0] for r in conn.execute("SELECT DISTINCT file_name FROM runs WHERE status='completed'")}


def _save_upload(up) -> dict:
    comp.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = pathlib.Path(up.name).name or "upload.pdf"      # never trust a path from the browser
    p = comp.UPLOAD_DIR / name
    p.write_bytes(up.getvalue())
    return {"path": str(p), "name": name}


def _paint_from_db(conn, run_id, ph, doc_ph, data_ph, hero_ph):
    run = db.get_run(conn, run_id)
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (run["invoice_id"],)).fetchone() if run["invoice_id"] else None
    inv = dict(inv) if inv else None
    for s in run["steps"]:
        comp.paint_step(ph[s["stage"]], s["title"], s["status"], s["summary"], s["checks"], s["duration_ms"])
    comp.show_document(doc_ph, comp.find_file(run["file_name"]))
    if inv:
        import json
        comp.show_extraction(data_ph, json.loads(inv["extraction_json"]))
    else:
        data_ph.markdown('<div class="ap-muted">Nothing was read for this run.</div>', unsafe_allow_html=True)
    hero_ph.markdown(comp.hero_html(run, inv, run["steps"]), unsafe_allow_html=True)
    return run


def _stream(conn, chosen, settings, ph, doc_ph, data_ph, hero_ph):
    comp.show_document(doc_ph, chosen["path"])
    data_ph.markdown('<div class="ap-muted">Waiting for the invoice to be read…</div>', unsafe_allow_html=True)
    hero_ph.markdown('<div class="ap-banner blue">Running…</div>', unsafe_allow_html=True)
    final = None
    for ev in pipeline.process_iter(conn, chosen["path"], file_name=chosen["name"], rules=settings["rules"],
                                    mode="golden" if settings["offline"] else None, use_cache=not settings["fresh"]):
        if ev["type"] == "step_start":
            comp.paint_step(ph[ev["stage"]], ev["title"], "running")
        elif ev["type"] == "step_end":
            comp.paint_step(ph[ev["stage"]], ev["title"], ev["status"], ev["summary"], ev["checks"], ev["duration_ms"])
            if ev["stage"] == "extract" and ev["status"] == "pass":
                comp.show_extraction(data_ph, ev["data"]["extraction"])
            elif ev["stage"] == "extract":
                data_ph.markdown('<div class="ap-muted">The invoice could not be read.</div>', unsafe_allow_html=True)
            if ev["stage"] != "extract" and ev["status"] != "skipped":
                time.sleep(settings["pace"])
        elif ev["type"] == "done":
            final = ev
    run = db.get_run(conn, final["run_id"])
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (run["invoice_id"],)).fetchone() if run["invoice_id"] else None
    hero_ph.markdown(comp.hero_html(run, dict(inv) if inv else None, run["steps"]), unsafe_allow_html=True)
    st.session_state["last_run_id"] = final["run_id"]
    if final["status"] == "completed":
        done_now = _runs_done(conn)
        nxt = next((c[0] for c in CASES if c[0] not in done_now), None)
        if nxt and st.session_state.get("src") == "Test invoice":
            st.session_state["_advance"] = nxt   # preselect the next invoice in the demo order
        st.rerun()                                # repaint from the database so the page is in its normal resting state
    return final


def render(conn, settings):
    st.markdown("## Process an invoice")
    st.caption("Pick a test invoice or upload your own. Every stage appears below as it runs, with the reason behind each result.")

    if settings["offline"]:
        banner("<b>Offline test data is ON.</b> The AI is not reading these files; hand-written answers are used instead. Switch it off in the sidebar before any real demo.", "red")
    if settings["rules_changed"]:
        banner("<b>Custom rules are active</b> (changed on the Rules page). Results may differ from the defaults.", "amber")

    done = _runs_done(conn)
    order = [c[0] for c in CASES]
    reseed = st.session_state.pop("_reseed_case", False)      # consume the flag unconditionally (a short-circuit would leave it set)
    if "case" not in st.session_state or reseed:
        st.session_state["case"] = next((f for f in order if f not in done), order[0])
    advance = st.session_state.pop("_advance", None)
    if advance:
        st.session_state["case"] = advance
    src = st.radio("Source", ["Test invoice", "Upload your own"], horizontal=True, label_visibility="collapsed", key="src")
    chosen, blocked = None, False
    if src == "Test invoice":
        strip = "".join(
            f'<span class="ap-pill" style="{"background:#dcfce7;border-color:#bbf7d0;color:#166534" if c[0] in done else ""}">{"✓ " if c[0] in done else ""}{i}</span>'
            for i, c in enumerate(CASES, 1))
        st.markdown(f'<div class="ap-muted" style="margin-bottom:4px">Demo progress &nbsp;{strip}</div>', unsafe_allow_html=True)
        labels = {c[0]: f"{i}. {c[1]}" for i, c in enumerate(CASES, 1)}   # constant labels: Streamlit resets a dropdown whose labels change
        file = st.selectbox("Invoice", order, format_func=lambda f: labels[f], label_visibility="collapsed", key="case")
        note = esc(dict((c[0], c[2]) for c in CASES)[file])
        if file in done:
            note += ('<br><b style="color:#92400e">Already processed in this session.</b> Running it again will be caught as a duplicate. '
                     'Use Reset demo data on the Dashboard to start clean.')
        st.markdown(f'<div class="ap-case">{note}</div>', unsafe_allow_html=True)
        chosen = {"path": str(ROOT / "invoices" / file), "name": file}
    else:
        up = st.file_uploader("Upload an invoice", type=["pdf", "jpg", "jpeg", "png"], label_visibility="collapsed")
        if up is not None:
            chosen = _save_upload(up)
        if settings["offline"]:
            st.warning("Offline test data only works for the 9 test invoices. Switch to live AI reading to upload your own.")
            blocked = True
    run_clicked = st.button("▶  Run", type="primary", disabled=(chosen is None or blocked))

    retry = st.session_state.pop("do_retry", False)
    if retry and st.session_state.get("last_input"):
        chosen = st.session_state["last_input"]
    trigger = (run_clicked or retry) and chosen is not None and not blocked
    if trigger:
        st.session_state["last_input"] = chosen

    hero_ph = st.empty()
    retry_slot = st.container()
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("#### Live run")
        ph = {k: st.empty() for k, _ in pipeline.STAGES}
    with right:
        st.markdown("#### The invoice")
        t_doc, t_data = st.tabs(["Document", "What the AI read"])
        doc_ph, data_ph = t_doc.empty(), t_data.empty()

    for k, title in pipeline.STAGES:
        comp.paint_step(ph[k], title, "pending")
    data_ph.markdown('<div class="ap-muted">Extracted fields appear here after the AI reads the invoice.</div>', unsafe_allow_html=True)

    final = None
    if trigger:
        final = _stream(conn, chosen, settings, ph, doc_ph, data_ph, hero_ph)
    else:
        last = st.session_state.get("last_run_id")
        if last and conn.execute("SELECT 1 FROM runs WHERE id=?", (last,)).fetchone():
            run = _paint_from_db(conn, last, ph, doc_ph, data_ph, hero_ph)
            if run["status"] == "failed":
                final = {"status": "failed", "retryable": run["error_code"] in pipeline.RETRYABLE}
        else:
            hero_ph.markdown('<div class="ap-muted">Choose an invoice above and press Run.</div>', unsafe_allow_html=True)
    if final and final.get("status") == "failed" and final.get("retryable"):
        with retry_slot:
            st.button("↻  Retry", on_click=_set_retry, key="retry_btn")

    st.divider()
    stored = st.session_state.get("rehearsal")
    with st.expander("Rehearsal tools", expanded=bool(stored)):
        st.markdown(ORDER_NOTE)
        st.caption("Resets all history and balances, then runs the nine test invoices in order and compares each result with the answer key. Uses the settings in the sidebar.")
        if st.button("Reset data, then run all 9 in order"):
            db.reset_demo(conn, clear_extraction_cache=settings["fresh"])
            expected = list(csv.DictReader(open(ROOT / "data" / "expected_results.csv")))
            bar, rows = st.progress(0.0), []
            for i, r in enumerate(expected, 1):
                res = pipeline.process(conn, str(ROOT / "invoices" / r["file"]), rules=settings["rules"],
                                       mode="golden" if settings["offline"] else None, use_cache=not settings["fresh"])
                want_po = None if r["matched_po"] in ("-", "") else r["matched_po"].split(" ")[0]
                po = conn.execute("SELECT matched_po FROM invoices WHERE id=?", (res["invoice_id"],)).fetchone() if res.get("invoice_id") else None
                ok = res.get("decision") == r["expected_decision"] and (po[0] if po else None) == want_po
                rows.append({"Invoice": r["file"], "Result": res.get("decision") or f"FAILED ({res.get('error_code')})",
                             "Expected": r["expected_decision"], "Match": "✓" if ok else "✗"})
                bar.progress(i / len(expected))
            st.session_state["rehearsal"] = {"rows": rows, "good": sum(1 for r in rows if r["Match"] == "✓")}
            st.session_state.pop("last_run_id", None)
            st.session_state["_reseed_case"] = True    # next load: preselect the first un-run invoice
            st.rerun()
        if stored:
            (st.success if stored["good"] == len(stored["rows"]) else st.error)(f"{stored['good']}/{len(stored['rows'])} match the answer key")
            st.dataframe(pd.DataFrame(stored["rows"]), hide_index=True)
