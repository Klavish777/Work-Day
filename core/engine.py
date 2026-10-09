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
from collections import deque
from typing import Any, Dict, List, Optional

from agents.market_analyst.agent import MarketAnalystAgent
from agents.risk_analyst.agent import RiskAnalystAgent
from agents.strategy_trader.agent import StrategyTraderAgent
from agents import strategy_library as slib
from agents.common import price_distance_for_profit
from core.appstate import AppState
from core.decision_engine import CentralDecisionEngine
from core.memory import AgentMemory
from core.volodya import VolodyaCore
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
        # live price history for the dashboard chart (~25 min at 5 Hz)
        self.tick_history: deque = deque(maxlen=7500)
        # short cache for the MT5-based chart timeframes
        self._chart_cache: Dict[str, Any] = {}
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
        # Agents' persistent memory: full trade-history backfill + live
        # learning of strategy weights from every closed trade.
        self.memory = AgentMemory()
        try:
            self.memory.backfill_from_journal(self.journal.recent_trades(1000))
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Memory backfill skipped: %s", exc)
        # "Володя" — the built-in trading brain (works without any LLM)
        self.volodya = VolodyaCore(self.memory)

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

    def cmd_manual_trade(self, side: str) -> Dict[str, Any]:
        """Manual BUY/SELL from the dashboard. Runs in the engine thread and
        returns the execution result synchronously (for UI feedback)."""
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._cmd.put({"type": "manual_trade", "side": side, "result": q})
        try:
            return q.get(timeout=12)
        except queue.Empty:
            return {"ok": False, "error": "Движок не ответил (таймаут)."}

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
            time.sleep(0.2)   # fast loop: fresh tick/balance every ~200 ms
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
                # demo accounts: full autonomy — start trading right after
                # a successful connection (REAL is NEVER auto-started)
                if ok:
                    st = self.settings_mgr.get()
                    if (st.get("mode") == "DEMO"
                            and st.get("auto_trading_enabled")
                            and self.engine_state == EngineState.STOPPED.value):
                        self.engine_state = EngineState.RUNNING.value
                        self.state.update(engine_state=self.engine_state)
                        self.journal.db.log_event(
                            "INFO", "engine",
                            "Auto-started after demo connection")
            elif t == "disconnect":
                self.gw.disconnect()
                self.journal.db.log_event("INFO", "mt5", "Disconnected")
            elif t == "close_position":
                self._manual_close(all_positions=False)
            elif t == "close_all":
                self._manual_close(all_positions=True)
            elif t == "manual_trade":
                res = self._manual_open(str(cmd.get("side", "")).upper())
                try:
                    cmd.get("result").put(res)
                except Exception:  # noqa: BLE001
                    pass

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
            prev = self._known_tickets.get(p.ticket, {})
            self._known_tickets[p.ticket] = {
                "profit": p.profit, "side": p.side, "lot": p.lot,
                "votes": prev.get("votes", [])}

        # chart history: remember every live mid-price
        if tick is not None:
            self.tick_history.append(
                (tick.time or time.time(), (tick.bid + tick.ask) / 2.0))

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
                self.memory.on_close(
                    ticket, profit,
                    self._known_tickets[ticket].get("votes") or None)
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
                    self.memory.on_close(
                        p.ticket, p.profit,
                        self._known_tickets.get(p.ticket, {}).get("votes") or None)
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
                self.memory.on_close(
                    p.ticket, p.profit,
                    self._known_tickets.get(p.ticket, {}).get("votes") or None)
                self._known_tickets.pop(p.ticket, None)

    # ------------------------------------------------------------------ #
    # Manual trading (BUY / SELL buttons on the dashboard)
    # ------------------------------------------------------------------ #
    def _manual_open(self, side: str) -> Dict[str, Any]:
        settings = self.settings_mgr.get()
        symbol = settings.get("symbol", "AUDCAD")
        if side not in ("BUY", "SELL"):
            return {"ok": False, "error": "Неверная сторона: только BUY или SELL."}
        if self.gw.status != "CONNECTED":
            return {"ok": False,
                    "error": "MT5 не подключён — сначала подключите терминал."}
        if settings.get("mode") == "REAL" and not settings.get("real_confirmed"):
            return {"ok": False, "error": "Режим REAL не подтверждён пользователем."}

        tick = self.gw.tick(symbol)
        sym = self.gw.symbol_view(symbol)
        if tick is None:
            return {"ok": False, "error": "Нет живых тиковых данных."}

        lot = float(settings.get("lot_size", 0.01))
        point = sym.point if sym else 0.00001
        price = tick.ask if side == "BUY" else tick.bid

        # TP / SL from user settings (points override, else $ targets)
        tp_price = sl_price = 0.0
        tp_points = float(settings.get("take_profit_points", 0) or 0)
        sl_points = float(settings.get("stop_loss_points", 0) or 0)
        if tp_points > 0:
            d = tp_points * point
            tp_price = price + d if side == "BUY" else price - d
        else:
            d = price_distance_for_profit(
                sym, lot, float(settings.get("profit_target_usd", 0.5)))
            if d:
                tp_price = price + d if side == "BUY" else price - d
        if sl_points > 0:
            d = sl_points * point
            sl_price = price - d if side == "BUY" else price + d
        else:
            d = price_distance_for_profit(
                sym, lot, float(settings.get("stop_loss_usd", 1.0)))
            if d:
                sl_price = price - d if side == "BUY" else price + d

        result = self.execution.open_position(
            side, lot, sl_price, tp_price, "MANUAL: ручная сделка пользователя")
        if not result.get("ok"):
            errs = result.get("errors") or ["исполнение отклонено"]
            return {"ok": False, "error": "; ".join(str(e) for e in errs)}

        ticket = result.get("ticket")
        report = CycleReport(engine_state="MANUAL", mt5_status=self.gw.status)
        self.journal.record_open(
            report, ticket, symbol, side, lot,
            result.get("price", price), "MANUAL: ручная сделка пользователя")
        self.journal.db.log_event("INFO", "manual",
                                  f"Manual {side} #{ticket} lot {lot}")
        return {"ok": True, "ticket": ticket, "side": side,
                "price": result.get("price")}

    # ------------------------------------------------------------------ #
    # Chart data: second-based TFs from tick history, MT5 TFs from terminal
    # ------------------------------------------------------------------ #
    SEC_TIMEFRAMES = {"S1": 1, "S5": 5, "S15": 15, "S30": 30}

    def chart_candles(self, tf: str, count: int = 400) -> Dict[str, Any]:
        tf = (tf or "S5").upper()
        count = max(30, min(900, int(count or 400)))
        if tf in self.SEC_TIMEFRAMES:
            return {"tf": tf, "source": "ticks",
                    "candles": self._ticks_to_candles(self.SEC_TIMEFRAMES[tf],
                                                      count)}
        now = time.time()
        cached = self._chart_cache.get(tf)
        if cached and now - cached[0] < 2.0:
            return {"tf": tf, "source": "mt5", "candles": cached[1]}
        symbol = self.settings_mgr.get().get("symbol", "AUDCAD")
        candles = None
        if self.gw.status == "CONNECTED":
            try:
                candles = self.gw.candles(symbol, tf, count)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("Chart candles %s failed: %s", tf, exc)
        out = [{"t": c.time, "o": c.open, "h": c.high, "l": c.low,
                "c": c.close} for c in candles or []]
        self._chart_cache[tf] = (now, out)
        return {"tf": tf, "source": "mt5", "candles": out}

    def _ticks_to_candles(self, step: int, count: int) -> List[Dict[str, float]]:
        buckets: Dict[int, List[float]] = {}
        order: List[int] = []
        for ts, mid in self.tick_history:
            b = int(ts // step) * step
            e = buckets.get(b)
            if e is None:
                buckets[b] = [mid, mid, mid, mid]
                order.append(b)
            else:
                if mid > e[1]:
                    e[1] = mid
                if mid < e[2]:
                    e[2] = mid
                e[3] = mid
        out = [{"t": b, "o": buckets[b][0], "h": buckets[b][1],
                "l": buckets[b][2], "c": buckets[b][3]} for b in order]
        return out[-count:]

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

        # --- strategy library: every classic system votes on real candles -- #
        votes = slib.vote_all(tf_candles, tick)

        # --- AI pipeline ------------------------------------------------- #
        report.market = self.market_agent.analyze(symbol, tick, tf_candles)
        report.market.strategies = votes
        report.strategy = self.strategy_agent.plan(
            report.market, tick, tf_candles, settings, position, sym)
        agg = slib.aggregate(votes, self.memory.weights())
        report.strategy.reason += (
            f" | Библиотека стратегий: {agg['buy_n']} за BUY, "
            f"{agg['sell_n']} за SELL, взвешенный счёт {agg['net']:+.2f} "
            f"(веса обучены на истории сделок)")
        report.risk = self.risk_agent.review(
            report.market, report.strategy, account, position, settings,
            recent_trades, tick, sym)
        report.consensus = consensus_mod.evaluate(report.market,
                                                  report.strategy, report.risk)
        if getattr(self.provider, "name", "off") == "off":
            # no external LLM -> the built-in brain "Володя" decides,
            # using the weighted votes of the strategy library + memory
            report.llm_decision = self.volodya.decide(
                votes, report.market, report.strategy, report.risk,
                report.consensus, settings, account, position, tick,
                recent_trades)
        else:
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
                self.memory.on_close(
                    position.ticket, position.profit,
                    self._known_tickets.get(position.ticket, {}).get("votes") or None)
                self._known_tickets.pop(position.ticket, None)
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
                    # learning: remember which strategies voted at this entry
                    ticket = result.get("ticket")
                    active = [nm for nm, vv in votes.items()
                              if vv.get("vote") == decision.action]
                    self.memory.on_entry(ticket, active)
                    if ticket in self._known_tickets:
                        self._known_tickets[ticket]["votes"] = active
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
