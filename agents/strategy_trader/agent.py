"""AI Agent #2 — STRATEGY TRADER.

Responsibility: turn the market picture into a concrete trade plan:
BUY/SELL/HOLD, entry, TP, SL, risk/reward, exit conditions.
It NEVER sends orders to MT5 — it only proposes.

TP/SL prices are derived from the user-configured dollar targets through the
real tick value of the symbol (or from explicit points, or ATR as last
resort), so the math matches the broker's actual P/L calculation.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from agents import indicators as ta
from agents.common import (ask_llm, compact, price_distance_for_profit,
                           profit_for_price_distance)
from core.models import (Candle, MarketAnalysis, PositionView, StrategySignal,
                         SymbolView, Tick)
from llm.prompts import STRATEGY_TRADER_SYSTEM

LOG = logging.getLogger("workday.strategy_trader")


class StrategyTraderAgent:
    name = "strategy_trader"

    def __init__(self, provider, llm_flags: Dict[str, bool]):
        self._provider = provider
        self._llm_enabled = bool(llm_flags.get("strategy_trader", True))

    # ------------------------------------------------------------------ #
    def plan(self, market: MarketAnalysis, tick: Optional[Tick],
             tf_candles: Dict[str, List[Candle]], settings: Dict,
             position: Optional[PositionView],
             sym: Optional[SymbolView]) -> StrategySignal:
        if position is not None:
            return StrategySignal(
                action="HOLD",
                confidence=0.0,
                reason=(f"Position #{position.ticket} is already open "
                        f"({position.side} {position.lot}). Exit is managed by "
                        f"the profit target / stop rules; no new entry."),
                exit_conditions="profit target or stop loss",
                source="DETERMINISTIC",
            )

        if tick is None:
            return StrategySignal(action="HOLD", reason="No live tick data.")

        side = market.signal
        if side not in ("BUY", "SELL"):
            return StrategySignal(
                action="HOLD",
                confidence=market.confidence,
                reason=f"Market Analyst is not directional ({market.trend}, "
                       f"score confidence {market.confidence:.2f}) — wait.",
                source="DETERMINISTIC",
            )

        lot = float(settings.get("lot_size", 0.01))
        point = sym.point if sym else 0.00001

        # ---- ATR of the reference timeframe, used as fallback distance ----
        atr_v = None
        for tf in ("M15", "M5", "M30", "H1"):
            if tf in tf_candles and len(tf_candles[tf]) > 20:
                c = tf_candles[tf]
                a = ta.atr([x.high for x in c], [x.low for x in c],
                           [x.close for x in c], 14)[-1]
                if a:
                    atr_v = a
                    break

        tp_points = float(settings.get("take_profit_points", 0) or 0)
        sl_points = float(settings.get("stop_loss_points", 0) or 0)
        tp_dist = tp_points * point if tp_points > 0 else \
            price_distance_for_profit(sym, lot, float(settings["profit_target_usd"]))
        sl_dist = sl_points * point if sl_points > 0 else \
            price_distance_for_profit(sym, lot, float(settings.get("stop_loss_usd", 0) or 0))
        if not tp_dist and atr_v:
            tp_dist = atr_v * 1.5
        if not sl_dist and atr_v:
            sl_dist = atr_v * 1.2

        if settings.get("require_sl", True) and not sl_dist:
            return StrategySignal(
                action="HOLD",
                reason="Cannot compute stop-loss distance (no tick value, "
                       "no ATR) — refusing to plan an unprotected trade.",
                source="DETERMINISTIC",
            )

        entry = tick.ask if side == "BUY" else tick.bid
        if side == "BUY":
            tp_price = entry + tp_dist if tp_dist else 0.0
            sl_price = entry - sl_dist if sl_dist else 0.0
        else:
            tp_price = entry - tp_dist if tp_dist else 0.0
            sl_price = entry + sl_dist if sl_dist else 0.0

        tp_usd = profit_for_price_distance(sym, lot, tp_dist) or \
            float(settings.get("profit_target_usd", 0.0))
        sl_usd = profit_for_price_distance(sym, lot, sl_dist) or \
            float(settings.get("stop_loss_usd", 0.0))
        rr = (tp_dist / sl_dist) if (tp_dist and sl_dist) else 0.0

        min_conf = float(settings.get("min_confidence", 0.65))
        min_rr = float(settings.get("min_risk_reward", 1.2))
        action = side if (market.confidence >= min_conf and rr >= min_rr) else "HOLD"

        confidence = round(min(0.97, market.confidence *
                               (0.85 + 0.15 * min(rr, 2.0) / 2.0)), 3)
        det = StrategySignal(
            action=action,
            entry=round(entry, 6),
            take_profit=round(tp_price, 6),
            stop_loss=round(sl_price, 6),
            tp_usd=round(tp_usd, 2),
            sl_usd=round(sl_usd, 2),
            risk_reward=round(rr, 2),
            confidence=confidence if action != "HOLD" else round(market.confidence, 3),
            reason=(f"{side} plan: entry {entry:.5f}, TP +{tp_dist:.5f} "
                    f"(~${tp_usd:.2f}), SL -{sl_dist:.5f} (~${sl_usd:.2f}), "
                    f"R:R {rr:.2f}. Market confidence {market.confidence:.2f}."
                    if action != "HOLD" else
                    f"HOLD: confidence {market.confidence:.2f} < {min_conf} or "
                    f"R:R {rr:.2f} < {min_rr}."),
            exit_conditions=(f"Auto-close at profit >= ${tp_usd:.2f}; protective "
                             f"close at loss >= ${sl_usd:.2f}; early exit on "
                             f"M15 trend reversal against the position."),
            source="DETERMINISTIC",
        )

        llm_answer = ask_llm(
            self._provider, self._llm_enabled,
            STRATEGY_TRADER_SYSTEM,
            self._build_user_prompt(market, tick, settings, det),
            self.name,
        )
        if llm_answer is not None:
            merged = self._merge_llm(det, llm_answer, tick)
            if merged is not None:
                merged.source = "LLM"
                return merged
            det.source = "LLM_FALLBACK_DETERMINISTIC"
        return det

    # ------------------------------------------------------------------ #
    def _build_user_prompt(self, market: MarketAnalysis, tick: Tick,
                           settings: Dict, plan: StrategySignal) -> str:
        payload = {
            "market_analysis": market.to_dict(),
            "tick": tick.to_dict(),
            "deterministic_plan": plan.to_dict(),
            "limits": {
                "lot_size": settings.get("lot_size"),
                "profit_target_usd": settings.get("profit_target_usd"),
                "min_profit_usd": settings.get("min_profit_usd"),
                "max_profit_usd": settings.get("max_profit_usd"),
                "stop_loss_usd": settings.get("stop_loss_usd"),
                "min_confidence": settings.get("min_confidence"),
                "min_risk_reward": settings.get("min_risk_reward"),
            },
        }
        return ("Market context:\n" + compact(payload, 8000) +
                "\n\nConfirm or adjust the plan. Respond with the JSON object.")

    @staticmethod
    def _merge_llm(base: StrategySignal, ans: Dict,
                   tick: Tick) -> Optional[StrategySignal]:
        action = str(ans.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            return None
        try:
            conf = float(ans.get("confidence", base.confidence))
        except (TypeError, ValueError):
            conf = base.confidence
        conf = max(0.0, min(1.0, conf))

        # The LLM may veto or confirm, but prices/levels always come from the
        # deterministic math tied to broker tick value.
        if action == "HOLD":
            return StrategySignal(
                action="HOLD", entry=base.entry, take_profit=base.take_profit,
                stop_loss=base.stop_loss, tp_usd=base.tp_usd, sl_usd=base.sl_usd,
                risk_reward=base.risk_reward, confidence=conf,
                reason=f"LLM chose to wait: {str(ans.get('reason', ''))[:300]}",
                exit_conditions=base.exit_conditions, source="LLM",
            )
        if action != base.action and base.action == "HOLD":
            # LLM cannot upgrade a HOLD produced by hard thresholds.
            return None
        return StrategySignal(
            action=action, entry=base.entry, take_profit=base.take_profit,
            stop_loss=base.stop_loss, tp_usd=base.tp_usd, sl_usd=base.sl_usd,
            risk_reward=base.risk_reward, confidence=conf,
            reason=str(ans.get("reason", base.reason))[:500],
            exit_conditions=str(ans.get("exit_conditions", base.exit_conditions))[:300],
            source="LLM",
        )
