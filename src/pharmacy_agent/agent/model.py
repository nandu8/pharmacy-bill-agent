"""ADK scaffold (PRD S7.1/S9 / T26): wires Gemini 3.5 Flash, via Vertex AI,
as the agent's reasoning model.

gemini-3.5-flash only serves from the Vertex AI "global" location for this
project (confirmed in T18 / formats/parse_pdf_vision.py -- it 404s from a
regional endpoint like us-central1). ADK's `Gemini` model wrapper doesn't
expose `location` as a field, so the fix is the pattern ADK's own docstring
recommends: subclass `Gemini` and override `api_client` to build the
underlying `google.genai.Client` with the location pinned.
"""
from __future__ import annotations

import os
from functools import cached_property

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.genai import Client

_MODEL = os.environ.get("PHARMACY_AGENT_GEMINI_MODEL", "gemini-3.5-flash")
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

AGENT_NAME = "pharmacy_bill_agent"

DEFAULT_INSTRUCTION = """You are an autonomous agent that processes incoming
pharmacy purchase bills for a retail pharmacy in India. You work turn by
turn: inspect the current state, choose one tool, observe its result, and
decide what to do next. You do not follow a fixed checklist -- form a
hypothesis about what the bill needs and choose the tools that confirm or
rule it out."""


class VertexGlobalGemini(Gemini):
    """Gemini model pinned to the Vertex AI "global" location (see module
    docstring) instead of the region ADK would otherwise default to."""

    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, project=_PROJECT, location=_LOCATION)


def build_model() -> Gemini:
    return VertexGlobalGemini(model=_MODEL)


def build_agent(
    *,
    instruction: str = DEFAULT_INSTRUCTION,
    tools: list[object | BaseTool | BaseToolset] | None = None,
) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=build_model(),
        instruction=instruction,
        tools=tools or [],
    )
