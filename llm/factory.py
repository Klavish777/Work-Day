"""Build the configured LLM provider. Swapping the LLM is a settings change
only — no trading code is touched."""
from __future__ import annotations

from typing import Any, Dict

from llm.base import LLMProvider, NullProvider
from llm.http_provider import (OpenAICompatibleProvider, OpenAIProvider,
                               resolve_api_key)


def build_provider(llm_cfg: Dict[str, Any]) -> LLMProvider:
    kind = (llm_cfg.get("provider") or "off").lower()
    model = llm_cfg.get("model") or "gpt-4o-mini"
    temperature = float(llm_cfg.get("temperature", 0.1))
    max_tokens = int(llm_cfg.get("max_tokens", 700))
    timeout_sec = int(llm_cfg.get("timeout_sec", 30))
    api_key = resolve_api_key(llm_cfg)

    if kind == "openai":
        return OpenAIProvider(api_key=api_key, model=model,
                              temperature=temperature, max_tokens=max_tokens,
                              timeout_sec=timeout_sec)
    if kind == "openai_compatible":
        base_url = llm_cfg.get("base_url") or "http://127.0.0.1:11434/v1"
        return OpenAICompatibleProvider(base_url=base_url, api_key=api_key,
                                        model=model, temperature=temperature,
                                        max_tokens=max_tokens,
                                        timeout_sec=timeout_sec)
    return NullProvider()
