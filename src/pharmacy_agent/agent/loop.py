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
"""
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
from .resume_state import serialize_run_state
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
    agent_tools.record_purchase,
    agent_tools.notify_pharmacist,
    agent_tools.ask_pharmacist,
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

If the bill is clean (no validation issues, not a duplicate or reconciliation
case, and no confirmed unexplained price deviation), record the purchase.

Before concluding, tell the pharmacist what happened over WhatsApp: if you
are about to finish with status="resolved", call notify_pharmacist once with
a short confirmation of what was done. If you are about to finish with
status="pending_pharmacist", call ask_pharmacist once with your specific
open question instead -- never park a bill without asking a concrete
question. status="pending_vendor" does not need either call.

You must end the run by calling `finish` exactly once, as your last action:
status="resolved" if the bill was verified and (when appropriate) recorded,
status="pending_pharmacist" if you are genuinely unsure and a human should
decide (including a confirmed price deviation you could not resolve via
cross_check_other_vendors), or status="pending_vendor" if the file could not
be read by any available tool. Include a one-sentence summary of your
conclusion."""

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

    session = await runner.session_service.get_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session.id
    )
    bill = session.state.get("_bill")
    issues = session.state.get("_validation_issues", [])
    findings = list(session.state.get("_findings", []))
    status = session.state.get("_terminal_status") or terminal_status

    if status is None:
        # PRD S7.10: no bill is ever silently dropped -- if the model
        # stopped without declaring a terminal state, park it rather than
        # report an undefined outcome.
        status = PENDING_PHARMACIST
        if turn_cap_exceeded:
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

    bill_doc_id = record_bill_result(bill, status, findings, trace_id=trace_id)

    if status in (PENDING_PHARMACIST, PENDING_VENDOR):
        # PRD S7.6/T44: park durably rather than leave the run's context
        # only in this process's memory -- a later inbound reply (T45/T46)
        # needs the full tool-call history and open question to resume from
        # precisely this turn, on a different container, possibly days later.
        serialize_run_state(bill, status, findings, tool_call_history, bill_doc_id)

    return AgentRunResult(
        status=status,
        final_text=final_text,
        tool_call_history=tool_call_history,
        turn_count=turn_count,
        bill=bill,
        validation_issues=issues,
        findings=findings,
        bill_doc_id=bill_doc_id,
        trace_id=trace_id,
    )


def run_bill(file_bytes: bytes, vendor_hint: str = "", max_turns: int = _DEFAULT_MAX_TURNS) -> AgentRunResult:
    return asyncio.run(_run_async(file_bytes, vendor_hint, max_turns))
