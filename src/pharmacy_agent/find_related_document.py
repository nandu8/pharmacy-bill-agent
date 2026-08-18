"""find_related_document (PRD S7.2/S7.3 / T35): given a target invoice
number, search a set of candidate documents already available to this run
(other attachments from the same email, or other files already staged for
this vendor -- Drive staging isn't wired yet, so callers supply whatever
candidates ingestion context has) for the one that is genuinely the SAME
invoice arriving in a DIFFERENT container. Feeds two callers: the
corrupted-file recovery ladder (S7.3 step 3 -- current file failed every
known parse route, is a readable twin among the candidates?) and
reconciliation (T36) (same invoice number, conflicting content -- is the
vendor's own authoritative PDF among the candidates?).

Reuses the existing format detector and per-format parsers/normalizer --
this module adds no new parsing logic, just tries each candidate with the
route detect_format already picks and compares the resulting invoice_no.

Matching is by digit suffix, not exact string equality: normalize.py
(S7.3/T19) already found that Format A/C's assembled invoice number
("I152516", pfx+invno) doesn't carry the full branch/series prefix the
vendor's own PDF prints ("260027300152516") -- only the numeric tail is
common to both. Comparing digit-only suffixes is what actually lines them
up; a bare exact-equality check would silently fail to find the twin the
recovery ladder and reconciliation case both depend on.
"""
from __future__ import annotations

import dataclasses
import re

from . import normalize
from .formats import detect as detect_mod
from .formats import parse_csv as parse_csv_mod
from .formats import parse_pdf_vision as parse_pdf_vision_mod
from .formats import parse_xls as parse_xls_mod
from .formats.schema import Bill

_DIGITS_RE = re.compile(r"\D+")
# Below this many digits, a suffix match is more likely coincidence than a
# genuine shared invoice number -- real samples carry 5+ digit tails.
_MIN_SUFFIX_DIGITS = 4


@dataclasses.dataclass
class RelatedDocumentMatch:
    filename: str
    detected_format: str
    bill: Bill


def _digits(invoice_no: str) -> str:
    return _DIGITS_RE.sub("", invoice_no)


def invoice_numbers_match(a: str, b: str) -> bool:
    if a == b:
        return True
    da, db = _digits(a), _digits(b)
    shorter, longer = (da, db) if len(da) <= len(db) else (db, da)
    if len(shorter) < _MIN_SUFFIX_DIGITS:
        return False
    return longer.endswith(shorter)


def _parse_candidate(fmt: str, data: bytes, vendor: str) -> Bill | None:
    if fmt == detect_mod.FORMAT_A_CSV:
        rows = parse_csv_mod.parse_format_a_csv(data)
        return normalize.build_bill_from_format_a_rows(rows, vendor=vendor)
    if fmt == detect_mod.FORMAT_B_CSV:
        parsed = parse_csv_mod.parse_format_b_csv(data)
        return normalize.build_bill_from_format_b(parsed)
    if fmt == detect_mod.FORMAT_C_XLS:
        rows = parse_xls_mod.parse_format_c_xls(data)
        return normalize.build_bill_from_format_a_rows(rows, vendor=vendor, source_format="format_c")
    if fmt == detect_mod.FORMAT_D_PDF:
        return parse_pdf_vision_mod.parse_pdf_vision(data)
    return None


def find_related_document(
    invoice_no: str,
    vendor: str,
    candidates: list[tuple[str, bytes]],
    exclude_format: str | None = None,
) -> RelatedDocumentMatch | None:
    """Search `candidates` (filename, raw bytes pairs) for a document that
    parses to the same `invoice_no`, in a container other than
    `exclude_format` (the format already tried, or already in hand). Returns
    the first match, or None if nothing among the candidates both parses and
    genuinely matches. A candidate that fails to parse (unreadable,
    unrelated content) is skipped rather than raised, since most candidates
    are expected not to match."""
    for filename, data in candidates:
        fmt = detect_mod.detect_format(data)
        if fmt == detect_mod.UNKNOWN or fmt == exclude_format:
            continue
        try:
            bill = _parse_candidate(fmt, data, vendor)
        except Exception:
            continue
        if bill is not None and invoice_numbers_match(bill.invoice_no, invoice_no):
            return RelatedDocumentMatch(filename=filename, detected_format=fmt, bill=bill)
    return None
