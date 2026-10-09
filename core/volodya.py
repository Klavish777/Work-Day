"""ВОЛОДЯ — the built-in trading brain (no external LLM required).

Volodya is a proprietary, self-tuning expert core that was built around the
strategy library: it consumes the weighted votes of all 13 strategies
(12 classics + the proprietary meta-strategy), the Market Analyst picture,
the Risk Analyst verdict and the live position, and produces the final
structured decision BUY / SELL / HOLD / CLOSE — exactly like the central
LLM manager would, but deterministically and offline.

Learning: the entry threshold (params["entry_net"]) is tuned online from
every closed trade — wins make Volodya slightly more aggressive, losses
make it more cautious (see AgentMemory.tune_after_trade). Strategy weights
themselves are learned per-strategy from the trade history.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from agents import strategy_library as slib
from core.models import (AccountView, ConsensusResult, LLMDecision,
                         MarketAnalysis, PositionView, RiskVerdict,
                         StrategySignal, Tick)

LOG = logging.getLogger("workday.volodya")

#: minimum number of strategies that must vote for the entry side
ENTRY_MIN_VOTES = 4


class VolodyaCore:
    name = "volodya"

    def __init__(self, memory):
        self.memory = memory

    # ------------------------------------------------------------------ #
    def decide(self, votes: Dict[str, Dict], market: MarketAnalysis,
               strategy: Optional[StrategySignal],
               risk: Optional[RiskVerdict],
               consensus: Optional[ConsensusResult],
               settings: Dict, account: Optional[AccountView],
               position: Optional[PositionView], tick: Optional[Tick],
               recent_trades: List[Dict]) -> LLMDecision:
        weights = self.memory.weights() if self.memory else {}
        params = self.memory.get_params() if self.memory else {"entry_net": 1.2}
        entry_net = float(params.get("entry_net", 1.2))
        agg = slib.aggregate(votes or {}, weights)

        target = float(settings.get("profit_target_usd", 0.5))
        sl_usd = float(settings.get("stop_loss_usd", 1.0) or 0)

        # ---------------- position management ------------------------- #
        if position is not None:
            if position.profit >= target:
                return LLMDecision(
                    action="CLOSE", confidence=0.92, take_profit_usd=target,
                    reason=(f"Володя: прибыль ${position.profit:.2f} достигла "
                            f"цели ${target:.2f} — фиксирую."),
                    source="VOLODYA")
            if sl_usd > 0 and position.profit <= -sl_usd:
                return LLMDecision(
                    action="CLOSE", confidence=0.9,
                    reason=(f"Володя: убыток ${position.profit:.2f} достиг "
                            f"стоп-лимита ${sl_usd:.2f} — закрываю."),
                    source="VOLODYA")
            against = (agg["sell_score"] if position.side == "BUY"
                       else agg["buy_score"])
            if against >= 2.2:
                opposite = "SELL" if position.side == "BUY" else "BUY"
                return LLMDecision(
                    action="CLOSE", confidence=0.8,
                    reason=(f"Володя: голоса развернулись против позиции — "
                            f"вес {against:.2f} за {opposite}. Выхожу."),
                    source="VOLODYA")
            return LLMDecision(
                action="HOLD", confidence=0.5,
                reason=(f"Володя: позиция {position.side} #{position.ticket}, "
                        f"P/L ${position.profit:.2f}, цель ${target:.2f}. "
                        f"Голоса: BUY {agg['buy_n']} / SELL {agg['sell_n']} "
                        f"(net {agg['net']:+.2f})."),
                source="VOLODYA")

        # ---------------- entry logic ---------------------------------- #
        side = agg["side"]
        n_side = agg["buy_n"] if side == "BUY" else agg["sell_n"]
        if side == "NEUTRAL" or abs(agg["net"]) < entry_net:
            return LLMDecision(
                action="HOLD", confidence=0.25,
                reason=(f"Володя: нет доминирования стратегий — счёт "
                        f"{agg['net']:+.2f} при пороге {entry_net:.2f} "
                        f"(BUY {agg['buy_n']} / SELL {agg['sell_n']}). "
                        f"Жду усиления сигнала."),
                source="VOLODYA")
        if n_side < ENTRY_MIN_VOTES:
            return LLMDecision(
                action="HOLD", confidence=0.25,
                reason=(f"Володя: за {side} только {n_side} стратегий из "
                        f"нужных {ENTRY_MIN_VOTES}. Жду подтверждения."),
                source="VOLODYA")
        if market.signal in ("BUY", "SELL") and market.signal != side \
                and market.confidence >= 0.6:
            return LLMDecision(
                action="HOLD", confidence=0.3,
                reason=(f"Володя: библиотека за {side}, но аналитик рынка "
                        f"уверенно за {market.signal} — конфликт, не вхожу."),
                source="VOLODYA")

        conf = round(min(0.95, 0.42 + 0.06 * abs(agg["net"])), 3)
        risk_note = ("Риск-аналитик одобрил" if (risk and risk.approved)
                     else "Риск-аналитик: жду одобрения")
        return LLMDecision(
            action=side, confidence=conf, take_profit_usd=target,
            reason=(f"Володя: {n_side} стратегий за {side}, взвешенный счёт "
                    f"{agg['net']:+.2f} (порог {entry_net:.2f}). Тренд: "
                    f"{market.trend} ({market.trend_strength}), волатильность "
                    f"{market.volatility}. {risk_note}."),
            source="VOLODYA")
