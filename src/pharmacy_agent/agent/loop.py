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
as its last action -- see terminal.py.
"""
from __future__ import annotations

import asyncio
import dataclasses

from google.adk.runners import InMemoryRunner
from google.genai import types

from . import tools as agent_tools
from .model import build_agent
from .terminal import PENDING_PHARMACIST, finish, record_bill_result
from ..formats.schema import Bill
from ..validate import ValidationIssue

_APP_NAME = "pharmacy-bill-agent"
_USER_ID = "pharmacy-agent-runner"

_TOOLS = [
    agent_tools.detect_format,
    agent_tools.parse_csv,
    agent_tools.parse_xls,
    agent_tools.parse_pdf_vision,
    agent_tools.lookup_vendor_history,
    agent_tools.check_duplicate,
    agent_tools.record_purchase,
    finish,
]

_KICKOFF_MESSAGE = """A new pharmacy purchase bill has arrived and is waiting
in your session state. Process it: detect its format, parse it with the
matching tool, and check whether it's a duplicate of something already on
file. If the bill is clean (no validation issues, not a duplicate or
reconciliation case), record the purchase.

You must end the run by calling `finish` exactly once, as your last action:
status="resolved" if the bill was verified and (when appropriate) recorded,
status="pending_pharmacist" if you are genuinely unsure and a human should
decide, or status="pending_vendor" if the file could not be read by any
available tool. Include a one-sentence summary of your conclusion."""

_NO_TERMINAL_STATUS_FINDING = (
    "agent stopped without calling finish -- parked for pharmacist review"
)


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


async def _run_async(file_bytes: bytes, vendor_hint: str) -> AgentRunResult:
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

    async for event in runner.run_async(user_id=_USER_ID, session_id=session.id, new_message=message):
        calls = event.get_function_calls()
        if calls:
            turn_count += 1
            tool_call_history.extend(ToolCallRecord(tool=c.name, args=dict(c.args or {})) for c in calls)
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text += part.text

    session = await runner.session_service.get_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session.id
    )
    bill = session.state.get("_bill")
    issues = session.state.get("_validation_issues", [])
    findings = list(session.state.get("_findings", []))
    status = session.state.get("_terminal_status")
    if status is None:
        # PRD S7.10: no bill is ever silently dropped -- if the model
        # stopped without declaring a terminal state, park it rather than
        # report an undefined outcome.
        status = PENDING_PHARMACIST
        findings.append(_NO_TERMINAL_STATUS_FINDING)

    bill_doc_id = record_bill_result(bill, status, findings)

    return AgentRunResult(
        status=status,
        final_text=final_text,
        tool_call_history=tool_call_history,
        turn_count=turn_count,
        bill=bill,
        validation_issues=issues,
        findings=findings,
        bill_doc_id=bill_doc_id,
    )


def run_bill(file_bytes: bytes, vendor_hint: str = "") -> AgentRunResult:
    return asyncio.run(_run_async(file_bytes, vendor_hint))
