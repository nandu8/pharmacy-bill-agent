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
    ("AKCDA CODE NO ENP-629, MOOTHAKUNNAM, MOOTHAKUNNAM, ERNAKULAM -683516, KERALA-32",
     "AKCDA CODE NO ENP-000, RIVERDALE, RIVERDALE, ERNAKULAM -683599, KERALA-32"),
    ("PARAVOOR,MOOTHAKUNNAM", "PARAVOOR,RIVERDALE"),
    ("MOOTHAKUNNAMMoothakunnam", "RIVERDALERiverdale"),
    ("MOOTHAKUNNAM", "RIVERDALE"),
    ("KALAVAMPARA MEDICALS", "MERIDIAN MEDICALS"),
    ("Pin:683101", "Pin:683199"),
    ("Phone:2482263", "Phone:2411111"),
    ("St No:32205581509", "St No:32200000000"),
    ("7-29/20,7-30/21/98", "1-11/11,1-12/11/11"),
    ("BWLPS6154H", "AAAAA0000A"),
    ("8547284947", "9000011111"),
    ("118400,\n118500", "100000,\n100001"),
    ("118400, 118500", "100000, 100001"),

    # --- Vendor: Bruklyn Associates (the four .xls-twin PDFs) ---
    ("BRUKLYN ASSOCIATES", "NORTHFIELD ASSOCIATES"),
    ("19/685-4,ABN ARCADE, GREAT LANE ROAD, BAZAR P O", "19/000-0,DEMO ARCADE, SAMPLE LANE ROAD, BAZAR P O"),
    ("Phone No.: 0484-2630444, 6657444", "Phone No.: 0000-0000000, 0000000"),
    ("0484-2630444, 6657444", "0000-0000000, 0000000"),
    ("orders@abnbusinessgroup.com", "orders@example-distributors.com"),
    ("32AACFB1927D1Z6", "32AAAAA0000A1Z0"),
    ("AACFB1927D", "AAAAA0000A"),
    ("CHNB01658G", "AAAA00000A"),
    ("21318181000596", "10000000000000"),
    ("1.KL-EKM-7-590/20B/2011", "1.KL-EKM-0-000/00B/0000"),
    ("2.KL-EKM-7-591/21B/2011", "2.KL-EKM-0-000/00B/0000"),
    ("KL-EKM-7-590/20B/2011", "KL-EKM-0-000/00B/0000"),
    ("KL-EKM-7-591/21B/2011", "KL-EKM-0-000/00B/0000"),
    ("SEBASTIAN T.F", "JOHN D."),
    ("9847027652", "9000022222"),
    ("FEDERAL BANK", "SAMPLE BANK"),
    ("10015500006289", "00000000000000"),
    ("FDRL0001001", "SAMP0000000"),
    ("ALUVA", "GREENVILLE"),

    # --- Vendor: Getwell Medicare Solution (the standalone GSPL PDF) ---
    ("GETWELL MEDICARE SOLUTION PVT. LTD.", "HARBOR MEDICARE SOLUTIONS PVT. LTD."),
    ("66/1956 A, B, B1, C, C1, D GETWELL ESTATE, NEAR VEEKSHANAM PRESS,VEEKSHANAM ROAD, , ERNAKULAM, KOCHI-682018 KERALA",
     "00/0000 A, B, B1, C, C1, D SAMPLE ESTATE, NEAR DEMO PRESS,DEMO ROAD, , ERNAKULAM, KOCHI-682099 KERALA"),
    # The PDF wraps this address across two lines with inconsistent spacing
    # in different blocks, so the combined string above doesn't always match
    # -- these shorter same-line fragments catch the rest.
    ("66/1956 A, B, B1, C, C1, D", "00/0000 A, B, B1, C, C1, D"),
    ("GETWELL ESTATE", "SAMPLE ESTATE"),
    ("VEEKSHANAM PRESS", "DEMO PRESS"),
    ("VEEKSHANAM ROAD", "DEMO ROAD"),
    ("KOCHI-682018  KERALA", "KOCHI-682099  KERALA"),
    ("KOCHI-682018 KERALA", "KOCHI-682099 KERALA"),
    ("4842352288, 8593060708", "0000000000, 0000000000"),
    ("GWMC@GETWELLENTERPRISES.COM", "CONTACT@EXAMPLE-MEDICARE.COM"),
    ("32AAHCG5616N1ZF", "32AAAAA0000B1Z0"),
    ("AAHCG5616N", "AAAAA0000B"),
    ("WLF20B2025KL000017, WLF21B2025KL000026", "WLF00B0000KL000000, WLF00B0000KL000001"),
    ("11317007000691", "10000000000001"),
    ("U5310KL2018PTC055604", "U00000KL0000PTC000000"),
    ("INDUSIND BANK", "SAMPLE BANK TWO"),
    ("650014123253", "000000000001"),
    ("INDB0000010", "SAMP0000001"),
    ("PRAKASH V.J", "ARUN K."),
    ("KALAVAMPARA MEDICALS(018018)", "MERIDIAN MEDICALS(018018)"),
]

# Format B (H/D/F) CSV -- plain literal text replacement, one field.
CSV_TEXT_REPLACEMENTS: list[tuple[str, str]] = [
    ("STERLING PHARMA", "SUMMIT PHARMA"),
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
