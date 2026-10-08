"""MT5 Gateway — the ONLY module that talks to the MetaTrader 5 terminal.

Uses the official `MetaTrader5` Python package (Windows). No AI agent, the
LLM or the UI ever touches this API directly; only the gateway and (through
it) the Execution Engine do.

If the package is not installed / platform is unsupported the gateway
honestly reports status="unavailable" — nothing is faked.
"""
from __future__ import annotations

import logging
import platform
import time
from typing import Any, Dict, List, Optional, Tuple

from core.models import AccountView, Candle, PositionView, SymbolView, Tick

LOG = logging.getLogger("workday.mt5")

# ENUM_TIMEFRAMES values used by the MT5 terminal protocol.
TIMEFRAME_MAP = {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
                 "H1": 16385, "H4": 16388, "D1": 16408}

# Trade constants (identical to MetaTrader5 package values).
CONSTANTS = {
    "TRADE_ACTION_DEAL": 0,
    "ORDER_TYPE_BUY": 0,
    "ORDER_TYPE_SELL": 1,
    "ORDER_TIME_GTC": 0,
    "ORDER_FILLING_FOK": 0,
    "ORDER_FILLING_IOC": 1,
    "ORDER_FILLING_RETURN": 2,
    "RETCODE_DONE": 10009,
    "RETCODE_PLACED": 10010,
    "RETCODE_INVALID_FILL": 10030,
}

SYMBOL_SUFFIXES = ["", ".m", ".pro", ".ecn", ".raw", ".a", ".b", "_"]


class MT5Gateway:
    def __init__(self, logger=None):
        self.log = logger or LOG
        self._mt5 = None
        self.status = "DISCONNECTED"   # DISCONNECTED | CONNECTED | UNAVAILABLE | ERROR
        self.detail = ""
        self.resolved_symbol: Optional[str] = None
        try:
            import MetaTrader5 as _mt5  # noqa: PLC0415 — optional dependency
            self._mt5 = _mt5
        except ImportError:
            self.status = "UNAVAILABLE"
            self.detail = ("MetaTrader5 Python package is not installed on "
                           f"this platform ({platform.system()}). The package "
                           "requires Windows + an installed MT5 terminal. "
                           "Run the server on the machine with MT5 and do: "
                           "pip install MetaTrader5")

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._mt5 is not None

    def constants(self) -> Dict[str, int]:
        return CONSTANTS

    def connect(self, cfg: Dict[str, str]) -> Tuple[bool, str]:
        if not self.available:
            return False, self.detail
        kwargs: Dict[str, Any] = {}
        if cfg.get("terminal_path"):
            kwargs["path"] = cfg["terminal_path"]
        if cfg.get("login"):
            try:
                kwargs["login"] = int(cfg["login"])
            except ValueError:
                return False, "Login must be numeric."
        if cfg.get("password"):
            kwargs["password"] = cfg["password"]
        if cfg.get("server"):
            kwargs["server"] = cfg["server"]

        if not self._mt5.initialize(**kwargs):
            err = self._mt5.last_error()
            self.status = "ERROR"
            self.detail = f"mt5.initialize failed: {err}"
            return False, self.detail

        info = self._mt5.terminal_info()
        acc = self._mt5.account_info()
        if info is None or acc is None:
            self._mt5.shutdown()
            self.status = "ERROR"
            self.detail = "Connected but cannot read terminal/account info."
            return False, self.detail

        self.status = "CONNECTED"
        self.detail = (f"Terminal: {info.name} build {info.build}; "
                       f"account {acc.login} @ {acc.server} "
                       f"({'DEMO' if acc.trade_mode == 0 else 'REAL'}).")
        self.log.info("MT5 connected: %s", self.detail)
        return True, self.detail

    def disconnect(self) -> None:
        if self.available and self.status == "CONNECTED":
            self._mt5.shutdown()
        self.status = "DISCONNECTED"
        self.detail = "Disconnected by user."
        self.resolved_symbol = None

    # ------------------------------------------------------------------ #
    def ensure_symbol(self, symbol: str) -> Optional[str]:
        """Select the symbol; brokers often add suffixes (AUDCAD.m etc.)."""
        if not self.available or self.status != "CONNECTED":
            return None
        if self.resolved_symbol:
            return self.resolved_symbol
        for suffix in SYMBOL_SUFFIXES:
            name = symbol + suffix
            if self._mt5.symbol_info(name) is not None:
                self._mt5.symbol_select(name, True)
                self.resolved_symbol = name
                if suffix:
                    self.log.info("Symbol resolved as %s", name)
                return name
        return None

    def account(self) -> Optional[AccountView]:
        if not self.available or self.status != "CONNECTED":
            return None
        a = self._mt5.account_info()
        if a is None:
            return None
        return AccountView(login=a.login, server=a.server, currency=a.currency,
                           balance=a.balance, equity=a.equity,
                           margin_free=a.margin_free, profit=a.profit,
                           is_demo=(a.trade_mode == 0))

    def symbol_view(self, symbol: str) -> Optional[SymbolView]:
        if not self.available or self.status != "CONNECTED":
            return None
        name = self.ensure_symbol(symbol)
        if not name:
            return None
        si = self._mt5.symbol_info(name)
        if si is None:
            return None
        return SymbolView(
            symbol=name, digits=si.digits, point=si.point,
            tick_size=si.trade_tick_size, tick_value=si.trade_tick_value,
            volume_min=si.volume_min, volume_max=si.volume_max,
            volume_step=si.volume_step,
            stops_level_points=si.trade_stops_level,
            trade_allowed=bool(si.trade_mode and si.visible),
        )

    def tick(self, symbol: str) -> Optional[Tick]:
        if not self.available or self.status != "CONNECTED":
            return None
        name = self.ensure_symbol(symbol)
        if not name:
            return None
        si = self._mt5.symbol_info(name)
        t = self._mt5.symbol_info_tick(name)
        if si is None or t is None or si.point <= 0:
            return None
        return Tick(symbol=name, bid=t.bid, ask=t.ask,
                    spread_points=(t.ask - t.bid) / si.point, time=t.time)

    def candles(self, symbol: str, timeframe: str,
                count: int = 220) -> Optional[List[Candle]]:
        if not self.available or self.status != "CONNECTED":
            return None
        name = self.ensure_symbol(symbol)
        if not name:
            return None
        tf = TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            return None
        rates = self._mt5.copy_rates_from_pos(name, tf, 0, count)
        if rates is None or len(rates) == 0:
            return None
        out: List[Candle] = []
        for r in rates:
            out.append(Candle(time=int(r["time"]), open=float(r["open"]),
                              high=float(r["high"]), low=float(r["low"]),
                              close=float(r["close"]),
                              tick_volume=float(r["tick_volume"])))
        # drop the last (still forming) candle for indicator stability
        return out[:-1] if len(out) > 1 else out

    # ------------------------------------------------------------------ #
    def positions(self, symbol: Optional[str] = None) -> List[PositionView]:
        if not self.available or self.status != "CONNECTED":
            return []
        if symbol:
            name = self.ensure_symbol(symbol)
            if not name:
                return []
            raw = self._mt5.positions_get(symbol=name)
        else:
            raw = self._mt5.positions_get()
        if not raw:
            return []
        out = []
        for p in raw:
            side = "BUY" if p.type == CONSTANTS["ORDER_TYPE_BUY"] else "SELL"
            out.append(PositionView(
                ticket=p.ticket, symbol=p.symbol, side=side, lot=p.volume,
                open_price=p.price_open, open_time=p.time,
                profit=float(p.profit) + float(p.swap),
                sl=p.sl, tp=p.tp, comment=p.comment))
        return out

    def order_send(self, request: Dict[str, Any]):
        if not self.available or self.status != "CONNECTED":
            return None
        return self._mt5.order_send(request)

    def position_close_deal(self, ticket: int) -> Optional[Dict[str, Any]]:
        """Find the closing deal of a position in history (for the journal)."""
        if not self.available or self.status != "CONNECTED":
            return None
        try:
            deals = self._mt5.history_deals_get(
                int(time.time()) - 7 * 24 * 3600, int(time.time()) + 60,
                position=ticket)
        except TypeError:
            return None
        if not deals:
            return None
        out = None
        for d in deals:
            if getattr(d, "entry", 0) == 1:  # DEAL_ENTRY_OUT
                out = d
        if out is None:
            return None
        return {"profit": float(out.profit) + float(out.swap) + float(out.commission),
                "price": float(out.price), "time": int(out.time),
                "comment": out.comment}
