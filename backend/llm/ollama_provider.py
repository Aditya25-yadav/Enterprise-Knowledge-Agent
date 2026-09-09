"""
Ollama LLM Provider — local LLM server.

Implements LLMProvider using Ollama (https://ollama.com) for fully
local inference. Swap in when enterprise data cannot leave the network.

Ollama must be running: `ollama serve`
A tool-capable model must be pulled: `ollama pull llama3.1`

Recommended models for reliable function/tool calling:
  llama3.1    (8B)   — good balance of speed and accuracy
  qwen2.5     (7B)   — very strong tool calling
  mistral-nemo       — reliable structured output

Environment variables:
  OLLAMA_MODEL     — model name (default: llama3.1)
  OLLAMA_BASE_URL  — server URL (default: http://localhost:11434)

Usage:
    from backend.llm.ollama_provider import OllamaProvider
    provider = OllamaProvider()            # reads from env
    provider = OllamaProvider(model="qwen2.5")
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from backend.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolDefinition,
)


class OllamaProvider(LLMProvider):
    """
    LLMProvider backed by Ollama (local LLM server).

    Requires:
      - Ollama running at OLLAMA_BASE_URL
      - A tool-calling capable model pulled (e.g., llama3.1)

    Lazy import of `ollama` package so the rest of the system
    works even without Ollama installed.
    """

    DEFAULT_MODEL = "llama3.1"
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._model = model or os.getenv("OLLAMA_MODEL", self.DEFAULT_MODEL)
        self._base_url = base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)
        self._client = self._make_client()

    def _make_client(self) -> Any:
        try:
            import ollama
            return ollama.Client(host=self._base_url)
        except ImportError:
            raise ImportError(
                "OllamaProvider requires the 'ollama' package. "
                "Install it: pip install ollama"
            )

    @property
    def provider_name(self) -> str:
        return f"ollama/{self._model}"

    # ── Internal helpers ──────────────────────────────────────────────────

    def _to_ollama_messages(self, messages: List[Message]) -> List[dict]:
        """
        Convert our Message list to Ollama's chat message format.

        Ollama follows the OpenAI message format:
          {"role": "user",      "content": "..."}
          {"role": "assistant", "content": "", "tool_calls": [...]}
          {"role": "tool",      "content": "result string"}
        """
        result = []
        for msg in messages:
            if msg.role == MessageRole.USER:
                result.append({"role": "user", "content": msg.content})

            elif msg.role == MessageRole.ASSISTANT:
                if msg.tool_calls:
                    result.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": tc.tool_name,
                                    "arguments": tc.arguments,
                                }
                            }
                            for tc in msg.tool_calls
                        ],
                    })
                else:
                    result.append({"role": "assistant", "content": msg.content})

            elif msg.role == MessageRole.TOOL_RESULT:
                # Ollama expects tool results as role="tool"
                result.append({
                    "role": "tool",
                    "content": msg.content,
                })

        return result

    def _to_ollama_tools(self, tools: List[ToolDefinition]) -> List[dict]:
        """Convert our ToolDefinition list to Ollama tool format (OpenAI-style)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    # ── Public interface ──────────────────────────────────────────────────

    def generate(self, messages: List[Message]) -> str:
        """Plain text generation — no tools."""
        response = self._client.chat(
            model=self._model,
            messages=self._to_ollama_messages(messages),
        )
        return response["message"]["content"]

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        """Tool/function calling. Used by the Agent Planner."""
        response = self._client.chat(
            model=self._model,
            messages=self._to_ollama_messages(messages),
            tools=self._to_ollama_tools(tools),
        )

        msg = response["message"]

        if msg.get("tool_calls"):
            tool_calls = []
            for i, tc in enumerate(msg["tool_calls"]):
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                # Some Ollama versions return args as a JSON string
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(ToolCall(
                    tool_name=fn.get("name", ""),
                    arguments=args,
                    call_id=f"ollama_call_{i}",
                ))
            return LLMResponse(tool_calls=tool_calls)

        return LLMResponse(content=msg.get("content", ""))
