"""Central LLM Trading Manager (Decision Engine).

Receives everything (price, bid/ask, spread, trend, volatility, indicators on
multiple timeframes, open position, previous trade results, account state,
agent recommendations) and produces the final structured decision:
BUY / SELL / HOLD / CLOSE.

Safety: whatever the LLM answers is forcibly coerced so it can NEVER
  - open a second position while one is open,
  - act against the agents' consensus,
  - act without the Risk Analyst approval.
The deterministic Risk Manager and the Execution Engine validate it again.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from agents.common import ask_llm, compact
from core.models import (AccountView, ConsensusResult, LLMDecision,
                         MarketAnalysis, PositionView, RiskVerdict,
                         StrategySignal, Tick)
from llm.prompts import CENTRAL_LLM_SYSTEM

LOG = logging.getLogger("workday.central_llm")


class CentralDecisionEngine:
    def __init__(self, provider, llm_flags: Dict[str, bool]):
        self._provider = provider
        self._llm_enabled = bool(llm_flags.get("central", True))

    # ------------------------------------------------------------------ #
    def decide(self, market: MarketAnalysis, strategy: StrategySignal,
               risk: RiskVerdict, consensus: ConsensusResult,
               settings: Dict, account: Optional[AccountView],
               position: Optional[PositionView], tick: Optional[Tick],
               recent_trades: List[Dict]) -> LLMDecision:
        det = self._deterministic(market, strategy, risk, consensus,
                                  settings, position)
        llm_answer = ask_llm(
            self._provider, self._llm_enabled,
            CENTRAL_LLM_SYSTEM,
            self._build_user_prompt(market, strategy, risk, consensus,
                                    settings, account, position, tick,
                                    recent_trades),
            "central_llm",
        )
        if llm_answer is not None:
            return self._coerce(llm_answer, market, strategy, risk,
                                consensus, position, settings)
        det.source = "DETERMINISTIC"
        return det

    # ------------------------------------------------------------------ #
    def _deterministic(self, market, strategy, risk, consensus,
                       settings, position) -> LLMDecision:
        target = float(settings.get("profit_target_usd", 0.5))
        if position is not None:
            if position.profit >= target:
                return LLMDecision(
                    action="CLOSE", confidence=0.9, take_profit_usd=target,
                    reason=(f"Position #{position.ticket} profit "
                            f"${position.profit:.2f} reached target "
                            f"${target:.2f} — close."),
                    source="DETERMINISTIC")
            return LLMDecision(
                action="HOLD", confidence=0.5,
                reason=(f"Position #{position.ticket} open, P/L "
                        f"${position.profit:.2f} below target ${target:.2f}. "
                        f"Keep and monitor."),
                source="DETERMINISTIC")

        if consensus.executable and risk.approved and \
                strategy.action in ("BUY", "SELL"):
            conf = round(min(market.confidence, strategy.confidence), 3)
            return LLMDecision(
                action=consensus.side, confidence=conf,
                take_profit_usd=strategy.tp_usd,
                reason=(f"Full consensus: {consensus.reason} "
                        f"Risk: {risk.risk}."),
                source="DETERMINISTIC")
        reason = consensus.reason or risk.reason or "No actionable signal."
        return LLMDecision(action="HOLD", confidence=0.2, reason=reason,
                           source="DETERMINISTIC")

    # ------------------------------------------------------------------ #
    def _build_user_prompt(self, market, strategy, risk, consensus,
                           settings, account, position, tick,
                           recent_trades) -> str:
        per_tf = [a.to_dict() for a in market.per_timeframe]
        payload = {
            "symbol": settings.get("symbol"),
            "tick": tick.to_dict() if tick else None,
            "market_summary": {
                "trend": market.trend,
                "trend_strength": market.trend_strength,
                "volatility": market.volatility,
                "momentum": market.momentum,
                "reversal_risk": market.reversal_risk,
                "buy_probability": market.buy_probability,
                "sell_probability": market.sell_probability,
                "support": market.support,
                "resistance": market.resistance,
            },
            "indicators_per_timeframe": per_tf,
            "open_position": position.to_dict() if position else None,
            "account": account.to_dict() if account else None,
            "recent_trades": recent_trades[-10:],
            "agent_recommendations": {
                "market_analyst": {"signal": market.signal,
                                   "confidence": market.confidence},
                "strategy_trader": {"action": strategy.action,
                                    "confidence": strategy.confidence,
                                    "tp_usd": strategy.tp_usd,
                                    "sl_usd": strategy.sl_usd,
                                    "risk_reward": strategy.risk_reward},
                "risk_analyst": {"approved": risk.approved,
                                 "risk": risk.risk,
                                 "recommendation": risk.recommendation},
                "consensus": consensus.to_dict(),
            },
            "constraints": {
                "mode": settings.get("mode"),
                "lot_size": settings.get("lot_size"),
                "max_lot": settings.get("max_lot"),
                "profit_target_usd": settings.get("profit_target_usd"),
                "stop_loss_usd": settings.get("stop_loss_usd"),
                "max_spread_points": settings.get("max_spread_points"),
            },
        }
        return ("Full system context:\n" + compact(payload, 9000) +
                "\n\nMake the final decision. Respond with the JSON object.")

    # ------------------------------------------------------------------ #
    def _coerce(self, ans: Dict, market, strategy, risk, consensus,
                position, settings) -> LLMDecision:
        """Validate and forcibly constrain the raw LLM answer."""
        action = str(ans.get("action", "HOLD")).upper()
        if action not in ("BUY", "SELL", "HOLD", "CLOSE"):
            action = "HOLD"
        try:
            conf = float(ans.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        try:
            tp_usd = float(ans.get("take_profit_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            tp_usd = 0.0
        tp_usd = max(0.0, min(100.0, tp_usd))
        reason = str(ans.get("reason", ""))[:500]
        notes = []

        # --- hard coercions (LLM cannot break engine invariants) ---------- #
        if action in ("BUY", "SELL"):
            if position is not None:
                notes.append("COERCED: a position is already open — only one "
                             "position allowed; forced HOLD.")
                action = "HOLD"
            elif not consensus.executable or consensus.side != action:
                notes.append("COERCED: agents' consensus does not support "
                             f"{action}; forced HOLD.")
                action = "HOLD"
            elif not risk.approved:
                notes.append("COERCED: Risk Analyst did not approve; "
                             "forced HOLD.")
                action = "HOLD"
            else:
                # confidence cannot exceed the agents' own confidence
                conf = min(conf, market.confidence, strategy.confidence)
        elif action == "CLOSE" and position is None:
            notes.append("COERCED: nothing to close; forced HOLD.")
            action = "HOLD"

        if notes:
            reason = (reason + " | " + " ".join(notes)).strip(" |")
        return LLMDecision(action=action, confidence=round(conf, 3),
                           reason=reason or "LLM decision.",
                           take_profit_usd=tp_usd, raw=ans, source="LLM")
