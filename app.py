"""Invoice Desk: AP invoice automation demo (Kestrel Components). Run with:  streamlit run app.py"""
from __future__ import annotations
import os, pathlib, re, tempfile, time, uuid

import streamlit as st

from engine import db
from ui import page_dashboard, page_rules, page_run, theme

st.set_page_config(page_title="Invoice Desk", page_icon="🧾", layout="wide")
theme.inject()



def _db_path():
    """Each visitor gets a private SQLite file, tied to ?s=<token> in the URL so a refresh keeps it.
    Set DB_MODE=shared to use one common database instead (single-user local use)."""
    if os.environ.get("DB_MODE", "per_visitor") == "shared":
        return db.DEFAULT_DB, False
    token = st.query_params.get("s", "")
    if isinstance(token, list):
        token = token[0] if token else ""
    if not re.fullmatch(r"[0-9a-f]{12}", str(token)):
        token = uuid.uuid4().hex[:12]
        st.query_params["s"] = token
    folder = pathlib.Path(tempfile.gettempdir()) / "invoice_desk"
    folder.mkdir(exist_ok=True)
    for f in folder.glob("*.db"):                       # tidy up sandboxes idle for a day
        try:
            if time.time() - f.stat().st_mtime > 86400:
                f.unlink()
        except OSError:
            pass
    return str(folder / f"{token}.db"), True


db_path, private = _db_path()
conn = db.connect(db_path)
if conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0:
    db.seed_master(conn)

PAGES = ["Process invoice", "Dashboard", "Rules & assumptions"]
with st.sidebar:
    st.markdown('<div class="ap-brand">Invoice <span>Desk</span></div><div class="ap-muted">Kestrel Components · accounts payable</div>', unsafe_allow_html=True)
    st.write("")
    page = st.radio("Go to", PAGES, label_visibility="collapsed")
    st.divider()
    st.markdown("**Settings**")
    offline = st.radio("Invoice reading", ["Live AI (Gemini)", "Offline test data"], help="Offline uses hand-written answers for the 9 test invoices. For rehearsal only.") == "Offline test data"
    fresh = st.checkbox("Fresh AI read every time", help="By default the test invoices reuse a saved real Gemini result (fast, and kind to the free quota). Tick this to call the model live every time.")
    pace = st.slider("Demo pacing (seconds per step)", 0.0, 1.5, 0.4, 0.1, help="Only slows the on-screen display so each step is visible. It does not change any result.")
    if private:
        st.markdown('<div class="ap-muted">🔒 Your own sandbox: other visitors cannot see or change your runs.</div>', unsafe_allow_html=True)
    if offline:
        st.markdown('<div class="ap-banner red"><b>Offline test data.</b> The AI is not being used.</div>', unsafe_allow_html=True)
    elif os.environ.get("GEMINI_API_KEY"):
        st.markdown('<div class="ap-muted">✓ Gemini API key found</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="ap-banner amber"><b>No GEMINI_API_KEY set.</b> Live reading will fail unless a saved result exists.</div>', unsafe_allow_html=True)

rules = page_rules.effective_rules()
settings = {"offline": offline, "fresh": fresh, "pace": pace, "rules": rules,
            "rules_changed": bool(st.session_state.get("rules_override"))}

{"Process invoice": page_run, "Dashboard": page_dashboard, "Rules & assumptions": page_rules}[page].render(conn, settings)
