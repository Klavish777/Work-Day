"""LLM provider interface and shared helpers."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class LLMError(Exception):
    pass


class LLMProvider(ABC):
    """Minimal contract every LLM backend must fulfil.

    The trading system only ever calls chat_json(); providers return parsed
    JSON dicts or raise LLMError. No provider has any access to the trading
    API — it merely receives text and returns text.
    """

    name: str = "abstract"

    @abstractmethod
    def chat_json(self, system: str, user: str) -> Dict[str, Any]:
        """Send a chat request and return the parsed JSON object answer."""

    def is_available(self) -> bool:
        return True


class NullProvider(LLMProvider):
    """Used when the LLM is switched off; agents fall back to deterministic
    logic on real market data (clearly labelled, never faked)."""

    name = "off"

    def chat_json(self, system: str, user: str) -> Dict[str, Any]:
        raise LLMError("LLM provider is disabled")

    def is_available(self) -> bool:
        return False


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract the first JSON object from an LLM reply."""
    if not text:
        return None
    text = text.strip()
    # strip code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))
