"""detect_format tool (PRD S7.2 / T15).

Inspects file BYTES, never the extension/filename, and returns which parser
should handle the file. Confirmed against the full real sample set (13
files): every `.xls` in the sample set is a raw BIFF2 stream (not an OLE2
compound container, not corrupt) -- `pandas.read_excel(engine="openpyxl")`
fails on it with BadZipFile because openpyxl only reads the zip-based
`.xlsx` container. Routing on the BIFF2 magic bytes below and reading with
`engine="xlrd"` is what actually works (see formats/parse_xls.py).
"""
from __future__ import annotations

# BIFF2 BOF record: opcode 0x0009, record length 0x0004, BIFF version 0x0002.
# `file(1)` identifies these samples as "Excel 2 BIFF 2 Sheet"; this is the
# same magic sequence, checked directly rather than shelling out to `file`.
_BIFF2_MAGIC = b"\x09\x00\x04\x00\x02\x00"

_PDF_MAGIC = b"%PDF"

FORMAT_A_CSV = "format_a_csv"
FORMAT_B_CSV = "format_b_csv"
FORMAT_C_XLS = "format_c_xls"
FORMAT_D_PDF = "format_d_pdf"
UNKNOWN = "unknown"


def detect_format(data: bytes) -> str:
    if data.startswith(_PDF_MAGIC):
        return FORMAT_D_PDF
    if data[:6] == _BIFF2_MAGIC:
        return FORMAT_C_XLS

    # Not a known binary container -- try decoding as delimited text and
    # sniff the header/row-type structure, since Format A and Format B are
    # both plain CSV but carry incompatible schemas.
    try:
        text = data.decode("latin-1")
    except UnicodeDecodeError:
        return UNKNOWN

    first_line = text.split("\n", 1)[0].strip().lower()
    if first_line.startswith("c2code,br,yr,pfx,invno"):
        return FORMAT_A_CSV
    if first_line.startswith("type,") and "batch no." in first_line and "mrp" in first_line:
        return FORMAT_B_CSV

    return UNKNOWN


def detect_format_from_path(path: str) -> str:
    with open(path, "rb") as f:
        return detect_format(f.read())
