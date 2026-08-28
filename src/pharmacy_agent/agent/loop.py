"""Agent loop (PRD S7.1 / T28): turn-by-turn tool selection, observation,
decision, driven by Gemini 3.5 Flash via ADK (agent/model.py) over the
toolbox in agent/tools.py.

`run_bill` seeds one bill's raw file bytes (and, for Format A/C, the vendor
supplied by ingestion context -- see tools.py) into a fresh session's state,
sends a single kickoff message, and lets the ADK Runner drive turns:
inspect state -> pick a tool -> observe the result -> decide the next
tool, or stop. Each event's function call(s) are recorded as one turn in
`tool_call_history` -- the trace this produces is what PRD S8 calls a demo
asset (a clean bill's short chain vs. a corrupted bill's longer one).

Terminal-state interpretation (T29) comes from the model calling `finish`
as its last action; the run also stops -- and is parked, never left running
forever -- if it exceeds `max_turns` without doing so (PRD S7.10 turn-cap
guardrail, T30).

`resume_bill` (PRD S7.6 / T45) is the other entry point: instead of raw
file bytes, it rehydrates a parked run's serialized bill/findings
(resume_state.py, T44) into a fresh session and kicks off with the
pharmacist's reply instead of a new bill. `_drive_runner`/`_finalize_run`
factor out the turn-driving and outcome-recording logic both entry points
share, so a resumed run gets the same terminal-state handling, turn cap,
trace, and Firestore writes a fresh one does -- just seeded differently and
(if it parks again) with its tool-call history prefixed by the prior pause's.

The resume kickoff prompt also asks the model to call
record_pharmacist_resolution (T58/PRD S7.7) when a reply settles a price
question, so check_price_deviation's memory of past approve/reject
decisions applies to bills that arrive after this one, not just this one."""
from __future__ import annotations

import asyncio
import dataclasses
import logging

from contextlib import aclosing

from google.adk.runners import InMemoryRunner
from google.genai import types
from opentelemetry import trace as otel_trace

from . import tools as agent_tools
from .model import build_agent
from .resume_state import (
    deserialize_bill,
    get_agent_run,
    mark_resumed,
    retire_placeholder,
    serialize_run_state,
)
from .terminal import (
    FINISH_TOOL_NAME,
    PENDING_PHARMACIST,
    PENDING_VENDOR,
    finish,
    record_bill_result,
)
from ..formats.schema import Bill
from ..telemetry import current_trace_id, setup_tracing
from ..validate import ValidationIssue

_logger = logging.getLogger(__name__)

_APP_NAME = "pharmacy-bill-agent"
_USER_ID = "pharmacy-agent-runner"

_TOOLS = [
    agent_tools.detect_format,
    agent_tools.parse_csv,
    agent_tools.parse_xls,
    agent_tools.parse_pdf_vision,
    agent_tools.lookup_vendor_history,
    agent_tools.check_duplicate,
    agent_tools.check_price_deviation,
    agent_tools.cross_check_other_vendors,
    agent_tools.send_dispute_email,
    agent_tools.record_purchase,
    agent_tools.notify_pharmacist,
    agent_tools.ask_pharmacist,
    agent_tools.record_pharmacist_resolution,
    finish,
]

_KICKOFF_MESSAGE = """A new pharmacy purchase bill has arrived and is waiting
in your session state. Process it: detect its format, parse it with the
matching tool, and check whether it's a duplicate of something already on
file.

Only consider investigating price history when the parsed bill has 3 or
fewer line items in total. For a bill with more line items than that, skip
price investigation entirely and proceed on the structural checks alone
(arithmetic validation, duplicate/reconciliation status) -- do not call
check_price_deviation on a multi-item bill. For a small bill (3 or fewer
line items) whose rate looks worth double-checking against this vendor's own
history, call check_price_deviation once for that item. If it reports a
confirmed deviation, call cross_check_other_vendors once for the same item
to see whether other vendors moved on it too (a market-wide shift) or not (a
vendor-specific anomaly). Never call check_price_deviation more than once
for the same item.

If cross_check_other_vendors reports signal="market_movement", the deviation
is a market-wide shift, not this vendor's error -- record the purchase as
usual, no dispute. If it reports signal="no_movement" -- a vendor-specific
anomaly -- call send_dispute_email once for that item with a factual,
non-accusatory subject/body citing the invoice number, the vendor's prior
rate, and the current rate. If send_dispute_email returns sent=true, record
the purchase (the bill is still real, write it as received), then call
notify_pharmacist with a short note that a dispute was sent, and finish
status="resolved". If it returns sent=false, call ask_pharmacist with the
reason it gave (and failed_conditions, if present) instead, then finish
status="pending_pharmacist" -- never retry send_dispute_email for the same
item. If cross_check_other_vendors reports signal="insufficient_data", you
cannot conclude either way -- call ask_pharmacist with your findings and
finish status="pending_pharmacist".

If the bill is clean (no validation issues, not a duplicate or reconciliation
case, and no confirmed unexplained price deviation), record the purchase.

Before concluding, tell the pharmacist what happened over WhatsApp: if you
are about to finish with status="resolved", call notify_pharmacist once with
a short confirmation of what was done (including, if you sent one, that a
dispute email went out). If you are about to finish with
status="pending_pharmacist", call ask_pharmacist once with your specific
open question instead -- never park a bill without asking a concrete
question. status="pending_vendor" does not need either call.

You must end the run by calling `finish` exactly once, as your last action:
status="resolved" if the bill was verified and (when appropriate) recorded
or disputed, status="pending_pharmacist" if you are genuinely unsure and a
human should decide (including a confirmed price deviation you could not
resolve via cross_check_other_vendors, or a dispute send_dispute_email
declined to send), or status="pending_vendor" if the file could not be read
by any available tool. Include a one-sentence summary of your conclusion."""

_RESUME_KICKOFF_TEMPLATE = """This bill was previously parked, waiting on the
pharmacist. Your earlier question was: "{open_question}"

The pharmacist just replied over WhatsApp: "{reply}"

The bill's parsed data (vendor, line items) and your prior findings are
already loaded in your session state -- you do not need to re-parse or
re-detect the format. Use the pharmacist's answer to decide how to proceed.

If the answer resolves your question, record the purchase (if not already
recorded) and call notify_pharmacist once with a short confirmation before
finishing status="resolved". If it doesn't and you still need more input,
call ask_pharmacist again with a new, more specific question before
finishing status="pending_pharmacist" -- never park again without asking a
concrete question.

If your earlier question was about a price deviation on a specific item and
the pharmacist's reply clearly approves or rejects that rate, call
record_pharmacist_resolution once for that item with decision="approved" or
decision="rejected" before finishing -- this is how future bills from this
vendor for this item remember the decision, so do this even though it isn't
needed to resolve the bill in front of you right now.

You must end by calling `finish` exactly once, as your last action, with a
one-sentence summary of your conclusion."""

_VENDOR_RESEND_PREAMBLE = """A vendor has resent this bill's file after the
previous attempt could not be read (see your prior findings in session
state, if any, for what went wrong before). """

_NO_TERMINAL_STATUS_FINDING = (
    "agent stopped without calling finish -- parked for pharmacist review"
)
_DEFAULT_MAX_TURNS = 15


def _turn_cap_finding(max_turns: int) -> str:
    return f"turn cap of {max_turns} exceeded without a conclusion -- parked for pharmacist review"


@dataclasses.dataclass
class ToolCallRecord:
    tool: str
    args: dict


@dataclasses.dataclass
class AgentRunResult:
    status: str
    final_text: str
    tool_call_history: list[ToolCallRecord]
    turn_count: int
    bill: Bill | None
    validation_issues: list[ValidationIssue]
    findings: list[str]
    bill_doc_id: str
    trace_id: str | None


@dataclasses.dataclass
class _DrivenRun:
    tool_call_history: list[ToolCallRecord]
    turn_count: int
    final_text: str
    terminal_status: str | None
    turn_cap_exceeded: bool


async def _drive_runner(runner: InMemoryRunner, session, message, max_turns: int) -> _DrivenRun:
    tool_call_history: list[ToolCallRecord] = []
    turn_count = 0
    final_text = ""
    terminal_status: str | None = None
    turn_cap_exceeded = False

    events = runner.run_async(user_id=_USER_ID, session_id=session.id, new_message=message)
    async with aclosing(events):
        async for event in events:
            calls = event.get_function_calls()
            if calls:
                turn_count += 1
                tool_call_history.extend(
                    ToolCallRecord(tool=c.name, args=dict(c.args or {})) for c in calls
                )
            for response in event.get_function_responses():
                if response.name == FINISH_TOOL_NAME and response.response:
                    terminal_status = response.response.get("status")
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        final_text += part.text

            # Once `finish` has been called, the model has nothing left to
            # do -- its very next turn is a plain text reply (if any) and
            # ADK's own loop ends on its own, so no forced break is needed
            # for that case. The turn cap (PRD S7.10) is still an
            # unconditional hard bound, independent of `finish`, so a model
            # that keeps calling tools after finishing can't run forever;
            # it just isn't misreported as a cap trip if `finish` already
            # legitimately concluded the run.
            if turn_count >= max_turns:
                if terminal_status is None:
                    turn_cap_exceeded = True
                break

    return _DrivenRun(tool_call_history, turn_count, final_text, terminal_status, turn_cap_exceeded)


async def _finalize_run(
    runner: InMemoryRunner,
    session_id: str,
    driven: _DrivenRun,
    trace_id: str | None,
    max_turns: int,
    vendor_hint: str = "",
    prior_tool_call_history: list[ToolCallRecord] | None = None,
    fallback_bill_doc_id: str | None = None,
) -> AgentRunResult:
    session = await runner.session_service.get_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
    )
    bill = session.state.get("_bill")
    issues = session.state.get("_validation_issues", [])
    findings = list(session.state.get("_findings", []))
    status = session.state.get("_terminal_status") or driven.terminal_status

    if status is None:
        # PRD S7.10: no bill is ever silently dropped -- if the model
        # stopped without declaring a terminal state, park it rather than
        # report an undefined outcome.
        status = PENDING_PHARMACIST
        if driven.turn_cap_exceeded:
            finding = _turn_cap_finding(max_turns)
            _logger.warning(
                "bill parked pending_pharmacist: %s (vendor=%s invoice_no=%s)",
                finding,
                bill.vendor if bill else vendor_hint,
                bill.invoice_no if bill else None,
            )
        else:
            finding = _NO_TERMINAL_STATUS_FINDING
        findings.append(finding)

    bill_doc_id = record_bill_result(
        bill, status, findings, trace_id=trace_id, fallback_doc_id=fallback_bill_doc_id
    )

    # T44/T45: the full history across every pause/resume cycle, not just
    # this segment's turns -- a run resumed a second time still needs to see
    # what happened before its first pause.
    full_tool_call_history = list(prior_tool_call_history or []) + driven.tool_call_history

    if status in (PENDING_PHARMACIST, PENDING_VENDOR):
        # PRD S7.6/T44: park durably rather than leave the run's context
        # only in this process's memory -- a later inbound reply (T45/T46)
        # needs the full tool-call history and open question to resume from
        # precisely this turn, on a different container, possibly days later.
        serialize_run_state(
            bill, status, findings, full_tool_call_history, bill_doc_id, vendor_hint=vendor_hint
        )

    return AgentRunResult(
        status=status,
        final_text=driven.final_text,
        tool_call_history=full_tool_call_history,
        turn_count=driven.turn_count,
        bill=bill,
        validation_issues=issues,
        findings=findings,
        bill_doc_id=bill_doc_id,
        trace_id=trace_id,
    )


async def _run_async(file_bytes: bytes, vendor_hint: str, max_turns: int) -> AgentRunResult:
    setup_tracing()
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("process_bill"):
        return await _run_traced(file_bytes, vendor_hint, max_turns)


async def _run_traced(file_bytes: bytes, vendor_hint: str, max_turns: int) -> AgentRunResult:
    # PRD S8: every bill's tool-call turns share this span's trace id (T54),
    # so the trace recorded on the `bills` doc covers the whole run, not
    # just whichever individual ADK-internal span happens to be current.
    trace_id = current_trace_id()
    agent = build_agent(tools=_TOOLS)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)

    session = await runner.session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        state={"_file_bytes": file_bytes, "_vendor_hint": vendor_hint, "_findings": []},
    )

    message = types.Content(role="user", parts=[types.Part.from_text(text=_KICKOFF_MESSAGE)])
    driven = await _drive_runner(runner, session, message, max_turns)
    return await _finalize_run(runner, session.id, driven, trace_id, max_turns, vendor_hint=vendor_hint)


def run_bill(file_bytes: bytes, vendor_hint: str = "", max_turns: int = _DEFAULT_MAX_TURNS) -> AgentRunResult:
    return asyncio.run(_run_async(file_bytes, vendor_hint, max_turns))


async def _resume_async(agent_run_id: str, reply_text: str, max_turns: int) -> AgentRunResult | None:
    setup_tracing()
    agent_run = get_agent_run(agent_run_id)
    if agent_run is None:
        return None
    mark_resumed(agent_run_id)
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("process_bill"):
        return await _run_resumed_traced(agent_run, reply_text, max_turns)


async def _run_resumed_traced(agent_run: dict, reply_text: str, max_turns: int) -> AgentRunResult:
    trace_id = current_trace_id()
    agent = build_agent(tools=_TOOLS)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)

    serialized_state = agent_run["serialized_state"]
    bill = deserialize_bill(serialized_state)
    prior_findings = list(serialized_state.get("findings", []))
    prior_tool_call_history = [
        ToolCallRecord(tool=r["tool"], args=r["args"]) for r in agent_run.get("tool_call_history", [])
    ]
    vendor_hint = bill.vendor if bill is not None else ""

    session = await runner.session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        state={"_bill": bill, "_findings": prior_findings, "_vendor_hint": vendor_hint},
    )

    kickoff = _RESUME_KICKOFF_TEMPLATE.format(
        open_question=agent_run.get("open_question") or "(no question recorded)",
        reply=reply_text,
    )
    message = types.Content(role="user", parts=[types.Part.from_text(text=kickoff)])
    driven = await _drive_runner(runner, session, message, max_turns)
    return await _finalize_run(
        runner,
        session.id,
        driven,
        trace_id,
        max_turns,
        vendor_hint=vendor_hint,
        prior_tool_call_history=prior_tool_call_history,
        fallback_bill_doc_id=agent_run["bill_id"],
    )


def resume_bill(
    agent_run_id: str, reply_text: str, max_turns: int = _DEFAULT_MAX_TURNS
) -> AgentRunResult | None:
    """Rehydrate a parked run (T44's agent_runs doc, matched by the inbound
    webhook via resume_state.find_resumable_run) and continue the loop from
    the pharmacist's reply, as PRD S7.6 describes. Returns None if the given
    id no longer has a parked run (already resumed, or never existed)."""
    return asyncio.run(_resume_async(agent_run_id, reply_text, max_turns))


async def _run_resumed_with_file_traced(
    agent_run: dict, file_bytes: bytes, max_turns: int
) -> AgentRunResult:
    trace_id = current_trace_id()
    agent = build_agent(tools=_TOOLS)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)

    serialized_state = agent_run["serialized_state"]
    prior_findings = list(serialized_state.get("findings", []))
    prior_tool_call_history = [
        ToolCallRecord(tool=r["tool"], args=r["args"]) for r in agent_run.get("tool_call_history", [])
    ]
    vendor_hint = serialized_state.get("vendor_hint") or serialized_state.get("vendor") or ""

    session = await runner.session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        state={"_file_bytes": file_bytes, "_vendor_hint": vendor_hint, "_findings": prior_findings},
    )

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=_VENDOR_RESEND_PREAMBLE + _KICKOFF_MESSAGE)]
    )
    driven = await _drive_runner(runner, session, message, max_turns)
    placeholder_id = agent_run["bill_id"]
    result = await _finalize_run(
        runner,
        session.id,
        driven,
        trace_id,
        max_turns,
        vendor_hint=vendor_hint,
        prior_tool_call_history=prior_tool_call_history,
        fallback_bill_doc_id=placeholder_id,
    )
    if result.bill_doc_id != placeholder_id:
        # The resend parsed successfully into a real vendor/invoice_no key
        # this time -- retire the pending_vendor placeholder (see
        # resume_state.retire_placeholder) so it doesn't linger as a
        # phantom bill next to the real, now-resolved one.
        retire_placeholder(placeholder_id)
    return result


async def _resume_with_file_async(
    agent_run_id: str, file_bytes: bytes, max_turns: int
) -> AgentRunResult | None:
    setup_tracing()
    agent_run = get_agent_run(agent_run_id)
    if agent_run is None:
        return None
    mark_resumed(agent_run_id)
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("process_bill"):
        return await _run_resumed_with_file_traced(agent_run, file_bytes, max_turns)


def resume_bill_with_file(
    agent_run_id: str, file_bytes: bytes, max_turns: int = _DEFAULT_MAX_TURNS
) -> AgentRunResult | None:
    """T46: resume a pending_vendor run once the vendor resends a
    (hopefully corrected) file, matched by
    resume_state.find_resumable_run(statuses=(PENDING_VENDOR,), vendor_hint=...).
    Unlike resume_bill (T45's WhatsApp text reply), this re-seeds
    _file_bytes and reruns the normal parse-and-check kickoff -- there's no
    bill already loaded to reason over, since the whole reason for the
    pause was that the original file couldn't be parsed by any tool."""
    return asyncio.run(_resume_with_file_async(agent_run_id, file_bytes, max_turns))
