"""Минимальная подделка официального пакета ``MetaTrader5``.

Нужна, чтобы протестировать перевод наших структур в вызовы терминала и
обратно без Windows, без терминала и без интернета. Все константы и имена
полей повторяют настоящий пакет (см. офф. документацию MQL5/Python).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

# --- константы (значения как в официальном пакете) ---------------------------
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_FILLING_BOC = 3

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1

TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021
TRADE_RETCODE_INVALID_FILL = 10030

_ACCOUNT_TRADE_MODE_DEMO = 0
_ACCOUNT_TRADE_MODE_REAL = 2

_STATE: dict = {
    "initialized": False,
    "connected": True,
    "trade_allowed": True,
    "init_calls": 0,
    "init_fail_times": 0,      # сколько первых initialize() должны «упасть»
    "orders": [],              # отправленные запросы
    "check_retcode": 0,        # код, который вернёт order_check
    "send_retcode": TRADE_RETCODE_DONE,
    "positions": [],
    "deals": [],
    "trade_mode": _ACCOUNT_TRADE_MODE_DEMO,
}


def reset(**overrides) -> None:
    _STATE.update({
        "initialized": False, "connected": True, "trade_allowed": True,
        "init_calls": 0, "init_fail_times": 0, "orders": [],
        "check_retcode": 0, "send_retcode": TRADE_RETCODE_DONE,
        "positions": [], "deals": [], "trade_mode": _ACCOUNT_TRADE_MODE_DEMO,
    })
    _STATE.update(overrides)


def state() -> dict:
    return _STATE


def _ts(offset_hours: float = 0.0) -> int:
    return int(datetime(2024, 6, 3, 12, tzinfo=timezone.utc).timestamp() + offset_hours * 3600)


# --- API ---------------------------------------------------------------------

def initialize(*args, **kwargs):
    _STATE["init_calls"] += 1
    if _STATE["init_fail_times"] >= _STATE["init_calls"]:
        return False
    _STATE["initialized"] = True
    return True


def shutdown() -> None:
    _STATE["initialized"] = False


def last_error():
    if _STATE["initialized"]:
        return (1, "Success")
    return (-10003, "IPC initialization failed")


def terminal_info():
    if not _STATE["initialized"]:
        return None
    return SimpleNamespace(connected=_STATE["connected"], trade_allowed=_STATE["trade_allowed"],
                           build=4200, path="C:/MT5/terminal64.exe")


def account_info():
    if not _STATE["initialized"]:
        return None
    return SimpleNamespace(
        login=12345678, server="Test-Demo", company="Test Broker", currency="USD",
        balance=1000.0, equity=1000.0, margin=0.0, margin_free=1000.0, margin_level=0.0,
        leverage=100, trade_allowed=_STATE["trade_allowed"], trade_expert=True,
        trade_mode=_STATE["trade_mode"],
    )


def symbol_info(symbol: str):
    if not _STATE["initialized"]:
        return None
    return SimpleNamespace(
        name=symbol, digits=5, point=1e-5, spread=12,
        trade_tick_size=1e-5, trade_tick_value=1.0,
        trade_tick_value_loss=1.0, trade_tick_value_profit=1.0,
        trade_contract_size=100_000.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0, filling_mode=2,  # 2 = IOC разрешён
        visible=True, trade_mode=4, currency_profit="USD",
    )


def symbol_select(symbol: str, enable: bool = True) -> bool:
    return True


def symbol_info_tick(symbol: str):
    return SimpleNamespace(
        time=_ts(), bid=1.10000, ask=1.10012, last=0.0, volume=0,
        flags=6, volume_real=0.0,
    )


def copy_rates_from_pos(symbol: str, timeframe: int, start: int, count: int):
    import numpy as np

    dtype = [
        ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
        ("close", "<f8"), ("tick_volume", "<i8"), ("spread", "<i4"), ("real_volume", "<i8"),
    ]
    array = np.zeros(count, dtype=dtype)
    base = 1.1
    for i in range(count):
        array["time"][i] = _ts() - (count - i) * 900
        price = base + 0.0001 * (i % 20) + 0.00001 * i
        array["open"][i] = price
        array["high"][i] = price + 0.0003
        array["low"][i] = price - 0.0003
        array["close"][i] = price + 0.0001
        array["tick_volume"][i] = 1000
        array["spread"][i] = 12
    return array


def positions_get(symbol=None, ticket=None, group=None):
    result = _STATE["positions"]
    if symbol:
        result = [p for p in result if p.symbol == symbol]
    if ticket is not None:
        result = [p for p in result if p.ticket == ticket]
    return tuple(result) if result else None


def orders_get(symbol=None, ticket=None, group=None):
    return None


def history_deals_get(*args, **kwargs):
    deals = _STATE["deals"]
    return tuple(deals) if deals else None


def order_calc_margin(action, symbol, volume, price):
    return 1000.0 * float(volume)


def order_calc_profit(action, symbol, volume, price_open, price_close):
    direction = 1.0 if action == ORDER_TYPE_BUY else -1.0
    return direction * (price_close - price_open) / 1e-5 * float(volume)


def order_check(request: dict):
    _STATE["orders"].append({"kind": "check", **request})
    retcode = _STATE["check_retcode"]
    comment = "Done" if retcode in (0, TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED) else "Invalid stops"
    return SimpleNamespace(retcode=retcode, comment=comment, margin_free=500.0, balance=1000.0)


def order_send(request: dict):
    _STATE["orders"].append({"kind": "send", **request})
    retcode = _STATE["send_retcode"]
    if retcode == TRADE_RETCODE_DONE:
        return SimpleNamespace(retcode=retcode, comment="Done", order=7001, deal=8001,
                               price=request.get("price", 1.1), volume=request.get("volume", 0.01))
    return SimpleNamespace(retcode=retcode, comment="Rejected", order=0, deal=0,
                           price=0.0, volume=0.0)


def make_position(ticket: int, symbol: str = "EURUSD", type_: int = ORDER_TYPE_BUY,
                  volume: float = 0.01, price_open: float = 1.1, sl: float = 1.09,
                  tp: float = 1.12, profit: float = 0.0, magic: int = 111111) -> SimpleNamespace:
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=type_, volume=volume,
                           price_open=price_open, sl=sl, tp=tp, profit=profit, swap=0.0,
                           magic=magic, comment="test", time=_ts())


def make_deal(ticket: int, position_id: int, profit: float, symbol: str = "EURUSD",
              magic: int = 111111, commission: float = 0.0, swap: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(ticket=ticket, order=ticket, time=_ts(), symbol=symbol, type=1,
                           entry=1, magic=magic, volume=0.01, price=1.1, profit=profit,
                           swap=swap, commission=commission, comment="", position_id=position_id)
