"""Fake MT5 gateway for tests.

Implements the same interface as mt5.gateway.MT5Gateway with a deterministic
synthetic uptrend — used ONLY in unit tests, never in production.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.models import AccountView, Candle, PositionView, SymbolView, Tick
from mt5.gateway import CONSTANTS, TIMEFRAME_MAP


class FakeGateway:
    def __init__(self, trend_step=0.0006, base=0.9000):
        self.status = "CONNECTED"
        self.detail = "fake"
        self.resolved_symbol = "AUDCAD"
        self._step = trend_step
        self._base = base
        self.balance = 1000.0
        self.positions_list: List[PositionView] = []
        self._ticket_seq = 1000
        self.spread_pts = 10.0
        self.flat = False  # oscillating series -> agents should HOLD

    def constants(self):
        return CONSTANTS

    @property
    def available(self):
        return True

    def connect(self, cfg):
        return True, "fake connected"

    def disconnect(self):
        self.status = "DISCONNECTED"

    # ------------------------------------------------------------------ #
    def account(self) -> Optional[AccountView]:
        floating = sum(p.profit for p in self.positions_list)
        return AccountView(login=1, server="Fake-Demo", currency="USD",
                           balance=self.balance,
                           equity=self.balance + floating,
                           margin_free=self.balance, profit=floating,
                           is_demo=True)

    def symbol_view(self, symbol) -> Optional[SymbolView]:
        return SymbolView(symbol=self.resolved_symbol, digits=5,
                          point=0.00001, tick_size=0.00001,
                          tick_value=0.7,   # ~CADUSD conversion for AUDCAD
                          volume_min=0.01, volume_max=100.0,
                          volume_step=0.01, stops_level_points=5,
                          trade_allowed=True)

    def tick(self, symbol) -> Optional[Tick]:
        price = self._base + self._step * 50
        spread = self.spread_pts * 0.00001
        return Tick(symbol=self.resolved_symbol, bid=price, ask=price + spread,
                    spread_points=self.spread_pts, time=int(time.time()))

    def candles(self, symbol, timeframe, count=220) -> Optional[List[Candle]]:
        if timeframe not in TIMEFRAME_MAP:
            return None
        import math
        out = []
        now = int(time.time())
        price = self._base
        for i in range(count):
            o = price
            if self.flat:
                # consistent alternating range: no direction, RSI ~50, ADX ~0
                o = self._base + (-0.0002 if i % 2 == 0 else 0.0002)
                c = self._base + (0.0002 if i % 2 == 0 else -0.0002)
            else:
                o = price
                c = price + self._step
            h = max(o, c) + 0.00008
            l = min(o, c) - 0.00008
            out.append(Candle(time=now - (count - i) * 60, open=o, high=h,
                              low=l, close=c, tick_volume=100))
            price = c
        return out

    def positions(self, symbol=None) -> List[PositionView]:
        return list(self.positions_list)

    def position_close_deal(self, ticket):
        return None

    # ------------------------------------------------------------------ #
    def order_send(self, request: Dict[str, Any]):
        class R:
            pass

        r = R()
        r.comment = "done"
        if "position" in request:  # close order
            self.positions_list = [p for p in self.positions_list
                                   if p.ticket != request["position"]]
            r.retcode = CONSTANTS["RETCODE_DONE"]
            r.order = request["position"]
            return r
        self._ticket_seq += 1
        self.positions_list.append(PositionView(
            ticket=self._ticket_seq, symbol=request["symbol"],
            side="BUY" if request["type"] == CONSTANTS["ORDER_TYPE_BUY"] else "SELL",
            lot=request["volume"], open_price=request["price"],
            open_time=int(time.time()), profit=0.0,
            sl=request.get("sl", 0.0), tp=request.get("tp", 0.0)))
        r.retcode = CONSTANTS["RETCODE_DONE"]
        r.order = self._ticket_seq
        return r
