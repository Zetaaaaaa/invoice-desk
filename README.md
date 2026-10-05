# PS-1 Test Kit: Kestrel Components Pvt Ltd (fictional buyer, Pune)

## What's in here

| Path | What it is |
|---|---|
| `data/vendors.csv` | Vendor master: 5 active, 1 blocked. Includes GSTIN, aliases for fuzzy name matching, and the last 4 bank-account digits. |
| `data/purchase_orders.csv` | 7 POs (6 open, 1 closed) with subtotal, tax and total. |
| `data/po_lines.csv` | Line items for each PO, used for line-level checks and PO inference. |
| `data/rules.json` | Every threshold in one place: tolerance, duplicate rules, confidence floor, decision definitions. Load this rather than hard-coding values. |
| `data/expected_results.csv` | The answer key. Use it as a regression check after every change. |
| `invoices/` | 9 invoice PDFs, plus a JPG of the scan, so you can also test image uploads. |
| `app.py`, `ui/` | The Streamlit app: process page with live run view, dashboard, rules page. `ui/test_ui.py` is its test suite. See "The app" below. |
| `engine/` | The rules engine and database: 9-stage pipeline, SQLite storage, 73 offline tests. See "Rules engine" below. |
| `extraction/` | The AI-extraction contract: JSON schema, prompt, Gemini extractor, golden answers and tests. See "Extraction step" below. |
| `gen_invoices.py`, `make_scan.py` | Regenerate or extend the invoices. **Run `make_scan.py` after `gen_invoices.py`**, because regenerating overwrites invoice 08 with a clean digital PDF. |

## The 9 invoices

There are 5 visual layouts with different fonts and field labels. For example, the PO field appears as "PO No", "Your Order Ref", "PO#", "PO Ref" and "Ref PO". The extractor must cope with that variety.

1. **Happy path:** `01` (exact match) and `02` (slightly over the PO, but within tolerance).
2. **Edge case A, split PO:** `03` → `04` → `05`.
   - Three invoices are billed against one ₹5.9L PO.
   - Parts 1 and 2 approve.
   - Part 3 is held because:
     - it breaches the remaining balance by ₹47,200
     - it bills 21 trips against 20 ordered
     - it adds a detention charge that isn't on the PO
   - The vendor writes the PO as `2026-0102`, so PO-number normalisation is needed too.
3. **Edge case B, disguised duplicate:** `06` → `07`.
   - `07` is the same bill as `06`, sent a week later.
   - It has a new layout ("we migrated billing systems"), its number is `42` instead of `INV-0042`, and its date format changes.
   - An exact-match duplicate check misses it.
4. **Edge case C, scanned invoice with no PO and tax embedded:** `08`.
   - It's an image-only PDF with no text layer. It has a stamp, a handwritten note and slight skew.
   - It has no PO number, only "Email dt. 04-Sep-2026 (Mr. Sandeep)".
   - Line rates *include* IGST. The extractor only flags `tax.treatment = inclusive` and the stated 18% rate; the **rules code** backs out ₹1,60,000 taxable + ₹28,800 IGST. (The AI reads, the code calculates.)
   - Orbit has two open POs, so inference has to choose between them: 0104 is the right one, 0105 is the decoy.

**Run order matters.** The process is stateful. Run 03→04→05 and 06→07 in sequence (01, 02, 08 and 09 are independent), and add a "reset demo data" button to the dashboard so rehearsals start clean. The reset should also clear the extraction cache, so your interview run makes a real model call.

5. **Edge case D, bank details changed:** `09`.
   - A perfect-looking Sahyadri invoice, same layout as `01`, matching PO-2026-0108 exactly: right quantity, right price, right tax.
   - But the bank account ends **8820** (master says **4417**), the sender email domain is `sahyadri-steel.in` (master: `sahyadristeel.in`), and a note asks the buyer to "update your vendor records".
   - Expected: **HOLD** with the action "verify by phone using the number in the vendor master". Never auto-approve and never REJECT: the vendor may be legitimate.

## Extraction step (the AI contract)

`extraction/` holds everything the AI step needs. The model returns only what is *printed* on the document, each key field with a confidence score and a verbatim evidence snippet. It makes no decisions.

| File | Purpose |
|---|---|
| `schema.py` | The JSON schema (also used as Gemini's structured-output schema) and a `validate()` helper. |
| `prompt.py` | System prompt. Bump `PROMPT_VERSION` after any edit (it is part of the cache key). |
| `extract.py` | `extract_invoice(path)`: calls Gemini, validates the result, caches by file hash, raises `ExtractionError` with a stable code (`RATE_LIMIT`, `BAD_JSON`, ...) for the UI to show with a Retry button. |
| `golden/*.json` | The perfect extraction of each test invoice, generated from the same data as the PDFs. |
| `make_golden.py` | Regenerates the goldens. |
| `test_extraction.py` | `--validate-goldens` runs offline. Without the flag it runs the live model on every invoice and diffs against the goldens. |

Setup (Python 3.9 or newer; tested on 3.9 and 3.12): `python3 -m pip install google-genai jsonschema`, then `export GEMINI_API_KEY=...` (free key from Google AI Studio). Optionally set `GEMINI_MODEL`; the default is `gemini-3.1-flash-lite`, so confirm the model name against the free models in your AI Studio.

**Status:** the schema, goldens and tests are verified offline. The live Gemini call has NOT been run against the real API from here. Your first job is `python -m extraction.test_extraction`. Start with `08` and `09`, since the scan and the bank-change flag are the hardest. Tune `prompt.py` until the diffs are clean.

`EXTRACT_MODE=golden` returns the golden JSON without calling the model. It is for developing the rules engine and UI before your key is ready. The UI must show a visible "offline test data" banner whenever `meta.source == "golden"`, and never demo in this mode.

## Rules engine

`engine/` is everything that happens after the AI has read the invoice. Principle: **the AI extracts, the rules decide**. Every number comes from `data/rules.json`, so "what if tolerance were 5%?" is a one-line edit.

| File | Purpose |
|---|---|
| `db.py` | SQLite: master data, `runs`, `run_steps` (powers the live run view), `invoices`, `invoice_lines`. `reset_demo()` wipes history and reloads the CSVs. `po_status()` gives balances for the dashboard. `apply_human_decision()` lets a reviewer resolve a NEEDS_REVIEW or HOLD. |
| `rules.py` | One function per stage. Each returns checks with a level: `pass < info < warn < review < hold < reject`. |
| `pipeline.py` | `process_iter()` is a generator that yields an event at every step start and end (the UI redraws on each). `process()` runs it to the end. |
| `util.py` | Indian-format money, invoice/PO number normalisers, fuzzy matching, amount-in-words parser. |
| `test_engine.py` | 73 offline tests: the 9-invoice answer key, ~35 edge cases, and the false-alarm cases that must NOT be flagged. |
| `run_demo.py` | Run the whole thing in a terminal, offline or on real Gemini results. |

**The nine steps:** Receive file, Read invoice (AI), Validate document, Identify vendor, Verify payment and compliance, Check for duplicates, Match purchase order, Check line items, Decide.

**Decision = the most severe level any check raised.** `warn` and `info` are notes and never change the decision.

| Level | Decision | Examples |
|---|---|---|
| reject | REJECT | duplicate, blocked or unknown vendor, lookalike vendor |
| hold | HOLD | over PO balance, quantity or price variance, bank account changed, wrong tax type, wrong buyer GSTIN, PO closed or missing, invoice arithmetic wrong, required field missing |
| review | NEEDS_REVIEW | inferred PO, low confidence, not a tax invoice, lookalike email domain, probable duplicate, amount in words disagrees |
| none | APPROVE | everything passed (notes allowed, e.g. "1.44% over, within tolerance") |

Design choices to be ready to defend:

- **Tolerance is the LOWER of 2% or INR 10,000**, applied to the PO's *remaining* balance. Only APPROVED invoices consume balance and quantity.
- **Duplicate detection is precise.** Same vendor plus the same *normalised* number means duplicate (INV-0042 equals 42). A different number is only "probable" if the amount matches AND the date or line items match too. Otherwise invoice 04 would be flagged against 03. Same number from a *different* vendor is never a duplicate. A definite duplicate stops the run, since the PO and line findings would just be noise.
- **GSTIN beats the name** for identifying a vendor. A similar name with an unknown GSTIN is a lookalike and gets REJECT.
- **PO inference never auto-approves.** If two POs fit about equally well it refuses to choose.
- **Tax-inclusive prices** are backed out by code, then compared to PO prices ex-tax.
- **Arithmetic is the code's job**, never the model's. Totals, tax, amount-in-words and PO-consumption are all recalculated.

Commands (run from the kit folder):

```bash
python3 -m engine.test_engine              # 73 offline tests, no API key needed
python3 -m engine.run_demo                 # offline: 9 invoices in order vs the answer key
python3 -m engine.run_demo --live          # real Gemini results (cached) through the engine
python3 -m engine.run_demo --live --steps  # also print every step
```

Changing a rule is a test too: `test_engine.py` proves that tightening tolerance to 0.5% flips invoice 02 from APPROVE to HOLD.

> **Submitting?** Read `DEPLOY.md` (get the live link) and `VIDEO_SCRIPT.md` (record the 5-minute video).

## The app

```bash
python3 -m pip install -r requirements.txt     # streamlit, google-genai, jsonschema, pypdfium2
export GEMINI_API_KEY="..."                     # not needed for offline test data
streamlit run app.py
```

| Page | What it does |
|---|---|
| **Process invoice** | Pick a test invoice (a progress strip shows which are done, and the next one is preselected so Run walks the demo in order) or upload your own. Each of the nine steps appears as it runs, with its reasons. The document is shown beside the timeline, and a tab shows what the AI read, with confidence per field. Failed runs show the reason and a Retry button when retrying makes sense. |
| **Dashboard** | KPIs, outcomes, PO balances, a **review queue** (approve or reject held invoices with a required note; approving uses up the PO balance and is recorded in the audit trail), run history, and a drill-down into any run. Danger zone: reset demo data. |
| **Rules & assumptions** | Sliders for the main thresholds, for "what if tolerance were 0.5%?" questions. Changes apply to this browser session only. |

Sidebar settings: *Offline test data* (rehearsal only: hand-written answers, no AI, with a red banner and a red pill on every result), *Fresh AI read every time* (ignore the cache), and *Demo pacing* (slows the on-screen display only; it changes no result).

Before each real demo: Dashboard, Danger zone, Reset demo data. Make sure Offline test data is OFF. Open the app a few minutes early if it is hosted (see below).

Tests: `python3 -m ui.test_ui` (headless, no API key, a few seconds per scenario).

**Hosting on Streamlit Community Cloud (free):** push the folder to GitHub, deploy `app.py`, and paste `GEMINI_API_KEY = "..."` into the app's Secrets. Never commit the key. Free apps sleep after about 12 hours without visitors, so open the link before the interview. The database is a local SQLite file, so treat it as disposable: use Reset demo data before each demo. Your local laptop run is the most reliable path for a live demo.

## Spare data for a 5th case, if time allows

Vendor V006 (Zenith) is **blocked**, and its PO-2026-0107 is **closed**. Adding an invoice from them would demo a REJECT on both counts. Add an entry to `INVOICES` in `gen_invoices.py` to generate one.

## Assumptions (note these for the interview)

- One buyer entity, INR only, GST at 18%.
- Tolerance is applied to the PO's *remaining* balance, not the original total.
- Header-level matching drives the decision. Line-level differences add reasons and severity.
- No goods-receipt (GRN) data, so this is 2-way matching rather than 3-way. 3-way matching is a good "what I'd build next" talking point.
