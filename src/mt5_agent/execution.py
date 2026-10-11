"""Исполнение: превращение сигнала в заявку и ведение открытых позиций.

Модуль ничего не решает про «куда идти» — он аккуратно исполняет уже
одобренное: считает цены SL/TP от цены входа, проверяет их допустимость у
брокера, отправляет ордер и ведёт учёт.

Отдельная ответственность — :meth:`OrderExecutor.sync_closed_trades`: агент
обязан замечать, что позиция закрылась (по стопу, тейку или вручную), и
записать результат в дневную статистику. Без этого дневные лимиты — фикция.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .broker import Position, Tick, TradeResult
from .config import ExecutionConfig, RiskConfig
from .exceptions import ExecutionError
from .logging_setup import get_logger, log_event
from .signals import Signal
from .sizing import SizingResult
from .specs import SymbolSpec
from .utils import UTC, round_price, utc_now

log = get_logger("execution")


@dataclass
class ExecutionPlan:
    symbol: str
    side: str
    volume: float
    entry_price: float
    sl_price: float
    tp_price: float
    risk_money: float
    risk_pct: float
    margin_required: float | None
    sl_distance: float
    tp_distance: float
    magic: int = 0
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"ПЛАН {self.side.upper()} {self.symbol} {self.volume:.2f} лот "
            f"@ {self.entry_price:.5f} | SL {self.sl_price:.5f} | TP {self.tp_price:.5f} | "
            f"риск {self.risk_money:.2f} ({self.risk_pct:.2f}%) | "
            f"маржа {self.margin_required if self.margin_required is not None else 'н/д'}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side, "volume": self.volume,
            "entry_price": self.entry_price, "sl_price": self.sl_price,
            "tp_price": self.tp_price, "risk_money": self.risk_money,
            "risk_pct": self.risk_pct, "margin_required": self.margin_required,
            "magic": self.magic, "notes": self.notes,
        }


class OrderExecutor:
    """Отправка ордеров и ведение позиций. Единственный путь к деньгам."""

    def __init__(
        self,
        broker,
        *,
        exec_cfg: ExecutionConfig,
        risk_cfg: RiskConfig,
        logger=None,
        dry_run: bool = False,
    ) -> None:
        self.broker = broker
        self.cfg = exec_cfg
        self.risk_cfg = risk_cfg
        self.log = logger or log
        self.dry_run = dry_run

    # -- планирование ------------------------------------------------------------
    def build_plan(
        self,
        *,
        signal: Signal,
        sizing: SizingResult,
        spec: SymbolSpec,
        tick: Tick,
        magic: int,
    ) -> ExecutionPlan:
        """Посчитать цены SL/TP от реальной цены входа (ask для buy, bid для sell)."""
        if not sizing.approved:
            raise ExecutionError(f"Нельзя построить план: {sizing.reject_reason}")
        entry = tick.ask if signal.side == "buy" else tick.bid
        if signal.side == "buy":
            sl, tp = entry - sizing.sl_price_distance, entry + sizing.tp_price_distance
        else:
            sl, tp = entry + sizing.sl_price_distance, entry - sizing.tp_price_distance

        min_dist = spec.min_stop_distance_price(tick.spread_points)
        notes = []
        if sizing.sl_price_distance < min_dist:
            raise ExecutionError(
                f"SL {sizing.sl_price_distance:.5f} ближе минимума брокера {min_dist:.5f}"
            )
        if sizing.tp_price_distance and sizing.tp_price_distance < min_dist:
            raise ExecutionError(
                f"TP {sizing.tp_price_distance:.5f} ближе минимума брокера {min_dist:.5f}"
            )

        sl = round_price(sl, spec.digits)
        tp = round_price(tp, spec.digits)
        # защита от «стоп по другую сторону цены» после округления
        if signal.side == "buy" and not (sl < entry < tp):
            raise ExecutionError(f"Некорректные SL/TP для buy: {sl} / {tp} при цене {entry}")
        if signal.side == "sell" and not (tp < entry < sl):
            raise ExecutionError(f"Некорректные SL/TP для sell: {sl} / {tp} при цене {entry}")

        plan = ExecutionPlan(
            symbol=signal.symbol,
            side=signal.side,
            volume=sizing.volume,
            entry_price=round_price(entry, spec.digits),
            sl_price=sl,
            tp_price=tp,
            risk_money=sizing.risk_money,
            risk_pct=sizing.risk_pct,
            margin_required=sizing.margin_required,
            sl_distance=sizing.sl_price_distance,
            tp_distance=sizing.tp_price_distance,
            magic=magic,
            notes=notes,
        )
        log_event("plan", **plan.to_dict())
        return plan

    # -- отправка ---------------------------------------------------------------------
    def execute(self, plan: ExecutionPlan, *, spec: SymbolSpec | None = None) -> TradeResult:
        """Отправить заявку. Здесь и только здесь агент тратит деньги."""
        if self.dry_run:
            self.log.warning("[dry-run] заявка НЕ отправляется: %s", plan.describe())
            return TradeResult(True, 0, "dry-run", price=plan.entry_price, volume=plan.volume)

        result = self.broker.market_order(
            plan.symbol,
            plan.side,
            plan.volume,
            sl=plan.sl_price,
            tp=plan.tp_price,
            magic=plan.magic,
            comment=self.cfg.order_comment,
            deviation_points=self.cfg.deviation_points,
            filling_preference=self.cfg.filling_fallback,
            spec=spec,
        )
        if result.ok:
            log_event(
                "order_sent", symbol=plan.symbol, side=plan.side, volume=plan.volume,
                price=result.price, sl=plan.sl_price, tp=plan.tp_price,
                retcode=result.retcode, order=result.order_ticket,
            )
            self._check_slippage(plan, result)
        else:
            log_event(
                "order_failed", symbol=plan.symbol, side=plan.side, volume=plan.volume,
                retcode=result.retcode, comment=result.comment,
            )
        return result

    def _check_slippage(self, plan: ExecutionPlan, result: TradeResult) -> None:
        if not result.price or not plan.entry_price:
            return
        slip_price = abs(result.price - plan.entry_price)
        if plan.sl_distance <= 0:
            return
        # Разница в «пунктах» — через оценку: slippage / (sl_distance / ...) не нужен,
        # сравниваем допустимое отклонение, заданное в конфиге.
        spec = None
        try:
            spec = self.broker.symbol_spec(plan.symbol)
        except Exception:  # pragma: no cover
            pass
        if spec is None:
            return
        slip_points = slip_price / spec.point
        if slip_points > self.cfg.max_slippage_points:
            self.log.warning(
                "Проскальзывание %.1f пт больше лимита %s пт по %s — проверь ликвидность",
                slip_points, self.cfg.max_slippage_points, plan.symbol,
            )
            log_event("slippage", symbol=plan.symbol, slippage_points=slip_points,
                      entry_planned=plan.entry_price, entry_actual=result.price)

    # -- ведение позиций ----------------------------------------------------------------
    def manage_positions(
        self,
        positions: list[Position],
        *,
        specs: dict[str, SymbolSpec],
        atr_by_symbol: dict[str, float] | None = None,
    ) -> list[str]:
        """Перевод в безубыток и (опционально) сопровождение по ATR.

        Возвращает список сообщений о выполненных действиях — для логов/отчётов.
        """
        actions: list[str] = []
        atr_by_symbol = atr_by_symbol or {}
        for pos in positions:
            spec = specs.get(pos.symbol)
            if spec is None:
                continue
            try:
                tick = self.broker.tick(pos.symbol, spec)
            except Exception as exc:  # pragma: no cover
                self.log.warning("Нет тика по %s: %s", pos.symbol, exc)
                continue
            price = tick.bid if pos.is_long() else tick.ask
            if pos.sl <= 0:
                # Без стопа позицию держать нельзя — закрываем немедленно.
                self.log.error("Позиция %s без стоп-лосса — закрываю принудительно", pos.ticket)
                result = self.broker.close_position(pos)
                actions.append(f"{pos.symbol} #{pos.ticket}: стоп отсутствовал → закрыта ({result.comment})")
                log_event("unprotected_position_closed", ticket=pos.ticket, symbol=pos.symbol)
                continue

            new_sl = None

            # 1) безубыток при +X R
            if self.cfg.breakeven_at_r > 0:
                risk = abs(pos.price_open - pos.sl)
                profit_move = (price - pos.price_open) if pos.is_long() else (pos.price_open - price)
                if risk > 0 and profit_move >= self.cfg.breakeven_at_r * risk:
                    if (pos.is_long() and pos.sl < pos.price_open) or \
                       (not pos.is_long() and pos.sl > pos.price_open):
                        new_sl = pos.price_open

            # 2) сопровождение по ATR
            if self.cfg.trail_atr_mult > 0 and pos.symbol in atr_by_symbol:
                trail_distance = self.cfg.trail_atr_mult * atr_by_symbol[pos.symbol]
                candidate = price - trail_distance if pos.is_long() else price + trail_distance
                if pos.is_long() and candidate > (new_sl or pos.sl):
                    new_sl = candidate
                if not pos.is_long() and candidate < (new_sl or pos.sl):
                    new_sl = candidate

            if new_sl is None:
                continue
            new_sl = round_price(new_sl, spec.digits)

            # не двигаем стоп против позиции и не приближаем к цене ближе минимума
            min_dist = spec.min_stop_distance_price(tick.spread_points)
            if pos.is_long():
                if new_sl <= pos.sl or (price - new_sl) < min_dist:
                    continue
            else:
                if new_sl >= pos.sl or (new_sl - price) < min_dist:
                    continue

            result = self.broker.modify_position(pos.ticket, sl=new_sl)
            if result.ok:
                msg = f"{pos.symbol} #{pos.ticket}: SL {pos.sl:.5f} → {new_sl:.5f}"
                actions.append(msg)
                self.log.info("Стоп передвинут: %s", msg)
                log_event("stop_moved", ticket=pos.ticket, symbol=pos.symbol,
                          old_sl=pos.sl, new_sl=new_sl)
            else:
                self.log.warning("Не удалось изменить стоп %s: %s", pos.ticket, result.comment)
        return actions

    # -- учёт закрытий --------------------------------------------------------------------
    def closed_trade_pnl(self, ticket: int, opened_at: datetime | None = None) -> float:
        """Суммарный результат закрытой позиции из истории сделок."""
        start = (opened_at or (utc_now() - timedelta(days=30))).astimezone(UTC) - timedelta(minutes=1)
        end = utc_now() + timedelta(minutes=5)
        try:
            frame = self.broker.deals(start, end)
        except Exception as exc:  # pragma: no cover
            self.log.warning("Не удалось получить историю сделок: %s", exc)
            return 0.0
        if frame.empty:
            return 0.0
        column = "position_id" if "position_id" in frame.columns else None
        subset = frame[frame[column] == ticket] if column else frame.iloc[0:0]
        if subset.empty:
            return 0.0
        total = 0.0
        for col in ("profit", "swap", "commission"):
            if col in subset.columns:
                total += float(subset[col].sum())
        return total
