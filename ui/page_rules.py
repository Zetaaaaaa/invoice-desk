"""Page 3: the rules in force (adjustable for 'what if?' questions) and the assumptions behind them."""
from __future__ import annotations
import copy

import streamlit as st

from engine import rules as R

# label, path into rules.json, widget kwargs
FIELDS = [
    ("Price tolerance (% of remaining PO balance)", ("matching", "price_tolerance_pct"), dict(min_value=0.0, max_value=25.0, step=0.5)),
    ("Tolerance cap (₹). The lower of the % and the cap applies", ("matching", "price_tolerance_max_abs"), dict(min_value=0, max_value=1000000, step=1000)),
    ("Duplicate look-back (days)", ("duplicate_detection", "lookback_days"), dict(min_value=1, max_value=365, step=1)),
    ("Probable-duplicate amount match (%)", ("duplicate_detection", "amount_match_pct"), dict(min_value=0.0, max_value=5.0, step=0.1)),
    ("Minimum AI confidence per key field", ("extraction", "min_field_confidence"), dict(min_value=0.5, max_value=1.0, step=0.01)),
    ("PO inference: minimum score to suggest a PO", ("po_inference", "min_score"), dict(min_value=0.3, max_value=1.0, step=0.05)),
    ("Line price tolerance (%)", ("line_matching", "price_tolerance_pct"), dict(min_value=0.0, max_value=25.0, step=0.5)),
]


def _get(d, path):
    for p in path:
        d = d[p]
    return d


def effective_rules() -> dict:
    """Default rules.json plus any overrides made on this page (kept in session state)."""
    rules = R.load_rules()
    for path, value in st.session_state.get("rules_override", {}).items():
        node = rules
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
    return rules


def _reset():
    st.session_state["rules_override"] = {}
    for i in range(len(FIELDS)):
        st.session_state.pop(f"rule_{i}", None)


def render(conn, settings):
    st.markdown("## Rules & assumptions")
    st.caption("Every threshold lives in data/rules.json. Change one here to answer 'what if tolerance were 5%?' live. Changes last for this browser session only.")
    base = R.load_rules()
    left, right = st.columns([1, 1.15])
    with left:
        st.markdown("### Adjust thresholds")
        override = {}
        for i, (label, path, kw) in enumerate(FIELDS):
            default = _get(base, path)
            current = st.session_state.get("rules_override", {}).get(path, default)
            is_int = isinstance(default, int) and not isinstance(default, bool)
            val = st.number_input(label, value=type(default)(current), key=f"rule_{i}", **({k: (int(v) if is_int else float(v)) for k, v in kw.items()}))
            if val != default:
                override[path] = val
        st.session_state["rules_override"] = override
        if override:
            st.markdown(f'<div class="ap-banner amber"><b>{len(override)} rule(s) changed</b> from the defaults. Re-run an invoice to see the effect.</div>', unsafe_allow_html=True)
        st.button("Reset to defaults", on_click=_reset)
        with st.expander("Rules currently in force (JSON)"):
            st.json(effective_rules())
    with right:
        st.markdown("### How a decision is made")
        st.markdown(
            "Every check raises a level. **The decision is the most severe level raised.** Notes never change it.\n\n"
            "| Decision | When | Examples |\n|---|---|---|\n"
            "| 🔴 **Rejected** | The invoice must not be paid | Duplicate, blocked or unknown vendor, lookalike vendor |\n"
            "| 🟠 **On hold** | A rule failed but the invoice may be valid | Over the PO balance, quantity or price variance, bank account changed, wrong tax type, PO closed or missing, arithmetic wrong |\n"
            "| 🟡 **Needs review** | The process is uncertain | Inferred PO, low AI confidence, not a tax invoice, lookalike email, probable duplicate |\n"
            "| 🟢 **Approved** | Every check passed | Notes allowed, e.g. 'within tolerance' |")
        st.markdown("### Principles")
        st.markdown(
            "- **The AI reads, the rules decide.** The model only transcribes the invoice, with a confidence for each field. Arithmetic, matching and every decision are plain code, so each outcome is repeatable and explainable.\n"
            "- **Precise, not paranoid.** A duplicate needs the same vendor and the same normalised number. Same amount alone is not enough, because split POs legitimately repeat amounts.\n"
            "- **GSTIN beats the name.** A similar name with an unknown GSTIN is treated as impersonation.\n"
            "- **No guessing.** An inferred PO is never auto-approved, and two equally good POs go to a person.")
        st.markdown("### Assumptions")
        st.markdown(
            "- One buyer entity (Kestrel Components), INR only, GST at 18%.\n"
            "- Tolerance applies to the PO's **remaining** balance. Only approved invoices consume balance and quantity.\n"
            "- Two-way matching (invoice vs PO). There is no goods-receipt data, so three-way matching is the natural next step.\n"
            "- Header-level amounts drive the decision. Line-level differences add reasons.\n"
            "- Invoice data is synthetic. The free Gemini tier may use inputs to improve Google's models, so real company documents should not be uploaded.")
