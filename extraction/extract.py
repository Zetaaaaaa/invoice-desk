"""
Extraction step: file in -> validated JSON out (Gemini by default).

    from extraction.extract import extract_invoice, ExtractionError
    result = extract_invoice("invoices/01_happy_sahyadri_steel.pdf")
    result["data"]   # dict matching schema.SCHEMA
    result["meta"]   # model, cached, latency_ms, tokens, prompt_version

Failures raise ExtractionError(code, message) with a stable `code` the UI can show and
offer a Retry for: NO_API_KEY, UNSUPPORTED_FILE, RATE_LIMIT, API_ERROR, BAD_JSON, SCHEMA_INVALID.

Env vars: GEMINI_API_KEY (required for live calls), GEMINI_MODEL (default below - confirm the
name against the free models listed in Google AI Studio), EXTRACT_CACHE_DIR (default .cache/extractions).
"""
from __future__ import annotations
import hashlib, json, os, pathlib, tempfile, time

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT
from .schema import SCHEMA, SCHEMA_VERSION, validate

DEFAULT_MODEL = "gemini-3.1-flash-lite"
MIME = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp"}
BACKOFF_SECONDS = (4, 12, 30)   # retries on 429 (free tier is rate limited per minute) and 5xx (model overloaded)
RETRY_CODES = {429, 500, 502, 503, 504}
GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cache_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("EXTRACT_CACHE_DIR", ".cache/extractions"))
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.touch(); probe.unlink()
    except OSError:                       # read-only app folder (some hosts): fall back to the temp directory
        d = pathlib.Path(tempfile.gettempdir()) / "invoice_desk_cache"
        d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_dir() -> pathlib.Path:
    """Read-only folder of REAL saved Gemini results shipped with the repo (see extraction/export_seed.py)."""
    return pathlib.Path(os.environ.get("EXTRACT_SEED_DIR", ROOT / "seed_cache"))


def clear_cache() -> int:
    n = 0
    for f in _cache_dir().glob("*.json"):
        f.unlink(); n += 1
    return n


def _cache_key(raw: bytes, model: str) -> str:
    h = hashlib.sha256(raw)
    h.update(f"|{model}|prompt{PROMPT_VERSION}|schema{SCHEMA_VERSION}".encode())
    return h.hexdigest()


def extract_invoice(path: str, *, use_cache: bool = True, model: str | None = None,
                    api_key: str | None = None, mode: str | None = None) -> dict:
    """mode='golden' is DEV ONLY (returns hand-written expected JSON, no API call). The UI must
    show a visible 'offline test data' banner whenever meta['source'] == 'golden'."""
    p = pathlib.Path(path)
    mode = mode or os.environ.get("EXTRACT_MODE", "live")
    if mode == "golden":
        g = GOLDEN_DIR / (p.stem + ".json")
        if not g.exists():
            raise ExtractionError("UNSUPPORTED_FILE", f"No golden extraction for {p.name}")
        return {"data": json.loads(g.read_text()),
                "meta": {"source": "golden", "model": None, "cached": False, "latency_ms": 0,
                         "input_tokens": 0, "output_tokens": 0, "prompt_version": PROMPT_VERSION}}

    mime = MIME.get(p.suffix.lower())
    if not mime:
        raise ExtractionError("UNSUPPORTED_FILE", f"{p.suffix or 'this file type'} is not supported. Use PDF, JPG or PNG.")
    raw = p.read_bytes()
    model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    key = _cache_key(raw, model)
    cache_file = _cache_dir() / f"{key}.json"
    if use_cache:
        for f in (cache_file, _seed_dir() / f"{key}.json"):
            if f.exists():
                cached = json.loads(f.read_text())
                cached["meta"].update(cached=True, latency_ms=0)
                return cached

    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ExtractionError("NO_API_KEY", "Set GEMINI_API_KEY (free key from Google AI Studio).")

    import logging
    # the SDK logs a harmless 'non-text parts (thought_signature)' warning on every call; keep the console clean
    logging.getLogger("google_genai.types").setLevel(logging.ERROR)
    from google import genai
    from google.genai import types, errors

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, temperature=0,
        response_mime_type="application/json", response_json_schema=SCHEMA)
    contents = [types.Part.from_bytes(data=raw, mime_type=mime), USER_PROMPT]

    t0, resp = time.time(), None
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            break
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code in RETRY_CODES and attempt < len(BACKOFF_SECONDS):
                time.sleep(BACKOFF_SECONDS[attempt]); continue
            if code == 429:
                raise ExtractionError("RATE_LIMIT", "Free-tier rate limit hit. Wait a minute and retry.") from e
            raise ExtractionError("API_ERROR", f"Model API error ({code}): {getattr(e, 'message', e)}") from e
        except Exception as e:  # network down, DNS, timeout...
            raise ExtractionError("API_ERROR", f"Could not reach the model API: {e}") from e

    try:
        data = json.loads(resp.text)
    except Exception as e:
        raise ExtractionError("BAD_JSON", "Model did not return valid JSON.") from e
    problems = validate(data)
    if problems:
        raise ExtractionError("SCHEMA_INVALID", "; ".join(problems[:5]))

    usage = getattr(resp, "usage_metadata", None)
    result = {"data": data,
              "meta": {"source": "live", "model": model, "cached": False,
                       "latency_ms": int((time.time() - t0) * 1000),
                       "input_tokens": getattr(usage, "prompt_token_count", None),
                       "output_tokens": getattr(usage, "candidates_token_count", None),
                       "prompt_version": PROMPT_VERSION}}
    cache_file.write_text(json.dumps(result))
    return result
