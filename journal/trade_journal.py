"""Trade Journal — records AI decisions, openings, closings, results.

Everything needed for later strategy-effectiveness analysis is stored:
every agent's recommendation, the central LLM decision, the gate verdict,
execution result, prices, lot, P/L, timestamps and reasons.
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

from core.models import CycleReport


def _start_of_day() -> float:
    now = datetime.now()
    return datetime.combine(now.date(), dtime.min).timestamp()


class TradeJournal:
    def __init__(self, storage):
        self.db = storage
        # ticket -> journal row id (for closing positions later)
        self._open_rows: Dict[int, int] = {}

    # ------------------------------------------------------------------ #
    def record_cycle(self, report: CycleReport) -> None:
        self.db.save_cycle(report.cycle_id, report.ts, report.engine_state,
                           report.mt5_status, report.to_dict())

    def record_open(self, report: CycleReport, ticket: Optional[int],
                    symbol: str, side: str, lot: float, price: float,
                    reason: str) -> None:
        row_id = self.db.trade_open(
            cycle_id=report.cycle_id, ticket=ticket, symbol=symbol,
            side=side, lot=lot, open_ts=time.time(), open_price=price,
            reason_open=reason,
            payload={"llm_decision": (report.llm_decision.to_dict()
                                      if report.llm_decision else None),
                     "market_signal": (report.market.signal if report.market
                                       else None),
                     "strategy": (report.strategy.to_dict()
                                  if report.strategy else None),
                     "risk": (report.risk.to_dict() if report.risk else None)})
        if ticket:
            self._open_rows[ticket] = row_id

    def record_close(self, ticket: Optional[int], price: Optional[float],
                     profit: float, reason: str) -> None:
        row_id = self._open_rows.pop(ticket, None) if ticket else None
        self.db.trade_close(ticket, row_id, time.time(), price, profit,
                            reason)

    # ------------------------------------------------------------------ #
    def recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.db.recent_trades(limit)

    def recent_decisions(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self.db.recent_cycles(limit)

    def trades_today(self) -> List[Dict[str, Any]]:
        return self.db.trades_since(_start_of_day())

    def stats(self) -> Dict[str, Any]:
        trades = [t for t in self.db.recent_trades(1000)
                  if t.get("close_ts") is not None]
        wins = [t for t in trades if (t.get("profit") or 0) > 0]
        losses = [t for t in trades if (t.get("profit") or 0) <= 0]
        total_profit = sum(t.get("profit") or 0 for t in trades)
        gross_win = sum(t["profit"] for t in wins)
        gross_loss = abs(sum(t["profit"] for t in losses))
        today = self.trades_today()
        today_closed = [t for t in today if t.get("close_ts") is not None]
        return {
            "closed_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
            "total_profit": round(total_profit, 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "trades_today": len(today),
            "realized_today": round(sum(t.get("profit") or 0
                                        for t in today_closed), 2),
        }

    def realized_loss_today(self) -> float:
        today = [t for t in self.trades_today() if t.get("close_ts")]
        loss = sum(t.get("profit") or 0 for t in today if (t.get("profit") or 0) < 0)
        return abs(loss)
