"""AI Agent #3 — RISK & DECISION ANALYST.

Independent controller. It receives the two other agents' outputs, the
account state, drawdown, open position, lot size, TP/SL and recent trade
history, and decides whether the proposed trade fits risk limits.

Deterministic rules always win: the LLM may make the verdict STRICTER, but
never softer than the hard checks.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from agents import strategy_library as slib
from agents.common import ask_llm, compact
from core.models import (AccountView, MarketAnalysis, PositionView,
                         RiskVerdict, StrategySignal, SymbolView, Tick)
from llm.prompts import RISK_ANALYST_SYSTEM

LOG = logging.getLogger("workday.risk_analyst")


class RiskAnalystAgent:
    name = "risk_analyst"

    def __init__(self, provider, llm_flags: Dict[str, bool]):
        self._provider = provider
        self._llm_enabled = bool(llm_flags.get("risk_analyst", True))

    # ------------------------------------------------------------------ #
    def review(self, market: MarketAnalysis, strategy: StrategySignal,
               account: Optional[AccountView], position: Optional[PositionView],
               settings: Dict, recent_trades: List[Dict],
               tick: Optional[Tick], sym: Optional[SymbolView]) -> RiskVerdict:
        rejects: List[str] = []
        warnings: List[str] = []

        min_conf = float(settings.get("min_confidence", 0.65))
        min_rr = float(settings.get("min_risk_reward", 1.2))
        max_spread = float(settings.get("max_spread_points", 30))
        max_lot = float(settings.get("max_lot", 0.10))
        lot = float(settings.get("lot_size", 0.01))
        max_daily_loss = float(settings.get("max_daily_loss_usd", 5.0))

        if position is not None:
            rejects.append("A position is already open — only one position "
                           "is allowed at a time.")
        if strategy.action not in ("BUY", "SELL"):
            rejects.append(f"Strategy Trader proposes {strategy.action}, "
                           "nothing to approve.")
        elif market.signal != strategy.action:
            # a strategy signal backed by a clear dominance of the strategy
            # library is accepted even when the Market Analyst is neutral
            backed = False
            votes = getattr(market, "strategies", None)
            if votes:
                lside, _ = slib.dominant_side(votes)
                backed = (lside == strategy.action)
            if not backed:
                rejects.append(f"Agents contradict each other: market "
                               f"{market.signal} vs strategy {strategy.action}.")
        elif strategy.confidence < min_conf:
            rejects.append(f"Signal too weak: confidence "
                           f"{strategy.confidence:.2f} < required {min_conf}.")

        if strategy.action in ("BUY", "SELL"):
            if strategy.risk_reward < min_rr:
                rejects.append(f"Risk/reward {strategy.risk_reward:.2f} below "
                               f"minimum {min_rr}.")
            if settings.get("require_sl", True) and strategy.stop_loss <= 0:
                rejects.append("Stop-loss is required but missing.")
            if lot > max_lot:
                rejects.append(f"Lot {lot} exceeds max lot {max_lot}.")
            if sym is not None and lot < sym.volume_min:
                rejects.append(f"Lot {lot} below broker minimum "
                               f"{sym.volume_min}.")

        if tick is not None and tick.spread_points > max_spread:
            rejects.append(f"Spread {tick.spread_points:.1f} pts exceeds max "
                           f"{max_spread} pts.")

        if account is not None and max_daily_loss > 0:
            day_loss = account.balance - account.equity
            if day_loss >= max_daily_loss:
                rejects.append(f"Daily loss limit reached: -{day_loss:.2f} "
                               f">= {max_daily_loss:.2f}.")
            elif day_loss >= max_daily_loss * 0.6:
                warnings.append("Approaching daily loss limit.")

        losses_streak = 0
        for t in recent_trades[-3:]:
            if (t.get("profit") or 0) < 0:
                losses_streak += 1
        if len(recent_trades) >= 3 and losses_streak == 3:
            warnings.append("Three consecutive losing trades — cooling down "
                            "is recommended.")
            if not rejects:
                rejects.append("Loss streak: wait for the market to settle.")

        risk_level = "LOW"
        if warnings:
            risk_level = "MEDIUM"
        if market.volatility == "HIGH" or market.reversal_risk == "HIGH":
            risk_level = "MEDIUM" if risk_level == "LOW" else "HIGH"
        if rejects:
            risk_level = "HIGH"

        approved = not rejects and strategy.action in ("BUY", "SELL")
        recommendation = ("EXECUTE" if approved else
                          "REJECT" if rejects else "WAIT")

        det = RiskVerdict(
            approved=approved,
            risk=risk_level,
            confidence=round(min(0.99, strategy.confidence), 3),
            recommendation=recommendation,
            reason="; ".join(rejects or warnings or ["All risk checks passed."]),
            checks=[f"OK" if not rejects else f"FAIL: {r}" for r in
                    (rejects or ["all checks"])][:10],
            source="DETERMINISTIC",
        )

        llm_answer = ask_llm(
            self._provider, self._llm_enabled,
            RISK_ANALYST_SYSTEM,
            self._build_user_prompt(market, strategy, account, position,
                                    settings, recent_trades, tick),
            self.name,
        )
        if llm_answer is not None:
            return self._merge_llm(det, llm_answer)
        return det

    # ------------------------------------------------------------------ #
    def _build_user_prompt(self, market, strategy, account, position,
                           settings, recent_trades, tick) -> str:
        payload = {
            "market_analysis": market.to_dict(),
            "strategy_proposal": strategy.to_dict(),
            "account": account.to_dict() if account else None,
            "open_position": position.to_dict() if position else None,
            "tick": tick.to_dict() if tick else None,
            "risk_settings": {
                "lot_size": settings.get("lot_size"),
                "max_lot": settings.get("max_lot"),
                "min_confidence": settings.get("min_confidence"),
                "min_risk_reward": settings.get("min_risk_reward"),
                "max_spread_points": settings.get("max_spread_points"),
                "max_daily_loss_usd": settings.get("max_daily_loss_usd"),
                "max_trades_per_day": settings.get("max_trades_per_day"),
            },
            "recent_trades": recent_trades[-10:],
        }
        return ("Full context:\n" + compact(payload, 8000) +
                "\n\nRespond with the JSON verdict.")

    @staticmethod
    def _merge_llm(base: RiskVerdict, ans: Dict) -> RiskVerdict:
        try:
            llm_approved = bool(ans.get("approved", False))
        except (TypeError, ValueError):
            llm_approved = False
        rec = str(ans.get("recommendation", "")).upper()
        if rec not in ("EXECUTE", "WAIT", "REJECT"):
            rec = base.recommendation
        risk = str(ans.get("risk", base.risk)).upper()
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if risk not in order or order.get(risk, 2) < order[base.risk]:
            risk = base.risk
        try:
            conf = float(ans.get("confidence", base.confidence))
        except (TypeError, ValueError):
            conf = base.confidence

        approved = base.approved and llm_approved
        if not llm_approved and base.approved:
            rec = "REJECT"
        reason = base.reason
        if str(ans.get("reason", "")):
            reason = f"{base.reason} | LLM: {str(ans['reason'])[:300]}"
        return RiskVerdict(
            approved=approved,
            risk=risk,
            confidence=round(max(0.0, min(1.0, conf)), 3),
            recommendation=rec if not approved or rec == "EXECUTE" else
            ("EXECUTE" if approved else rec),
            reason=reason[:700],
            checks=base.checks,
            source="LLM",
        )
