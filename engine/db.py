"""SQLite storage: master data, runs, run steps, invoices. Stdlib only."""
from __future__ import annotations
import csv, json, os, pathlib, sqlite3, tempfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = os.environ.get("AP_DB_PATH") or (
    str(ROOT / "ap_demo.db") if os.access(ROOT, os.W_OK) else str(pathlib.Path(tempfile.gettempdir()) / "ap_demo.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors(
  vendor_id TEXT PRIMARY KEY, vendor_name TEXT, aliases TEXT, gstin TEXT, state TEXT, city TEXT,
  payment_terms_days INTEGER, bank_account_last4 TEXT, contact_email TEXT, status TEXT, status_note TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders(
  po_number TEXT PRIMARY KEY, vendor_id TEXT, po_date TEXT, description TEXT, currency TEXT,
  subtotal REAL, tax_type TEXT, tax_rate_pct REAL, tax_amount REAL, total REAL, status TEXT,
  requester TEXT, cost_center TEXT);
CREATE TABLE IF NOT EXISTS po_lines(
  po_number TEXT, line_no INTEGER, item_description TEXT, hsn_sac TEXT, quantity REAL, uom TEXT,
  unit_price REAL, line_amount REAL, PRIMARY KEY(po_number, line_no));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, file_name TEXT, status TEXT, decision TEXT, summary TEXT,
  extraction_source TEXT, error_code TEXT, error_message TEXT, invoice_id INTEGER,
  started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS run_steps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, seq INTEGER, stage TEXT, title TEXT, status TEXT,
  summary TEXT, checks_json TEXT, data_json TEXT, started_at TEXT, finished_at TEXT, duration_ms INTEGER);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, file_name TEXT, vendor_id TEXT,
  invoice_number_raw TEXT, invoice_number_norm TEXT, invoice_date TEXT, total_amount REAL,
  matched_po TEXT, po_match_method TEXT, po_match_score REAL, decision TEXT, summary TEXT,
  reasons_json TEXT, actions_json TEXT, extraction_json TEXT, lines_sig TEXT,
  human_decision TEXT, human_note TEXT, human_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS invoice_lines(
  invoice_id INTEGER, line_no INTEGER, description TEXT, hsn_sac TEXT, quantity REAL, uom TEXT,
  unit_price REAL, line_amount REAL, po_line_no INTEGER, PRIMARY KEY(invoice_id, line_no));
"""
TABLES = ["invoice_lines", "invoices", "run_steps", "runs", "po_lines", "purchase_orders", "vendors"]
NUMERIC = {"payment_terms_days": int, "subtotal": float, "tax_rate_pct": float, "tax_amount": float, "total": float,
           "line_no": int, "quantity": float, "unit_price": float, "line_amount": float}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DEFAULT_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _load_csv(conn, table, fname, data_dir):
    with open(pathlib.Path(data_dir) / fname, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r = {k: (NUMERIC[k](v) if k in NUMERIC and v != "" else v) for k, v in r.items()}
        cols = ",".join(r)
        conn.execute(f"INSERT INTO {table}({cols}) VALUES({','.join('?' * len(r))})", list(r.values()))


def seed_master(conn, data_dir=DATA_DIR) -> None:
    _load_csv(conn, "vendors", "vendors.csv", data_dir)
    _load_csv(conn, "purchase_orders", "purchase_orders.csv", data_dir)
    _load_csv(conn, "po_lines", "po_lines.csv", data_dir)
    conn.commit()


def reset_demo(conn, data_dir=DATA_DIR, clear_extraction_cache: bool = True) -> None:
    """Wipe everything (history, balances) and reload master data from the CSVs."""
    for t in TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    init_db(conn)
    seed_master(conn, data_dir)
    if clear_extraction_cache:
        try:
            from extraction.extract import clear_cache
            clear_cache()
        except Exception:
            pass


def fresh_memory_db():
    """In-memory DB seeded with master data - used by tests."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    seed_master(conn)
    return conn


# ---------- reads used by the rules ----------
def po_consumed(conn, po_number) -> float:
    r = conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM invoices WHERE matched_po=? "
                     "AND COALESCE(human_decision, decision)='APPROVE'", (po_number,)).fetchone()
    return float(r[0])


def po_line_billed_qty(conn, po_number, line_no) -> float:
    r = conn.execute("SELECT COALESCE(SUM(l.quantity),0) FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id "
                     "WHERE i.matched_po=? AND l.po_line_no=? AND COALESCE(i.human_decision, i.decision)='APPROVE'",
                     (po_number, line_no)).fetchone()
    return float(r[0])


# ---------- writes used by the pipeline ----------
def create_run(conn, file_name) -> int:
    cur = conn.execute("INSERT INTO runs(file_name,status,started_at) VALUES(?,?,?)", (file_name, "running", now()))
    conn.commit()
    return cur.lastrowid


def add_step(conn, run_id, seq, stage, title, status, summary, checks, data, started_at, finished_at, duration_ms):
    conn.execute("INSERT INTO run_steps(run_id,seq,stage,title,status,summary,checks_json,data_json,started_at,"
                 "finished_at,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (run_id, seq, stage, title, status, summary, json.dumps(checks, default=str),
                  json.dumps(data, default=str), started_at, finished_at, duration_ms))
    conn.commit()


def finish_run(conn, run_id, status, decision=None, summary=None, source=None, error_code=None,
               error_message=None, invoice_id=None):
    conn.execute("UPDATE runs SET status=?,decision=?,summary=?,extraction_source=?,error_code=?,error_message=?,"
                 "invoice_id=?,finished_at=? WHERE id=?",
                 (status, decision, summary, source, error_code, error_message, invoice_id, now(), run_id))
    conn.commit()


def get_run(conn, run_id) -> dict:
    run = dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    steps = []
    for s in conn.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY seq,id", (run_id,)):
        s = dict(s)
        s["checks"] = json.loads(s.pop("checks_json") or "[]")
        s["data"] = json.loads(s.pop("data_json") or "{}")
        steps.append(s)
    run["steps"] = steps
    return run


def list_runs(conn, limit=200) -> list:
    q = ("SELECT r.id run_id, r.file_name, r.status, r.decision, r.summary, r.extraction_source, r.error_code, "
         "r.started_at, i.id invoice_id, i.vendor_id, v.vendor_name, i.invoice_number_raw, i.total_amount, "
         "i.matched_po, i.human_decision FROM runs r LEFT JOIN invoices i ON i.id=r.invoice_id "
         "LEFT JOIN vendors v ON v.vendor_id=i.vendor_id ORDER BY r.id DESC LIMIT ?")
    return [dict(r) for r in conn.execute(q, (limit,))]


def po_status(conn) -> list:
    """Per-PO consumption, for the dashboard."""
    out = []
    for po in conn.execute("SELECT * FROM purchase_orders ORDER BY po_number"):
        used = po_consumed(conn, po["po_number"])
        out.append({**dict(po), "invoiced": used, "remaining": po["total"] - used})
    return out


def apply_human_decision(conn, invoice_id, action, note="", po_number=None) -> None:
    """A reviewer resolves a NEEDS_REVIEW / HOLD invoice. action: 'APPROVE' or 'REJECT'. Approvals consume PO balance."""
    action = action.upper()
    if action not in ("APPROVE", "REJECT"):
        raise ValueError("action must be APPROVE or REJECT")
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv is None:
        raise ValueError("unknown invoice")
    if inv["decision"] not in ("NEEDS_REVIEW", "HOLD"):
        raise ValueError(f"only NEEDS_REVIEW or HOLD invoices can be resolved (this one is {inv['decision']})")
    conn.execute("UPDATE invoices SET human_decision=?, human_note=?, human_at=?, matched_po=COALESCE(?,matched_po) "
                 "WHERE id=?", (action, note, now(), po_number, invoice_id))
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM run_steps WHERE run_id=?", (inv["run_id"],)).fetchone()[0]
    t = now()
    add_step(conn, inv["run_id"], seq, "human_review", "Human review", "pass" if action == "APPROVE" else "fail",
             f"Reviewer {action.lower()}d" + (f": {note}" if note else ""), [], {"action": action, "note": note,
             "po_number": po_number or inv["matched_po"]}, t, t, 0)
    conn.commit()
