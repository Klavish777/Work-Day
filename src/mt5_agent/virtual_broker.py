"""Виртуальный брокер: полный цикл агента без MetaTrader5.

Зачем это нужно, если есть бэктест: бэктест проверяет **стратегию**, а виртуальный
брокер проверяет **сам агент** — состояние, лимиты, безубыток, учёт закрытых сделок,
записи в `state/`, дашборд, уведомления. Это разные вопросы, и смешивать их опасно.

Как это работает: агент думает, что говорит с терминалом, а на самом деле цены идут
из заранее сгенерированного ряда (синтетика или твои CSV-бары), позиции открываются
по ask/bid, стопы и тейки срабатывают по high/low следующего бара.

ЧЕГО ЭТО НЕ ДЕЛАЕТ И НЕ ДОКАЗЫВАЕТ:

* доходность на синтетических данных ничего не говорит о реальном рынке;
* здесь нет реквот, свопов, изменения спреда и проскальзывания — они появятся
  только на демо-счёте реального брокера;
* режим нужен, чтобы убедиться, что агент корректно работает как программа.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .broker import AccountSnapshot, Position, Tick, TradeResult
from .data_io import synthetic_bars
from .logging_setup import get_logger
from .specs import SymbolSpec, fallback_spec
from .utils import UTC

log = get_logger("virtual")


@dataclass
class VirtualBroker:
    """Заменитель MT5Broker: синтетический (или локальный) рынок, реальные деньги — нет."""

    symbol: str = "EURUSD"
    timeframe: str = "M15"
    balance: float = 1000.0
    digits: int = 5
    bars: pd.DataFrame | None = None
    warmup_bars: int = 800          # сколько истории «прошло» до старта агента
    spread_points: float = 8.0
    commission_per_lot: float = 7.0
    slippage_points: float = 0.0

    is_virtual: bool = True   # маркер для дашборда: это синтетический рынок

    positions_list: list[Position] = field(default_factory=list)
    deals_list: list[dict] = field(default_factory=list)
    orders_log: list[dict] = field(default_factory=list)
    equity: float = 0.0
    cursor: int = 0
    ticket_counter: int = 10_000

    def __post_init__(self) -> None:
        if self.bars is None:
            self.bars = synthetic_bars(self.symbol, self.timeframe, bars=6000, seed=11)
        self.cursor = min(self.warmup_bars, len(self.bars) - 2)
        self.equity = self.balance

    # -- служебное ----------------------------------------------------------------
    @property
    def spec(self) -> SymbolSpec:
        base = fallback_spec(self.symbol)
        overrides = {"digits": self.digits, "spread_points": self.spread_points}
        if self.digits != base.digits:
            point = 10 ** (-self.digits)
            overrides.update({"point": point, "tick_size": point})
        return fallback_spec(self.symbol, **overrides)

    def _price(self, offset: int = 0) -> float:
        index = max(0, min(self.cursor + offset, len(self.bars) - 1))
        return float(self.bars["close"].iloc[index])

    def _half_spread(self, spec: SymbolSpec) -> float:
        return self.spread_points / 2.0 * spec.point

    # -- интерфейс брокера -----------------------------------------------------------
    def ensure_connected(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def terminal_ready(self):
        return True, "ok (виртуальный брокер)"

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            login=99999999, server="Virtual-Demo", company="Виртуальный рынок",
            balance=self.balance, equity=self.equity, margin=0.0,
            margin_free=self.equity, leverage=100, trade_allowed=True,
            trade_expert=True, is_demo=True,
        )

    def symbol_spec(self, name: str | None = None) -> SymbolSpec:
        return self.spec

    def tick(self, name: str | None = None, spec: SymbolSpec | None = None) -> Tick:
        spec = spec or self.spec
        price = self._price()
        half = self._half_spread(spec)
        bar_time = self.bars.index[self.cursor].to_pydatetime()
        return Tick(symbol=name or self.symbol, time=bar_time, bid=price - half,
                    ask=price + half, spread_points=self.spread_points)

    def rates(self, name: str, timeframe: str, count: int) -> pd.DataFrame:
        frame = self.bars.iloc[max(0, self.cursor - int(count)): self.cursor + 1].copy()
        frame.attrs["symbol"] = name
        frame.attrs["point"] = self.spec.point
        frame.attrs["digits"] = self.spec.digits
        return frame

    def calc_margin(self, side: str, symbol: str, volume: float, price: float) -> float:
        return self.spec.contract_size * float(volume) * float(price) / 100.0

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        result = [self._mark(p) for p in self.positions_list]
        if symbol:
            result = [p for p in result if p.symbol == symbol]
        if magic is not None:
            result = [p for p in result if p.magic == magic]
        return result

    def _mark(self, position: Position) -> Position:
        """Пересчитать плавающий результат позиции по текущей цене."""
        spec = self.spec
        price = self._price()
        exit_price = price - self._half_spread(spec) if position.side == "buy" else price + self._half_spread(spec)
        delta = (exit_price - position.price_open) if position.side == "buy" else (position.price_open - exit_price)
        position.profit = round(spec.money_for_price_distance(delta, position.volume), 2)
        return position

    def market_order(self, symbol: str, side: str, volume: float, *, sl: float = 0.0,
                     tp: float = 0.0, magic: int = 0, comment: str = "",
                     deviation_points: int = 10, filling_preference=None,
                     spec: SymbolSpec | None = None, dry_run: bool = False) -> TradeResult:
        if not sl:
            return TradeResult(False, 10016, "без стоп-лосса ордера не принимаются")
        spec = spec or self.spec
        price = self._price() + (self._half_spread(spec) if side == "buy" else -self._half_spread(spec))
        price += self.slippage_points * spec.point
        self.ticket_counter += 1
        self.positions_list.append(
            Position(ticket=self.ticket_counter, symbol=symbol, side=side, volume=volume,
                     price_open=price, sl=sl, tp=tp, profit=0.0, magic=magic,
                     comment=comment, time=self.bars.index[self.cursor].to_pydatetime())
        )
        self.orders_log.append({"side": side, "volume": volume, "price": price, "sl": sl, "tp": tp})
        log.info("[виртуально] открыта позиция %s %s %.2f @ %.5f (SL %.5f TP %.5f)",
                 side.upper(), symbol, volume, price, sl, tp)
        return TradeResult(True, 10009, "виртуальное исполнение", order_ticket=self.ticket_counter,
                           price=price, volume=volume)

    def modify_position(self, ticket: int, *, sl: float | None = None, tp: float | None = None) -> TradeResult:
        for position in self.positions_list:
            if position.ticket == ticket:
                if sl is not None:
                    position.sl = sl
                if tp is not None:
                    position.tp = tp
                log.info("[виртуально] изменён стоп #%s → %.5f", ticket, sl)
                return TradeResult(True, 10009, "ок")
        return TradeResult(False, 10013, "позиция не найдена")

    def close_position(self, position: Position, **kwargs) -> TradeResult:
        return self._close(position, reason="manual")

    def _close(self, position: Position, *, reason: str, exit_price: float | None = None) -> TradeResult:
        spec = self.spec
        if exit_price is None:
            price = self._price()
            exit_price = price - self._half_spread(spec) if position.side == "buy" else price + self._half_spread(spec)
        delta = (exit_price - position.price_open) if position.side == "buy" else (position.price_open - exit_price)
        gross = spec.money_for_price_distance(delta, position.volume) * (1 if delta >= 0 else -1)
        commission = self.commission_per_lot * position.volume
        pnl = round(gross - commission, 2)
        self.balance = round(self.balance + pnl, 2)
        self.positions_list = [p for p in self.positions_list if p.ticket != position.ticket]
        self.deals_list.append({
            "ticket": position.ticket, "order": position.ticket,
            "time": int((self.bars.index[self.cursor]).timestamp()),
            "symbol": position.symbol, "type": 0 if position.side == "buy" else 1,
            "entry": 1, "magic": position.magic, "volume": position.volume,
            "price": exit_price, "profit": round(gross, 2), "swap": 0.0,
            "commission": -commission, "comment": reason, "position_id": position.ticket,
        })
        self._recalculate_equity()
        log.info("[виртуально] закрыта #%s (%s): %+.2f", position.ticket, reason, pnl)
        return TradeResult(True, 10009, f"закрыто ({reason})", price=exit_price, volume=position.volume)

    def deals(self, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        if not self.deals_list:
            return pd.DataFrame()
        frame = pd.DataFrame(self.deals_list)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        return frame.sort_values("time").reset_index(drop=True)

    # -- движение рынка ----------------------------------------------------------------
    def advance(self, bars: int = 1) -> list[str]:
        """Прокрутить рынок на N баров, попутно исполняя стопы и тейки.

        Возвращает список сообщений о закрытых позициях — чтобы демонстрационный
        прогон показал понятную картину.
        """
        events: list[str] = []
        for _ in range(max(1, bars)):
            if self.cursor >= len(self.bars) - 2:
                break
            self.cursor += 1
            bar = self.bars.iloc[self.cursor]
            for position in list(self.positions_list):
                if position.side == "buy":
                    if position.sl and float(bar["low"]) <= position.sl:
                        events.append(f"#{position.ticket}: сработал стоп")
                        self._close(position, reason="sl", exit_price=position.sl)
                    elif position.tp and float(bar["high"]) >= position.tp:
                        events.append(f"#{position.ticket}: достигнут тейк-профит")
                        self._close(position, reason="tp", exit_price=position.tp)
                else:
                    if position.sl and float(bar["high"]) >= position.sl:
                        events.append(f"#{position.ticket}: сработал стоп")
                        self._close(position, reason="sl", exit_price=position.sl)
                    elif position.tp and float(bar["low"]) <= position.tp:
                        events.append(f"#{position.ticket}: достигнут тейк-профит")
                        self._close(position, reason="tp", exit_price=position.tp)
            self._recalculate_equity()
        return events

    def _recalculate_equity(self) -> None:
        floating = sum(self._mark(p).profit for p in self.positions_list)
        self.equity = round(self.balance + floating, 2)

    @property
    def current_time(self) -> datetime:
        return self.bars.index[self.cursor].to_pydatetime().astimezone(UTC)

    def summary(self) -> str:
        return (
            f"Виртуальный счёт: баланс {self.balance:.2f}, equity {self.equity:.2f}, "
            f"сделок закрыто {len(self.deals_list)}, результат закрытых "
            f"{sum(d['profit'] + d['commission'] for d in self.deals_list):+.2f}"
        )
