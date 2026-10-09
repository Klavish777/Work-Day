"""Manual BUY/SELL trading buttons + chart timeframe data (v2.3)."""
import time

import pytest
from fastapi.testclient import TestClient

from core.engine import TradingEngine
from core.settings import SettingsManager
from database.storage import Storage
from journal.trade_journal import TradeJournal
from llm.base import NullProvider
from tests.fake_gateway import FakeGateway
from ui.api import create_app


@pytest.fixture()
def env(tmp_path):
    sm = SettingsManager(path=tmp_path / "s.json",
                         default_path=tmp_path / "d.json")
    sm.update({"lot_size": 0.01, "profit_target_usd": 0.5,
               "stop_loss_usd": 1.0})
    storage = Storage(path=tmp_path / "db.sqlite")
    journal = TradeJournal(storage)
    gw = FakeGateway()
    engine = TradingEngine(sm, gw, journal, NullProvider())
    engine.start()
    yield sm, gw, journal, engine
    engine.shutdown()


def test_manual_buy_opens_position_and_second_is_blocked(env):
    sm, gw, journal, engine = env
    c = TestClient(create_app(engine, sm, journal, gw))
    r = c.post("/api/trade/manual", json={"side": "BUY"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["ticket"]
    assert len(gw.positions_list) == 1

    # single-position rule: a second manual order must be rejected
    r2 = c.post("/api/trade/manual", json={"side": "SELL"})
    assert r2.json()["ok"] is False


def test_manual_trade_requires_valid_side(env):
    sm, gw, journal, engine = env
    c = TestClient(create_app(engine, sm, journal, gw))
    r = c.post("/api/trade/manual", json={"side": "HOLD"})
    assert r.status_code == 400


def test_chart_candles_second_and_minute_timeframes(env):
    sm, gw, journal, engine = env
    c = TestClient(create_app(engine, sm, journal, gw))
    time.sleep(1.3)   # let the fast loop collect some ticks
    d = c.get("/api/chart/candles?tf=S1&count=60").json()
    assert d["tf"] == "S1" and d["source"] == "ticks"
    assert isinstance(d["candles"], list) and len(d["candles"]) >= 1
    for key in ("t", "o", "h", "l", "c"):
        assert key in d["candles"][0]

    d2 = c.get("/api/chart/candles?tf=M1&count=50").json()
    assert d2["tf"] == "M1" and d2["source"] == "mt5"
    assert isinstance(d2["candles"], list)
