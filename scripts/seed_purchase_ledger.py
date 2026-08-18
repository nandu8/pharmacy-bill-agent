"""Seed script (PRD S10 / T25): populate purchase_ledger with the real
sample invoices, and separately with clearly-labelled synthetic price
history for price-drift dev/demo work.

Two independent entry points:

- seed_real_samples: parses every clean Format B/C sample in
  samples_sanitized/ plus the authoritative version of the Format A
  PH-26-49832 pair, and records them via record_purchase with seeded=False.
  PDFs are skipped -- they are twins of a CSV/XLS already in this set (same
  invoice, same line items), so re-reading them would mean a live Gemini
  call for data already captured deterministically. The 14-item/3268.00
  PH-26-49832 twin is skipped too: it's the same invoice number as the
  13-item/2959.00 version with different content, reserved for the T36
  reconciliation demo rather than being clean price history.

- seed_synthetic_price_history: writes a run of synthetic prior invoices for
  one vendor+item (not present in any real sample, so it can't collide with
  observed data) at a gently rising rate across recent months, all marked
  seeded=True. PRD S10: "any synthesized history beyond what the samples
  contain is labelled as seeded ... rather than presented as observed."

Safe to re-run: record_purchase's doc IDs are deterministic hashes of
vendor+invoice_no+item_name+batch_no, so running this script twice
overwrites the same docs rather than duplicating them.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from google.cloud import firestore  # noqa: E402

from pharmacy_agent.firestore_client import get_client  # noqa: E402
from pharmacy_agent.formats.parse_csv import parse_format_a_csv, parse_format_b_csv  # noqa: E402
from pharmacy_agent.formats.parse_xls import parse_format_c_xls  # noqa: E402
from pharmacy_agent.formats.schema import Bill, LineItem  # noqa: E402
from pharmacy_agent.normalize import build_bill_from_format_a_rows, build_bill_from_format_b  # noqa: E402
from pharmacy_agent.purchase_ledger import record_purchase  # noqa: E402
from pharmacy_agent.validate import validate_bill  # noqa: E402

SAMPLES_DIR = ROOT / "samples_sanitized"

# Vendor names for Format A/C are supplied by ingestion context (Gmail
# sender / vendor folder) -- these files carry no vendor field at all (see
# normalize.py). Same convention as tests/test_parse_and_normalize.py.
FORMAT_C_VENDOR = "Northfield Associates"
FORMAT_A_VENDOR = "Harbor Medicare Solutions"
FORMAT_B_SAMPLE_VENDOR = "SUMMIT PHARMA"  # carried in the Format B file itself

# The 13-item, 2959.00 version -- confirmed authoritative against the
# vendor's own PDF (S7.3/S7.4). Its 14-item/3268.00 twin is the T36
# reconciliation demo's raw material, not clean ledger history.
_FORMAT_A_AUTHORITATIVE_FILE = "PH-26-49832_16-Aug-26_172026215756652.csv"


def _real_bills() -> list[Bill]:
    bills: list[Bill] = []

    for f in sorted(SAMPLES_DIR.glob("*.xls")):
        rows = parse_format_c_xls(f.read_bytes())
        bills.append(build_bill_from_format_a_rows(rows, vendor=FORMAT_C_VENDOR, source_format="format_c"))

    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    bills.append(build_bill_from_format_b(parse_format_b_csv(data)))

    data = (SAMPLES_DIR / _FORMAT_A_AUTHORITATIVE_FILE).read_bytes()
    rows = parse_format_a_csv(data)
    bills.append(build_bill_from_format_a_rows(rows, vendor=FORMAT_A_VENDOR))

    return bills


def seed_real_samples(client: firestore.Client | None = None) -> int:
    client = client or get_client()
    doc_count = 0
    for bill in _real_bills():
        issues = validate_bill(bill)
        if issues:
            raise ValueError(f"sample {bill.vendor}/{bill.invoice_no} failed validation: {issues}")
        doc_count += len(record_purchase(bill, client=client, seeded=False))
    return doc_count


# Synthetic price history for a vendor+item combination that appears in no
# real sample (so its doc IDs can never collide with observed data). Rate
# rises gently across four prior months -- enough prior invoices to satisfy
# the dispute-gating floor (PRD S7.5: >=3 prior invoices) once a demo bill
# with a real deviation is layered on top in T59.
SYNTHETIC_VENDOR = "SUMMIT PHARMA"
_SYNTHETIC_ITEM = "AMLODIPINE 5MG"
_SYNTHETIC_BATCH = "AMLO-SYN"
_SYNTHETIC_EXPIRY = "2028-01-01"
_SYNTHETIC_HSN = "3004"
_SYNTHETIC_MRP = 30.0

SYNTHETIC_HISTORY = [
    ("SYN-AMLO-001", date(2026, 5, 15), 20.00),
    ("SYN-AMLO-002", date(2026, 6, 15), 20.50),
    ("SYN-AMLO-003", date(2026, 7, 15), 21.00),
    ("SYN-AMLO-004", date(2026, 8, 1), 21.20),
]


def _synthetic_bill(invoice_no: str, purchase_date: date, rate: float) -> Bill:
    taxable_value = round(rate, 2)
    tax1 = round(taxable_value * 0.06, 2)
    tax2 = round(taxable_value * 0.06, 2)
    item = LineItem(
        vendor=SYNTHETIC_VENDOR,
        invoice_no=invoice_no,
        invoice_date=purchase_date.isoformat(),
        item_name=_SYNTHETIC_ITEM,
        batch_no=_SYNTHETIC_BATCH,
        expiry_date=_SYNTHETIC_EXPIRY,
        quantity=1.0,
        rate=rate,
        discount=0.0,
        taxable_value=taxable_value,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=tax1,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=tax2,
        mrp=_SYNTHETIC_MRP,
        line_total=round(taxable_value + tax1 + tax2, 2),
        hsn_code=_SYNTHETIC_HSN,
        source_format="synthetic",
    )
    return Bill(
        vendor=SYNTHETIC_VENDOR,
        invoice_no=invoice_no,
        invoice_date=purchase_date.isoformat(),
        source_format="synthetic",
        line_items=[item],
        total_amount=item.line_total,
    )


def seed_synthetic_price_history(client: firestore.Client | None = None) -> int:
    client = client or get_client()
    doc_count = 0
    for invoice_no, purchase_date, rate in SYNTHETIC_HISTORY:
        bill = _synthetic_bill(invoice_no, purchase_date, rate)
        doc_count += len(record_purchase(bill, client=client, seeded=True))
    return doc_count


def main() -> None:
    client = get_client()
    real_count = seed_real_samples(client=client)
    print(f"seeded {real_count} real purchase_ledger docs (seeded=False)")
    synthetic_count = seed_synthetic_price_history(client=client)
    print(f"seeded {synthetic_count} synthetic purchase_ledger docs (seeded=True)")


if __name__ == "__main__":
    main()
