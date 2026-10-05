"""
Extraction contract v1.0 - what the AI step must return for every invoice.

Design principle: THE AI EXTRACTS, THE RULES DECIDE.
This schema contains only things *printed on the document* (plus the model's own
confidence). It has no decision fields, no PO inference, no arithmetic.
Everything derived (normalising invoice numbers, backing tax out of inclusive prices,
matching to POs, duplicate detection) happens later in plain Python.

Standard JSON Schema (no $ref / oneOf) so it works both as Gemini's
`response_json_schema` and with the `jsonschema` library for validation.
"""
from __future__ import annotations
import copy
from jsonschema import Draft202012Validator

SCHEMA_VERSION = "1.0"

DOC_TYPES = ["tax_invoice", "proforma_invoice", "credit_note", "debit_note", "quotation",
             "delivery_challan", "purchase_order", "statement", "other"]
QUALITY = ["digital", "scan_clear", "scan_poor"]
TAX_TREATMENT = ["separate", "inclusive", "not_stated"]
TAX_TYPES = ["CGST", "SGST", "IGST", "CESS", "OTHER"]


def _t(t, nullable=False):
    return [t, "null"] if nullable else t


def _obj(props: dict, desc: str | None = None) -> dict:
    o = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    if desc:
        o["description"] = desc
    return o


def F(kind: str, desc: str) -> dict:
    """A scalar value wrapped with the model's confidence and a verbatim evidence snippet."""
    return _obj({
        "value": {"type": _t(kind, True), "description": desc + " Null if absent from the document."},
        "confidence": {"type": "number",
                       "description": "0.0-1.0. Your confidence that `value` is exactly what the document says "
                                      "(for null: how sure you are it is truly absent)."},
        "evidence": {"type": _t("string", True),
                     "description": "Short verbatim snippet (max 80 chars) copied from the document that supports "
                                    "the value. Null if the value is null."},
    })


SCHEMA = _obj({
    "document": _obj({
        "document_type": {"type": "string", "enum": DOC_TYPES,
                          "description": "What this document actually is. A 'Proforma Invoice', quotation, delivery "
                                         "challan or statement is NOT a tax_invoice even if it looks like one."},
        "document_quality": {"type": "string", "enum": QUALITY,
                             "description": "digital = clean machine-generated; scan_clear = scanned/photographed but "
                                            "fully legible; scan_poor = parts are hard to read."},
        "multiple_invoices_detected": {"type": "boolean",
                                       "description": "True if the file contains more than one distinct invoice. "
                                                      "If so, extract only the first one."},
        "annotations": {"type": "array", "items": {"type": "string"},
                        "description": "Stamps, handwriting and sticky notes that are NOT part of the printed "
                                       "invoice, e.g. 'RECEIVED 26 SEP 2026 (stamp)'. Never use these to fill invoice fields."},
    }),
    "vendor": _obj({
        "name": F("string", "Legal name of the seller issuing the invoice."),
        "gstin": F("string", "Seller's 15-character GSTIN, uppercase, no spaces."),
        "email": F("string", "Seller's email address printed in the invoice header or footer."),
        "address": {"type": _t("string", True), "description": "Seller address on one line."},
    }),
    "buyer": _obj({
        "name": F("string", "Name of the party billed (Bill To / Buyer)."),
        "gstin": F("string", "Buyer's GSTIN, uppercase, no spaces."),
    }),
    "invoice": _obj({
        "number": F("string", "Invoice number EXACTLY as printed, keeping prefixes, slashes, dashes and leading zeros."),
        "date": F("string", "Invoice date as ISO 8601 YYYY-MM-DD. Indian format is day-first."),
        "due_date": {"type": _t("string", True),
                     "description": "Due date as YYYY-MM-DD only if an explicit date is printed; null if only "
                                    "payment terms (e.g. 'Net 30') are given."},
        "po_reference": F("string", "Purchase order number the invoice cites, EXACTLY as printed. Only a real PO "
                                    "number belongs here (see rules)."),
        "other_references": {"type": "array", "items": {"type": "string"},
                             "description": "Any other reference text that is not a PO number, copied verbatim, "
                                            "e.g. 'Your Ref: Email dt. 04-Sep-2026'."},
        "currency": F("string", "ISO currency code, e.g. INR. Infer from the symbol (₹ = INR)."),
    }),
    "lines": {
        "type": "array",
        "description": "One entry per row of the line-item table, in order. Include charges such as freight, "
                       "courier or detention. Do not merge or split rows.",
        "items": _obj({
            "description": {"type": "string"},
            "hsn_sac": {"type": _t("string", True)},
            "quantity": {"type": _t("number", True)},
            "uom": {"type": _t("string", True)},
            "unit_price": {"type": _t("number", True), "description": "Rate as printed (may include tax)."},
            "line_amount": {"type": "number", "description": "Amount as printed for this row."},
            "confidence": {"type": "number", "description": "0.0-1.0 confidence for this whole row."},
        }),
    },
    "tax": _obj({
        "treatment": {"type": "string", "enum": TAX_TREATMENT,
                      "description": "separate = tax shown as its own lines; inclusive = document says prices include "
                                     "tax and does not break it out; not_stated = no tax information."},
        "tax_lines": {"type": "array",
                      "description": "Each tax line shown. For inclusive pricing, add one entry with the stated "
                                     "type and rate and amount = null.",
                      "items": _obj({
                          "tax_type": {"type": "string", "enum": TAX_TYPES},
                          "rate_pct": {"type": _t("number", True)},
                          "amount": {"type": _t("number", True)},
                      })},
        "inclusive_statement": {"type": _t("string", True),
                                "description": "Verbatim sentence saying amounts include tax, if any."},
    }),
    "totals": _obj({
        "subtotal": F("number", "Taxable value / sub total as printed BEFORE tax."),
        "total": F("number", "Final invoice total payable, as printed."),
        "total_in_words": {"type": _t("string", True), "description": "The 'amount in words' line, verbatim."},
    }),
    "bank": _obj({
        "bank_name": F("string", "Bank name and branch the vendor asks to be paid at."),
        "account_number": F("string", "Account number, digits only, as printed."),
        "ifsc": F("string", "IFSC code, uppercase."),
        "change_notice": _obj({
            "detected": {"type": "boolean",
                         "description": "True if the document says the vendor's bank details have changed or asks "
                                        "the buyer to update bank records."},
            "evidence": {"type": _t("string", True), "description": "Verbatim sentence, or null."},
        }),
    }),
})


def _confidences(node, path=""):
    """Yield (path, confidence) for every confidence value in an extraction."""
    if isinstance(node, dict):
        if "confidence" in node and isinstance(node["confidence"], (int, float)):
            yield path, node["confidence"]
        for k, v in node.items():
            yield from _confidences(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _confidences(v, f"{path}[{i}]")


_VALIDATOR = Draft202012Validator(SCHEMA)


def validate(data: dict) -> list[str]:
    """Return a list of human-readable problems ([] = valid)."""
    problems = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                for e in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path))]
    if not problems:
        for p, c in _confidences(data):
            if not 0.0 <= c <= 1.0:
                problems.append(f"{p}: confidence {c} outside 0-1")
    return problems


def field_confidences(data: dict) -> dict[str, float]:
    """Flat {path: confidence} map. The rules engine uses this to apply the confidence floor."""
    return dict(_confidences(data))


def schema_copy() -> dict:
    return copy.deepcopy(SCHEMA)
