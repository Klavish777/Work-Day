"""MT5 Execution Engine — the only module allowed to send orders.

Neither the LLM nor the AI agents can call it directly with arbitrary
parameters: it receives a validated decision plus settings and re-checks
EVERYTHING itself (connection, account, single-position rule, lot limits,
trading hours/mode, spread, order validity) before sending anything.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.models import PositionView, SymbolView, Tick
from mt5.gateway import MT5Gateway

LOG = logging.getLogger("workday.execution")


def normalize_lot(lot: float, sym: Optional[SymbolView]) -> float:
    if sym is None or sym.volume_step <= 0:
        return round(lot, 2)
    steps = round((lot - sym.volume_min) / sym.volume_step)
    value = sym.volume_min + max(0, steps) * sym.volume_step
    return round(min(max(value, sym.volume_min), sym.volume_max), 8)


class ExecutionEngine:
    def __init__(self, gateway: MT5Gateway, settings_mgr, logger=None):
        self.gw = gateway
        self.settings_mgr = settings_mgr
        self.log = logger or LOG

    # ------------------------------------------------------------------ #
    def preflight(self, side: str, lot: float, tick: Optional[Tick],
                  sym: Optional[SymbolView],
                  positions: List[PositionView]) -> Tuple[bool, List[str]]:
        """Full technical validation before an order may leave the system."""
        settings = self.settings_mgr.get()
        fails: List[str] = []

        if self.gw.status != "CONNECTED":
            fails.append(f"MT5 not connected (status={self.gw.status}).")
        if side not in ("BUY", "SELL"):
            fails.append(f"Invalid side {side}.")

        if positions:
            fails.append("Open position exists — only ONE position allowed.")

        max_lot = float(settings.get("max_lot", 0.10))
        if lot > max_lot:
            fails.append(f"Lot {lot} exceeds max allowed {max_lot}.")
        if sym is not None:
            if not sym.trade_allowed:
                fails.append("Broker does not allow trading this symbol.")
            if lot < sym.volume_min or lot > sym.volume_max:
                fails.append(f"Lot {lot} outside broker volume range "
                             f"[{sym.volume_min}, {sym.volume_max}].")

        max_spread = float(settings.get("max_spread_points", 30))
        if tick is None:
            fails.append("No tick data — cannot validate price/spread.")
        elif tick.spread_points > max_spread:
            fails.append(f"Spread {tick.spread_points:.1f} pts exceeds limit "
                         f"{max_spread} pts.")

        return (not fails), fails

    # ------------------------------------------------------------------ #
    def open_position(self, side: str, lot: float, sl_price: float,
                      tp_price: float, reason: str) -> Dict[str, Any]:
        settings = self.settings_mgr.get()
        symbol = settings.get("symbol", "AUDCAD")
        tick = self.gw.tick(symbol)
        sym = self.gw.symbol_view(symbol)
        positions = self.gw.positions(symbol)

        ok, fails = self.preflight(side, lot, tick, sym, positions)
        if not ok:
            return {"ok": False, "ticket": None, "errors": fails}

        lot = normalize_lot(lot, sym)
        price = tick.ask if side == "BUY" else tick.bid
        digits = sym.digits if sym else 5
        c = self.gw.constants()

        # respect broker minimal stop distance
        if sym is not None and sym.stops_level_points > 0 and sym.point > 0:
            min_dist = sym.stops_level_points * sym.point
            if side == "BUY":
                if sl_price and price - sl_price < min_dist:
                    sl_price = round(price - min_dist, digits)
                if tp_price and tp_price - price < min_dist:
                    tp_price = round(price + min_dist, digits)
            else:
                if sl_price and sl_price - price < min_dist:
                    sl_price = round(price + min_dist, digits)
                if tp_price and price - tp_price < min_dist:
                    tp_price = round(price - min_dist, digits)

        request = {
            "action": c["TRADE_ACTION_DEAL"],
            "symbol": self.gw.resolved_symbol or symbol,
            "volume": lot,
            "type": c["ORDER_TYPE_BUY"] if side == "BUY" else c["ORDER_TYPE_SELL"],
            "price": round(price, digits),
            "sl": round(sl_price, digits) if sl_price else 0.0,
            "tp": round(tp_price, digits) if tp_price else 0.0,
            "deviation": int(settings.get("deviation_points", 20)),
            "magic": int(settings.get("magic_number", 20261008)),
            "comment": "WorkDay AI",
            "type_time": c["ORDER_TIME_GTC"],
            "type_filling": c["ORDER_FILLING_IOC"],
        }

        for filling in ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK",
                        "ORDER_FILLING_RETURN"):
            request["type_filling"] = c[filling]
            result = self.gw.order_send(request)
            if result is None:
                return {"ok": False, "ticket": None,
                        "errors": [f"order_send failed: {self.gw.detail}"]}
            retcode = getattr(result, "retcode", None)
            if retcode in (c["RETCODE_DONE"], c["RETCODE_PLACED"]):
                ticket = getattr(result, "order", None)
                self.log.info("OPENED %s %s @ %s ticket=%s (%s)",
                              side, lot, price, ticket, reason[:120])
                return {"ok": True, "ticket": ticket, "price": price,
                        "errors": []}
            if retcode == c["RETCODE_INVALID_FILL"]:
                continue
            return {"ok": False, "ticket": None,
                    "errors": [f"retcode={retcode}: "
                               f"{getattr(result, 'comment', '')}"]}
        return {"ok": False, "ticket": None,
                "errors": ["No supported filling mode accepted by broker."]}

    # ------------------------------------------------------------------ #
    def close_position(self, position: PositionView,
                       reason: str) -> Dict[str, Any]:
        sym = self.gw.symbol_view(position.symbol)
        tick = self.gw.tick(position.symbol)
        if tick is None:
            return {"ok": False, "errors": ["No tick data to close."]}
        c = self.gw.constants()
        digits = sym.digits if sym else 5
        price = tick.bid if position.side == "BUY" else tick.ask
        close_type = (c["ORDER_TYPE_SELL"] if position.side == "BUY"
                      else c["ORDER_TYPE_BUY"])
        request = {
            "action": c["TRADE_ACTION_DEAL"],
            "symbol": position.symbol,
            "volume": position.lot,
            "type": close_type,
            "position": position.ticket,
            "price": round(price, digits),
            "deviation": 20,
            "magic": int(self.settings_mgr.get().get("magic_number", 20261008)),
            "comment": "WorkDay AI close",
            "type_time": c["ORDER_TIME_GTC"],
            "type_filling": c["ORDER_FILLING_IOC"],
        }
        for filling in ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK",
                        "ORDER_FILLING_RETURN"):
            request["type_filling"] = c[filling]
            result = self.gw.order_send(request)
            if result is None:
                return {"ok": False, "errors": [f"order_send failed: "
                                                f"{self.gw.detail}"]}
            retcode = getattr(result, "retcode", None)
            if retcode in (c["RETCODE_DONE"], c["RETCODE_PLACED"]):
                self.log.info("CLOSED #%s at %s (%s)", position.ticket,
                              price, reason[:120])
                return {"ok": True, "price": price, "errors": []}
            if retcode == c["RETCODE_INVALID_FILL"]:
                continue
            return {"ok": False,
                    "errors": [f"retcode={retcode}: "
                               f"{getattr(result, 'comment', '')}"]}
        return {"ok": False, "errors": ["No filling mode accepted."]}
