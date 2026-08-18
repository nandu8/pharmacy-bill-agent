import asyncio

from google.adk.runners import InMemoryRunner
from google.genai import types

from pharmacy_agent.agent.model import AGENT_NAME, build_agent


def _run_single_turn(agent, message_text: str) -> str:
    runner = InMemoryRunner(agent=agent, app_name="pharmacy-bill-agent-test")

    async def _go() -> str:
        session = await runner.session_service.create_session(
            app_name="pharmacy-bill-agent-test", user_id="test-user"
        )
        message = types.Content(role="user", parts=[types.Part.from_text(text=message_text)])
        reply = ""
        async for event in runner.run_async(
            user_id="test-user", session_id=session.id, new_message=message
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        reply += part.text
        return reply

    return asyncio.run(_go())


def test_build_agent_is_named_and_toolless_by_default():
    agent = build_agent()
    assert agent.name == AGENT_NAME
    assert agent.tools == []


def test_agent_reasons_via_vertex_gemini():
    # Live call -- proves the model is actually reachable via Vertex AI at
    # the "global" location (T26), not just constructible.
    agent = build_agent(instruction="Reply with exactly the single word PONG and nothing else.")
    reply = _run_single_turn(agent, "ping")
    assert "PONG" in reply.upper()
