"""Small pure helpers: money formatting, normalisers, fuzzy matching, amount-in-words parsing."""
from __future__ import annotations
import re
from difflib import SequenceMatcher

LEGAL_SUFFIXES = {"pvt", "ltd", "llp", "private", "limited", "co", "company", "inc", "corp"}
GENERIC_INVOICE_TOKENS = {"INV", "INVOICE", "BILL", "NO", "NUM", "NUMBER"}
STOPWORDS = {"the", "of", "and", "to", "at", "for", "a", "in", "on", "with"}


def fmt_inr(x) -> str:
    """Indian digit grouping: 1180000 -> ₹11,80,000 ; 1888.5 -> ₹1,888.50"""
    if x is None:
        return "n/a"
    neg = float(x) < 0
    x = round(abs(float(x)), 2)
    ip = str(int(x))
    frac = f"{x - int(x):.2f}"[1:]
    if len(ip) > 3:
        head, tail, groups = ip[:-3], ip[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        ip = ",".join(groups) + "," + tail
    return ("-" if neg else "") + "₹" + ip + ("" if frac == ".00" else frac)


def fmt_pct(x, nd: int = 1) -> str:
    return f"{x:.{nd}f}%"


def fmt_qty(q) -> str:
    return f"{q:g}"


def short(s: str, n: int = 46) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---------- normalisers ----------
def _strip_leading_zeros(token: str) -> str:
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", token)


def normalize_invoice_number(s) -> str:
    """'INV-0042' and '42' both -> '42'. 'BL/INV/2026/0811' -> 'BL-2026-811'."""
    if not s:
        return ""
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", str(s).upper()) if t]
    toks = [t for t in toks if t not in GENERIC_INVOICE_TOKENS]
    return "-".join(_strip_leading_zeros(t) for t in toks)


def normalize_po(s) -> str:
    """'PO-2026-0101', 'PO/2026/0101', '2026-0101' all -> '20260101'."""
    if not s:
        return ""
    t = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    return t[2:] if t.startswith("PO") else t


# ---------- fuzzy matching ----------
def norm_name(s) -> str:
    toks = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    return " ".join(t for t in toks if t not in LEGAL_SUFFIXES)


def name_sim(a, b) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    contain = len(ta & tb) / min(len(ta), len(tb))
    return max(ratio, contain * 0.9)


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") else t


def _toks(s) -> list:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in STOPWORDS]


def desc_sim(a, b) -> float:
    """Word-level similarity 0-1: share of the shorter description's words found in the other
    (tolerating plurals and small typos). Character-level ratios give ~0.4 even for unrelated
    text, so they are deliberately not used."""
    ta, tb = set(_toks(a)), set(_toks(b))
    if not ta or not tb:
        return 0.0
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hits = sum(1 for t in small if t in big or any(SequenceMatcher(None, t, u).ratio() >= 0.85 for u in big))
    return hits / len(small)


# ---------- amount in words ----------
_UNITS = {w.lower(): i for i, w in enumerate(
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen "
    "Sixteen Seventeen Eighteen Nineteen".split())}
_TENS = {w.lower(): (i + 2) * 10 for i, w in enumerate(
    "Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split())}
_FILLER = {"rupees", "rupee", "rs", "inr", "only", "and"}


def words_to_number(text):
    """'Rupees One Lakh Eighty Eight Thousand Eight Hundred Only' -> 188800. None if it can't be parsed."""
    if not text:
        return None
    toks = [w for w in re.sub(r"[^a-z\s-]", " ", text.lower()).replace("-", " ").split() if w not in _FILLER]
    if not toks:
        return None
    total = current = 0
    for w in toks:
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total, current = total + (current or 1) * 1000, 0
        elif w in ("lakh", "lakhs", "lac"):
            total, current = total + (current or 1) * 100000, 0
        elif w in ("crore", "crores"):
            total, current = total + (current or 1) * 10000000, 0
        else:
            return None  # unknown word (e.g. paise) - don't guess
    return total + current
