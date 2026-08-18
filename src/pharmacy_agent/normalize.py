"""Normalizer (PRD S7.2 / T19): map every vendor format onto the unified
schema in formats/schema.py, including the generic tax_component_1/2
label/rate/amount fields -- never hardcode CGST/SGST, since Format B uses
VAT/TS for the same two slots.
"""
from __future__ import annotations

from .formats.dates import parse_date
from .formats.parse_csv import FormatBParsed
from .formats.schema import Bill, LineItem


def _num(value: str | None) -> float:
    text = (value or "").strip()
    if not text:
        return 0.0
    return float(text)


def _assemble_invoice_no(pfx: str, invno: str) -> str:
    """Format A/C split the invoice number across `pfx` and `invno`.

    Confirmed from real samples: sometimes `invno` already carries the
    prefix as a substring (Format A CSV: pfx="PH", invno="PH-26-49832" --
    used as-is), and sometimes it's genuinely just the numeric tail
    (Format C XLS: pfx="I", invno="152516", while the number printed on
    the vendor's own PDF is "260027300152516" -- a longer string this CSV
    schema does not fully carry). In the latter case we concatenate pfx+
    invno as the ledger key; reconciliation against a PDF twin (S7.3) must
    match by suffix, not exact equality, since the full branch/series
    prefix isn't present in this export.
    """
    pfx = (pfx or "").strip()
    invno = (invno or "").strip()
    if not pfx or invno.startswith(pfx):
        return invno
    return f"{pfx}{invno}"


def normalize_format_a_row(row: dict[str, str], vendor: str, source_format: str = "format_a") -> LineItem:
    """Normalize one raw row from Format A CSV or Format C XLS.

    `vendor` is not present anywhere in this 79-column schema (confirmed
    by scanning every real sample) -- it comes from ingestion context
    (the Gmail sender / vendor folder), same as the real pipeline would
    supply it, not from the file content.
    """
    qty = _num(row.get("invqty"))
    rate = _num(row.get("salerate"))
    discount = _num(row.get("invdisc"))
    taxable_value = round(qty * rate - discount, 2)

    cgst_rate = _num(row.get("cgstper"))
    sgst_rate = _num(row.get("sgstper"))
    tax1_amount = round(taxable_value * cgst_rate / 100, 2)
    tax2_amount = round(taxable_value * sgst_rate / 100, 2)
    line_total = round(taxable_value + tax1_amount + tax2_amount, 2)

    return LineItem(
        vendor=vendor,
        invoice_no=_assemble_invoice_no(row.get("pfx", ""), row.get("invno", "")),
        invoice_date=parse_date(row.get("invdate", "")),
        item_name=(row.get("itemname") or "").strip(),
        batch_no=(row.get("batchno") or "").strip(),
        expiry_date=parse_date(row.get("expdate", "")),
        quantity=qty,
        rate=rate,
        discount=discount,
        taxable_value=taxable_value,
        tax_component_1_label="CGST",
        tax_component_1_rate=cgst_rate,
        tax_component_1_amount=tax1_amount,
        tax_component_2_label="SGST",
        tax_component_2_rate=sgst_rate,
        tax_component_2_amount=tax2_amount,
        mrp=_num(row.get("itemmrp")),
        line_total=line_total,
        hsn_code=(row.get("hsnsaccode") or "").strip(),
        source_format=source_format,
    )


def build_bill_from_format_a_rows(rows: list[dict[str, str]], vendor: str, source_format: str = "format_a") -> Bill:
    if not rows:
        raise ValueError("no rows to build a bill from")
    items = [normalize_format_a_row(r, vendor, source_format) for r in rows]
    first = items[0]
    return Bill(
        vendor=vendor,
        invoice_no=first.invoice_no,
        invoice_date=first.invoice_date,
        source_format=source_format,
        line_items=items,
        total_amount=_num(rows[0].get("invamt")),
    )


def normalize_format_b_row(row: dict[str, str], vendor: str, invoice_no: str, invoice_date_iso: str) -> LineItem:
    """Normalize one D-row from Format B CSV.

    Unlike Format A, Format B gives explicit per-line tax amounts (VAT Amt,
    TS Amt) rather than only rates -- taken as given, not recomputed. Its
    "Amount" column is confirmed (against the real sample: 30 x 3.92 =
    117.60 = the printed Amount, while VAT Amt 5.88 is additional) to be
    the *taxable* value, not the tax-inclusive line total, so `line_total`
    is still computed generically rather than read from that column.
    """
    qty = _num(row.get("quantity"))
    rate = _num(row.get("selling_rate"))
    discount = _num(row.get("discount"))
    taxable_value = round(qty * rate - discount, 2)

    tax1_rate = _num(row.get("vat_pct"))
    tax1_amount = _num(row.get("vat_amt"))
    tax2_rate = _num(row.get("ts_pct"))
    tax2_amount = _num(row.get("ts_amt"))
    line_total = round(taxable_value + tax1_amount + tax2_amount, 2)

    return LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=invoice_date_iso,
        item_name=(row.get("name") or "").strip(),
        batch_no=(row.get("batch_no") or "").strip(),
        expiry_date=parse_date(row.get("exp_date", "")),
        quantity=qty,
        rate=rate,
        discount=discount,
        taxable_value=taxable_value,
        tax_component_1_label="VAT",
        tax_component_1_rate=tax1_rate,
        tax_component_1_amount=tax1_amount,
        tax_component_2_label="TS",
        tax_component_2_rate=tax2_rate,
        tax_component_2_amount=tax2_amount,
        mrp=_num(row.get("mrp")),
        line_total=line_total,
        hsn_code=(row.get("hsn") or "").strip(),
        source_format="format_b",
    )


def build_bill_from_format_b(parsed: FormatBParsed) -> Bill:
    invoice_date_iso = parse_date(parsed.invoice_date)
    items = [
        normalize_format_b_row(row, parsed.vendor, parsed.invoice_no, invoice_date_iso)
        for row in parsed.rows
    ]
    return Bill(
        vendor=parsed.vendor,
        invoice_no=parsed.invoice_no,
        invoice_date=invoice_date_iso,
        source_format="format_b",
        line_items=items,
        total_amount=_num(parsed.bill_amount),
    )
