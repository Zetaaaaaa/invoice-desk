"""
Run the invoices through the whole pipeline in your terminal.

    python3 -m engine.run_demo                 # offline: uses the hand-written golden extractions
    python3 -m engine.run_demo --live          # real Gemini extraction (results are cached, so repeat runs are free)
    python3 -m engine.run_demo --live --steps  # also print every step of every run
    python3 -m engine.run_demo --live 05 08    # only these invoices (note: the process is stateful, order matters)

Uses a fresh in-memory database each time, so it never touches your demo data.
"""
from __future__ import annotations
import csv, json, pathlib, sys, warnings

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import db, pipeline

ICON = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL", "skipped": "skip"}


def main(argv):
    live, steps = "--live" in argv, "--steps" in argv
    only = [a for a in argv if not a.startswith("--")]
    conn = db.fresh_memory_db()
    rows = list(csv.DictReader(open(ROOT / "data" / "expected_results.csv")))
    print(("LIVE extraction (Gemini, cached)" if live else "OFFLINE golden extraction (no AI involved)") + "\n")
    mism, shown = 0, 0
    for r in rows:
        want_po = None if r["matched_po"] in ("-", "") else r["matched_po"].split(" ")[0]
        run_it = not only or any(r["file"].startswith(o) for o in only)
        res = None
        for ev in pipeline.process_iter(conn, str(ROOT / "invoices" / r["file"]), mode=None if live else "golden"):
            if ev["type"] == "step_end" and steps and run_it:
                print(f"    {ICON[ev['status']]} {ev['title']:30s} {ev['summary'][:100]}")
            if ev["type"] == "done":
                res = ev
        if not run_it:
            continue  # still ran it (state), just don't print
        shown += 1
        if res["status"] == "failed":
            print(f"ERR  {r['file']:34s} {res['error_code']}: {res['summary']}")
            mism += 1
            continue
        inv = conn.execute("SELECT matched_po, po_match_method FROM invoices WHERE id=?", (res["invoice_id"],)).fetchone()
        good = res["decision"] == r["expected_decision"] and inv["matched_po"] == want_po
        mism += not good
        print(f"{'OK  ' if good else 'DIFF'} {r['file']:34s} {res['decision']:12s} (expected {r['expected_decision']}) PO={inv['matched_po']} [{inv['po_match_method']}]")
        print(f"       {res['summary'][:170]}")
        if not good:
            for x in json.loads(conn.execute("SELECT reasons_json FROM invoices WHERE id=?", (res["invoice_id"],)).fetchone()[0]):
                print(f"       - [{x['level']}] {x['code']}: {x['message'][:140]}")
    print(f"\n{shown - mism}/{shown} matched the answer key (decision and PO).")
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
