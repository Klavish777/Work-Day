"""End-to-end pipeline test with a fake MT5 gateway and deterministic agents.

Verifies the full chain:
  market data -> agents -> consensus -> central decision -> risk gate ->
  execution -> single-position rule -> profit target close -> journal.
"""
import time

import pytest

from core.engine import TradingEngine
from core.settings import SettingsManager
from database.storage import Storage
from journal.trade_journal import TradeJournal
from llm.base import NullProvider
from tests.fake_gateway import FakeGateway


@pytest.fixture()
def env(tmp_path):
    sm = SettingsManager(path=tmp_path / "s.json",
                         default_path=tmp_path / "d.json")
    # trend is strong -> deterministic agents should want to BUY
    sm.update({
        "min_confidence": 0.20,
        "min_risk_reward": 0.3,
        "profit_target_usd": 0.40,
        "stop_loss_usd": 1.0,
        "engine_cycle_sec": 2,
        "timeframes": ["M5", "M15", "H1"],
    })
    storage = Storage(path=tmp_path / "db.sqlite")
    journal = TradeJournal(storage)
    gw = FakeGateway()
    engine = TradingEngine(sm, gw, journal, NullProvider())
    return sm, gw, journal, engine


def run_cycle(engine):
    engine.engine_state = "RUNNING"
    engine._refresh_market_state()
    engine._cycle()


def test_full_chain_opens_one_position_and_blocks_second(env):
    sm, gw, journal, engine = env
    run_cycle(engine)

    assert len(gw.positions_list) == 1, "first cycle must open a BUY"
    pos = gw.positions_list[0]
    assert pos.side == "BUY" and pos.lot == 0.01

    snap = engine.state.snapshot()
    cycle = snap["last_cycle"]
    assert cycle["market"]["signal"] == "BUY"
    assert cycle["consensus"]["executable"] is True
    assert cycle["llm_decision"]["action"] == "BUY"
    assert cycle["gate"]["approved"] is True

    # Second cycle: single-position rule must block a new entry.
    run_cycle(engine)
    assert len(gw.positions_list) == 1
    snap = engine.state.snapshot()
    blocked = snap["last_cycle"].get("executed") or {}
    if blocked.get("action") == "BLOCKED_BY_RISK_MANAGER":
        assert any("position" in r.lower() for r in blocked["reasons"])


def test_profit_target_closes_position(env):
    sm, gw, journal, engine = env
    run_cycle(engine)
    assert len(gw.positions_list) == 1

    # simulate profit reaching the target
    gw.positions_list[0].profit = 0.45
    engine._refresh_market_state()
    engine._monitor_positions()
    assert len(gw.positions_list) == 0, "position must be auto-closed"

    closed = [t for t in journal.recent_trades() if t["close_ts"]]
    assert closed and "PROFIT_TARGET" in closed[0]["reason_close"]


def test_conflict_signal_holds(env):
    sm, gw, journal, engine = env
    # Flat / oscillating market: deterministic agents must not find a direction
    gw.flat = True
    run_cycle(engine)
    assert len(gw.positions_list) == 0
    cycle = engine.state.snapshot()["last_cycle"]
    assert cycle["llm_decision"]["action"] in ("HOLD",)


def test_journal_records_cycle_decisions(env):
    sm, gw, journal, engine = env
    run_cycle(engine)
    cycles = journal.recent_decisions(5)
    assert cycles
    payload = cycles[0]["payload"]
    for key in ("market", "strategy", "risk", "consensus", "llm_decision"):
        assert key in payload
