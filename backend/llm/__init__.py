"""
LLM Provider abstraction for Enterprise Knowledge Agent.

Usage:
    from backend.llm.factory import get_llm_provider
    from backend.llm.base import LLMProvider, Message, MessageRole, ToolDefinition, ToolCall, LLMResponse

    provider = get_llm_provider()   # reads LLM_PROVIDER env var
    response = provider.generate_with_tools(messages, tools)
"""
from backend.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolDefinition,
)

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "Message",
    "MessageRole",
    "ToolCall",
    "ToolDefinition",
]
