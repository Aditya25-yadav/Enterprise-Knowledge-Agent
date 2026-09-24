"""
LLM Provider Abstraction — Base Types and Interface.

Design principle:
  Agent / AnswerGenerator depend ONLY on LLMProvider (this file).
  They never import GeminiProvider or OllamaProvider directly.

  Swapping providers = change LLM_PROVIDER env var, nothing else.

  LLMProvider (abstract)
       │
  ┌────┴────┐
  ▼         ▼
Gemini    Ollama
(API)    (local)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"  # returning a tool's output back to the model


@dataclass
class Message:
    """
    One turn in a conversation, provider-agnostic.

    When role=ASSISTANT and tool_calls is non-empty → model wants tools.
    When role=TOOL_RESULT → we are sending a tool result back to the model.
      - tool_name:  which tool produced this result
      - content:    the tool's return value (string)
    """
    role: MessageRole
    content: str = ""
    tool_calls: List["ToolCall"] = field(default_factory=list)
    tool_name: str = ""     # set when role == TOOL_RESULT
    tool_call_id: str = ""  # used by OpenAI-style providers for correlation


# ---------------------------------------------------------------------------
# Tool types
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """
    A tool the model can call, described in provider-agnostic JSON Schema.

    parameters example:
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "top_k": {"type": "integer", "description": "Number of results"}
        },
        "required": ["query"]
    }
    """
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class ToolCall:
    """
    A single tool invocation the model has requested.
    Produced by LLMProvider.generate_with_tools().
    """
    tool_name: str
    arguments: Dict[str, Any]
    call_id: str = ""


# ---------------------------------------------------------------------------
# LLM Response
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """
    The model's response to generate_with_tools().

    Exactly one path:
      has_tool_calls == True  → model wants to call tools (run them, then reply)
      is_text        == True  → model answered directly (no tools needed)
    """
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def is_text(self) -> bool:
        return not self.has_tool_calls and bool(self.content)


# ---------------------------------------------------------------------------
# Abstract Provider Interface
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """
    Interface all LLM providers implement.

    generate()            → simple text generation (used for final answer)
    generate_with_tools() → function/tool calling (used by agent planner)
    """

    @abstractmethod
    def generate(self, messages: List[Message]) -> str:
        """
        Plain text generation — no tools.
        Used by AnswerGenerator for the final synthesis call.
        """
        ...

    @abstractmethod
    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        """
        Generation with tool/function calling.
        Used by Agent Planner on every planning step.

        Returns LLMResponse with either:
          .tool_calls  → tools the model wants to invoke
          .content     → direct text answer (no tools needed)
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """e.g. 'gemini/gemini-2.0-flash'"""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider_name}>"
