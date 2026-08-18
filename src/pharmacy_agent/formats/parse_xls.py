"""parse_xls tool (PRD S7.2 / T17).

Format C is a raw BIFF2 (Excel 2.0-era) stream carrying the exact same
~79-column schema as Format A CSV, from the same ERP. `detect_format`
routes any file starting with the BIFF2 BOF magic bytes here.

`pandas.read_excel()` with no engine specified, or with `engine="openpyxl"`
(the natural next guess), both fail on these files -- openpyxl only reads
the zip-based `.xlsx` container. `engine="xlrd"` is what actually works;
confirmed 5/5 on the real sample set.
"""
from __future__ import annotations

import io

import pandas as pd


def parse_format_c_xls(data: bytes) -> list[dict[str, str]]:
    """Return the raw rows of a Format C XLS as column-name -> value dicts."""
    df = pd.read_excel(io.BytesIO(data), engine="xlrd", header=0, dtype=str)
    df = df.fillna("")
    return df.to_dict(orient="records")
