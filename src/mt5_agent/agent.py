"""Оркестратор: один цикл = один «вдох» агента.

Порядок действий в цикле (важен именно такой):

1.  Подключение и снимок счёта (equity, баланс, статус терминала).
2.  Прокатка периодов (новый день/неделя) и обновление пика equity.
3.  Kill switch: если файл появился — аварийно закрыть свои позиции и встать.
4.  Синхронизация закрытых сделок → дневная/недельная статистика.
   (без этого шага дневные лимиты не работают)
5.  Ведение открытых позиций: безубыток/трейлинг, закрытие позиций без стопа.
6.  По каждому символу: данные → сигнал → риск → размер → заявка.
7.  Heartbeat, снапшот состояния/отчёт для дашборда, уведомления.

Здесь нет «умных» решений: вся математика — в отдельных модулях, а этот файл
отвечает только за порядок и за то, чтобы ни один шаг не был пропущен.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .broker import MT5Broker, MT5Credentials, Position
from .config import Config, SymbolConfig
from .exceptions import (
    AgentError,
    BrokerError,
    DataError,
    ExecutionError,
    ModelNotTrainedError,
    SafetyError,
)
from .execution import OrderExecutor
from .logging_setup import get_logger, log_event
from .model import SignalModel, load_or_fail
from .notifications import Notifier
from .risk import RiskManager
from .sentiment import SentimentFilter
from .signals import generate_signal, signal_as_dict
from .specs import SymbolSpec
from .state import AgentState
from .utils import UTC, fmt_money, read_json, utc_now, write_json

log = get_logger("agent")


def model_path_for(cfg: Config, symbol_cfg: SymbolConfig) -> Path:
    """Путь к файлу модели символа.

    Если торгуется один инструмент — используется имя из конфига
    (``signal.model_path``), иначе на каждый символ свой файл, чтобы модели
    не перезаписывали друг друга.
    """
    enabled = cfg.trading.enabled_symbols
    if len(enabled) == 1:
        return cfg.path(cfg.signal.model_path)
    return cfg.root_dir / "models" / f"signal_{symbol_cfg.name}_{symbol_cfg.timeframe}.joblib"


RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"


# -----------------------------------------------------------------------------
# Отчёт о цикле
# -----------------------------------------------------------------------------

@dataclass
class CycleReport:
    ts: datetime = field(default_factory=utc_now)
    mode: str = "paper"
    connected: bool = False
    account_desc: str = ""
    equity: float = 0.0
    balance: float = 0.0
    pnl_today_pct: float = 0.0
    open_positions: int = 0
    signals: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ts"] = self.ts.astimezone(UTC).isoformat()
        return data

    def summary_text(self) -> str:
        lines = [
            f"{BOLD}Цикл {self.ts.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"(режим {self.mode}){RESET}",
            f"  счёт: {self.account_desc or 'нет подключения'}",
            f"  equity {fmt_money(self.equity)} | баланс {fmt_money(self.balance)} | "
            f"день {self.pnl_today_pct:+.2f}% | открыто позиций: {self.open_positions}",
        ]
        for signal in self.signals:
            side = signal.get("side", "none")
            color = GREEN if side == "buy" else RED if side == "sell" else DIM
            lines.append(
                f"  {color}{signal['symbol']:>8} {side.upper():<4}{RESET} p={signal.get('probability', 0):.3f} "
                f"— {signal.get('reason', '')}"
            )
        for plan in self.plans:
            lines.append(f"  {CYAN}план{RESET} {plan.get('describe', '')}")
        for order in self.orders:
            color = GREEN if order.get("ok") else RED
            lines.append(f"  {color}ордер{RESET} {order.get('describe', '')}")
        for item in self.closed:
            lines.append(
                f"  закрыто {item['symbol']}: {'+' if item['pnl'] >= 0 else ''}{fmt_money(item['pnl'])}"
            )
        for item in self.blocked:
            lines.append(f"  {YELLOW}блок{RESET} {item['symbol']}: {item['reason']}")
        for action in self.actions:
            lines.append(f"  действие: {action}")
        for error in self.errors:
            lines.append(f"  {RED}ошибка{RESET} {error}")
        if self.halted:
            lines.append(f"  {RED}{BOLD}ТОРГОВЛЯ ОСТАНОВЛЕНА: {self.halt_reason}{RESET}")
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# Агент
# -----------------------------------------------------------------------------

class TradingAgent:
    """Основной цикл. Не делает ничего вне :meth:`run_once`."""

    def __init__(
        self,
        cfg: Config,
        *,
        dry_run: bool = False,
        mode_override: str | None = None,
        once: bool = False,
    ) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.mode = mode_override or cfg.agent.mode
        self.once = once
        self.root = cfg.root_dir

        self.state_path = self.root / "state" / "agent_state.json"
        self.dashboard_path = self.root / "state" / "dashboard.json"
        self.equity_path = self.root / "state" / "equity_curve.json"
        self.heartbeat_path = cfg.path(cfg.agent.heartbeat_file)
        self.reports_dir = self.root / "reports"

        self.state = AgentState.load(self.state_path)
        self.risk = RiskManager(
            cfg.risk,
            self.state,
            state_path=self.state_path,
            kill_switch_path=cfg.path(cfg.safety.kill_switch_file),
            own_magics=tuple(s.magic for s in cfg.trading.symbols),
        )
        self.notifier = Notifier(cfg.notifications)
        self.sentiment = SentimentFilter(cfg.sentiment, state_dir=self.root / "state")
        self.broker: MT5Broker | None = None
        self.executor: OrderExecutor | None = None
        self.models: dict[str, SignalModel] = {}
        self._stop_requested = False
        self._kill_switch_handled = False
        self._atr_cache: dict[str, float] = {}

    # -- жизненный цикл ----------------------------------------------------------
    def start(self) -> None:
        """Подключиться, проверить безопасность и войти в цикл."""
        self._prepare_broker()
        self._safety_guard()
        self.notifier.started(self.mode, self.account_desc)
        try:
            while not self._stop_requested:
                report = self.run_once()
                if report.halted:
                    self.notifier.halted(report.halt_reason)
                    log.warning("Цикл завершён: торговля остановлена (%s)", report.halt_reason)
                    break
                if self.once:
                    break
                time.sleep(max(5, int(self.cfg.agent.loop_interval_sec)))
        except KeyboardInterrupt:
            log.warning("Получен Ctrl+C — останавливаюсь штатно")
            self.notifier.stopped("Ctrl+C")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.state.save(self.state_path)
        if self.broker is not None:
            self.broker.shutdown()
        log.info("Агент остановлен. Открытые позиции, если есть, защищены стоп-лоссами.")

    def request_stop(self) -> None:
        self._stop_requested = True

    # -- подготовка ------------------------------------------------------------------
    def _prepare_broker(self) -> None:
        cfg = self.cfg
        prefix = "MT5_REAL" if self.mode == "live" else "MT5"
        credentials = MT5Credentials.from_env(prefix)
        if self.mode == "live":
            # В live-режиме НЕ подставляем логин/пароль из config.yaml (там демо):
            # только путь к терминалу и флаг portable.
            credentials = MT5Credentials(
                login=credentials.login,
                password=credentials.password,
                server=credentials.server,
                terminal_path=credentials.terminal_path or cfg.mt5.get("terminal_path") or None,
                portable=credentials.portable or bool(cfg.mt5.get("portable")),
            )
        else:
            credentials = credentials.merged(cfg.mt5)

        if not credentials.login:
            raise SafetyError(
                f"Не заданы учётные данные MT5 ({prefix}_LOGIN в .env). "
                "Скопируй .env.example в .env и заполни реквизиты ДЕМО-счёта."
            )
        self.broker = MT5Broker(
            credentials,
            timeout_ms=int(cfg.mt5.get("timeout_ms", 60_000)),
            reconnect_attempts=int(cfg.mt5.get("reconnect_attempts", 5)),
            reconnect_backoff_sec=float(cfg.mt5.get("reconnect_backoff_sec", 5)),
            expected_login=int(credentials.login) if cfg.safety.verify_account_number else None,
        )
        snapshot = self.broker.connect()
        self.account_desc = snapshot.describe()
        self.executor = OrderExecutor(
            self.broker,
            exec_cfg=cfg.execution,
            risk_cfg=cfg.risk,
            dry_run=self.dry_run or self.mode == "shadow",
        )

    def _safety_guard(self) -> None:
        """Проверки ДО первой сделки. Ошибка здесь — правильное поведение."""
        cfg = self.cfg
        account = self.broker.account()
        if self.mode == "paper" and account.is_real:
            raise SafetyError(
                "Режим paper предполагает ДЕМО-счёт, а подключён реальный "
                f"(login={account.login}). Это защита от случайной торговли на реале: "
                "поменяй реквизиты в .env или выбери режим shadow для наблюдения."
            )
        if self.mode == "live":
            problems = []
            import os

            if cfg.safety.demo_only:
                problems.append("в config.yaml стоит safety.demo_only: true")
            if cfg.safety.require_confirmation_for_live and \
                    os.getenv("I_UNDERSTAND_REAL_MONEY_RISK", "").strip().upper() != "TRUE":
                problems.append("в .env не выставлен I_UNDERSTAND_REAL_MONEY_RISK=TRUE")
            if not account.is_real:
                log.warning("Режим live, но счёт — демонстрационный. Это безопасно, но учти это.")
            if problems:
                raise SafetyError(
                    "Реальный счёт требует ОСОЗНАННОГО подтверждения. Не выполнено: "
                    + "; ".join(problems)
                    + ". Это три независимых барьера — снимай их по одному и только осознанно."
                )
        ok, reason = self.broker.terminal_ready()
        if not ok:
            log.warning("Терминал не готов: %s", reason)

    # -- модели ----------------------------------------------------------------------
    def model_path_for(self, symbol_cfg: SymbolConfig) -> Path:
        return model_path_for(self.cfg, symbol_cfg)

    def _model_for(self, symbol_cfg: SymbolConfig) -> SignalModel | None:
        if symbol_cfg.name in self.models:
            return self.models[symbol_cfg.name]
        path = self.model_path_for(symbol_cfg)
        try:
            model = load_or_fail(path)
        except ModelNotTrainedError as exc:
            if self.cfg.signal.require_model_trained:
                log.warning("Нет модели для %s: %s", symbol_cfg.name, exc)
                return None
            log.warning("Модель для %s не загружена, продолжаю без ML: %s", symbol_cfg.name, exc)
            return None
        self.models[symbol_cfg.name] = model
        return model

    # -- один цикл -------------------------------------------------------------------
    def run_once(self) -> CycleReport:
        cfg = self.cfg
        now = utc_now()
        report = CycleReport(ts=now, mode=self.mode)
        if self.broker is None or self.executor is None:
            raise AgentError("Агент не инициализирован: вызови start() или _prepare_broker()")

        try:
            self.broker.ensure_connected()
            account = self.broker.account()
        except BrokerError as exc:
            report.errors.append(str(exc))
            self._handle_error(report, str(exc))
            return report

        report.connected = True
        report.account_desc = account.describe()
        report.equity, report.balance = account.equity, account.balance
        report.mode = self.mode

        # 2. периоды и пик equity
        self.state.roll_periods(now, account.equity)
        self.state.update_equity(account.equity)

        # 3. kill switch
        if self.risk.kill_switch_active() and not self._kill_switch_handled:
            self._kill_switch_handled = True
            self._emergency_flat(report)

        report.halted = self.state.halted
        report.halt_reason = self.state.halt_reason

        own_positions = self._own_positions()
        foreign = self._foreign_positions()
        report.open_positions = len(own_positions)

        # 4. синхронизация закрытий
        self._sync_closed_trades(own_positions, report)

        if not self.state.halted:
            # 5. ведение позиций
            self._manage_positions(own_positions, report)

            # 6. новые сигналы
            daily_ref = self.state.daily.start_equity or account.equity
            report.pnl_today_pct = (account.equity - daily_ref) / daily_ref * 100.0 if daily_ref else 0.0
            for symbol_cfg in cfg.trading.enabled_symbols:
                if self.state.halted:
                    break
                try:
                    self._process_symbol(symbol_cfg, account, own_positions, foreign, report, now)
                except (DataError, ModelNotTrainedError) as exc:
                    report.blocked.append({"symbol": symbol_cfg.name, "reason": str(exc)})
                    log.warning("%s: %s", symbol_cfg.name, exc)
                except ExecutionError as exc:
                    report.errors.append(f"{symbol_cfg.name}: {exc}")
                    log.error("%s: %s", symbol_cfg.name, exc)
                except SafetyError:
                    raise
                except Exception as exc:  # прагматично: один символ не должен ронять цикл
                    report.errors.append(f"{symbol_cfg.name}: неожиданная ошибка: {exc}")
                    log.exception("Ошибка обработки %s", symbol_cfg.name)
                own_positions = self._own_positions()

        # 7. финализация
        report.halted = self.state.halted
        report.halt_reason = self.state.halt_reason
        self._write_outputs(report)
        if report.errors:
            self._handle_error(report, report.errors[-1])
        else:
            self.risk.register_success()
        return report

    # -- вспомогательное --------------------------------------------------------------
    def _own_positions(self) -> list[Position]:
        if self.broker is None:
            return []
        magics = {s.magic for s in self.cfg.trading.symbols}
        return [p for p in self.broker.positions() if int(p.magic) in magics]

    def _foreign_positions(self) -> list[Position]:
        if self.broker is None:
            return []
        magics = {s.magic for s in self.cfg.trading.symbols}
        return [p for p in self.broker.positions() if int(p.magic) not in magics]

    def _emergency_flat(self, report: CycleReport) -> None:
        """Аварийный выход: закрыть все свои позиции и остановиться."""
        log.critical("KILL SWITCH: закрываю все свои позиции и останавливаю торговлю")
        message = ""
        for pos in self._own_positions():
            result = self.broker.close_position(pos, comment="kill switch")
            message = f"закрытие #{pos.ticket} {pos.symbol}: {result.describe()}"
            report.actions.append(message)
            log_event("kill_switch_close", ticket=pos.ticket, symbol=pos.symbol,
                      ok=result.ok, retcode=result.retcode)
        self.notifier.halted("KILL SWITCH: все позиции закрыты, агент остановлен")
        self.state.halt("kill switch: аварийное закрытие позиций")
        self.risk.persist()

    def _handle_error(self, report: CycleReport, detail: str) -> None:
        limit = int(self.cfg.safety.max_consecutive_errors)
        if self.risk.register_error(limit, detail):
            report.halted = True
            report.halt_reason = self.state.halt_reason
            self.notifier.halted(self.state.halt_reason)

    def _sync_closed_trades(self, open_positions: list[Position], report: CycleReport) -> None:
        """Заметить закрытые позиции и записать их результат в статистику."""
        alive = {str(p.ticket) for p in open_positions}
        tracked = dict(self.state.open_tickets)
        for ticket_str, info in tracked.items():
            if ticket_str in alive:
                continue
            opened_at = None
            raw_ts = info.get("opened_at")
            if raw_ts:
                try:
                    opened_at = datetime.fromisoformat(raw_ts)
                except ValueError:
                    opened_at = None
            pnl = self.executor.closed_trade_pnl(int(ticket_str), opened_at)
            symbol = str(info.get("symbol", "?"))
            self.risk.register_close(symbol, pnl, when=utc_now())
            self.state.untrack(int(ticket_str))
            entry = {"symbol": symbol, "pnl": round(pnl, 2), "ticket": int(ticket_str)}
            report.closed.append(entry)
            self.notifier.trade_closed(symbol, pnl, self.state.daily.realized_pnl)
            log.info("Позиция %s (%s) закрыта, результат: %+.2f", ticket_str, symbol, pnl)
            log_event("trade_closed", ticket=int(ticket_str), symbol=symbol, pnl=pnl,
                      day_pnl=self.state.daily.realized_pnl)
        self.risk.persist()

    def _manage_positions(self, positions: list[Position], report: CycleReport) -> None:
        if not positions:
            return
        specs: dict[str, SymbolSpec] = {}
        for pos in positions:
            if pos.symbol in specs:
                continue
            try:
                specs[pos.symbol] = self.broker.symbol_spec(pos.symbol)
            except BrokerError as exc:
                log.warning("Нет спецификации %s: %s", pos.symbol, exc)
        actions = self.executor.manage_positions(positions, specs=specs, atr_by_symbol=self._atr_cache)
        report.actions.extend(actions)

    def _process_symbol(
        self,
        symbol_cfg: SymbolConfig,
        account,
        own_positions: list[Position],
        foreign: list[Position],
        report: CycleReport,
        now: datetime | None = None,
    ) -> None:
        now = now or utc_now()
        cfg = self.cfg
        spec = self.broker.symbol_spec(symbol_cfg.name)
        tick = self.broker.tick(symbol_cfg.name, spec)
        df = self.broker.rates(symbol_cfg.name, symbol_cfg.timeframe, symbol_cfg.history_bars)
        df.attrs["point"] = spec.point
        df.attrs["digits"] = spec.digits

        model = self._model_for(symbol_cfg)
        if model is None and cfg.signal.require_model_trained:
            report.blocked.append({
                "symbol": symbol_cfg.name,
                "reason": f"нет обученной модели ({self.model_path_for(symbol_cfg).name}) — обучение: "
                          f"python -m mt5_agent train --symbol {symbol_cfg.name}",
            })
            self._cache_atr(symbol_cfg, df)
            return

        sentiment = self.sentiment.evaluate(symbol=symbol_cfg.name)
        min_probability = cfg.signal.min_probability
        if sentiment.score > 0:
            min_probability = min(0.95, min_probability + float(cfg.sentiment.positive_boost))

        signal = generate_signal(
            df,
            model,
            symbol_cfg,
            now=now,
            spread_points=tick.spread_points,
            min_probability=min_probability,
            allow_short=cfg.signal.allow_short,
            require_model=cfg.signal.require_model_trained,
            sentiment_block=sentiment.blocked,
            sentiment_note=sentiment.note,
        )
        self._cache_atr(symbol_cfg, df)
        report.signals.append(signal_as_dict(signal))
        if not signal.is_entry:
            return

        # риск-проверки
        decision = self.risk.check_entry(
            equity=account.equity,
            balance=account.balance,
            symbol_cfg=symbol_cfg,
            positions=own_positions,
            foreign_positions=foreign,
            now=now,
        )
        if not decision.approved:
            report.blocked.append({"symbol": symbol_cfg.name, "reason": decision.reason})
            return

        # размер позиции
        entry_price = tick.ask if signal.side == "buy" else tick.bid
        margin_per_lot = None
        try:
            margin_per_lot = self.broker.calc_margin(signal.side, symbol_cfg.name, 1.0, entry_price)
        except BrokerError as exc:
            log.warning("order_calc_margin недоступен: %s", exc)
        sizing = self.risk.size(
            spec=spec,
            equity=account.equity,
            sl_price_distance=signal.sl_distance,
            tp_price_distance=signal.tp_distance,
            margin_per_lot=margin_per_lot,
        )
        if not sizing.approved:
            report.blocked.append({"symbol": symbol_cfg.name, "reason": sizing.reject_reason or "отказ риска"})
            log.warning("%s: %s", symbol_cfg.name, sizing.describe())
            return

        plan = self.executor.build_plan(
            signal=signal, sizing=sizing, spec=spec, tick=tick, magic=symbol_cfg.magic
        )
        report.plans.append({"describe": plan.describe(), **plan.to_dict()})

        if self.state.halted:
            report.blocked.append({"symbol": symbol_cfg.name, "reason": "торговля остановлена"})
            return

        result = self.executor.execute(plan, spec=spec)
        report.orders.append({"describe": result.describe(), "ok": result.ok,
                              "retcode": result.retcode, "comment": result.comment})
        if result.ok:
            self.risk.register_open(symbol_cfg.name, when=now)
            ticket = result.order_ticket
            if ticket:
                self.state.track_open(
                    ticket, symbol_cfg.name, plan.volume,
                    magic=symbol_cfg.magic, entry=result.price or plan.entry_price,
                )
                self.state.save(self.state_path)
            self.notifier.trade_opened(plan.describe())
            log_event("trade_opened", **plan.to_dict())
        else:
            log.error("Вход по %s не удался: %s", symbol_cfg.name, result.describe())

    def _cache_atr(self, symbol_cfg: SymbolConfig, df: pd.DataFrame) -> None:
        try:
            from .features import atr as atr_fn

            value = float(atr_fn(df, symbol_cfg.atr_period).iloc[-1])
            if value == value:  # не NaN
                self._atr_cache[symbol_cfg.name] = value
        except Exception:  # pragma: no cover
            pass

    # -- артефакты ------------------------------------------------------------------------
    def _write_outputs(self, report: CycleReport) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(report.ts.astimezone(UTC).isoformat(), encoding="utf-8")
        self.state.save(self.state_path)

        equity_curve = read_json(self.equity_path, default=[]) or []
        equity_curve.append({"ts": report.ts.astimezone(UTC).isoformat(), "equity": report.equity})
        equity_curve = equity_curve[-2000:]
        write_json(self.equity_path, equity_curve)

        dashboard = {
            "ts": report.ts.astimezone(UTC).isoformat(),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "virtual_market": bool(getattr(self.broker, "is_virtual", False)),
            "account": {
                "desc": report.account_desc,
                "equity": report.equity,
                "balance": report.balance,
                "pnl_today_pct": round(report.pnl_today_pct, 3),
                "open_positions": report.open_positions,
            },
            "risk": {
                "summary": self.risk.summary(report.equity),
                "halted": self.state.halted,
                "halt_reason": self.state.halt_reason,
                "kill_switch": self.risk.kill_switch_active(),
                "drawdown_pct": round(self.state.drawdown_from_peak_pct(report.equity), 2),
                "daily_realized": round(self.state.daily.realized_pnl, 2),
                "daily_trades": self.state.daily.trades_opened,
                "daily_loss_limit_pct": self.cfg.risk.max_daily_loss_pct,
                "risk_per_trade_pct": self.cfg.risk.risk_per_trade_pct,
            },
            "positions": [],
            "signals": report.signals,
            "blocked": report.blocked,
            "closed": report.closed,
            "errors": report.errors,
        }
        if self.broker is not None:
            for pos in self._own_positions():
                dashboard["positions"].append({
                    "ticket": pos.ticket, "symbol": pos.symbol, "side": pos.side,
                    "volume": pos.volume, "price_open": pos.price_open,
                    "sl": pos.sl, "tp": pos.tp, "profit": round(pos.profit, 2),
                })
        # Снимок должен отражать состояние НА МОМЕНТ записи, а не на начало цикла.
        dashboard["account"]["open_positions"] = len(dashboard["positions"])
        write_json(self.dashboard_path, dashboard)

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.reports_dir / "last_cycle.txt").write_text(report.summary_text(), encoding="utf-8")
        log_event("cycle", **{k: v for k, v in report.to_dict().items() if k != "ts"})
