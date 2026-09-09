"""
LLM Provider Factory.

Single entry point for creating the configured LLM provider.
Reads LLM_PROVIDER from the environment and returns the correct
LLMProvider implementation.

Usage:
    from backend.llm.factory import get_llm_provider

    provider = get_llm_provider()         # reads LLM_PROVIDER env var
    provider = get_llm_provider("ollama") # override: use Ollama
    provider = get_llm_provider("gemini") # override: use Gemini

Supported values of LLM_PROVIDER:
    gemini   → GeminiProvider  (default, needs GEMINI_API_KEY)
    ollama   → OllamaProvider  (local, needs Ollama running)

Switching from API to local:
    # development
    LLM_PROVIDER=gemini
    GEMINI_API_KEY=your_key

    # private enterprise deployment
    LLM_PROVIDER=ollama
    OLLAMA_MODEL=llama3.1
    OLLAMA_BASE_URL=http://localhost:11434
"""

from __future__ import annotations

import os
from typing import Optional

from backend.llm.base import LLMProvider


_SUPPORTED_PROVIDERS = ("gemini", "ollama")


def get_llm_provider(provider: Optional[str] = None) -> LLMProvider:
    """
    Factory function — instantiates the correct LLMProvider.

    Args:
        provider: Provider name override. If None, reads LLM_PROVIDER env var.
                  Defaults to "gemini" if neither is set.

    Returns:
        A fully configured LLMProvider instance ready to use.

    Raises:
        ValueError: If the provider name is not supported.
        ValueError: If required env vars (e.g. GEMINI_API_KEY) are missing.
    """
    name = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower().strip()

    if name == "gemini":
        from backend.llm.gemini_provider import GeminiProvider
        return GeminiProvider(
            api_key=os.getenv("GEMINI_API_KEY"),
            model=os.getenv("GEMINI_MODEL"),
        )

    if name == "ollama":
        from backend.llm.ollama_provider import OllamaProvider
        return OllamaProvider(
            model=os.getenv("OLLAMA_MODEL"),
            base_url=os.getenv("OLLAMA_BASE_URL"),
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER='{name}'. "
        f"Supported: {', '.join(_SUPPORTED_PROVIDERS)}"
    )
