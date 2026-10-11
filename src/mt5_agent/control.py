"""Управление агентом извне: панель управления, планировщик, скрипты.

Модуль **не содержит кода GUI** и не рендерит ни одного окна. Он умеет ровно то,
что нужно любому «пульту»:

* показать состояние (счёт, риск-лимиты, позиции, сигналы, блокировки);
* запустить агента отдельным процессом (тот же самый CLI, что и вручную);
* попросить мягкую остановку (файл ``state/STOP_REQUEST``) — агент завершит текущий
  цикл и выйдет, оставив открытые позиции под защитой стоп-лоссов у брокера;
* включить/снять kill switch;
* читать журналы и поток событий;
* запускать вспомогательные задачи (check / fetch / train / backtest) с записью
  вывода в файл.

Почему так, а не «GUI делает всё сам»: логика остаётся в одном месте (CLI и агент),
а панель — тонкая оболочка. Это значит, что панель **не может** начать считать риск
иначе, чем тесты, и что все действия можно повторить из консоли и из планировщика.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .config import Config, load_config
from .logging_setup import get_logger
from .state import AgentState
from .utils import file_age_sec, read_json, utc_now

log = get_logger("control")

# Режимы, которые разрешено запускать из панели. Реальный счёт сюда НЕ входит
# намеренно: он требует трёх независимых подтверждений и осознанного действия
# (см. docs/SAFETY.md), а «одна кнопка» этому противоречит.
GUI_MODES = ("shadow", "paper")


@dataclass
class StatusSnapshot:
    """Всё, что нужно показать на панели в одном объекте."""

    running: bool = False
    heartbeat_age_sec: float | None = None
    mode: str = "—"
    dry_run: bool = False
    virtual_market: bool = False
    halted: bool = False
    halt_reason: str = ""
    kill_switch: bool = False
    stop_requested: bool = False
    has_dashboard: bool = False
    equity: float | None = None
    balance: float | None = None
    currency: str = "USD"
    account_desc: str = ""
    pnl_today_pct: float | None = None
    open_positions: int = 0
    drawdown_pct: float = 0.0
    daily_realized: float = 0.0
    daily_trades: int = 0
    daily_loss_limit_pct: float = 0.0
    risk_per_trade_pct: float = 0.0
    positions: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_update: datetime | None = None

    # -- человекочитаемый вид ------------------------------------------------------
    @property
    def state_label(self) -> str:
        if self.kill_switch:
            return "KILL SWITCH АКТИВЕН"
        if self.halted:
            return "ОСТАНОВЛЕН"
        if self.running:
            return "РАБОТАЕТ"
        return "ОСТАНОВЛЕН"

    def describe(self) -> str:
        parts = [f"Состояние: {self.state_label}"]
        if self.running and self.heartbeat_age_sec is not None:
            parts.append(f"последний цикл {self.heartbeat_age_sec:.0f} сек. назад")
        elif not self.running:
            parts.append("агент не запущен")
        parts.append(f"режим: {self.mode}" + (" (расчёт без ордеров)" if self.dry_run else ""))
        if self.virtual_market:
            parts.append("ВИРТУАЛЬНЫЙ РЫНОК (синтетика)")
        if self.halt_reason:
            parts.append(f"причина: {self.halt_reason}")
        return " | ".join(parts)


class AgentController:
    """Единая точка управления агентом для GUI, скриптов и тестов."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        python_exe: str | Path | None = None,
        config: Config | None = None,
    ) -> None:
        self.cfg = config or load_config(root=root)
        self.root = Path(self.cfg.root_dir)
        self.state_dir = self.root / "state"
        self.logs_dir = self.root / "logs"
        self.python_exe = Path(python_exe) if python_exe else self._default_python()

    # -- пути ---------------------------------------------------------------------
    def _default_python(self) -> Path:
        """Python текущего окружения (в .venv он же и запускает агента)."""
        return Path(sys.executable)

    @property
    def dashboard_path(self) -> Path:
        return self.state_dir / "dashboard.json"

    @property
    def state_path(self) -> Path:
        return self.state_dir / "agent_state.json"

    @property
    def heartbeat_path(self) -> Path:
        return self.cfg.path(self.cfg.agent.heartbeat_file)

    @property
    def kill_switch_path(self) -> Path:
        return self.cfg.path(self.cfg.safety.kill_switch_file)

    @property
    def stop_request_path(self) -> Path:
        return self.state_dir / "STOP_REQUEST"

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def agent_log_path(self) -> Path:
        return self.cfg.path(self.cfg.notifications.log_file)

    @property
    def task_log_path(self) -> Path:
        return self.logs_dir / "gui_task.log"

    # -- чтение состояния ------------------------------------------------------------
    def dashboard(self) -> dict:
        return read_json(self.dashboard_path, default={}) or {}

    def snapshot(self) -> StatusSnapshot:
        """Собрать полный снимок состояния (не бросает исключений)."""
        snapshot = StatusSnapshot()
        age = file_age_sec(self.heartbeat_path)
        snapshot.heartbeat_age_sec = age
        snapshot.running = age is not None and age <= float(self.cfg.agent.heartbeat_stale_sec)
        snapshot.kill_switch = self.kill_switch_path.exists()
        snapshot.stop_requested = self.stop_request_path.exists()

        try:
            state = AgentState.load(self.state_path)
            snapshot.halted = state.halted
            snapshot.halt_reason = state.halt_reason
        except Exception as exc:  # pragma: no cover - повреждённый файл состояния
            snapshot.errors.append(f"не читается состояние агента: {exc}")

        dash = self.dashboard()
        if dash:
            snapshot.has_dashboard = True
            account = dash.get("account") or {}
            risk = dash.get("risk") or {}
            snapshot.mode = str(dash.get("mode", "—"))
            snapshot.dry_run = bool(dash.get("dry_run", False))
            snapshot.virtual_market = bool(dash.get("virtual_market", False))
            snapshot.account_desc = str(account.get("desc", ""))
            snapshot.equity = _as_float(account.get("equity"))
            snapshot.balance = _as_float(account.get("balance"))
            snapshot.pnl_today_pct = _as_float(account.get("pnl_today_pct"))
            snapshot.open_positions = int(account.get("open_positions") or 0)
            snapshot.drawdown_pct = _as_float(risk.get("drawdown_pct")) or 0.0
            snapshot.daily_realized = _as_float(risk.get("daily_realized")) or 0.0
            snapshot.daily_trades = int(risk.get("daily_trades") or 0)
            snapshot.daily_loss_limit_pct = _as_float(risk.get("daily_loss_limit_pct")) or 0.0
            snapshot.risk_per_trade_pct = _as_float(risk.get("risk_per_trade_pct")) or 0.0
            snapshot.positions = list(dash.get("positions") or [])
            snapshot.signals = list(dash.get("signals") or [])
            snapshot.blocked = list(dash.get("blocked") or [])
            snapshot.errors.extend(str(e) for e in (dash.get("errors") or []))
            raw_ts = dash.get("ts")
            if raw_ts:
                try:
                    snapshot.last_update = datetime.fromisoformat(str(raw_ts))
                except ValueError:
                    snapshot.last_update = None
        return snapshot

    def config_summary(self) -> list[tuple[str, str]]:
        """Пары «параметр → значение» для отображения в панели (только чтение)."""
        cfg = self.cfg
        rows = [
            ("Режим в конфиге", cfg.agent.mode),
            ("Период цикла", f"{cfg.agent.loop_interval_sec} сек."),
            ("Риск на сделку", f"{cfg.risk.risk_per_trade_pct}%"),
            ("Дневной лимит", f"-{cfg.risk.max_daily_loss_pct}%"),
            ("Недельный лимит", f"-{cfg.risk.max_weekly_loss_pct}%"),
            ("Лимит просадки", f"-{cfg.risk.max_drawdown_pct}%"),
            ("Макс. позиций", str(cfg.risk.max_open_positions)),
            ("Сделок в день", str(cfg.risk.max_daily_trades)),
            ("Пауза после убытка", f"{cfg.risk.cooldown_after_loss_min} мин."),
            ("Мартингейл/сетки", "запрещены в коде"),
            ("Порог вероятности", f"{cfg.signal.min_probability}"),
            ("Лонги и шорты", "да" if cfg.signal.allow_short else "только лонги"),
            ("Требуется модель", "да" if cfg.signal.require_model_trained else "нет"),
            ("Новостной фильтр", "включён" if cfg.sentiment.enabled else "выключен"),
        ]
        for symbol in cfg.trading.symbols:
            mark = "" if symbol.enabled else " (выключен)"
            rows.append((
                f"{symbol.name}{mark}",
                f"{symbol.timeframe}, сессия {symbol.session_start_utc}–{symbol.session_end_utc} UTC, "
                f"SL {symbol.sl_atr_mult}×ATR, TP {symbol.tp_atr_mult}×ATR",
            ))
        return rows

    # -- управление ---------------------------------------------------------------------
    def request_stop(self, reason: str = "остановка из панели") -> Path:
        """Мягкая остановка: агент доработает цикл и выйдет. Позиции НЕ закрываются."""
        self.stop_request_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_request_path.write_text(f"{utc_now().isoformat()} | {reason}\n", encoding="utf-8")
        log.info("Запрошена мягкая остановка: %s", reason)
        return self.stop_request_path

    def cancel_stop_request(self) -> bool:
        if self.stop_request_path.exists():
            self.stop_request_path.unlink()
            return True
        return False

    def activate_kill_switch(self, reason: str = "остановка из панели") -> Path:
        """Аварийный стоп: агент закроет СВОИ позиции и остановится.

        Пишем И файл (агент увидит его на ближайшем цикле), И флаг остановки в
        состоянии — чтобы стоп действовал, даже если процесс агента сейчас не запущен
        и запустится позже.
        """
        self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        self.kill_switch_path.write_text(f"{utc_now().isoformat()} | {reason}\n", encoding="utf-8")
        try:
            state = AgentState.load(self.state_path)
            state.halt(f"KILL_SWITCH: {reason}")
            state.save(self.state_path)
        except Exception as exc:  # pragma: no cover - повреждённый файл состояния
            log.warning("Не удалось записать флаг остановки: %s", exc)
        log.warning("Kill switch активирован: %s", reason)
        return self.kill_switch_path

    def deactivate_kill_switch(self, *, reset_halt: bool = True) -> bool:
        """Снять kill switch и (по умолчанию) сбросить флаг остановки в состоянии."""
        changed = False
        if self.kill_switch_path.exists():
            self.kill_switch_path.unlink()
            changed = True
        if reset_halt:
            state = AgentState.load(self.state_path)
            if state.halted:
                state.resume()
                state.consecutive_errors = 0
                state.save(self.state_path)
                changed = True
        return changed

    def start(self, *, mode: str = "paper", dry_run: bool = False, once: bool = False) -> subprocess.Popen:
        """Запустить агента отдельным процессом (тот же CLI, что и вручную).

        Реальный счёт здесь недоступен намеренно: GUI_MODES = shadow | paper.
        """
        if mode not in GUI_MODES:
            raise ValueError(
                f"Из панели можно запускать только режимы {GUI_MODES}: реальный счёт требует "
                "осознанного запуска из консоли с флагом --i-understand-live (см. docs/SAFETY.md)"
            )
        if self.cfg.agent.mode not in ("paper", "shadow", "live"):
            raise ValueError(f"Некорректный режим в конфиге: {self.cfg.agent.mode}")
        self.cancel_stop_request()
        arguments = [str(self.python_exe), "-m", "mt5_agent", "run", "--mode", mode]
        if dry_run:
            arguments.append("--dry-run")
        if once:
            arguments.append("--once")

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        output = (self.logs_dir / "agent_stdout.log").open("a", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        log.info("Запуск агента: %s", " ".join(arguments))
        return subprocess.Popen(arguments, cwd=str(self.root), stdout=output, stderr=subprocess.STDOUT, env=env)

    def run_task(self, args: Sequence[str]) -> subprocess.Popen:
        """Запустить вспомогательную команду (check/fetch/train/backtest) с лог-файлом."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        header = f"\n{'=' * 78}\n{utc_now().isoformat()} | {' '.join(args)}\n{'=' * 78}\n"
        with self.task_log_path.open("a", encoding="utf-8") as fh:
            fh.write(header)
        output = self.task_log_path.open("a", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        return subprocess.Popen(
            [str(self.python_exe), "-m", "mt5_agent", *args],
            cwd=str(self.root), stdout=output, stderr=subprocess.STDOUT, env=env,
        )

    # -- журналы ------------------------------------------------------------------------
    def tail_events(self, count: int = 60) -> list[dict]:
        """Последние события (логи сдел и решений) как словари."""
        if not self.events_path.exists():
            return []
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()[-count:]
        except OSError:
            return []
        events: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def tail_text(self, path: Path, count: int = 200) -> str:
        if not path.exists():
            return ""
        try:
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:])
        except OSError as exc:  # pragma: no cover
            return f"не читается {path}: {exc}"

    def tail_agent_log(self, count: int = 200) -> str:
        return self.tail_text(self.agent_log_path, count)

    def tail_task_log(self, count: int = 200) -> str:
        return self.tail_text(self.task_log_path, count)

    def last_cycle_report(self) -> str:
        return self.tail_text(self.root / "reports" / "last_cycle.txt", 200)

    # -- проверки ------------------------------------------------------------------------
    def preflight(self) -> list[tuple[bool, str]]:
        """Быстрые проверки готовности к запуску (что показать перед стартом)."""
        checks: list[tuple[bool, str]] = []

        env_path = self.root / ".env"
        checks.append((env_path.exists(), f".env {'найден' if env_path.exists() else 'НЕ найден — заполни MT5_LOGIN/MT5_PASSWORD/MT5_SERVER'}"))

        try:
            import MetaTrader5 as _mt5  # noqa: F401  (нужна только проверка установки)

            checks.append((True, f"модуль MetaTrader5 установлен (версия {_mt5.__version__})"))
        except ImportError:
            checks.append((False, "модуль MetaTrader5 не установлен (нужен Windows и pip install MetaTrader5)"))

        for symbol in self.cfg.trading.enabled_symbols:
            from .agent import model_path_for

            path = model_path_for(self.cfg, symbol)
            checks.append((path.exists(), f"модель для {symbol.name}: {'есть' if path.exists() else 'НЕТ — выполни train'}"))

        if self.cfg.signal.require_model_trained:
            pass
        checks.append((not self.kill_switch_path.exists(), "kill switch снят" if not self.kill_switch_path.exists() else "KILL SWITCH АКТИВЕН"))
        checks.append((not self.stop_request_path.exists(), "запрос остановки отсутствует" if not self.stop_request_path.exists() else "есть STOP_REQUEST (агент остановится сразу после старта)"))
        return checks


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def format_events(events: list[dict]) -> str:
    """Строки для журнала: что произошло и когда."""
    labels = {
        "trade_opened": "ВХОД", "trade_closed": "ЗАКРЫТИЕ", "plan": "ПЛАН",
        "order_sent": "ОРДЕР", "order_failed": "ОРДЕР ОТКЛОНЁН",
        "entry_blocked": "БЛОК ВХОДА", "signal": "СИГНАЛ", "signal_blocked_by_news": "СИГНАЛ (новости)",
        "stop_moved": "СТОП ПЕРЕДВИНУТ", "slippage": "ПРОСКАЛЬЗЫВАНИЕ",
        "kill_switch": "KILL SWITCH", "kill_switch_close": "ЗАКРЫТИЕ (kill switch)",
        "unprotected_position_closed": "ЗАКРЫТИЕ (не было стопа)", "cycle": "ЦИКЛ",
        "sentiment": "НОВОСТНОЙ ФИЛЬТР", "notify": "УВЕДОМЛЕНИЕ",
    }
    lines: list[str] = []
    for event in events:
        kind = str(event.get("kind", "?"))
        label = labels.get(kind, kind.upper())
        timestamp = str(event.get("ts", ""))[:19].replace("T", " ")
        details = {
            key: value for key, value in event.items()
            if key not in ("kind", "ts") and value not in (None, "", [], {})
        }
        if kind == "cycle":
            details = {
                "equity": event.get("equity"), "позиций": event.get("open_positions"),
                "блокировок": len(event.get("blocked") or []), "ошибок": len(event.get("errors") or []),
            }
        tail = ", ".join(f"{key}={value}" for key, value in list(details.items())[:6])
        lines.append(f"{timestamp}  {label:<22} {tail}")
    return "\n".join(lines)
