"""Риск-модуль — главный предохранитель агента.

Один метод :meth:`RiskManager.check_entry` собирает ВСЕ проверки перед входом
и возвращает решение с полным списком причин. Ни один ордер не уходит в
терминал, минуя этот модуль.

Проверки:
    1.  kill switch (файл state/KILL_SWITCH)
    2.  ручная остановка агента (state.halted)
    3.  минимальный остаток на счёте (equity floor)
    4.  дневной лимит убытка
    5.  недельный лимит убытка
    6.  общая просадка от пика equity
    7.  максимальное число открытых позиций
    8.  максимум позиций на символ
    9.  максимум сделок в день (защита от переторговли)
    10. пауза после убытка по символу
    11. запрет хеджирования (одновременный лонг и шорт)
    12. посторонние позиции на счёте (не наши magic) — halt по умолчанию
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import RiskConfig, SymbolConfig
from .logging_setup import get_logger, log_event
from .sizing import SizingResult, size_position
from .specs import SymbolSpec
from .state import AgentState
from .utils import utc_now

log = get_logger("risk")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "OK " if self.ok else "БЛОК"
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class RiskDecision:
    approved: bool = True
    reason: str = ""
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))
        if not ok and not self.reason:
            self.reason = f"{name}: {detail}"
            self.approved = False

    def report(self) -> str:
        return "\n".join(str(c) for c in self.checks)

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


class RiskManager:
    """Считает лимиты и разрешает/запрещает вход."""

    def __init__(
        self,
        cfg: RiskConfig,
        state: AgentState,
        *,
        state_path: Path | None = None,
        kill_switch_path: Path | None = None,
        own_magics: tuple[int, ...] = (),
        logger=None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.state_path = state_path
        self.kill_switch_path = kill_switch_path
        self.own_magics = set(int(m) for m in own_magics)
        self.log = logger or log

    # -- kill switch --------------------------------------------------------
    def kill_switch_active(self) -> bool:
        return bool(self.kill_switch_path and self.kill_switch_path.exists())

    def activate_kill_switch(self, reason: str) -> None:
        """Создать файл KILL_SWITCH: агент немедленно прекращает работу."""
        self.state.halt(f"KILL_SWITCH: {reason}")
        if self.kill_switch_path:
            self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
            self.kill_switch_path.write_text(
                f"{utc_now().isoformat()} | {reason}\n", encoding="utf-8"
            )
        self.log.critical("KILL SWITCH АКТИВИРОВАН: %s", reason)
        log_event("kill_switch", reason=reason)

    def deactivate_kill_switch(self) -> None:
        if self.kill_switch_path and self.kill_switch_path.exists():
            self.kill_switch_path.unlink()
        self.state.resume()
        self.log.warning("Kill switch снят вручную")

    def persist(self) -> None:
        if self.state_path:
            self.state.save(self.state_path)

    # -- проверки ------------------------------------------------------------
    def check_entry(
        self,
        *,
        equity: float,
        balance: float,
        symbol_cfg: SymbolConfig,
        positions: list,          # list[Position] — свои позиции по magic
        now: datetime | None = None,
        foreign_positions: list | None = None,
    ) -> RiskDecision:
        now = now or utc_now()
        cfg, symbol, state = self.cfg, symbol_cfg.name, self.state
        decision = RiskDecision()

        # 1. kill switch
        decision.add(
            "kill switch",
            not self.kill_switch_active(),
            "файл state/KILL_SWITCH на месте" if self.kill_switch_active() else "выключен",
        )

        # 2. ручная остановка
        decision.add("halted", not state.halted, state.halt_reason or "нет остановки")

        # 3. минимальный остаток
        decision.add(
            "минимальный equity",
            equity >= cfg.min_balance_floor,
            f"equity {equity:.2f} против порога {cfg.min_balance_floor:.2f}",
        )

        # 4. дневной лимит убытка (equity против equity на начало дня — учитывает и плавающий убыток)
        daily_ref = state.daily.start_equity or equity
        daily_pnl = equity - daily_ref
        daily_pnl_pct = daily_pnl / daily_ref * 100.0 if daily_ref else 0.0
        decision.add(
            "дневной лимит",
            daily_pnl_pct > -cfg.max_daily_loss_pct,
            f"P/L дня {daily_pnl_pct:+.2f}% (лимит -{cfg.max_daily_loss_pct:.2f}%), "
            f"реализовано {state.daily.realized_pnl:+.2f}",
        )

        # 5. недельный лимит
        weekly_ref = state.weekly.start_equity or equity
        weekly_pnl_pct = (equity - weekly_ref) / weekly_ref * 100.0 if weekly_ref else 0.0
        decision.add(
            "недельный лимит",
            weekly_pnl_pct > -cfg.max_weekly_loss_pct,
            f"P/L недели {weekly_pnl_pct:+.2f}% (лимит -{cfg.max_weekly_loss_pct:.2f}%)",
        )

        # 6. просадка от пика
        dd = state.drawdown_from_peak_pct(equity)
        decision.add(
            "просадка от пика",
            dd < cfg.max_drawdown_pct,
            f"просадка {dd:.2f}% (лимит {cfg.max_drawdown_pct:.2f}%), пик equity "
            f"{state.equity_peak:.2f}",
        )

        # 7. всего позиций
        decision.add(
            "максимум открытых позиций",
            len(positions) < cfg.max_open_positions,
            f"сейчас {len(positions)} из {cfg.max_open_positions}",
        )

        # 8. позиций по символу
        per_symbol = [p for p in positions if getattr(p, "symbol", "").upper() == symbol]
        decision.add(
            "позиций по символу",
            len(per_symbol) < cfg.max_positions_per_symbol,
            f"{symbol}: {len(per_symbol)} из {cfg.max_positions_per_symbol}",
        )

        # 9. сделок за день
        decision.add(
            "сделок за день",
            state.daily.trades_opened < cfg.max_daily_trades,
            f"открыто {state.daily.trades_opened} из {cfg.max_daily_trades}",
        )

        # 10. пауза после убытка
        cooling, minutes_left = state.cooldown_active(symbol, cfg.cooldown_after_loss_min, now)
        decision.add(
            "пауза после убытка",
            not cooling,
            f"{symbol}: ещё {minutes_left} мин" if cooling else f"{symbol}: паузы нет",
        )

        # 11. хеджирование
        if not cfg.allow_hedging and per_symbol:
            sides = {getattr(p, "side", "") for p in per_symbol}
            decision.add(
                "хеджирование",
                len(sides) <= 1,
                f"по {symbol} уже есть {'/'.join(sorted(sides))} — вторая сторона запрещена",
            )
        else:
            decision.add("хеджирование", True, "по символу позиций нет")

        # 12. посторонние позиции
        foreign = list(foreign_positions or [])
        if foreign:
            detail = (
                f"на счёте {len(foreign)} сделок без нашего magic "
                f"({', '.join(sorted({getattr(p, 'symbol', '?') for p in foreign}))}) — "
                "это могут быть ручные сделки или другой советник"
            )
            action = cfg.unknown_position_action
            if action == "halt":
                decision.add("посторонние позиции", False, detail + "; выбран halt")
            elif action == "warn":
                self.log.warning(detail)
                decision.add("посторонние позиции", True, detail + " (режим warn)")
            else:
                decision.add("посторонние позиции", True, detail + " (игнорируется)")
        else:
            decision.add("посторонние позиции", True, "нет")

        if not decision.approved:
            log_event("entry_blocked", symbol=symbol, reason=decision.reason)
        return decision

    # -- размер позиции --------------------------------------------------------
    def size(
        self,
        *,
        spec: SymbolSpec,
        equity: float,
        sl_price_distance: float,
        tp_price_distance: float | None = None,
        margin_per_lot: float | None = None,
    ) -> SizingResult:
        return size_position(
            spec,
            equity=equity,
            risk_pct=self.cfg.risk_per_trade_pct,
            sl_price_distance=sl_price_distance,
            tp_price_distance=tp_price_distance,
            max_leverage=self.cfg.max_leverage_used,
            margin_per_lot=margin_per_lot,
        )

    # -- учёт -------------------------------------------------------------------
    def register_open(self, symbol: str, *, when: datetime | None = None) -> None:
        self.state.register_open()
        self.state.note_trade_time(symbol, when)
        self.persist()

    def register_close(self, symbol: str, pnl: float, *, when: datetime | None = None) -> None:
        self.state.register_close(pnl, symbol, when)
        self.persist()

    def register_error(self, limit: int, detail: str = "") -> bool:
        """Учесть ошибку. True — если лимит ошибок исчерпан (агент должен встать)."""
        count = self.state.note_error()
        self.persist()
        if count >= limit:
            self.state.halt(f"{count} ошибок подряд. Последняя: {detail}")
            self.persist()
            return True
        return False

    def register_success(self) -> None:
        self.state.note_success()
        self.persist()

    # -- сводка -------------------------------------------------------------------
    def summary(self, equity: float) -> str:
        s = self.state
        return (
            f"режим риска: {self.cfg.risk_per_trade_pct:.2f}% на сделку | "
            f"день {s.daily.key}: {'+' if s.daily.realized_pnl >= 0 else ''}{s.daily.realized_pnl:.2f} "
            f"({s.daily.trades_opened} входов) | "
            f"неделя {s.weekly.key}: {'+' if s.weekly.realized_pnl >= 0 else ''}{s.weekly.realized_pnl:.2f} | "
            f"просадка {s.drawdown_from_peak_pct(equity):.2f}% | "
            f"{'HALT: ' + s.halt_reason if s.halted else 'работает'}"
        )
