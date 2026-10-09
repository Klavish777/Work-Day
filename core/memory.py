"""Agent Memory — persistent learning from the trade history.

The agents remember every closed trade in data/agent_memory.json:
  * baseline statistics are backfilled from the FULL trade journal;
  * per-strategy results (which library strategies voted at entry) are
    accumulated live; each closed trade shifts the strategy weights
    (EWMA of win/loss), so good strategies gain influence and bad ones
    lose it. Nothing here can change user settings — memory only affects
    the analytical weighting inside the AI pipeline.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, List, Optional

from core.paths import MEMORY_FILE

LOG = logging.getLogger("workday.memory")

SCHEMA = 1
EWMA_ALPHA = 0.25          # responsiveness of the learning curve
WEIGHT_MIN, WEIGHT_MAX = 0.25, 2.0
# Volodya self-tuning: wins lower the entry threshold slightly, losses
# raise it. Clamped so the brain can never become reckless.
VOL_PARAMS = {"entry_net": 1.2}
TUNE_WIN, TUNE_LOSS = 0.97, 1.04
ENTRY_NET_MIN, ENTRY_NET_MAX = 0.9, 2.2


class AgentMemory:
    """Thread-safe persistent memory of trade outcomes per strategy."""

    def __init__(self, path: Optional[str] = None):
        self._path = str(path or MEMORY_FILE)
        self._lock = threading.Lock()
        self._data: Dict = {"schema": SCHEMA,
                            "baseline": {"closed": 0, "wins": 0,
                                         "losses": 0, "pnl": 0.0},
                            "strategies": {},
                            "entries": {},
                            "volodya": dict(VOL_PARAMS)}
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
                self._data.update(raw)
        except (OSError, ValueError):
            pass  # first start or damaged file -> start clean

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except OSError as exc:
            LOG.warning("AgentMemory save failed: %s", exc)

    def _strat(self, name: str) -> Dict:
        s = self._data["strategies"].setdefault(
            name, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                   "ewma": 0.0, "weight": 1.0})
        return s

    # ------------------------------------------------------------------ #
    def backfill_from_journal(self, trades: List[Dict]) -> None:
        """Ingest the full historical journal once (baseline counters)."""
        with self._lock:
            closed = [t for t in (trades or [])
                      if t.get("profit") is not None]
            self._data["baseline"] = {
                "closed": len(closed),
                "wins": sum(1 for t in closed if t["profit"] > 0),
                "losses": sum(1 for t in closed if t["profit"] < 0),
                "pnl": round(sum(float(t["profit"]) for t in closed), 2),
            }
            self._save()

    def on_entry(self, ticket, strategies: List[str]) -> None:
        """Remember which library strategies voted at this entry."""
        with self._lock:
            self._data["entries"][str(ticket)] = list(strategies or [])
            self._save()

    def on_close(self, ticket, profit: Optional[float],
                 strategies: Optional[List[str]] = None) -> None:
        """A trade was closed -> learn from the outcome."""
        if profit is None:
            return
        profit = float(profit)
        win = profit > 0
        with self._lock:
            b = self._data["baseline"]
            b["closed"] += 1
            b["wins"] += 1 if win else 0
            b["losses"] += 0 if win else 1
            b["pnl"] = round(b.get("pnl", 0.0) + profit, 2)

            names = strategies
            if names is None:
                names = self._data["entries"].pop(str(ticket), [])
            else:
                self._data["entries"].pop(str(ticket), None)

            # Volodya self-tuning: adjust the entry threshold from the outcome
            v = self._data.setdefault("volodya", dict(VOL_PARAMS))
            factor = TUNE_WIN if profit > 0 else TUNE_LOSS
            v["entry_net"] = round(max(ENTRY_NET_MIN,
                                       min(ENTRY_NET_MAX,
                                           v.get("entry_net", 1.2) * factor)), 3)

            outcome = 1.0 if win else -1.0
            for name in names:
                s = self._strat(name)
                s["trades"] += 1
                s["wins"] += 1 if win else 0
                s["losses"] += 0 if win else 1
                s["pnl"] = round(s.get("pnl", 0.0) + profit, 2)
                s["ewma"] = round((1 - EWMA_ALPHA) * s.get("ewma", 0.0)
                                  + EWMA_ALPHA * outcome, 4)
                s["weight"] = round(max(WEIGHT_MIN,
                                        min(WEIGHT_MAX, 1.0 + s["ewma"])), 2)
            self._save()

    # ------------------------------------------------------------------ #
    def weights(self) -> Dict[str, float]:
        with self._lock:
            return {name: float(s.get("weight", 1.0))
                    for name, s in self._data.get("strategies", {}).items()}

    def get_params(self) -> Dict:
        with self._lock:
            return dict(self._data.setdefault("volodya", dict(VOL_PARAMS)))

    def summary(self) -> Dict:
        with self._lock:
            strategies = {}
            for name, s in self._data.get("strategies", {}).items():
                strategies[name] = {"trades": s["trades"], "wins": s["wins"],
                                    "pnl": s["pnl"], "weight": s["weight"]}
            return {"baseline": dict(self._data.get("baseline", {})),
                    "strategies": strategies}
