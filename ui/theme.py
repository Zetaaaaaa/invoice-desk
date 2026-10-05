"""Look and feel: colours, CSS, and tiny HTML helpers. HTML strings are deliberately single-line
(no indentation) because Streamlit's markdown treats indented lines as code blocks."""
from __future__ import annotations
import html
from datetime import datetime

import streamlit as st

COLORS = {"APPROVE": "#16a34a", "NEEDS_REVIEW": "#d97706", "HOLD": "#ea580c", "REJECT": "#dc2626", "FAILED": "#475569"}
LABELS = {"APPROVE": "Approved", "NEEDS_REVIEW": "Needs review", "HOLD": "On hold", "REJECT": "Rejected", "FAILED": "Run failed"}
EMOJI = {"APPROVE": "🟢", "NEEDS_REVIEW": "🟡", "HOLD": "🟠", "REJECT": "🔴", "FAILED": "⚫"}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fmt_when(iso) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b, %H:%M:%S")
    except Exception:
        return iso or ""


CSS = """
<style>
:root{--green:#16a34a;--amber:#d97706;--orange:#ea580c;--red:#dc2626;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--soft:#f8fafc}
.block-container{padding-top:2.4rem;max-width:1280px}
h1,h2,h3,h4{letter-spacing:-.01em}
section[data-testid="stSidebar"]{border-right:1px solid var(--line)}
.ap-brand{font-size:1.25rem;font-weight:700;letter-spacing:-.02em;color:var(--ink)}
.ap-brand span{color:#2563eb}
.ap-step{display:flex;gap:12px;padding:11px 14px;border:1px solid var(--line);border-radius:12px;margin-bottom:8px;background:#fff}
.ap-icon{flex:0 0 26px;height:26px;width:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#fff;background:#cbd5e1;box-sizing:border-box}
.ap-step.pass .ap-icon{background:var(--green)}
.ap-step.warn .ap-icon{background:var(--amber)}
.ap-step.fail .ap-icon{background:var(--red)}
.ap-step.skipped,.ap-step.pending{opacity:.5}
.ap-step.running{border-color:#93c5fd;background:#f0f7ff}
.ap-step.running .ap-icon{background:transparent;border:3px solid #bfdbfe;border-top-color:#2563eb;animation:ap-spin .8s linear infinite}
@keyframes ap-spin{to{transform:rotate(360deg)}}
.ap-body{flex:1;min-width:0}
.ap-title{font-weight:600;color:var(--ink);font-size:.95rem;display:flex;justify-content:space-between;gap:8px}
.ap-time{font-weight:400;color:var(--muted);font-size:.78rem;white-space:nowrap}
.ap-sum{color:#334155;font-size:.86rem;margin-top:2px;line-height:1.38}
details.ap-checks{margin-top:6px}
details.ap-checks summary{cursor:pointer;color:var(--muted);font-size:.78rem}
.ap-check{font-size:.82rem;color:#334155;padding:3px 0 3px 16px;position:relative;line-height:1.38}
.ap-check:before{content:"";position:absolute;left:3px;top:.62em;height:7px;width:7px;border-radius:50%;background:#cbd5e1}
.ap-check.pass:before{background:var(--green)}
.ap-check.warn:before,.ap-check.review:before{background:var(--amber)}
.ap-check.hold:before{background:var(--orange)}
.ap-check.reject:before{background:var(--red)}
.ap-check .act{color:#0f766e;font-size:.78rem}
.ap-act{color:#0f766e;font-size:.8rem;margin-top:3px}
.ap-hero{border:1px solid var(--line);border-left-width:8px;border-radius:14px;padding:16px 20px;margin-bottom:14px;background:#fff}
.ap-hero .lbl{font-size:1.5rem;font-weight:700;letter-spacing:-.02em}
.ap-hero .sum{color:#1e293b;margin-top:4px;font-size:.98rem;line-height:1.45}
.ap-hero .sec{margin-top:10px;font-size:.86rem;color:#334155}
.ap-hero .sec b{color:var(--ink);display:block;margin-bottom:2px}
.ap-hero ul{margin:2px 0 0 18px;padding:0}
.ap-hero li{margin:2px 0;line-height:1.4}
.ap-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.ap-pill{display:inline-block;border:1px solid var(--line);background:var(--soft);border-radius:999px;padding:2px 10px;font-size:.78rem;color:#334155}
.ap-pill.red{background:#fef2f2;border-color:#fecaca;color:#b91c1c;font-weight:600}
.ap-pill.blue{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8}
.ap-kpi{border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:#fff}
.ap-kpi .n{font-size:1.7rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.ap-kpi .t{color:var(--muted);font-size:.8rem;margin-top:3px}
.ap-bar{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:.86rem}
.ap-bar .name{width:100px;color:#334155}
.ap-bar .track{flex:1;background:#f1f5f9;border-radius:6px;height:14px;overflow:hidden}
.ap-bar .fill{height:100%;border-radius:6px}
.ap-bar .val{width:28px;text-align:right;font-weight:600;color:var(--ink)}
.ap-banner{border-radius:10px;padding:9px 12px;font-size:.85rem;margin:0 0 10px 0}
.ap-banner.red{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}
.ap-banner.amber{background:#fffbeb;border:1px solid #fde68a;color:#92400e}
.ap-banner.blue{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af}
.ap-muted{color:var(--muted);font-size:.85rem}
.ap-case{border:1px dashed var(--line);border-radius:10px;padding:8px 12px;color:#334155;font-size:.86rem;background:var(--soft);margin:2px 0 10px 0}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def banner(text: str, kind: str = "blue") -> None:
    st.markdown(f'<div class="ap-banner {kind}">{text}</div>', unsafe_allow_html=True)


def kpi(n, label, color=None) -> str:
    style = f' style="color:{color}"' if color else ""
    return f'<div class="ap-kpi"><div class="n"{style}>{esc(n)}</div><div class="t">{esc(label)}</div></div>'


def bar(name, n, total, color) -> str:
    pct = 0 if not total else round(100 * n / total)
    return (f'<div class="ap-bar"><div class="name">{esc(name)}</div><div class="track"><div class="fill" '
            f'style="width:{pct}%;background:{color}"></div></div><div class="val">{n}</div></div>')
