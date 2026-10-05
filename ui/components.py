"""Reusable display pieces: stage cards, the decision card, extracted-data tables, document preview."""
from __future__ import annotations
import io, json, pathlib, tempfile

import pandas as pd
import streamlit as st

from engine import pipeline
from engine.util import fmt_inr
from ui.theme import COLORS, EMOJI, LABELS, esc

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_DIR = pathlib.Path(tempfile.gettempdir()) / "invoice_desk_uploads"
ICON = {"pass": "✓", "warn": "!", "fail": "✕", "skipped": "–", "pending": "", "running": ""}
SOURCE_PILL = {"live": ('<span class="ap-pill blue">Live AI read</span>'), "cached": '<span class="ap-pill blue">Cached AI read</span>',
               "golden": '<span class="ap-pill red">OFFLINE TEST DATA (AI not used)</span>',
               "injected": '<span class="ap-pill">Pre-extracted data</span>'}


def fmt_ms(ms) -> str:
    if ms is None:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{int(ms)} ms"


# ---------------------------------------------------------------- stage card
def step_html(title, status, summary="", checks=None, duration_ms=None) -> str:
    checks = checks or []
    body = f'<div class="ap-title"><span>{esc(title)}</span><span class="ap-time">{esc(fmt_ms(duration_ms))}</span></div>'
    if status == "running":
        body += '<div class="ap-sum">Working…</div>'
    elif summary and status != "pending":
        body += f'<div class="ap-sum">{esc(summary)}</div>'
    if len(checks) == 1:
        c0 = checks[0]
        if c0.get("action") and c0["level"] in ("review", "hold", "reject"):
            body += f'<div class="ap-act">→ {esc(c0["action"])}</div>'
    elif checks:
        items = "".join(
            f'<div class="ap-check {esc(c["level"])}">{esc(c["message"])}'
            + (f'<div class="act">→ {esc(c["action"])}</div>' if c.get("action") and c["level"] in ("review", "hold", "reject") else "")
            + "</div>" for c in checks)
        opened = " open" if status in ("warn", "fail") else ""
        body += f'<details class="ap-checks"{opened}><summary>{len(checks)} checks</summary>{items}</details>'
    return f'<div class="ap-step {esc(status)}"><div class="ap-icon">{ICON.get(status, "")}</div><div class="ap-body">{body}</div></div>'


def paint_step(placeholder, title, status, summary="", checks=None, duration_ms=None) -> None:
    placeholder.markdown(step_html(title, status, summary, checks, duration_ms), unsafe_allow_html=True)


# ---------------------------------------------------------------- decision card
def effective_decision(run: dict, inv: dict | None) -> str:
    if run["status"] == "failed" or inv is None:
        return "FAILED"
    return inv.get("human_decision") or inv["decision"]


def hero_html(run: dict, inv: dict | None, steps: list | None = None) -> str:
    dec = effective_decision(run, inv)
    color = COLORS[dec]
    pills, sections = [], ""
    if dec == "FAILED":
        retry = run["error_code"] in pipeline.RETRYABLE
        summary = f'{run["error_code"]}: {run["error_message"]}'
        nxt = "Nothing was decided and nothing was saved. Use Retry." if retry else "Nothing was decided and nothing was saved. Fix the input and run again."
        sections = f'<div class="sec"><b>What to do next</b>{esc(nxt)}</div>'
    else:
        reasons = json.loads(inv["reasons_json"] or "[]")
        actions = json.loads(inv["actions_json"] or "[]")
        blocking = [r for r in reasons if r["level"] in ("review", "hold", "reject")]
        notes = [r for r in reasons if r["level"] == "warn"]
        summary = inv["summary"]
        if inv.get("human_decision"):
            summary = f'Reviewer {inv["human_decision"].lower()}d this invoice' + (f': {inv["human_note"]}' if inv.get("human_note") else "") + f'. The system had said {LABELS[inv["decision"]].lower()}.'
        elif len(blocking) == 1:
            summary = blocking[0]["message"]            # one reason: say it once, in the headline
        elif len(blocking) > 1:
            summary = f"Stopped for {len(blocking)} reasons"
            sections += '<div class="sec"><b>Why</b><ul>' + "".join(f"<li>{esc(r['message'])}</li>" for r in blocking[:6]) + "</ul></div>"
        if notes:
            sections += '<div class="sec"><b>Notes</b><ul>' + "".join(f"<li>{esc(r['message'])}</li>" for r in notes) + "</ul></div>"
        if actions and not inv.get("human_decision"):
            sections += '<div class="sec"><b>What to do next</b><ul>' + "".join(f"<li>{esc(a)}</li>" for a in actions) + "</ul></div>"
        elif dec == "APPROVE" and not inv.get("human_decision"):
            sections += '<div class="sec"><b>What to do next</b>Ready for payment scheduling.</div>'
        pills += [f'<span class="ap-pill">Invoice {esc(inv["invoice_number_raw"])}</span>', f'<span class="ap-pill">{esc(fmt_inr(inv["total_amount"]))}</span>']
        if inv["matched_po"]:
            how = {"inferred": " (inferred)", "explicit": ""}.get(inv["po_match_method"], "")
            pills.append(f'<span class="ap-pill">{esc(inv["matched_po"])}{how}</span>')
        if inv.get("human_decision"):
            pills.append('<span class="ap-pill blue">Reviewer decision</span>')
    if run.get("extraction_source"):
        pills.append(SOURCE_PILL.get(run["extraction_source"], ""))
    if steps:
        pills.append(f'<span class="ap-pill">{fmt_ms(sum(s["duration_ms"] or 0 for s in steps))} end to end</span>')
    return (f'<div class="ap-hero" style="border-left-color:{color}"><div class="lbl" style="color:{color}">{EMOJI[dec]} {LABELS[dec]}</div>'
            f'<div class="sum">{esc(summary)}</div>{sections}<div class="ap-meta">{"".join(pills)}</div></div>')


# ---------------------------------------------------------------- extracted data
def _row(label, node):
    v = node["value"]
    return {"Field": label, "Value": "—" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)),
            "Confidence": float(node["confidence"]), "Evidence": node.get("evidence") or ""}


def extraction_frames(ex: dict):
    f = [_row("Vendor", ex["vendor"]["name"]), _row("Vendor GSTIN", ex["vendor"]["gstin"]), _row("Vendor email", ex["vendor"]["email"]),
         _row("Buyer GSTIN", ex["buyer"]["gstin"]), _row("Invoice number", ex["invoice"]["number"]), _row("Invoice date", ex["invoice"]["date"]),
         _row("PO reference", ex["invoice"]["po_reference"]), _row("Sub total", ex["totals"]["subtotal"]), _row("Total", ex["totals"]["total"]),
         _row("Bank account", ex["bank"]["account_number"]), _row("IFSC", ex["bank"]["ifsc"])]
    fields = pd.DataFrame(f)
    lines = pd.DataFrame([{"#": i, "Description": l["description"], "HSN/SAC": l["hsn_sac"] or "", "Qty": "" if l["quantity"] is None else f'{l["quantity"]:g}',
                           "Rate": "" if l["unit_price"] is None else f'{l["unit_price"]:,.2f}', "Amount": f'{l["line_amount"]:,.2f}'}
                          for i, l in enumerate(ex["lines"], 1)])
    return fields, lines


def show_extraction(container, ex: dict) -> None:
    fields, lines = extraction_frames(ex)
    tax = ex["tax"]
    with container.container():
        extra = []
        if tax["treatment"] == "inclusive":
            extra.append("prices include tax")
        if ex["document"]["annotations"]:
            extra.append("stamps/notes seen: " + "; ".join(ex["document"]["annotations"]))
        if ex["bank"]["change_notice"]["detected"]:
            extra.append("invoice says bank details changed")
        st.markdown(f'<div class="ap-muted">Document type: <b>{esc(ex["document"]["document_type"].replace("_", " "))}</b> · quality: <b>{esc(ex["document"]["document_quality"].replace("_", " "))}</b>'
                    + (" · " + esc(" · ".join(extra)) if extra else "") + "</div>", unsafe_allow_html=True)
        st.dataframe(fields, hide_index=True, column_config={
            "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0.0, max_value=1.0, format="%.2f")})
        st.dataframe(lines, hide_index=True)


# ---------------------------------------------------------------- document preview
@st.cache_data(show_spinner=False)
def _render_pdf(data: bytes) -> bytes:
    import pypdfium2 as pdfium
    page = pdfium.PdfDocument(data)[0]
    buf = io.BytesIO()
    page.render(scale=1.6).to_pil().save(buf, format="PNG")
    return buf.getvalue()


def find_file(file_name: str):
    for base in (ROOT / "invoices", UPLOAD_DIR):
        p = base / file_name
        if p.exists():
            return p
    return None


def show_document(container, path) -> None:
    with container.container():
        if path is None:
            st.markdown('<div class="ap-muted">Original file not available.</div>', unsafe_allow_html=True)
            return
        data = pathlib.Path(path).read_bytes()
        try:
            st.image(_render_pdf(data) if str(path).lower().endswith(".pdf") else data)
        except Exception:
            st.markdown('<div class="ap-muted">Preview unavailable (install pypdfium2 to preview PDFs).</div>', unsafe_allow_html=True)
