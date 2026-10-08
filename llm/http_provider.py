"""OpenAI-compatible chat provider (works for OpenAI itself and for local
servers such as Ollama, LM Studio, llama.cpp server, vLLM, LiteLLM...)."""
from __future__ import annotations

import os
from typing import Any, Dict

import requests

from llm.base import LLMError, LLMProvider, extract_json


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.1, max_tokens: int = 700,
                 timeout_sec: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec

    # ------------------------------------------------------------------ #
    def chat_json(self, system: str, user: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout_sec,
            )
        except requests.RequestException as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response format: {exc}") from exc

        parsed = extract_json(content)
        if parsed is None:
            raise LLMError("LLM did not return valid JSON")
        return parsed


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(
            base_url="https://api.openai.com/v1",
            api_key=api_key,
            model=model,
            **kwargs,
        )


def resolve_api_key(cfg: Dict[str, Any]) -> str:
    """API key comes from settings or from an environment variable."""
    key = cfg.get("api_key") or ""
    env_name = cfg.get("api_key_env") or ""
    if not key and env_name:
        key = os.environ.get(env_name, "")
    return key
