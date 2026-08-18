"""parse_csv tool (PRD S7.2 / T16) -- both confirmed real dialects.

Format A: the ~79-column pharma-distribution ERP export (one line item per
row, CGST/SGST percentage columns, no per-line amount column).

Format B: H/D/F row-type CSV (one header/meta row per H, one line item per
D, one footer per F), VAT/TS tax naming.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field


def parse_format_a_csv(data: bytes) -> list[dict[str, str]]:
    """Return the raw rows of a Format A CSV as column-name -> value dicts."""
    text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader]


@dataclass
class FormatBParsed:
    vendor: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    bill_amount: str = ""
    rows: list[dict[str, str]] = field(default_factory=list)


# The D-row column order, taken from the Format B header row itself
# (confirmed identical across the sample set).
_FORMAT_B_D_COLUMNS = [
    "type", "code", "name", "packing", "quantity", "free", "selling_rate",
    "mrp", "batch_no", "exp_date", "discount", "vat_pct", "vat_amt",
    "ts_pct", "ts_amt", "cess", "amount", "hsn", "ptr", "rack_no",
]


def parse_format_b_csv(data: bytes) -> FormatBParsed:
    text = data.decode("latin-1")
    result = FormatBParsed()
    for raw_fields in csv.reader(io.StringIO(text)):
        if not raw_fields or not "".join(raw_fields).strip():
            continue
        fields = [f.strip() for f in raw_fields]
        row_type = fields[0].strip().upper()
        if row_type == "TYPE":
            continue  # the D-row header/template line
        if row_type == "H":
            key = fields[1].strip().lower() if len(fields) > 1 else ""
            value = fields[2].strip() if len(fields) > 2 else ""
            if key == "supplier":
                result.vendor = value
            elif key == "inv.no.":
                result.invoice_no = value
            elif key == "inv.date":
                result.invoice_date = value
        elif row_type == "D":
            row = dict(zip(_FORMAT_B_D_COLUMNS, fields))
            result.rows.append(row)
        elif row_type == "F":
            if len(fields) > 1 and fields[1].strip().lower() == "bill amount":
                result.bill_amount = fields[2].strip() if len(fields) > 2 else ""
    return result
