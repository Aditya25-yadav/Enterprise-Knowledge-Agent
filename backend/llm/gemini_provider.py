"""
Gemini LLM Provider — google-genai SDK.

Implements LLMProvider using Google Gemini 2.0 Flash (or any Gemini model)
via the official google-genai Python SDK.

Key design choices:
  - parameters_json_schema: passes our JSON Schema dict directly to Gemini —
    no manual type conversion needed (SDK handles it).
  - response.function_calls: direct list access, not through candidates[].
  - Tool response role is "tool" in Gemini's Content format.
  - automatic_function_calling is DISABLED — we control the loop in planner.py.

Usage:
    from backend.llm.gemini_provider import GeminiProvider
    provider = GeminiProvider(api_key="...")
    # or: provider = GeminiProvider()  (reads GEMINI_API_KEY from env)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from backend.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolDefinition,
)


class GeminiProvider(LLMProvider):
    """
    LLMProvider backed by Google Gemini via the google-genai SDK.

    Supports:
      - Text generation: generate()
      - Tool/function calling: generate_with_tools()

    Environment variables:
      GEMINI_API_KEY  — required
      GEMINI_MODEL    — optional, default gemini-2.0-flash
    """

    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "GeminiProvider requires GEMINI_API_KEY env var or api_key argument."
            )
        self._model = model or os.getenv("GEMINI_MODEL", self.DEFAULT_MODEL)
        self._client = genai.Client(api_key=self._api_key)

    @property
    def provider_name(self) -> str:
        return f"gemini/{self._model}"

    # ── Internal helpers ──────────────────────────────────────────────────

    def _to_gemini_contents(self, messages: List[Message]) -> List[types.Content]:
        """
        Convert our Message list into Gemini Content objects.

        Role mapping:
          USER        → role="user"   + text Part
          ASSISTANT   → role="model"  + text Part (or FunctionCall Parts)
          TOOL_RESULT → role="tool"   + FunctionResponse Part
        """
        contents: List[types.Content] = []

        for msg in messages:
            if msg.role == MessageRole.USER:
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=msg.content)],
                ))

            elif msg.role == MessageRole.ASSISTANT:
                if msg.tool_calls:
                    # Model previously requested tool calls — reconstruct those parts
                    parts = [
                        types.Part.from_function_call(
                            name=tc.tool_name,
                            args=tc.arguments,
                        )
                        for tc in msg.tool_calls
                    ]
                    contents.append(types.Content(role="model", parts=parts))
                else:
                    contents.append(types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=msg.content)],
                    ))

            elif msg.role == MessageRole.TOOL_RESULT:
                # Return a tool's output back to the model
                contents.append(types.Content(
                    role="tool",
                    parts=[types.Part.from_function_response(
                        name=msg.tool_name,
                        response={"result": msg.content},
                    )],
                ))

        return contents

    def _to_gemini_tool(self, tools: List[ToolDefinition]) -> types.Tool:
        """
        Convert our ToolDefinition list into a single Gemini Tool object.

        Uses parameters_json_schema — the SDK accepts our JSON Schema dict
        directly without any manual type conversion.
        """
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters_json_schema=t.parameters,
            )
            for t in tools
        ]
        return types.Tool(function_declarations=declarations)

    # ── Public interface ──────────────────────────────────────────────────

    def generate(self, messages: List[Message]) -> str:
        """
        Plain text generation — no tools.
        Used by AnswerGenerator for the final synthesis.
        """
        contents = self._to_gemini_contents(messages)
        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
        )
        return response.text or ""

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        """
        Tool/function calling. Used by the Agent Planner.

        Automatic function calling is DISABLED — the agent loop in
        planner.py controls tool execution and conversation history.

        Returns LLMResponse with either .tool_calls or .content.
        """
        contents = self._to_gemini_contents(messages)
        gemini_tool = self._to_gemini_tool(tools)

        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[gemini_tool],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
            ),
        )

        # response.function_calls is a flat list of FunctionCall objects
        # (None if model replied with text instead)
        if response.function_calls:
            tool_calls = [
                ToolCall(
                    tool_name=fc.name,
                    arguments=dict(fc.args) if fc.args else {},
                    call_id=fc.name,  # Gemini doesn't provide a unique call ID
                )
                for fc in response.function_calls
            ]
            return LLMResponse(tool_calls=tool_calls)

        return LLMResponse(content=response.text or "")
