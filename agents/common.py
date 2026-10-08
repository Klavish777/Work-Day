"""Shared helpers for all AI agents."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.models import SymbolView
from llm.base import LLMError, LLMProvider

LOG = logging.getLogger("workday.agents")


def ask_llm(provider: Optional[LLMProvider], enabled: bool, system: str,
            user: str, agent_name: str) -> Optional[Dict[str, Any]]:
    """Ask the LLM for a JSON answer. Returns None when the LLM is disabled,
    unavailable or fails — callers must always have a deterministic path."""
    if not enabled or provider is None or not provider.is_available():
        return None
    try:
        return provider.chat_json(system, user)
    except LLMError as exc:
        LOG.warning("[%s] LLM call failed, using deterministic logic: %s",
                    agent_name, exc)
        return None


def compact(obj: Any, limit: int = 6000) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text[:limit]


# --------------------------------------------------------------------------- #
# Money <-> price distance conversion.
# For FX: profit(account ccy) = volume * (distance / tick_size) * tick_value
# --------------------------------------------------------------------------- #
def price_distance_for_profit(sym: Optional[SymbolView], lot: float,
                              profit: float) -> Optional[float]:
    if sym is None or lot <= 0 or profit <= 0:
        return None
    if sym.tick_value <= 0 or sym.tick_size <= 0:
        return None
    return profit * sym.tick_size / (lot * sym.tick_value)


def profit_for_price_distance(sym: Optional[SymbolView], lot: float,
                              distance: float) -> Optional[float]:
    if sym is None or lot <= 0 or distance <= 0:
        return None
    if sym.tick_value <= 0 or sym.tick_size <= 0:
        return None
    return lot * (distance / sym.tick_size) * sym.tick_value
