"""Состояние агента между запусками.

Здесь живёт всё, что «помнит» дневные/недельные лимиты, просадку и
последовательные ошибки. Файл ``state/agent_state.json`` — настоящий
предохранитель: если агент перезапустится после убыточного дня, он не должен
«забыть», что лимит дня выбран.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .utils import UTC, day_key, read_json, utc_now, write_json


def week_key(ts: datetime) -> str:
    iso = ts.astimezone(UTC).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass
class PeriodStats:
    key: str = ""
    start_equity: float = 0.0
    realized_pnl: float = 0.0
    trades_opened: int = 0
    trades_closed: int = 0
    blocked: bool = False
    blocked_reason: str = ""

    def reset(self, key: str, equity: float) -> None:
        self.key = key
        self.start_equity = float(equity)
        self.realized_pnl = 0.0
        self.trades_opened = 0
        self.trades_closed = 0
        self.blocked = False
        self.blocked_reason = ""


@dataclass
class AgentState:
    """Всё изменяемое состояние агента."""

    daily: PeriodStats = field(default_factory=PeriodStats)
    weekly: PeriodStats = field(default_factory=PeriodStats)
    equity_peak: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    consecutive_errors: int = 0
    last_loss_at: dict[str, str] = field(default_factory=dict)   # symbol -> ISO время
    last_trade_at: dict[str, str] = field(default_factory=dict)  # symbol -> ISO время
    open_tickets: dict[str, dict] = field(default_factory=dict)  # str(ticket) -> инфо о входе
    version: int = 1
    updated_at: str = ""

    # -- файл -------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "AgentState":
        raw = read_json(path, default=None)
        if not raw:
            return cls()
        daily = PeriodStats(**{**asdict(PeriodStats()), **(raw.get("daily") or {})})
        weekly = PeriodStats(**{**asdict(PeriodStats()), **(raw.get("weekly") or {})})
        return cls(
            daily=daily,
            weekly=weekly,
            equity_peak=float(raw.get("equity_peak", 0.0)),
            halted=bool(raw.get("halted", False)),
            halt_reason=str(raw.get("halt_reason", "")),
            consecutive_errors=int(raw.get("consecutive_errors", 0)),
            last_loss_at=dict(raw.get("last_loss_at") or {}),
            last_trade_at=dict(raw.get("last_trade_at") or {}),
            open_tickets=dict(raw.get("open_tickets") or {}),
        )

    def save(self, path: Path) -> None:
        self.updated_at = utc_now().isoformat()
        payload: dict[str, Any] = asdict(self)
        write_json(path, payload)

    # -- периоды -------------------------------------------------------------------
    def roll_periods(self, now: datetime, equity: float) -> list[str]:
        """Сбросить дневную/недельную статистику при смене дня/недели."""
        events: list[str] = []
        today = day_key(now)
        if self.daily.key != today:
            self.daily.reset(today, equity)
            events.append(f"новый день {today}, дневные лимиты сброшены")
        this_week = week_key(now)
        if self.weekly.key != this_week:
            self.weekly.reset(this_week, equity)
            events.append(f"новая неделя {this_week}, недельные лимиты сброшены")
        if self.equity_peak <= 0:
            self.equity_peak = float(equity)
        return events

    def update_equity(self, equity: float) -> None:
        if equity > self.equity_peak:
            self.equity_peak = float(equity)

    @property
    def drawdown_pct(self) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return 0.0  # пересчитывается в RiskManager, где есть текущая equity

    def drawdown_from_peak_pct(self, equity: float) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - equity) / self.equity_peak * 100.0)

    # -- события ---------------------------------------------------------------------
    def register_open(self) -> None:
        self.daily.trades_opened += 1
        self.weekly.trades_opened += 1

    def register_close(self, pnl: float, symbol: str, closed_at: datetime | None = None) -> None:
        self.daily.realized_pnl += float(pnl)
        self.weekly.realized_pnl += float(pnl)
        self.daily.trades_closed += 1
        self.weekly.trades_closed += 1
        if pnl < 0:
            ts = (closed_at or utc_now()).astimezone(UTC).isoformat()
            self.last_loss_at[symbol.upper()] = ts

    def note_trade_time(self, symbol: str, when: datetime | None = None) -> None:
        self.last_trade_at[symbol.upper()] = (when or utc_now()).astimezone(UTC).isoformat()

    def last_loss_time(self, symbol: str) -> datetime | None:
        raw = self.last_loss_at.get(symbol.upper())
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:  # pragma: no cover
            return None

    def cooldown_active(self, symbol: str, minutes: int, now: datetime | None = None) -> tuple[bool, int]:
        """Возвращает (активна ли пауза, сколько минут осталось)."""
        if minutes <= 0:
            return False, 0
        last = self.last_loss_time(symbol)
        if last is None:
            return False, 0
        now = (now or utc_now()).astimezone(UTC)
        left = (last + timedelta(minutes=minutes)) - now
        if left.total_seconds() > 0:
            return True, int(left.total_seconds() // 60) + 1
        return False, 0

    # -- открытые позиции (учёт результата после закрытия) ---------------------------------
    def track_open(
        self, ticket: int, symbol: str, volume: float, *,
        magic: int = 0, when: datetime | None = None, entry: float = 0.0,
    ) -> None:
        self.open_tickets[str(int(ticket))] = {
            "symbol": symbol.upper(),
            "volume": float(volume),
            "magic": int(magic),
            "entry": float(entry),
            "opened_at": (when or utc_now()).astimezone(UTC).isoformat(),
        }

    def untrack(self, ticket: int) -> dict | None:
        return self.open_tickets.pop(str(int(ticket)), None)

    # -- ошибки -------------------------------------------------------------------------
    def note_error(self) -> int:
        self.consecutive_errors += 1
        return self.consecutive_errors

    def note_success(self) -> None:
        self.consecutive_errors = 0

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def resume(self) -> None:
        self.halted = False
        self.halt_reason = ""


def today_str(ts: datetime | None = None) -> str:
    return day_key(ts or utc_now())


def current_week(ts: datetime | None = None) -> str:
    return week_key(ts or utc_now())


def is_today(value: str) -> bool:
    return value == today_str()


__all__ = ["AgentState", "PeriodStats", "date", "day_key", "today_str", "week_key", "current_week", "is_today"]
