"""parse_pdf_vision tool (PRD S7.2 / T18).

Gemini multimodal read of a PDF tax invoice (Format D) -- the vendor's
legally authoritative GST document. Used on the recovery ladder (S7.3 step
3, when no other route parsed the file) and for the reconciliation case
(S7.3/S7.4), where the PDF resolves which of two conflicting CSV/XLS
versions of the same invoice number is correct.

Unlike parse_csv/parse_xls there is no raw-row schema for a PDF to key off
-- Gemini extracts straight into the same unified fields normalize.py
produces for the other formats, so this module builds a formats.schema.Bill
directly from the model's structured response instead of going through a
separate normalize step.
"""
from __future__ import annotations

import json
import os

from google import genai
from google.genai import types

from .schema import Bill, LineItem

_MODEL = os.environ.get("PHARMACY_AGENT_GEMINI_MODEL", "gemini-3.5-flash")
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")
# Confirmed against this project: gemini-3.5-flash 404s in a regional
# endpoint (e.g. us-central1) but serves from the "global" Vertex AI
# location.
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

_LINE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "item_name": {"type": "string"},
        "batch_no": {"type": "string"},
        "expiry_date": {
            "type": "string",
            "description": "ISO 8601 YYYY-MM-DD. If the invoice only prints "
            "month/year for expiry, default the day to 01.",
        },
        "quantity": {"type": "number"},
        "rate": {"type": "number"},
        "discount": {"type": "number"},
        "taxable_value": {"type": "number"},
        "tax_component_1_label": {"type": "string"},
        "tax_component_1_rate": {"type": "number"},
        "tax_component_1_amount": {"type": "number"},
        "tax_component_2_label": {"type": "string"},
        "tax_component_2_rate": {"type": "number"},
        "tax_component_2_amount": {"type": "number"},
        "mrp": {"type": "number"},
        "line_total": {"type": "number"},
        "hsn_code": {"type": "string"},
    },
    "required": [
        "item_name", "batch_no", "expiry_date", "quantity", "rate",
        "discount", "taxable_value", "tax_component_1_label",
        "tax_component_1_rate", "tax_component_1_amount",
        "tax_component_2_label", "tax_component_2_rate",
        "tax_component_2_amount", "mrp", "line_total", "hsn_code",
    ],
}

_BILL_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "invoice_no": {"type": "string"},
        "invoice_date": {"type": "string", "description": "ISO 8601 YYYY-MM-DD"},
        "total_amount": {
            "type": "number",
            "description": "The invoice's own printed grand/payable total.",
        },
        "line_items": {"type": "array", "items": _LINE_ITEM_SCHEMA},
    },
    "required": ["vendor", "invoice_no", "invoice_date", "total_amount", "line_items"],
}

_PROMPT = """This is a pharmaceutical distributor's PDF tax invoice (a GST-compliant
bill from a vendor to a pharmacy). Extract every line item exactly as
printed on the page -- transcribe the invoice's own numbers, do not
recompute or correct anything even if something looks inconsistent.

Each line item carries two tax components. Read whatever label the invoice
actually prints for them (commonly CGST/SGST, but do not assume) rather
than guessing a fixed pair. taxable_value is the line's value before tax.
line_total is the line's tax-inclusive total as printed; if no such column
exists, compute it as taxable_value + tax_component_1_amount +
tax_component_2_amount.

Convert every date to ISO 8601 (YYYY-MM-DD). If an expiry date only prints
month/year, default the day to 01.
"""


def parse_pdf_vision(data: bytes, *, client: genai.Client | None = None) -> Bill:
    """Read a PDF tax invoice (Format D) with Gemini multimodal and return a
    normalized Bill."""
    client = client or genai.Client(vertexai=True, project=_PROJECT, location=_LOCATION)

    response = client.models.generate_content(
        model=_MODEL,
        contents=[
            types.Part.from_bytes(data=data, mime_type="application/pdf"),
            _PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BILL_SCHEMA,
        ),
    )

    payload = json.loads(response.text)
    return _build_bill(payload)


def _build_bill(payload: dict) -> Bill:
    vendor = payload["vendor"].strip()
    invoice_no = payload["invoice_no"].strip()
    invoice_date = payload["invoice_date"].strip()

    items = [
        LineItem(
            vendor=vendor,
            invoice_no=invoice_no,
            invoice_date=invoice_date,
            item_name=row["item_name"].strip(),
            batch_no=row["batch_no"].strip(),
            expiry_date=row["expiry_date"].strip(),
            quantity=float(row["quantity"]),
            rate=float(row["rate"]),
            discount=float(row["discount"]),
            taxable_value=round(float(row["taxable_value"]), 2),
            tax_component_1_label=row["tax_component_1_label"].strip(),
            tax_component_1_rate=float(row["tax_component_1_rate"]),
            tax_component_1_amount=round(float(row["tax_component_1_amount"]), 2),
            tax_component_2_label=row["tax_component_2_label"].strip(),
            tax_component_2_rate=float(row["tax_component_2_rate"]),
            tax_component_2_amount=round(float(row["tax_component_2_amount"]), 2),
            mrp=float(row["mrp"]),
            line_total=round(float(row["line_total"]), 2),
            hsn_code=row["hsn_code"].strip(),
            source_format="format_d",
        )
        for row in payload["line_items"]
    ]

    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        source_format="format_d",
        line_items=items,
        total_amount=round(float(payload["total_amount"]), 2),
    )
