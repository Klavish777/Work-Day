"""LLM abstraction layer.

The trading system depends only on the LLMProvider interface, so the model
can be swapped (OpenAI, local Ollama/LM Studio/llama.cpp, any
OpenAI-compatible endpoint) without touching trading code.
"""
from llm.base import LLMProvider, LLMError, NullProvider
from llm.factory import build_provider

__all__ = ["LLMProvider", "LLMError", "NullProvider", "build_provider"]
