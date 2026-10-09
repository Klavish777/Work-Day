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
        except Exception as exc:  # ImportError / DLL load / .pyd init errors
            self.status = "UNAVAILABLE"
            self.detail = (f"MetaTrader5 import failed: {exc}. "
                           "Нужны Windows + установленный терминал MT5; если "
                           "это Windows — установите VC++ Redistributable "
                           "(2015-2022) и переустановите приложение.")

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._mt5 is not None

    def constants(self) -> Dict[str, int]:
        return CONSTANTS

    @staticmethod
    def candidate_terminal_paths(cfg_path: str) -> List[str]:
        """The user may paste a folder, or a full path; tolerate both and
        common terminal executable names."""
        import os
        p = (cfg_path or "").strip()
        out: List[str] = []
        if not p:
            return out
        out.append(p)
        if os.path.isdir(p):
            for name in ("terminal64.exe", "terminal.exe", "terminal_x64.exe",
                         "metatrader64.exe", "mt5terminal64.exe"):
                out.append(os.path.join(p, name))
        return out

    def connect(self, cfg: Dict[str, str]) -> Tuple[bool, str]:
        if not self.available:
            return False, self.detail

        login_raw = (cfg.get("login") or "").strip()
        login = int(login_raw) if login_raw else None
        password = cfg.get("password") or ""
        server = (cfg.get("server") or "").strip()

        attempts: List[str] = []

        def try_init(**kw) -> bool:
            try:
                ok = self._mt5.initialize(**kw)
            except Exception as exc:  # noqa: BLE001
                attempts.append(f"initialize({list(kw)}): {exc}")
                return False
            if ok:
                return True
            attempts.append(f"initialize({list(kw)}): {self._mt5.last_error()}")
            return False

        connected = False
        paths = self.candidate_terminal_paths(cfg.get("terminal_path", ""))
        if login is not None:
            for p in paths:
                if try_init(path=p, login=login, password=password,
                            server=server):
                    connected = True
                    break
            if not connected:
                connected = try_init(login=login, password=password,
                                     server=server)
        else:
            for p in paths:
                if try_init(path=p):
                    connected = True
                    break
            if not connected:
                connected = try_init()

        if not connected:
            self.status = "ERROR"
            self.detail = ("Не удалось запустить/подключить MT5. " +
                           "; ".join(attempts[:3]) +
                           " | Проверьте: терминал установлен; путь ведёт к "
                           "terminal64.exe (или оставьте пустым); терминал не "
                           "запущен параллельно.")
            return False, self.detail

        # A running terminal cannot be re-logged by the Python package: it
        # attaches to the already-running instance. Detect and explain.
        acc = self._mt5.account_info()
        if login is not None and acc is not None and acc.login != login:
            other = acc.login
            self._mt5.shutdown()
            self.status = "ERROR"
            self.detail = (f"Терминал уже работает под счётом {other}, а нужен "
                           f"{login}. Полностью ЗАКРОЙТЕ MetaTrader 5 "
                           "(трей → выход) и нажмите CONNECT MT5 ещё раз.")
            return False, self.detail

        info = self._mt5.terminal_info()
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
