"""
Copy the saved REAL Gemini results for the invoices in invoices/ into seed_cache/, so a hosted copy of the app
can serve them without calling the API. Run from the kit folder, after running the invoices once with a real key
(python3 -m engine.run_demo --live):

    python3 -m extraction.export_seed

Only genuine model outputs are copied (entries written by a live call), never the hand-written golden answers.
"""
from __future__ import annotations
import json, os, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from extraction import extract
from extraction.schema import validate


def main() -> int:
    model = os.environ.get("GEMINI_MODEL", extract.DEFAULT_MODEL)
    src, dst = extract._cache_dir(), ROOT / "seed_cache"
    dst.mkdir(exist_ok=True)
    files = sorted(p for p in (ROOT / "invoices").iterdir() if p.suffix.lower() in extract.MIME)
    missing, bad = [], []
    for f in files:
        s = src / f"{extract._cache_key(f.read_bytes(), model)}.json"
        if not s.exists():
            missing.append(f.name); print(f"MISSING  {f.name}"); continue
        entry = json.loads(s.read_text())
        problems = validate(entry["data"])
        if entry["meta"].get("source") != "live" or problems:
            bad.append(f.name); print(f"REJECTED {f.name}  (not a live model result, or invalid: {problems[:1]})"); continue
        shutil.copy(s, dst / s.name); print(f"ok       {f.name}")
    print(f"\n{len(files) - len(missing) - len(bad)}/{len(files)} saved into {dst.name}/")
    if missing:
        print("Missing entries: run  python3 -m engine.run_demo --live  first (with GEMINI_API_KEY set), then run this again.")
    return 1 if (missing or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
