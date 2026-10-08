"""Risk Manager — the deterministic HARD GATE.

The central LLM decides, but NOTHING reaches the MT5 Execution Engine without
passing these checks. The LLM has no API to bypass this module.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from core.models import (AccountView, ConsensusResult, EngineState,
                         LLMDecision, MarketAnalysis, PositionView,
                         RiskGateResult, RiskVerdict, StrategySignal,
                         SymbolView, Tick)


class RiskManager:
    def evaluate(self, *, settings: Dict, engine_state: str,
                 decision: LLMDecision, market: Optional[MarketAnalysis],
                 strategy: Optional[StrategySignal],
                 risk_verdict: Optional[RiskVerdict],
                 consensus: Optional[ConsensusResult],
                 account: Optional[AccountView],
                 position: Optional[PositionView], tick: Optional[Tick],
                 sym: Optional[SymbolView],
                 trades_today: int, realized_loss_today: float) -> RiskGateResult:
        fails: List[str] = []
        warns: List[str] = []

        # ---- engine / permission state --------------------------------- #
        if engine_state != EngineState.RUNNING.value:
            fails.append(f"Engine is not RUNNING (state={engine_state}).")
        if not settings.get("auto_trading_enabled", False):
            fails.append("Automatic trading is disabled by the user.")
        if decision.action not in ("BUY", "SELL"):
            fails.append(f"Decision {decision.action} is not an entry action.")

        # ---- mode guard ------------------------------------------------- #
        if settings.get("mode") == "REAL" and not settings.get("real_confirmed"):
            fails.append("REAL mode is not confirmed by the user.")

        # ---- single position rule (critical requirement) --------------- #
        if position is not None:
            fails.append("An open position already exists — a second "
                         "position is forbidden.")

        # ---- AI chain consistency --------------------------------------- #
        if consensus is None or not consensus.executable:
            fails.append("No executable agents consensus.")
        elif decision.action in ("BUY", "SELL") and consensus.side != decision.action:
            fails.append("Decision contradicts the agents consensus.")
        if risk_verdict is None or not risk_verdict.approved:
            fails.append("Risk & Decision Analyst did not approve.")

        # ---- confidence -------------------------------------------------- #
        min_conf = float(settings.get("min_confidence", 0.65))
        if decision.confidence < min_conf:
            fails.append(f"Decision confidence {decision.confidence:.2f} < "
                         f"required {min_conf}.")
        if strategy is not None and strategy.action in ("BUY", "SELL") \
                and strategy.confidence < min_conf:
            fails.append(f"Strategy confidence {strategy.confidence:.2f} < "
                         f"required {min_conf}.")

        # ---- sizing: the system never grows the lot by itself ---------- #
        lot = float(settings.get("lot_size", 0.01))
        max_lot = float(settings.get("max_lot", 0.10))
        if lot > max_lot:
            fails.append(f"Lot {lot} exceeds configured max lot {max_lot}.")
        if sym is not None:
            if lot > sym.volume_max:
                fails.append(f"Lot {lot} exceeds broker max {sym.volume_max}.")
            if lot < sym.volume_min:
                fails.append(f"Lot {lot} below broker min {sym.volume_min}.")

        # ---- market conditions ------------------------------------------- #
        max_spread = float(settings.get("max_spread_points", 30))
        if tick is None:
            fails.append("No live tick data.")
        elif tick.spread_points > max_spread:
            fails.append(f"Spread {tick.spread_points:.1f} pts > max "
                         f"{max_spread} pts.")
        elif tick.spread_points > max_spread * 0.7:
            warns.append("Spread is close to the limit.")
        if tick is not None and tick.time > 0:
            age = time.time() - tick.time
            if age > 300:
                warns.append(f"Tick is stale ({int(age)}s old).")

        if sym is not None and not sym.trade_allowed:
            fails.append("Symbol trading is not allowed by the broker.")

        # ---- strategy quality --------------------------------------------- #
        if strategy is not None and decision.action in ("BUY", "SELL"):
            if settings.get("require_sl", True) and strategy.stop_loss <= 0:
                fails.append("Stop-loss is required but absent.")
            if strategy.risk_reward < float(settings.get("min_risk_reward", 1.2)):
                fails.append(f"Risk/reward {strategy.risk_reward:.2f} below "
                             f"the minimum.")

        # ---- daily limits -------------------------------------------------- #
        max_trades = int(settings.get("max_trades_per_day", 10))
        if trades_today >= max_trades:
            fails.append(f"Daily trade limit reached ({trades_today}/"
                         f"{max_trades}).")
        max_loss = float(settings.get("max_daily_loss_usd", 5.0))
        if max_loss > 0 and realized_loss_today >= max_loss:
            fails.append(f"Daily loss limit reached (-{realized_loss_today:.2f}"
                         f" >= {max_loss:.2f}).")
        if account is not None and account.equity <= 0:
            fails.append("Account equity is not positive.")

        return RiskGateResult(approved=not fails, reasons=fails, warnings=warns)
