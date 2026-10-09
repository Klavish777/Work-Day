"""TradingEngine — the orchestrator thread.

Implements the chain:
  MARKET DATA -> MARKET ANALYST -> STRATEGY TRADER -> RISK ANALYST ->
  CENTRAL LLM -> RISK MANAGER -> MT5 EXECUTION ENGINE -> POSITION MONITOR ->
  PROFIT TARGET -> CLOSE POSITION -> TRADE JOURNAL

Safety first: decisions are produced by AI, execution is gated by the
deterministic Risk Manager and the Execution Engine's own pre-flight checks.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from agents.market_analyst.agent import MarketAnalystAgent
from agents.risk_analyst.agent import RiskAnalystAgent
from agents.strategy_trader.agent import StrategyTraderAgent
from core.appstate import AppState
from core.decision_engine import CentralDecisionEngine
from core.models import CycleReport, EngineState, PositionView
from core import consensus as consensus_mod
from execution.engine import ExecutionEngine
from execution.position_monitor import PositionMonitor
from risk.manager import RiskManager

LOG = logging.getLogger("workday.engine")


class TradingEngine(threading.Thread):
    def __init__(self, settings_mgr, gateway, journal, provider, logger=None):
        super().__init__(name="TradingEngine", daemon=True)
        self.settings_mgr = settings_mgr
        self.gw = gateway
        self.journal = journal
        self.provider = provider
        self.log = logger or LOG

        self.state = AppState()
        self.engine_state = EngineState.STOPPED.value
        self._cmd: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._last_cycle_ts = 0.0
        self._known_tickets: Dict[int, Dict[str, Any]] = {}
        # last MT5 connection attempt: {ts, ok, detail} — surfaced in the UI
        self.last_connect: Optional[Dict[str, Any]] = None

        llm_flags = settings_mgr.get()["llm"]["agents"]
        self.market_agent = MarketAnalystAgent(provider, llm_flags)
        self.strategy_agent = StrategyTraderAgent(provider, llm_flags)
        self.risk_agent = RiskAnalystAgent(provider, llm_flags)
        self.central = CentralDecisionEngine(provider, llm_flags)
        self.risk_manager = RiskManager()
        self.execution = ExecutionEngine(gateway, settings_mgr, logger)
        self.monitor = PositionMonitor()

    # ------------------------------------------------------------------ #
    # Control API (called from the UI thread; commands run in the loop)
    # ------------------------------------------------------------------ #
    def cmd_start(self) -> None:
        self._cmd.put({"type": "state", "value": EngineState.RUNNING.value})

    def cmd_pause(self) -> None:
        self._cmd.put({"type": "state", "value": EngineState.PAUSED.value})

    def cmd_stop(self) -> None:
        self._cmd.put({"type": "state", "value": EngineState.STOPPED.value})

    def cmd_close_position(self) -> None:
        self._cmd.put({"type": "close_position"})

    def cmd_close_all(self) -> None:
        self._cmd.put({"type": "close_all"})

    def cmd_connect_mt5(self, cfg: Dict[str, str]) -> None:
        self._cmd.put({"type": "connect", "cfg": cfg})

    def cmd_disconnect_mt5(self) -> None:
        self._cmd.put({"type": "disconnect"})

    def shutdown(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.log.info("Trading engine thread started")
        while not self._stop_event.is_set():
            try:
                self._process_commands()
                self._refresh_market_state()
                if self.engine_state in (EngineState.RUNNING.value,
                                         EngineState.PAUSED.value):
                    self._monitor_positions()
                if self.engine_state == EngineState.RUNNING.value:
                    interval = float(self.settings_mgr.get()
                                     .get("engine_cycle_sec", 5))
                    if time.time() - self._last_cycle_ts >= interval:
                        self._last_cycle_ts = time.time()
                        self._cycle()
            except Exception:  # noqa: BLE001 — engine must never die
                self.log.exception("Engine cycle error")
            time.sleep(0.5)
        self.log.info("Trading engine thread stopped")

    # ------------------------------------------------------------------ #
    def _process_commands(self) -> None:
        while True:
            try:
                cmd = self._cmd.get_nowait()
            except queue.Empty:
                return
            t = cmd.get("type")
            if t == "state":
                self.engine_state = cmd["value"]
                self.state.update(engine_state=self.engine_state)
                self.log.info("Engine state -> %s", self.engine_state)
                self.journal.db.log_event("INFO", "engine",
                                          f"State changed to {self.engine_state}")
            elif t == "connect":
                try:
                    ok, msg = self.gw.connect(cmd.get("cfg", {}))
                except Exception as exc:  # noqa: BLE001
                    ok, msg = False, f"unexpected connect error: {exc}"
                self.last_connect = {"ts": time.time(), "ok": bool(ok),
                                     "detail": str(msg)}
                self.journal.db.log_event("INFO" if ok else "ERROR", "mt5", msg)
            elif t == "disconnect":
                self.gw.disconnect()
                self.journal.db.log_event("INFO", "mt5", "Disconnected")
            elif t == "close_position":
                self._manual_close(all_positions=False)
            elif t == "close_all":
                self._manual_close(all_positions=True)

    # ------------------------------------------------------------------ #
    def _refresh_market_state(self) -> None:
        settings = self.settings_mgr.get()
        symbol = settings.get("symbol", "AUDCAD")
        account = self.gw.account()
        tick = self.gw.tick(symbol)
        positions = self.gw.positions(symbol)
        position = positions[0] if positions else None
        self.state.update(
            mt5_status=self.gw.status,
            mt5_detail=self.gw.detail,
            mt5_available=self.gw.available,
            account=account.to_dict() if account else None,
            tick=tick.to_dict() if tick else None,
            position=position.to_dict() if position else None,
            symbol=self.gw.resolved_symbol or symbol,
            engine_state=self.engine_state,
        )
        # track known tickets for close detection
        for p in positions:
            self._known_tickets[p.ticket] = {"profit": p.profit,
                                             "side": p.side, "lot": p.lot}

    # ------------------------------------------------------------------ #
    def _monitor_positions(self) -> None:
        """Position monitor + automatic close on profit target."""
        settings = self.settings_mgr.get()
        symbol = settings.get("symbol", "AUDCAD")
        positions = self.gw.positions(symbol)

        # detect externally closed positions -> finalize journal
        current = {p.ticket for p in positions}
        for ticket in list(self._known_tickets.keys()):
            if ticket not in current:
                deal = self.gw.position_close_deal(ticket)
                profit = deal["profit"] if deal else \
                    self._known_tickets[ticket].get("profit", 0.0)
                self.journal.record_close(
                    ticket, deal["price"] if deal else None, profit,
                    deal["comment"] if deal else "CLOSED_EXTERNALLY")
                self.journal.db.log_event("INFO", "journal",
                                          f"Position #{ticket} closed, "
                                          f"P/L {profit:.2f}")
                del self._known_tickets[ticket]

        for p in positions:
            reason = self.monitor.evaluate(p, settings)
            if reason:
                result = self.execution.close_position(p, reason)
                if result.get("ok"):
                    self.journal.record_close(p.ticket, result.get("price"),
                                              p.profit, reason)
                    self.journal.db.log_event("INFO", "monitor",
                                              f"Closed #{p.ticket}: {reason}")
                    self._known_tickets.pop(p.ticket, None)

    def _manual_close(self, all_positions: bool) -> None:
        settings = self.settings_mgr.get()
        positions = (self.gw.positions() if all_positions
                     else self.gw.positions(settings.get("symbol", "AUDCAD")))
        if not positions:
            self.journal.db.log_event("WARN", "manual", "Nothing to close")
            return
        for p in positions:
            result = self.execution.close_position(p, "MANUAL_CLOSE")
            if result.get("ok"):
                self.journal.record_close(p.ticket, result.get("price"),
                                          p.profit, "MANUAL_CLOSE")

    # ------------------------------------------------------------------ #
    def _cycle(self) -> None:
        settings = self.settings_mgr.get()
        symbol = settings.get("symbol", "AUDCAD")
        report = CycleReport(engine_state=self.engine_state,
                             mt5_status=self.gw.status)

        account = self.gw.account()
        tick = self.gw.tick(symbol)
        positions = self.gw.positions(symbol)
        position = positions[0] if positions else None

        # --- market data ------------------------------------------------ #
        if self.gw.status != "CONNECTED":
            report.skipped_reason = ("MT5 is not connected — no real market "
                                     "data. Connect MT5 to enable analysis.")
            self._finalize_cycle(report, settings, tick, position)
            return

        tf_candles = {}
        for tf in settings.get("timeframes", []):
            candles = self.gw.candles(symbol, tf, 220)
            if candles:
                tf_candles[tf] = candles
        if not tf_candles or tick is None:
            report.skipped_reason = "MT5 returned no candles/tick yet."
            self._finalize_cycle(report, settings, tick, position)
            return

        recent_trades = [
            {"side": t.get("side"), "profit": t.get("profit"),
             "open_ts": t.get("open_ts"), "reason_close": t.get("reason_close")}
            for t in self.journal.recent_trades(10)
        ]
        sym = self.gw.symbol_view(symbol)

        # --- AI pipeline ------------------------------------------------- #
        report.market = self.market_agent.analyze(symbol, tick, tf_candles)
        report.strategy = self.strategy_agent.plan(
            report.market, tick, tf_candles, settings, position, sym)
        report.risk = self.risk_agent.review(
            report.market, report.strategy, account, position, settings,
            recent_trades, tick, sym)
        report.consensus = consensus_mod.evaluate(report.market,
                                                  report.strategy, report.risk)
        report.llm_decision = self.central.decide(
            report.market, report.strategy, report.risk, report.consensus,
            settings, account, position, tick, recent_trades)

        decision = report.llm_decision
        self.log.info("Cycle %s: market=%s strategy=%s risk=%s llm=%s",
                      report.cycle_id, report.market.signal,
                      report.strategy.action,
                      "APPROVED" if report.risk.approved else "REJECTED",
                      decision.action)

        # --- execution ---------------------------------------------------- #
        if decision.action == "CLOSE" and position is not None:
            result = self.execution.close_position(position,
                                                   f"LLM_CLOSE: {decision.reason[:120]}")
            report.executed = {"action": "CLOSE", "result": result}
            if result.get("ok"):
                self.journal.record_close(position.ticket,
                                          result.get("price"), position.profit,
                                          f"LLM_CLOSE: {decision.reason[:120]}")
        elif decision.action in ("BUY", "SELL") and position is None:
            report.gate = self.risk_manager.evaluate(
                settings=settings, engine_state=self.engine_state,
                decision=decision, market=report.market,
                strategy=report.strategy, risk_verdict=report.risk,
                consensus=report.consensus, account=account,
                position=position, tick=tick, sym=sym,
                trades_today=self.journal.stats()["trades_today"],
                realized_loss_today=self.journal.realized_loss_today())
            if report.gate.approved:
                result = self.execution.open_position(
                    decision.action, float(settings.get("lot_size", 0.01)),
                    report.strategy.stop_loss, report.strategy.take_profit,
                    decision.reason)
                report.executed = {"action": decision.action, "result": result}
                if result.get("ok"):
                    self.journal.record_open(
                        report, result.get("ticket"), symbol, decision.action,
                        float(settings.get("lot_size", 0.01)),
                        result.get("price", 0.0), decision.reason)
            else:
                report.executed = {"action": "BLOCKED_BY_RISK_MANAGER",
                                   "reasons": report.gate.reasons}
                self.log.info("Risk manager blocked %s: %s", decision.action,
                              "; ".join(report.gate.reasons))

        self._finalize_cycle(report, settings, tick, position)

    def _finalize_cycle(self, report: CycleReport, settings, tick,
                        position) -> None:
        self.journal.record_cycle(report)
        self.state.update(last_cycle=report.to_dict(),
                          skipped_reason=report.skipped_reason)
