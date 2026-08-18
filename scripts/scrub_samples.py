"""Sanitize real vendor bill samples before they touch the repo or a recording.

Reads every file in samples/ (real, unscrubbed, gitignored) and writes a
sanitized copy to samples_sanitized/ (committed, used by tests and demos).

Rules (PRD v3 S10):
  - Replace vendor/pharmacy names, GSTINs, PAN/TAN, drug-licence numbers,
    FSSAI numbers, CIN, bank account/IFSC, phone/mobile numbers, and
    addresses with synthetic values.
  - Preserve invoice-number shape and every price, quantity, batch number,
    and date EXACTLY. Never touch those fields.

Findings from inspecting the real sample set (see conversation history):
  - The 79-column ERP CSV/XLS export (Format A/C) carries no vendor/pharmacy
    PII at all -- no name, address, phone, or GSTIN column exists in that
    schema, confirmed by scanning every cell of every sample. Those files
    are copied through unchanged.
  - The H/D/F CSV (Format B) carries exactly one PII field: the supplier
    name on the H,Supplier row.
  - The PDFs carry the real pharmacy's and vendors' full identity block
    (name, address, phone, email, GSTIN/PAN/TAN, drug licence, FSSAI, CIN,
    bank details, salesman name) and need full redaction.

Re-run this script any time samples/ changes; it always regenerates
samples_sanitized/ from scratch.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import fitz  # pymupdf

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "samples"
OUT_DIR = ROOT / "samples_sanitized"

# Ordered longest/most-specific first, so a shorter string (e.g. a bare PAN)
# never partially matches inside a longer one (e.g. the GSTIN containing
# that PAN) before the longer one has been replaced.
PDF_REDACTIONS: list[tuple[str, str]] = [
    # --- Buyer pharmacy (shared across every vendor's documents) ---
    ("AKCDA CODE NO ENP-000, RIVERDALE, RIVERDALE, ERNAKULAM -683599, KERALA-32",
     "AKCDA CODE NO ENP-000, RIVERDALE, RIVERDALE, ERNAKULAM -683599, KERALA-32"),
    ("PARAVOOR,RIVERDALE", "PARAVOOR,RIVERDALE"),
    ("RIVERDALERiverdale", "RIVERDALERiverdale"),
    ("RIVERDALE", "RIVERDALE"),
    ("MERIDIAN MEDICALS", "MERIDIAN MEDICALS"),
    ("Pin:683199", "Pin:683199"),
    ("Phone:2411111", "Phone:2411111"),
    ("St No:32200000000", "St No:32200000000"),
    ("1-11/11,1-12/11/11", "1-11/11,1-12/11/11"),
    ("AAAAA0000A", "AAAAA0000A"),
    ("9000011111", "9000011111"),
    ("118400,\n118500", "100000,\n100001"),
    ("100000, 100001", "100000, 100001"),

    # --- Vendor: Northfield Associates (the four .xls-twin PDFs) ---
    ("NORTHFIELD ASSOCIATES", "NORTHFIELD ASSOCIATES"),
    ("19/000-0,DEMO ARCADE, SAMPLE LANE ROAD, BAZAR P O", "19/000-0,DEMO ARCADE, SAMPLE LANE ROAD, BAZAR P O"),
    ("Phone No.: 0000-0000000, 0000000", "Phone No.: 0000-0000000, 0000000"),
    ("0000-0000000, 0000000", "0000-0000000, 0000000"),
    ("orders@example-distributors.com", "orders@example-distributors.com"),
    ("32AAAAA0000A1Z0", "32AAAAA0000A1Z0"),
    ("AAAAA0000A", "AAAAA0000A"),
    ("AAAA00000A", "AAAA00000A"),
    ("10000000000000", "10000000000000"),
    ("1.KL-EKM-0-000/00B/0000", "1.KL-EKM-0-000/00B/0000"),
    ("2.KL-EKM-0-000/00B/0000", "2.KL-EKM-0-000/00B/0000"),
    ("KL-EKM-0-000/00B/0000", "KL-EKM-0-000/00B/0000"),
    ("KL-EKM-0-000/00B/0000", "KL-EKM-0-000/00B/0000"),
    ("JOHN D.", "JOHN D."),
    ("9000022222", "9000022222"),
    ("SAMPLE BANK", "SAMPLE BANK"),
    ("00000000000000", "00000000000000"),
    ("SAMP0000000", "SAMP0000000"),
    ("GREENVILLE", "GREENVILLE"),

    # --- Vendor: Harbor Medicare Solutions (the standalone GSPL PDF) ---
    ("HARBOR MEDICARE SOLUTIONS PVT. LTD.", "HARBOR MEDICARE SOLUTIONS PVT. LTD."),
    ("00/0000 A, B, B1, C, C1, D SAMPLE ESTATE, NEAR DEMO PRESS,DEMO ROAD, , ERNAKULAM, KOCHI-682099 KERALA",
     "00/0000 A, B, B1, C, C1, D SAMPLE ESTATE, NEAR DEMO PRESS,DEMO ROAD, , ERNAKULAM, KOCHI-682099 KERALA"),
    # The PDF wraps this address across two lines with inconsistent spacing
    # in different blocks, so the combined string above doesn't always match
    # -- these shorter same-line fragments catch the rest.
    ("00/0000 A, B, B1, C, C1, D", "00/0000 A, B, B1, C, C1, D"),
    ("SAMPLE ESTATE", "SAMPLE ESTATE"),
    ("DEMO PRESS", "DEMO PRESS"),
    ("DEMO ROAD", "DEMO ROAD"),
    ("KOCHI-682099  KERALA", "KOCHI-682099  KERALA"),
    ("KOCHI-682099 KERALA", "KOCHI-682099 KERALA"),
    ("0000000000, 0000000000", "0000000000, 0000000000"),
    ("CONTACT@EXAMPLE-MEDICARE.COM", "CONTACT@EXAMPLE-MEDICARE.COM"),
    ("32AAAAA0000B1Z0", "32AAAAA0000B1Z0"),
    ("AAAAA0000B", "AAAAA0000B"),
    ("WLF00B0000KL000000, WLF00B0000KL000001", "WLF00B0000KL000000, WLF00B0000KL000001"),
    ("10000000000001", "10000000000001"),
    ("U00000KL0000PTC000000", "U00000KL0000PTC000000"),
    ("SAMPLE BANK TWO", "SAMPLE BANK TWO"),
    ("000000000001", "000000000001"),
    ("SAMP0000001", "SAMP0000001"),
    ("ARUN K.", "ARUN K."),
    ("MERIDIAN MEDICALS(018018)", "MERIDIAN MEDICALS(018018)"),
]

# Format B (H/D/F) CSV -- plain literal text replacement, one field.
CSV_TEXT_REPLACEMENTS: list[tuple[str, str]] = [
    ("SUMMIT PHARMA", "SUMMIT PHARMA"),
]

# Files confirmed (by scanning every cell) to carry no vendor/pharmacy PII
# in their schema -- copied through byte-for-byte unchanged.
NO_PII_SUFFIXES = (".xls",)


def scrub_pdf(src: Path, dst: Path) -> None:
    doc = fitz.open(src)
    for page in doc:
        pending: list[tuple[fitz.Rect, str]] = []
        for old, new in PDF_REDACTIONS:
            for rect in page.search_for(old):
                page.add_redact_annot(rect, fill=(1, 1, 1))
                pending.append((rect, new))
        page.apply_redactions()
        for rect, new in pending:
            fontsize = max(4.0, min(9.0, rect.height * 0.75))
            page.insert_textbox(
                rect, new, fontsize=fontsize, fontname="helv", color=(0, 0, 0)
            )
    doc.save(dst)
    doc.close()


def scrub_csv(src: Path, dst: Path) -> None:
    # Byte-level replace (not text mode) so any file with nothing to replace
    # comes out truly byte-for-byte identical -- no newline-style drift.
    data = src.read_bytes()
    for old, new in CSV_TEXT_REPLACEMENTS:
        data = data.replace(old.encode("latin-1"), new.encode("latin-1"))
    dst.write_bytes(data)


def scrub_passthrough(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)


def main() -> None:
    if not SRC_DIR.is_dir():
        raise SystemExit(f"no {SRC_DIR} directory -- nothing to scrub")
    OUT_DIR.mkdir(exist_ok=True)
    for f in sorted(SRC_DIR.iterdir()):
        if not f.is_file():
            continue
        dst = OUT_DIR / f.name
        suffix = f.suffix.lower()
        if suffix == ".pdf":
            scrub_pdf(f, dst)
        elif suffix in (".csv",):
            scrub_csv(f, dst)
        elif suffix in NO_PII_SUFFIXES:
            scrub_passthrough(f, dst)
        else:
            scrub_passthrough(f, dst)
        print(f"scrubbed {f.name} -> {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
