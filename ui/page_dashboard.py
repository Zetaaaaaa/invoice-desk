"""Page 2: history, status and outputs across all runs, plus the human review queue."""
from __future__ import annotations
import json
from collections import Counter

import pandas as pd
import streamlit as st

from engine import db
from engine.util import fmt_inr
from ui import components as comp
from ui.theme import COLORS, EMOJI, LABELS, bar, esc, fmt_when, kpi

SOURCE = {"live": "AI (live)", "cached": "AI (cached)", "golden": "Offline test data", "injected": "Pre-extracted"}


def _eff(r):
    return "FAILED" if r["status"] == "failed" else (r["human_decision"] or r["decision"])


def _kpis(conn, runs):
    done = [r for r in runs if r["status"] == "completed"]
    c = Counter(_eff(r) for r in done)
    avg = conn.execute("SELECT AVG(t) FROM (SELECT SUM(duration_ms) t FROM run_steps WHERE stage!='human_review' GROUP BY run_id)").fetchone()[0]
    cols = st.columns(6)
    items = [(len(done), "Invoices processed", None), (c["APPROVE"], "Approved", COLORS["APPROVE"]), (c["NEEDS_REVIEW"], "Needs review", COLORS["NEEDS_REVIEW"]),
             (c["HOLD"], "On hold", COLORS["HOLD"]), (c["REJECT"], "Rejected", COLORS["REJECT"]), (f"{(avg or 0) / 1000:.1f}s", "Avg time per invoice", None)]
    for col, (n, label, color) in zip(cols, items):
        col.markdown(kpi(n, label, color), unsafe_allow_html=True)
    return done, c


def _po_table(conn):
    vend = {r["vendor_id"]: r["vendor_name"] for r in conn.execute("SELECT * FROM vendors")}
    rows = [{"PO": p["po_number"] + (" (closed)" if p["status"] != "open" else ""), "Vendor": vend.get(p["vendor_id"], p["vendor_id"]),
             "PO total": fmt_inr(p["total"]), "Invoiced": fmt_inr(p["invoiced"]), "Remaining": fmt_inr(p["remaining"]),
             "Used": min(1.0, p["invoiced"] / p["total"]) if p["total"] else 0.0} for p in db.po_status(conn)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
        "PO": st.column_config.TextColumn(width="small"), "Vendor": st.column_config.TextColumn(width="medium"),
        "PO total": st.column_config.TextColumn(width="small"), "Invoiced": st.column_config.TextColumn(width="small"),
        "Remaining": st.column_config.TextColumn(width="small"),
        "Used": st.column_config.ProgressColumn("Used", min_value=0.0, max_value=1.0, format="percent", width="small")})


def _review_queue(conn):
    rows = [dict(r) for r in conn.execute(
        "SELECT i.*, v.vendor_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id=i.vendor_id "
        "WHERE i.decision IN ('NEEDS_REVIEW','HOLD') AND i.human_decision IS NULL ORDER BY i.id")]
    st.markdown(f"### Review queue ({len(rows)})")
    if not rows:
        st.caption("Nothing waiting. Invoices that need a person's decision appear here.")
        return
    st.caption("A reviewer's decision is recorded in the audit trail. Approving consumes the PO balance.")
    for r in rows:
        title = f"{EMOJI[r['decision']]} Run {r['run_id']} · {r['invoice_number_raw']} · {r['vendor_name'] or 'Unknown vendor'} · {fmt_inr(r['total_amount'])} · {LABELS[r['decision']]}"
        with st.expander(title, expanded=len(rows) <= 2):
            reasons = [x for x in json.loads(r["reasons_json"]) if x["level"] in ("review", "hold", "reject")]
            st.markdown("**Why it stopped:**\n" + "\n".join(f"- {x['message']}" for x in reasons))
            acts = json.loads(r["actions_json"] or "[]")
            if acts:
                st.markdown("**Suggested next steps:**\n" + "\n".join(f"- {a}" for a in acts))
            po_choice = None
            if r["vendor_id"] and (r["po_match_method"] == "inferred" or not r["matched_po"]):
                opts = [p[0] for p in conn.execute("SELECT po_number FROM purchase_orders WHERE vendor_id=? AND status='open' ORDER BY po_number", (r["vendor_id"],))]
                if opts:
                    po_choice = st.selectbox("Confirm the PO", opts, index=opts.index(r["matched_po"]) if r["matched_po"] in opts else 0, key=f"po{r['id']}")
            note = st.text_input("Reviewer note (required)", key=f"note{r['id']}", placeholder="e.g. Confirmed with the requester by phone")
            c1, c2, _ = st.columns([1, 1, 2])
            approve_label = "Override and approve" if r["decision"] == "HOLD" else "Approve"
            if c1.button(approve_label, key=f"ap{r['id']}", type="primary"):
                if len(note.strip()) < 3:
                    st.error("Please add a short note explaining the decision.")
                else:
                    db.apply_human_decision(conn, r["id"], "APPROVE", note.strip(), po_choice)
                    st.rerun()
            if c2.button("Reject", key=f"rj{r['id']}"):
                if len(note.strip()) < 3:
                    st.error("Please add a short note explaining the decision.")
                else:
                    db.apply_human_decision(conn, r["id"], "REJECT", note.strip())
                    st.rerun()


def _history(conn, runs):
    st.markdown("### Run history")
    rows = []
    for r in runs:
        eff = _eff(r)
        rows.append({"Run": r["run_id"], "When": fmt_when(r["started_at"]).split(", ")[-1],
                     "Status": f"{EMOJI[eff]} {LABELS[eff]}" + (" (by reviewer)" if r["human_decision"] else ""),
                     "Vendor": r["vendor_name"] or "—", "Invoice #": r["invoice_number_raw"] or "—",
                     "Amount": fmt_inr(r["total_amount"]) if r["total_amount"] is not None else "—",
                     "PO": r["matched_po"] or "—", "File": r["file_name"], "Read by": SOURCE.get(r["extraction_source"], "—")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={"Run": st.column_config.NumberColumn(width="small"), "When": st.column_config.TextColumn(width="small")})

    st.markdown("#### Open a run")
    ids = [r["run_id"] for r in runs]
    pick = st.selectbox("Run", ids, format_func=lambda i: next(f"Run {r['run_id']} · {r['file_name']} · {LABELS[_eff(r)]}" for r in runs if r["run_id"] == i),
                        label_visibility="collapsed")
    run = db.get_run(conn, pick)
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (run["invoice_id"],)).fetchone() if run["invoice_id"] else None
    inv = dict(inv) if inv else None
    st.markdown(comp.hero_html(run, inv, run["steps"]), unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs(["Steps", "The invoice", "What the AI read", "Raw data"])
    with t1:
        for s in run["steps"]:
            st.markdown(comp.step_html(s["title"], s["status"], s["summary"], s["checks"], s["duration_ms"]), unsafe_allow_html=True)
    with t2:
        comp.show_document(st.empty(), comp.find_file(run["file_name"]))
    with t3:
        if inv:
            comp.show_extraction(st.empty(), json.loads(inv["extraction_json"]))
        else:
            st.caption("Nothing was read for this run.")
    with t4:
        st.json({"run": {k: v for k, v in run.items() if k != "steps"}, "invoice": {k: v for k, v in (inv or {}).items() if k != "extraction_json"}})


def _do_reset(conn, fresh):
    """Runs as a button callback, before the page reruns, so it may safely change widget state."""
    db.reset_demo(conn, clear_extraction_cache=fresh)
    for k in ("last_run_id", "_advance", "rehearsal"):
        st.session_state.pop(k, None)
    st.session_state["_reseed_case"] = True
    st.session_state["wipe_ok"] = False


def render(conn, settings):
    st.markdown("## Dashboard")
    runs = db.list_runs(conn)
    if not runs:
        st.info("No runs yet. Process an invoice and it will appear here.")
    else:
        done, c = _kpis(conn, runs)
        st.write("")
        left, right = st.columns([0.8, 2.2])
        with left:
            st.markdown("### Outcomes")
            total = sum(c[k] for k in ("APPROVE", "NEEDS_REVIEW", "HOLD", "REJECT"))
            st.markdown("".join(bar(LABELS[k], c[k], total, COLORS[k]) for k in ("APPROVE", "NEEDS_REVIEW", "HOLD", "REJECT")), unsafe_allow_html=True)
            ok_val = sum(r["total_amount"] or 0 for r in done if _eff(r) == "APPROVE")
            stop_val = sum(r["total_amount"] or 0 for r in done if _eff(r) in ("HOLD", "REJECT", "NEEDS_REVIEW"))
            st.markdown(f'<div class="ap-muted">Approved value <b>{esc(fmt_inr(ok_val))}</b> · stopped or waiting <b>{esc(fmt_inr(stop_val))}</b></div>', unsafe_allow_html=True)
        with right:
            st.markdown("### Purchase order balances")
            _po_table(conn)
        st.divider()
        _review_queue(conn)
        st.divider()
        _history(conn, runs)
    st.divider()
    with st.expander("Danger zone"):
        st.caption("Wipes all history and PO consumption and reloads the master data. Use this before each rehearsal.")
        sure = st.checkbox("Yes, wipe everything", key="wipe_ok")
        st.button("Reset demo data", disabled=not sure, on_click=_do_reset, args=(conn, settings["fresh"]))
