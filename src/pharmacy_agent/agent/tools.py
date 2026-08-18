"""ADK toolbox (PRD S7.2 / T27): wraps the tool functions already built in
Phase 3/4 (detect_format, parse_csv, parse_xls, parse_pdf_vision,
lookup_vendor_history, check_duplicate, record_purchase) as ADK function
tools. The remaining PRD S7.2 tools -- find_related_document,
cross_check_other_vendors, stage_file, email_vendor, ask_pharmacist,
notify_pharmacist -- depend on infrastructure from later phases (6/7/9) and
aren't wired here.

Each tool reads/writes the run's session state (`tool_context.state`)
instead of taking the file bytes or the parsed Bill as an LLM-visible
argument: bytes isn't a JSON-schema type function-calling can represent, and
re-sending a whole parsed bill back to the model as a "please echo this
back" argument would be pointless. The raw file bytes and a vendor hint
(vendor identity for Format A/C comes from ingestion context -- the Gmail
sender or vendor folder -- never from the document itself, per
normalize.py) are seeded into state before a run starts (agent/loop.py).

The `_impl` functions below take a plain `state` mapping as their first
argument, separate from the `tool_context`-taking wrapper ADK actually
calls, so they're unit-testable with a plain dict -- it satisfies the same
get/set/contains protocol `tool_context.state` does, without constructing a
real ToolContext.
"""
from __future__ import annotations

import dataclasses

from google.adk.tools.tool_context import ToolContext

from .. import check_duplicate as check_duplicate_mod
from .. import lookup_vendor_history as lookup_vendor_history_mod
from .. import normalize
from .. import purchase_ledger
from .. import validate
from ..formats import detect as detect_mod
from ..formats import parse_csv as parse_csv_mod
from ..formats import parse_pdf_vision as parse_pdf_vision_mod
from ..formats import parse_xls as parse_xls_mod
from ..formats.schema import Bill


def _store_bill(state, bill: Bill) -> dict:
    issues = validate.validate_bill(bill)
    state["_bill"] = bill
    state["_validation_issues"] = issues
    return {
        "vendor": bill.vendor,
        "invoice_no": bill.invoice_no,
        "invoice_date": bill.invoice_date,
        "total_amount": bill.total_amount,
        "line_items": [dataclasses.asdict(li) for li in bill.line_items],
        "validation_issues": [issue.message for issue in issues],
    }


def _detect_format_impl(state) -> str:
    data = state["_file_bytes"]
    fmt = detect_mod.detect_format(data)
    state["_detected_format"] = fmt
    return fmt


def detect_format(tool_context: ToolContext) -> str:
    """Inspect the current bill file's actual bytes (not its filename or
    extension) and return which container format it is: "format_a_csv",
    "format_b_csv", "format_c_xls", "format_d_pdf", or "unknown". Call this
    first, before any parse tool."""
    return _detect_format_impl(tool_context.state)


def _parse_csv_impl(state) -> dict:
    fmt = state.get("_detected_format")
    data = state["_file_bytes"]
    if fmt == detect_mod.FORMAT_A_CSV:
        rows = parse_csv_mod.parse_format_a_csv(data)
        vendor = state.get("_vendor_hint") or ""
        bill = normalize.build_bill_from_format_a_rows(rows, vendor=vendor)
    elif fmt == detect_mod.FORMAT_B_CSV:
        parsed = parse_csv_mod.parse_format_b_csv(data)
        bill = normalize.build_bill_from_format_b(parsed)
    else:
        return {"error": f"parse_csv cannot handle detected format {fmt!r}; call detect_format first"}
    return _store_bill(state, bill)


def parse_csv(tool_context: ToolContext) -> dict:
    """Parse the current bill as a CSV -- handles both the 79-column ERP
    export dialect and the H/D/F row-type dialect. Only call this after
    detect_format has returned format_a_csv or format_b_csv. Returns the
    normalized bill (vendor, invoice_no, invoice_date, total_amount,
    line_items) plus any per-line arithmetic validation_issues found."""
    return _parse_csv_impl(tool_context.state)


def _parse_xls_impl(state) -> dict:
    fmt = state.get("_detected_format")
    if fmt != detect_mod.FORMAT_C_XLS:
        return {"error": f"parse_xls cannot handle detected format {fmt!r}; call detect_format first"}
    data = state["_file_bytes"]
    rows = parse_xls_mod.parse_format_c_xls(data)
    vendor = state.get("_vendor_hint") or ""
    bill = normalize.build_bill_from_format_a_rows(rows, vendor=vendor, source_format="format_c")
    return _store_bill(state, bill)


def parse_xls(tool_context: ToolContext) -> dict:
    """Parse the current bill as the legacy BIFF2 .xls container. Only call
    this after detect_format has returned format_c_xls. Returns the
    normalized bill plus any per-line arithmetic validation_issues found."""
    return _parse_xls_impl(tool_context.state)


def _parse_pdf_vision_impl(state) -> dict:
    fmt = state.get("_detected_format")
    if fmt != detect_mod.FORMAT_D_PDF:
        return {"error": f"parse_pdf_vision cannot handle detected format {fmt!r}; call detect_format first"}
    data = state["_file_bytes"]
    bill = parse_pdf_vision_mod.parse_pdf_vision(data)
    return _store_bill(state, bill)


def parse_pdf_vision(tool_context: ToolContext) -> dict:
    """Read the current bill's PDF tax invoice with Gemini multimodal (a
    separate vision call, not this agent's own reasoning turn). Only call
    this after detect_format has returned format_d_pdf, or as a
    reconciliation/recovery step to check the vendor's own PDF. Returns the
    normalized bill plus any per-line arithmetic validation_issues found."""
    return _parse_pdf_vision_impl(tool_context.state)


def _lookup_vendor_history_impl(state, item_name: str, limit: int | None) -> list[dict]:
    bill = state.get("_bill")
    if bill is None:
        return [{"error": "no bill parsed yet -- call a parse tool first"}]
    entries = lookup_vendor_history_mod.lookup_vendor_history(bill.vendor, item_name, limit=limit)
    return [dataclasses.asdict(e) for e in entries]


def lookup_vendor_history(item_name: str, tool_context: ToolContext, limit: int | None = None) -> list[dict]:
    """This vendor's prior purchases of the given item, most recent first,
    from the purchase ledger (real and seeded history alike -- check each
    entry's `seeded` flag before treating it as observed). `item_name`
    should match (or closely resemble) an item_name from the currently
    parsed bill; the vendor is always the current bill's own vendor, not a
    parameter you choose."""
    return _lookup_vendor_history_impl(tool_context.state, item_name, limit)


def _check_duplicate_impl(state) -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}
    result = check_duplicate_mod.check_duplicate(bill)
    return {
        "status": result.status.value,
        "matched_bill_id": result.matched_bill_id,
        "matched_bill_status": result.matched_bill_status,
    }


def check_duplicate(tool_context: ToolContext) -> dict:
    """Check whether this invoice number + vendor has already been
    recorded. status is one of: "new" (not seen before), "duplicate" (seen
    before with identical content -- nothing to do), or "reconciliation"
    (same invoice number seen before but with different content -- needs
    resolving, not a plain duplicate)."""
    return _check_duplicate_impl(tool_context.state)


def _record_purchase_impl(state) -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}
    doc_ids = purchase_ledger.record_purchase(bill)
    return {"doc_ids": doc_ids, "count": len(doc_ids)}


def record_purchase(tool_context: ToolContext) -> dict:
    """Write the currently parsed bill's line items to the purchase ledger.
    Only call this once the bill has been parsed, checked for duplicates,
    and any anomalies resolved or accepted."""
    return _record_purchase_impl(tool_context.state)
