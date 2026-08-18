"""Date parsing for the handful of literal formats seen across real samples.

Confirmed from the sample set:
  - Format A CSV invdate/expdate: "16-08-2026" (DD-MM-YYYY)
  - Format C XLS  invdate/expdate: "12/08/26"   (DD/MM/YY)
  - Format B CSV  Inv.Date:        "15/08/2026" (DD/MM/YYYY)
  - Format B CSV  Exp. Date:       "12/28"      (MM/YY, day not carried)
No format in the sample set is ambiguous with another (year length and
separator together disambiguate), so a fixed list of patterns is tried in
order rather than a heuristic guesser.
"""
from __future__ import annotations

from datetime import datetime

_DAY_FIRST_PATTERNS = ("%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%y")
_MONTH_YEAR_PATTERNS = ("%m/%y", "%m-%y")


def parse_date(raw: str) -> str:
    """Return an ISO 8601 (YYYY-MM-DD) date string, or "" if unparseable."""
    text = (raw or "").strip()
    if not text:
        return ""
    for pattern in _DAY_FIRST_PATTERNS:
        try:
            return _strptime(text, pattern)
        except ValueError:
            continue
    for pattern in _MONTH_YEAR_PATTERNS:
        try:
            return _strptime(text, pattern)
        except ValueError:
            continue
    return ""


def _strptime(text: str, pattern: str) -> str:
    return datetime.strptime(text, pattern).date().isoformat()
