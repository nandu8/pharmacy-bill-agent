"""ADK toolbox (PRD S7.2 / T27): wraps the tool functions already built in
Phase 3/4 (detect_format, parse_csv, parse_xls, parse_pdf_vision,
lookup_vendor_history, check_duplicate, record_purchase) as ADK function
tools, plus the Phase 6 investigation tools (check_price_deviation,
cross_check_other_vendors -- T55) once a bill needs more than structural
checks, plus the T41 WhatsApp tools (notify_pharmacist, ask_pharmacist), plus
the T43 dispute tool (send_dispute_email, wrapping T39's dispute_gate and
T38's email_vendor dispute mode -- gating enforced in code, not left to
prompting, same guardrail-in-code approach as the recipient-directory rule
below), plus the T58 memory tool (record_pharmacist_resolution, wrapping
pharmacist_resolutions.py -- writes a resumed run's price approve/reject
decision back to Firestore so check_price_deviation's PRD S7.7 memory
applies on future bills). The remaining PRD S7.2 tools -- find_related_document,
stage_file, email_vendor's resend mode -- depend on infrastructure not yet
wired into the loop (reconciliation, the unreadable-file recovery ladder) and
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
from .. import check_price_deviation as check_price_deviation_mod
from .. import cross_check_other_vendors as cross_check_other_vendors_mod
from .. import dispute_gate as dispute_gate_mod
from .. import email_vendor as email_vendor_mod
from .. import lookup_vendor_history as lookup_vendor_history_mod
from .. import normalize
from .. import pharmacist_resolutions as pharmacist_resolutions_mod
from .. import pharmacist_whatsapp as pharmacist_whatsapp_mod
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


def _check_price_deviation_impl(state, item_name: str, current_rate: float) -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}
    result = check_price_deviation_mod.check_price_deviation(bill.vendor, item_name, current_rate)
    # Kept as the raw result object (not the dict returned to the model) so
    # send_dispute_email can re-derive dispute_gate's PRD S7.5 conditions
    # without asking the model to echo structured findings back as an
    # argument -- same "state, not an LLM-visible argument" reasoning as the
    # module docstring gives for _bill/_file_bytes.
    state.setdefault("_price_deviation_results", {})[item_name] = result
    return {
        "signal": result.signal.value,
        "current_rate": result.current_rate,
        "reference_rate": result.reference_rate,
        "pct_change": result.pct_change,
        "prior_invoice_count": result.prior_invoice_count,
        "confirmed": result.confirmed,
        "prior_pharmacist_decision": result.resolution.decision.value if result.resolution else None,
    }


def check_price_deviation(item_name: str, current_rate: float, tool_context: ToolContext) -> dict:
    """Compare this bill's rate for an item against this vendor's own prior
    invoices for the same item. signal is "no_history" (nothing to compare
    against), "within_normal", or "deviation_detected" (10%+ move vs. the
    most recent prior invoice). confirmed=true means the deviation is backed
    by 3+ prior invoices, not a thin one-off. prior_pharmacist_decision is
    "approved"/"rejected" if a past resolution for this vendor+item already
    exists (already factored into signal -- an approved rate at or above
    this one is never flagged, a rejected pattern is flagged more
    sensitively) or null if none exists. Only worth calling for a line item
    you have a specific reason to double-check -- not every line on every
    bill."""
    return _check_price_deviation_impl(tool_context.state, item_name, current_rate)


def _cross_check_other_vendors_impl(state, item_name: str) -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}
    result = cross_check_other_vendors_mod.cross_check_other_vendors(
        bill.vendor, item_name, bill.invoice_date
    )
    state.setdefault("_cross_vendor_check_results", {})[item_name] = result
    return {
        "signal": result.signal.value,
        "vendor_movements": [dataclasses.asdict(m) for m in result.vendor_movements],
    }


def cross_check_other_vendors(item_name: str, tool_context: ToolContext) -> dict:
    """After check_price_deviation reports a confirmed deviation, check
    whether *other* vendors moved on the same item in the same window.
    signal="market_movement" means at least one other vendor also raised its
    rate (a market-wide shift, not this vendor's error).
    signal="no_movement" means no other vendor moved -- the strongest
    available signal that this is a vendor-specific anomaly.
    signal="insufficient_data" means there isn't enough other-vendor history
    for this item to tell either way."""
    return _cross_check_other_vendors_impl(tool_context.state, item_name)


def _send_dispute_email_impl(state, item_name: str, subject: str, body: str) -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}

    price_deviation = state.get("_price_deviation_results", {}).get(item_name)
    cross_vendor_check = state.get("_cross_vendor_check_results", {}).get(item_name)
    if price_deviation is None or cross_vendor_check is None:
        return {
            "sent": False,
            "reason": "must call check_price_deviation and cross_check_other_vendors "
            "for this item first",
        }

    line = next(
        (
            li
            for li in bill.line_items
            if purchase_ledger.normalize_item_key(li.item_name) == purchase_ledger.normalize_item_key(item_name)
        ),
        None,
    )
    if line is None:
        return {"sent": False, "reason": f"no line item named {item_name!r} on this bill"}

    reference_rate = price_deviation.reference_rate
    disputed_amount = (line.rate - reference_rate) * line.quantity if reference_rate is not None else 0.0

    gate_result = dispute_gate_mod.check_dispute_gate(
        bill.vendor, bill.invoice_no, price_deviation, cross_vendor_check, disputed_amount
    )
    if not gate_result.authorized:
        return {
            "sent": False,
            "reason": "dispute_gate_denied",
            "failed_conditions": gate_result.failed_conditions,
        }

    result = email_vendor_mod.email_vendor(bill.vendor, bill.invoice_no, "dispute", subject, body)
    result["disputed_amount"] = disputed_amount
    return result


def send_dispute_email(item_name: str, subject: str, body: str, tool_context: ToolContext) -> dict:
    """Send a factual, non-accusatory dispute email to the vendor for a
    confirmed pricing error on one item -- cites the invoice number, the
    vendor's own prior rate, and the current rate, and requests
    clarification rather than demanding a credit note. Always CCs the
    pharmacist. Only call this after check_price_deviation reported
    confirmed=true and cross_check_other_vendors reported signal="no_movement"
    for this exact item_name -- PRD S7.5's four dispute conditions (confirmed
    deviation, conclusive no-market-movement, disputed amount above the value
    floor, not already sent for this invoice) are re-checked and enforced
    here in code, not left to your judgment alone. If the conditions aren't
    met, or a dispute for this invoice was already sent, this returns
    sent=false with a reason (and failed_conditions, if the gate denied it)
    instead of sending anything -- fall back to ask_pharmacist with that
    reason in that case."""
    return _send_dispute_email_impl(tool_context.state, item_name, subject, body)


def _resolve_vendor_reference(state) -> tuple[str, str]:
    bill = state.get("_bill")
    if bill is not None:
        return bill.vendor, bill.invoice_no
    return state.get("_vendor_hint") or "unknown", "unparsed"


def _notify_pharmacist_impl(state, message: str) -> dict:
    vendor, reference = _resolve_vendor_reference(state)
    return pharmacist_whatsapp_mod.notify_pharmacist(vendor, reference, message)


def notify_pharmacist(message: str, tool_context: ToolContext) -> dict:
    """Send the pharmacist a WhatsApp message: a short confirmation that a
    bill was processed, or notice of an autonomous action already taken.
    Outbound only -- does not end the run. Call this once, before
    finish(status="resolved", ...)."""
    return _notify_pharmacist_impl(tool_context.state, message)


def _ask_pharmacist_impl(state, question: str) -> dict:
    vendor, reference = _resolve_vendor_reference(state)
    return pharmacist_whatsapp_mod.ask_pharmacist(vendor, reference, question)


def ask_pharmacist(question: str, tool_context: ToolContext) -> dict:
    """Send the pharmacist one specific, targeted WhatsApp question when you
    are genuinely unsure and a human needs to decide. Call this once, before
    finish(status="pending_pharmacist", ...) -- never park a bill without
    asking a concrete question first."""
    return _ask_pharmacist_impl(tool_context.state, question)


def _record_pharmacist_resolution_impl(state, item_name: str, decision: str, note: str = "") -> dict:
    bill = state.get("_bill")
    if bill is None:
        return {"error": "no bill parsed yet -- call a parse tool first"}

    line = next(
        (
            li
            for li in bill.line_items
            if purchase_ledger.normalize_item_key(li.item_name) == purchase_ledger.normalize_item_key(item_name)
        ),
        None,
    )
    if line is None:
        return {"error": f"no line item named {item_name!r} on this bill"}

    doc_id = pharmacist_resolutions_mod.record_pharmacist_resolution(
        bill.vendor,
        item_name,
        rate=line.rate,
        decision=pharmacist_resolutions_mod.PharmacistDecision(decision),
        note=note,
        invoice_no=bill.invoice_no,
    )
    return {"recorded": True, "doc_id": doc_id}


def record_pharmacist_resolution(
    item_name: str, decision: str, tool_context: ToolContext, note: str = ""
) -> dict:
    """Record the pharmacist's decision on a price question for one line
    item -- decision is "approved" (the rate is fine, stop flagging it for
    this vendor+item going forward) or "rejected" (the rate is not fine;
    future deviations for this vendor+item get flagged more sensitively).
    Only call this while resuming a run parked on a price-related
    ask_pharmacist question, once the reply makes the pharmacist's decision
    clear -- not for questions unrelated to pricing."""
    return _record_pharmacist_resolution_impl(tool_context.state, item_name, decision, note)
