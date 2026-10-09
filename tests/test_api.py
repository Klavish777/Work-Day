"""Smoke tests for the REST API + dashboard that the Android app loads."""
import pytest
from fastapi.testclient import TestClient

from core.engine import TradingEngine
from core.settings import SettingsManager, REAL_CONFIRMATION_PHRASE
from database.storage import Storage
from journal.trade_journal import TradeJournal
from llm.base import NullProvider
from tests.fake_gateway import FakeGateway
from ui.api import create_app


@pytest.fixture()
def client(tmp_path):
    sm = SettingsManager(path=tmp_path / "s.json",
                         default_path=tmp_path / "d.json")
    journal = TradeJournal(Storage(path=tmp_path / "db.sqlite"))
    gw = FakeGateway()
    engine = TradingEngine(sm, gw, journal, NullProvider())
    # NOTE: engine.start() is intentionally NOT called — commands are queued
    # and processed manually via _process_commands() where needed.
    app = create_app(engine, sm, journal, gw)
    return TestClient(app), engine


def test_status_endpoint(client):
    c, _ = client
    r = c.get("/api/status")
    assert r.status_code == 200
    d = r.json()
    for key in ("engine_state", "mt5", "account", "position", "settings",
                "stats", "real_phrase"):
        assert key in d
    assert d["settings"]["symbol"] == "AUDCAD"
    assert d["real_phrase"] == REAL_CONFIRMATION_PHRASE


def test_dashboard_html_and_static_assets(client):
    c, _ = client
    assert c.get("/").status_code == 200
    assert "Work-Day" in c.get("/").text
    assert c.get("/static/style.css").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/icons/icon-192.png").status_code == 200
    assert c.get("/manifest.webmanifest").status_code == 200
    assert c.get("/sw.js").status_code == 200


def test_engine_controls(client):
    c, engine = client
    assert c.post("/api/engine/start").json()["engine_state"] == "RUNNING"
    engine._process_commands()
    assert engine.engine_state == "RUNNING"
    assert c.post("/api/engine/pause").json()["engine_state"] == "PAUSED"
    engine._process_commands()
    assert engine.engine_state == "PAUSED"
    assert c.post("/api/engine/stop").json()["engine_state"] == "STOPPED"
    engine._process_commands()
    assert engine.engine_state == "STOPPED"


def test_settings_update_via_api(client):
    c, _ = client
    r = c.post("/api/settings", json={"settings": {"lot_size": 0.05,
                                                    "profit_target_usd": 0.6}})
    assert r.status_code == 200 and r.json()["ok"]
    assert c.get("/api/settings").json()["lot_size"] == 0.05


def test_settings_validation_rejects_bad_values(client):
    c, _ = client
    r = c.post("/api/settings", json={"settings": {"lot_size": 5,
                                                    "max_lot": 0.1}})
    assert r.status_code == 400 and not r.json()["ok"]


def test_real_mode_requires_typed_phrase(client):
    c, _ = client
    r = c.post("/api/settings", json={"settings": {"mode": "REAL"}})
    assert r.status_code == 400
    assert c.get("/api/settings").json()["mode"] == "DEMO"
    r = c.post("/api/settings", json={
        "settings": {"mode": "REAL"},
        "real_confirm": REAL_CONFIRMATION_PHRASE})
    assert r.status_code == 200 and r.json()["settings"]["mode"] == "REAL"


def test_mt5_password_never_leaks(client):
    c, _ = client
    c.post("/api/settings", json={"settings": {
        "mt5": {"login": "123", "password": "secret", "server": "X-Demo"}}})
    d = c.get("/api/status").json()
    assert d["settings"]["mt5"]["password"] == ""
    assert d["settings"]["mt5"]["password_saved"] is True
    assert "secret" not in c.get("/api/status").text


def test_mt5_credentials_remembered(client):
    c, engine = client
    c.post("/api/mt5/connect", json={"login": "777", "password": "pw123",
                                     "server": "MetaQuotes-Demo"})
    st = c.get("/api/status").json()["settings"]["mt5"]
    assert st["login"] == "777"
    assert st["password_saved"] is True
    # second connect with no password reuses the remembered one
    c.post("/api/mt5/connect", json={})
    assert engine.settings_mgr.get()["mt5"]["password"] == "pw123"
    assert engine.settings_mgr.get()["mt5"]["login"] == "777"


def test_status_exposes_agent_memory_and_catalog(client):
    c, _ = client
    d = c.get("/api/status").json()
    assert "agent_memory" in d and "baseline" in d["agent_memory"]
    assert "strategy_catalog" in d and "ema_cross" in d["strategy_catalog"]


def test_quick_endpoint_for_fast_dashboard(client):
    c, _ = client
    r = c.get("/api/quick")
    assert r.status_code == 200
    d = r.json()
    for key in ("engine_state", "mt5_status", "tick", "account",
                "position", "mode"):
        assert key in d


def test_kristina_settings_validated(client):
    c, _ = client
    r = c.post("/api/settings", json={"settings": {
        "kristina": {"background": "cave", "tool": "drill"}}})
    assert r.status_code == 200
    kr = r.json()["settings"]["kristina"]
    assert kr["background"] == "cave" and kr["tool"] == "drill"
    # invalid values fall back to safe defaults
    r = c.post("/api/settings", json={"settings": {
        "kristina": {"background": "mars", "tool": "laser"}}})
    kr = r.json()["settings"]["kristina"]
    assert kr["background"] == "mars" or kr["background"] == "mountains"
    assert kr["tool"] in ("pickaxe", "shovel", "drill")


def test_chart_endpoint(client):
    c, _ = client
    r = c.get("/api/chart")
    assert r.status_code == 200
    d = r.json()
    for key in ("t", "p", "entry", "side", "symbol"):
        assert key in d
    assert isinstance(d["t"], list) and isinstance(d["p"], list)


def test_demo_connect_auto_starts_engine(client):
    c, engine = client
    assert engine.engine_state == "STOPPED"
    c.post("/api/mt5/connect", json={"login": "1", "password": "x",
                                     "server": "MetaQuotes-Demo"})
    engine._process_commands()
    # demo + auto_trading_enabled => engine auto-runs after connect
    assert engine.engine_state == "RUNNING"


def test_connect_attempt_is_reported(client):
    c, engine = client
    r = c.post("/api/mt5/connect", json={"login": "1", "password": "x",
                                         "server": "MetaQuotes-Demo",
                                         "terminal_path": ""})
    assert r.status_code == 200
    engine._process_commands()
    assert engine.last_connect is not None
    assert engine.last_connect["ok"] is True      # FakeGateway connects
    assert "fake" in engine.last_connect["detail"].lower()
    d = c.get("/api/status").json()
    assert d["last_connect"]["ok"] is True


def test_close_endpoints_queue_commands(client):
    c, _ = client
    assert c.post("/api/position/close").status_code == 200
    assert c.post("/api/position/close-all").status_code == 200


def test_journal_endpoints(client):
    c, _ = client
    assert c.get("/api/journal/trades").status_code == 200
    assert c.get("/api/journal/decisions").status_code == 200
    assert c.get("/api/journal/stats").status_code == 200
