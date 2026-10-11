"""Единственный модуль, который напрямую общается с терминалом MetaTrader 5.

Ключевые принципы:

* ``import MetaTrader5`` делается **лениво** — пакет существует только под
  Windows, и весь остальной код (риск, ML, бэктест, тесты) должен работать
  без него.
* Агент никогда не «додумывает» параметры инструмента: ``digits``, ``point``,
  ``tick_value``, ``volume_step`` берутся из терминала.
* Каждая отправка ордера — через :meth:`MT5Broker.market_order`, который
  делает ``order_check`` перед ``order_send`` и понятно логирует retcode.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .exceptions import BrokerError, ExecutionError
from .logging_setup import get_logger
from .specs import SymbolSpec
from .utils import UTC, utc_now

log = get_logger("broker")

# Режимы исполнения. В Python-API нет констант SYMBOL_FILLING_*, поэтому
# используем официальные значения битовой маски symbol_info.filling_mode.
FILLING_FLAG_FOK = 1
FILLING_FLAG_IOC = 2

_FILLING_BY_NAME = {
    "FOK": "ORDER_FILLING_FOK",
    "IOC": "ORDER_FILLING_IOC",
    "RETURN": "ORDER_FILLING_RETURN",
    "BOC": "ORDER_FILLING_BOC",
}

_TIMEFRAME_BY_NAME = {
    "M1": "TIMEFRAME_M1", "M2": "TIMEFRAME_M2", "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4", "M5": "TIMEFRAME_M5", "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10", "M12": "TIMEFRAME_M12", "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4", "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def _load_mt5():
    """Лениво импортировать официальный пакет MetaTrader5."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:  # pragma: no cover - зависит от платформы
        raise BrokerError(
            "Пакет MetaTrader5 не установлен. Он существует ТОЛЬКО под Windows "
            "(включая виртуальную машину/Windows-хост). Установка в PowerShell: "
            "py -3.11 -m pip install MetaTrader5\n"
            f"Техническая причина: {exc}"
        ) from exc
    return mt5


# -----------------------------------------------------------------------------
# Структуры данных
# -----------------------------------------------------------------------------

@dataclass
class MT5Credentials:
    login: int | None = None
    password: str | None = None
    server: str | None = None
    terminal_path: str | None = None
    portable: bool = False

    @classmethod
    def from_env(cls, prefix: str = "MT5") -> "MT5Credentials":
        login_raw = os.getenv(f"{prefix}_LOGIN", "").strip()
        return cls(
            login=int(login_raw) if login_raw else None,
            password=os.getenv(f"{prefix}_PASSWORD") or None,
            server=os.getenv(f"{prefix}_SERVER") or None,
            terminal_path=os.getenv(f"{prefix}_TERMINAL_PATH") or None,
            portable=os.getenv(f"{prefix}_PORTABLE", "").strip().lower() in ("1", "true", "yes"),
        )

    def merged(self, mt5_section: dict[str, Any]) -> "MT5Credentials":
        """Дополнить секреты значениями из config.yaml.mt5 (без пароля)."""
        return MT5Credentials(
            login=self.login or (mt5_section.get("login") or None),
            password=self.password or (mt5_section.get("password") or None),
            server=self.server or (mt5_section.get("server") or None),
            terminal_path=self.terminal_path or (mt5_section.get("terminal_path") or None),
            portable=self.portable or bool(mt5_section.get("portable")),
        )


@dataclass
class AccountSnapshot:
    login: int
    server: str
    company: str = ""
    currency: str = "USD"
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float | None = None
    leverage: int = 0
    trade_allowed: bool = False
    trade_expert: bool = False
    is_demo: bool = True
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def is_real(self) -> bool:
        return not self.is_demo

    def describe(self) -> str:
        kind = "ДЕМО" if self.is_demo else "РЕАЛЬНЫЙ"
        return (
            f"[{kind}] login={self.login} @ {self.server} | equity={self.equity:.2f} "
            f"{self.currency} | balance={self.balance:.2f} | плечо 1:{self.leverage} | "
            f"торговля: {'разрешена' if self.trade_allowed else 'ЗАПРЕЩЕНА'}"
        )


@dataclass
class Tick:
    symbol: str
    time: datetime
    bid: float
    ask: float
    last: float = 0.0
    spread_points: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str           # "buy" | "sell"
    volume: float
    price_open: float
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    magic: int = 0
    comment: str = ""
    time: datetime | None = None

    def is_long(self) -> bool:
        return self.side == "buy"


@dataclass
class TradeResult:
    ok: bool
    retcode: int
    comment: str = ""
    order_ticket: int | None = None
    deal_ticket: int | None = None
    price: float | None = None
    volume: float | None = None
    request: dict | None = None
    raw: Any = None

    def describe(self) -> str:
        state = "OK" if self.ok else "ОТКАЗ"
        return (
            f"{state} retcode={self.retcode} ({self.comment}) "
            f"price={self.price} volume={self.volume} order={self.order_ticket}"
        )


# -----------------------------------------------------------------------------
# Брокер
# -----------------------------------------------------------------------------

class MT5Broker:
    """Тонкая обёртка над ``MetaTrader5`` с проверками и логированием."""

    def __init__(
        self,
        credentials: MT5Credentials,
        *,
        timeout_ms: int = 60_000,
        reconnect_attempts: int = 5,
        reconnect_backoff_sec: float = 5.0,
        expected_login: int | None = None,
        logger=None,
    ) -> None:
        self.credentials = credentials
        self.timeout_ms = timeout_ms
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_backoff_sec = reconnect_backoff_sec
        self.expected_login = expected_login or credentials.login
        self.log = logger or log
        self._mt5 = None
        self._connected = False

    # -- подключение ----------------------------------------------------------
    @property
    def mt5(self):
        if self._mt5 is None:
            self._mt5 = _load_mt5()
        return self._mt5

    def connect(self) -> AccountSnapshot:
        """Подключиться к терминалу (и, при необходимости, авторизоваться)."""
        mt5 = self.mt5
        kwargs: dict[str, Any] = {}
        if self.credentials.terminal_path:
            kwargs["path"] = str(self.credentials.terminal_path)
        if self.credentials.portable:
            kwargs["portable"] = True
        if self.credentials.login:
            kwargs["login"] = int(self.credentials.login)
        if self.credentials.password:
            kwargs["password"] = str(self.credentials.password)
        if self.credentials.server:
            kwargs["server"] = str(self.credentials.server)
        kwargs["timeout"] = int(self.timeout_ms)

        last_error = None
        for attempt in range(1, self.reconnect_attempts + 1):
            ok = mt5.initialize(**kwargs)
            if ok:
                terminal = mt5.terminal_info()
                if terminal is not None and not getattr(terminal, "connected", True):
                    last_error = "терминал не подключён к торговому серверу (нет связи)"
                    mt5.shutdown()
                else:
                    self._connected = True
                    snapshot = self.account()
                    self.log.info("MT5 подключён: %s", snapshot.describe())
                    if getattr(terminal, "trade_allowed", True) is False:
                        self.log.warning(
                            "В терминале выключена кнопка AutoTrading — ордера не пройдут"
                        )
                    self._verify_login(snapshot)
                    return snapshot
            else:
                code, message = mt5.last_error()
                last_error = f"initialize() вернул False: [{code}] {message}"
                self.log.warning("Попытка %s/%s: %s", attempt, self.reconnect_attempts, last_error)
            if attempt < self.reconnect_attempts:
                time.sleep(self.reconnect_backoff_sec)
        raise BrokerError(f"Не удалось подключиться к MT5. Последняя ошибка: {last_error}")

    def _verify_login(self, snapshot: AccountSnapshot) -> None:
        if self.expected_login and int(snapshot.login) != int(self.expected_login):
            raise BrokerError(
                f"Терминал подключён к счёту {snapshot.login}, а ожидался "
                f"{self.expected_login}. Остановка: торговать не тем счётом опаснее, чем пропуск сделки."
            )

    def shutdown(self) -> None:
        if self._mt5 is not None and self._connected:
            try:
                self._mt5.shutdown()
            finally:
                self._connected = False

    def ensure_connected(self) -> None:
        if not self._connected:
            self.connect()
            return
        info = self.mt5.terminal_info()
        if info is None:
            self.log.warning("Связь с терминалом потеряна, переподключаюсь")
            self._connected = False
            self.connect()

    def __enter__(self) -> "MT5Broker":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.shutdown()

    # -- счёт ------------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        info = self.mt5.account_info()
        if info is None:
            raise BrokerError(f"account_info() недоступен: {self.mt5.last_error()}")
        return AccountSnapshot(
            login=int(info.login),
            server=str(info.server),
            company=str(getattr(info, "company", "")),
            currency=str(getattr(info, "currency", "USD")),
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(getattr(info, "margin", 0.0)),
            margin_free=float(getattr(info, "margin_free", 0.0)),
            margin_level=float(info.margin_level) if getattr(info, "margin_level", 0) else None,
            leverage=int(getattr(info, "leverage", 0)),
            trade_allowed=bool(getattr(info, "trade_allowed", False)),
            trade_expert=bool(getattr(info, "trade_expert", False)),
            is_demo=int(getattr(info, "trade_mode", 0)) == 0,  # ACCOUNT_TRADE_MODE_DEMO
        )

    # -- инструменты ------------------------------------------------------------
    def symbol_spec(self, symbol: str) -> SymbolSpec:
        mt5 = self.mt5
        info = mt5.symbol_info(symbol)
        if info is None:
            raise BrokerError(f"Символ {symbol} не найден в терминале: {mt5.last_error()}")
        if not getattr(info, "visible", True):
            if not mt5.symbol_select(symbol, True):
                raise BrokerError(f"Не удалось добавить {symbol} в Market Watch")
            info = mt5.symbol_info(symbol)
        return SymbolSpec(
            name=symbol,
            digits=int(info.digits),
            point=float(info.point),
            tick_size=float(getattr(info, "trade_tick_size", info.point)) or float(info.point),
            tick_value_loss=float(getattr(info, "trade_tick_value_loss", 0.0))
            or float(getattr(info, "trade_tick_value", 0.0)),
            tick_value_profit=float(getattr(info, "trade_tick_value_profit", 0.0))
            or float(getattr(info, "trade_tick_value", 0.0)),
            contract_size=float(getattr(info, "trade_contract_size", 0.0)),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            stops_level_points=int(getattr(info, "trade_stops_level", 0)),
            freeze_level_points=int(getattr(info, "trade_freeze_level", 0)),
            spread_points=float(getattr(info, "spread", 0.0)),
            currency_profit=str(getattr(info, "currency_profit", "USD")),
            trade_allowed=int(getattr(info, "trade_mode", 4)) != 0,
        )

    def filling_modes(self, symbol: str) -> list[str]:
        """Разрешённые режимы исполнения в порядке предпочтения агента."""
        info = self.mt5.symbol_info(symbol)
        if info is None:
            return ["IOC", "RETURN"]
        mask = int(getattr(info, "filling_mode", 0))
        allowed: list[str] = []
        if mask & FILLING_FLAG_IOC:
            allowed.append("IOC")
        if mask & FILLING_FLAG_FOK:
            allowed.append("FOK")
        allowed.append("RETURN")  # доступен почти всегда для market-ордеров
        return allowed

    def tick(self, symbol: str, spec: SymbolSpec | None = None) -> Tick:
        raw = self.mt5.symbol_info_tick(symbol)
        if raw is None:
            raise BrokerError(f"Нет тика по {symbol}: {self.mt5.last_error()}")
        spec = spec or self.symbol_spec(symbol)
        spread_points = (float(raw.ask) - float(raw.bid)) / spec.point if spec.point else 0.0
        return Tick(
            symbol=symbol,
            time=datetime.fromtimestamp(int(raw.time), UTC),
            bid=float(raw.bid),
            ask=float(raw.ask),
            last=float(getattr(raw, "last", 0.0)),
            spread_points=round(spread_points, 1),
        )

    def rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Исторические бары. ``timeframe`` — строка вида 'M15', 'H1'."""
        name = _TIMEFRAME_BY_NAME.get(str(timeframe).upper())
        if name is None:
            raise BrokerError(f"Неизвестный таймфрейм: {timeframe}")
        tf = getattr(self.mt5, name)
        raw = self.mt5.copy_rates_from_pos(symbol, tf, 0, int(count))
        if raw is None or len(raw) == 0:
            raise BrokerError(f"Не удалось получить бары {symbol} {timeframe}: {self.mt5.last_error()}")
        frame = pd.DataFrame(raw)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame = frame.set_index("time").sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        frame.attrs["symbol"] = symbol
        frame.attrs["timeframe"] = timeframe
        return frame

    # -- позиции / ордера --------------------------------------------------------
    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Position]:
        raw = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        if raw is None:
            return []
        result = []
        for item in raw:
            if magic is not None and int(item.magic) != int(magic):
                continue
            result.append(
                Position(
                    ticket=int(item.ticket),
                    symbol=str(item.symbol),
                    side="buy" if int(item.type) == 0 else "sell",
                    volume=float(item.volume),
                    price_open=float(item.price_open),
                    sl=float(item.sl),
                    tp=float(item.tp),
                    profit=float(item.profit),
                    swap=float(getattr(item, "swap", 0.0)),
                    magic=int(item.magic),
                    comment=str(getattr(item, "comment", "")),
                    time=datetime.fromtimestamp(int(item.time), UTC) if getattr(item, "time", None) else None,
                )
            )
        return result

    def pending_orders(self, symbol: str | None = None) -> list[Any]:
        raw = self.mt5.orders_get(symbol=symbol) if symbol else self.mt5.orders_get()
        return list(raw) if raw else []

    def deals(self, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        """Сделки за период (для учёта закрытого PnL)."""
        raw = self.mt5.history_deals_get(date_from, date_to)
        if raw is None or len(raw) == 0:
            return pd.DataFrame(
                columns=["ticket", "order", "time", "symbol", "type", "entry",
                         "magic", "volume", "price", "profit", "swap", "commission", "comment"]
            )
        # Записи MT5 — именованные кортежи; приводим к словарям явно, чтобы
        # DataFrame гарантированно получил именованные колонки.
        records = []
        for item in raw:
            if hasattr(item, "_asdict"):
                records.append(item._asdict())
            elif hasattr(item, "__dict__"):
                records.append(vars(item))
            else:  # pragma: no cover
                records.append(dict(item))
        frame = pd.DataFrame(records)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        return frame.sort_values("time").reset_index(drop=True)

    def realized_pnl(self, date_from: datetime, date_to: datetime, magic: int | None = None) -> float:
        """Закрытый результат (profit + swap + commission) за период."""
        frame = self.deals(date_from, date_to)
        if frame.empty:
            return 0.0
        if magic is not None and "magic" in frame.columns:
            frame = frame[frame["magic"] == magic]
        cols = [c for c in ("profit", "swap", "commission") if c in frame.columns]
        if not cols:
            return 0.0
        return float(frame[cols].sum().sum())

    # -- расчёты брокера ----------------------------------------------------------
    def calc_margin(self, side: str, symbol: str, volume: float, price: float) -> float | None:
        action = self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL
        value = self.mt5.order_calc_margin(action, symbol, float(volume), float(price))
        return None if value is None else float(value)

    def calc_profit(
        self, side: str, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        action = self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL
        value = self.mt5.order_calc_profit(action, symbol, float(volume), float(price_open), float(price_close))
        return None if value is None else float(value)

    # -- торговля ------------------------------------------------------------------
    def _pick_filling(self, symbol: str, preferred: list[str]) -> int:
        allowed = self.filling_modes(symbol)
        for name in preferred:
            if name in allowed:
                return getattr(self.mt5, _FILLING_BY_NAME[name])
        return getattr(self.mt5, _FILLING_BY_NAME["RETURN"])

    def market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
        comment: str = "mt5-agent",
        deviation_points: int = 10,
        filling_preference: list[str] | None = None,
        spec: SymbolSpec | None = None,
        dry_run: bool = False,
    ) -> TradeResult:
        """Отправить рыночный ордер с обязательным SL/TP.

        ``dry_run=True`` — «холостой» прогон: заполняем заявку и сверяем её
        через ``order_check``, но НЕ отправляем. Используется для проверки
        расчётов перед реальным запуском.
        """
        mt5 = self.mt5
        if side not in ("buy", "sell"):
            raise ExecutionError(f"Сторона должна быть buy/sell, получено {side!r}")
        if volume <= 0:
            raise ExecutionError("Объём должен быть > 0")
        if sl <= 0:
            raise ExecutionError(
                "Запрещено открывать позицию без стоп-лосса. Сначала посчитай SL."
            )
        spec = spec or self.symbol_spec(symbol)
        tick = self.tick(symbol, spec)
        price = tick.ask if side == "buy" else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": int(deviation_points),
            "magic": int(magic),
            "comment": comment[:31],  # MT5 ограничивает длину комментария
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling(symbol, filling_preference or ["IOC", "FOK", "RETURN"]),
        }

        check = mt5.order_check(request)
        if check is None:
            return TradeResult(False, -1, f"order_check() вернул None: {mt5.last_error()}", request=request)
        if int(check.retcode) not in (0, mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            return TradeResult(
                False, int(check.retcode), f"предпроверка: {check.comment}",
                request=request, raw=check,
            )
        if dry_run:
            self.log.info("[dry_run] заявка прошла проверку: %s %s %.2f %s",
                          side.upper(), symbol, volume, request)
            return TradeResult(True, int(check.retcode), "dry_run: проверка пройдена",
                               price=price, volume=volume, request=request, raw=check)

        result = mt5.order_send(request)
        if result is None:
            return TradeResult(False, -1, f"order_send() вернул None: {mt5.last_error()}", request=request)
        ok = int(result.retcode) in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        trade = TradeResult(
            ok=ok,
            retcode=int(result.retcode),
            comment=str(getattr(result, "comment", "")),
            order_ticket=int(getattr(result, "order", 0)) or None,
            deal_ticket=int(getattr(result, "deal", 0)) or None,
            price=float(getattr(result, "price", 0.0)) or None,
            volume=float(getattr(result, "volume", 0.0)) or None,
            request=request,
            raw=result,
        )
        if ok:
            self.log.info("Ордер исполнен: %s", trade.describe())
        else:
            self.log.error("Ордер НЕ исполнен: %s", trade.describe())
        return trade

    def modify_position(self, ticket: int, *, sl: float | None = None, tp: float | None = None) -> TradeResult:
        """Изменить SL/TP открытой позиции (например, перевод в безубыток)."""
        mt5 = self.mt5
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise ExecutionError(f"Позиция {ticket} не найдена")
        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": str(pos.symbol),
            "sl": float(pos.sl if sl is None else sl),
            "tp": float(pos.tp if tp is None else tp),
            "magic": int(getattr(pos, "magic", 0)),
        }
        result = mt5.order_send(request)
        if result is None:
            return TradeResult(False, -1, f"order_send() вернул None: {mt5.last_error()}", request=request)
        ok = int(result.retcode) in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        return TradeResult(ok, int(result.retcode), str(getattr(result, "comment", "")), request=request, raw=result)

    def close_position(self, position: Position, *, deviation_points: int = 10,
                       filling_preference: list[str] | None = None,
                       comment: str = "mt5-agent close") -> TradeResult:
        """Закрыть позицию встречным рыночным ордером."""
        mt5 = self.mt5
        opposite = "sell" if position.is_long() else "buy"
        tick = self.tick(position.symbol)
        price = tick.bid if position.is_long() else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": float(position.volume),
            "type": mt5.ORDER_TYPE_SELL if position.is_long() else mt5.ORDER_TYPE_BUY,
            "position": int(position.ticket),
            "price": float(price),
            "deviation": int(deviation_points),
            "magic": int(position.magic),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling(position.symbol, filling_preference or ["IOC", "FOK", "RETURN"]),
        }
        result = mt5.order_send(request)
        if result is None:
            return TradeResult(False, -1, f"order_send() вернул None: {mt5.last_error()}", request=request)
        ok = int(result.retcode) in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        trade = TradeResult(
            ok=ok,
            retcode=int(result.retcode),
            comment=str(getattr(result, "comment", "")),
            price=float(getattr(result, "price", 0.0)) or None,
            volume=float(getattr(result, "volume", 0.0)) or None,
            request=request,
            raw=result,
        )
        self.log.info("Закрытие позиции %s (%s): %s", position.ticket, opposite, trade.describe())
        return trade

    def close_all_own_positions(self, magics: list[int]) -> list[TradeResult]:
        """Аварийное закрытие всех своих позиций (используется kill switch'ем)."""
        results = []
        for pos in self.positions():
            if int(pos.magic) in {int(m) for m in magics}:
                results.append(self.close_position(pos))
        return results

    def terminal_ready(self) -> tuple[bool, str]:
        """Готов ли терминал к торговле (AutoTrading и т.п.)."""
        info = self.mt5.terminal_info()
        if info is None:
            return False, "terminal_info() недоступен"
        if not getattr(info, "connected", False):
            return False, "терминал не подключён к серверу"
        if not getattr(info, "trade_allowed", True):
            return False, "в терминале выключена кнопка AutoTrading"
        return True, "ok"

    def server_time_offset(self) -> timedelta:
        """Разница между временем сервера (последний тик) и UTC — для логов."""
        try:
            raw = self.mt5.symbol_info_tick("EURUSD")
            if raw is None:
                return timedelta(0)
            return datetime.fromtimestamp(int(raw.time), UTC) - utc_now()
        except Exception:  # pragma: no cover
            return timedelta(0)
