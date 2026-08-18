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

This module does not yet interpret *which* terminal state a run ended in
(resolved / pending_pharmacist / pending_vendor) -- that's T29. Here a run
simply ends when the model stops calling tools and gives a final textual
reply, exactly as ADK's own loop naturally terminates.
"""
from __future__ import annotations

import asyncio
import dataclasses

from google.adk.runners import InMemoryRunner
from google.genai import types

from . import tools as agent_tools
from .model import build_agent
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
]

_KICKOFF_MESSAGE = """A new pharmacy purchase bill has arrived and is waiting
in your session state. Process it: detect its format, parse it with the
matching tool, and check whether it's a duplicate of something already on
file. If the bill is clean (no validation issues, not a duplicate or
reconciliation case), record the purchase. Report your conclusion and the
reasoning behind it in your final reply."""


@dataclasses.dataclass
class ToolCallRecord:
    tool: str
    args: dict


@dataclasses.dataclass
class AgentRunResult:
    final_text: str
    tool_call_history: list[ToolCallRecord]
    turn_count: int
    bill: Bill | None
    validation_issues: list[ValidationIssue]


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

    return AgentRunResult(
        final_text=final_text,
        tool_call_history=tool_call_history,
        turn_count=turn_count,
        bill=bill,
        validation_issues=issues,
    )


def run_bill(file_bytes: bytes, vendor_hint: str = "") -> AgentRunResult:
    return asyncio.run(_run_async(file_bytes, vendor_hint))
