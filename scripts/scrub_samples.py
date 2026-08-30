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

The actual redaction mapping (PDF_REDACTIONS, CSV_TEXT_REPLACEMENTS,
FILENAME_REDACTIONS) lives in scripts/scrub_mapping.local.py, which is
gitignored -- it necessarily contains the real PII values as the "find"
side of each pair, so it must never be committed (this was gotten wrong for
a while; the real values were hardcoded directly in this file and pushed
publicly until it was caught and the history rewritten -- see T41).
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import fitz  # pymupdf

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "samples"
OUT_DIR = ROOT / "samples_sanitized"
MAPPING_PATH = ROOT / "scripts" / "scrub_mapping.local.py"

if not MAPPING_PATH.exists():
    raise SystemExit(
        f"Missing {MAPPING_PATH} -- gitignored, holds the real PII values "
        "being redacted, and must exist locally to run this script. "
        "Recreate it from the real samples/ files if lost; never commit it."
    )
# A dotted filename (scrub_mapping.local.py) isn't a valid plain `import`
# target, so load it directly by path instead.
_spec = importlib.util.spec_from_file_location("_scrub_mapping_local", MAPPING_PATH)
_mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mapping)
CSV_TEXT_REPLACEMENTS = _mapping.CSV_TEXT_REPLACEMENTS
FILENAME_REDACTIONS = _mapping.FILENAME_REDACTIONS
PDF_REDACTIONS = _mapping.PDF_REDACTIONS

# Files confirmed (by scanning every cell) to carry no vendor/pharmacy PII
# in their schema -- copied through byte-for-byte unchanged.
NO_PII_SUFFIXES = (".xls",)


def scrub_filename(name: str) -> str:
    for old, new in FILENAME_REDACTIONS:
        name = name.replace(old, new)
    return name


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
            # insert_textbox wraps on spaces; a narrow rect can force a
            # multi-word replacement onto more lines than the (single-line)
            # rect is tall enough for, in which case it silently draws
            # nothing at all and returns a negative "shortage" value.
            # Shrink until it actually fits rather than lose the
            # replacement text.
            rc = page.insert_textbox(
                rect, new, fontsize=fontsize, fontname="helv", color=(0, 0, 0)
            )
            while rc < 0 and fontsize > 3.0:
                fontsize -= 0.5
                rc = page.insert_textbox(
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
        dst = OUT_DIR / scrub_filename(f.name)
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
