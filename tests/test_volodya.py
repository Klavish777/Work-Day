"""Володя — the built-in trading brain (no external LLM)."""
from core.memory import AgentMemory
from core.models import MarketAnalysis, PositionView
from core.volodya import VolodyaCore


def _brain(tmp_path):
    mem = AgentMemory(path=str(tmp_path / "m.json"))
    return VolodyaCore(mem), mem


def _votes(n_buy=6, n_sell=1, strength=0.8):
    v = {}
    for i in range(n_buy):
        v[f"b{i}"] = {"vote": "BUY", "strength": strength}
    for i in range(n_sell):
        v[f"s{i}"] = {"vote": "SELL", "strength": 0.4}
    v["n0"] = {"vote": "NEUTRAL", "strength": 0.0}
    return v


def test_enters_on_strong_dominance(tmp_path):
    vol, _ = _brain(tmp_path)
    m = MarketAnalysis(trend="BULLISH")
    d = vol.decide(_votes(), m, None, None, None,
                   {"profit_target_usd": 0.5}, None, None, None, [])
    assert d.action == "BUY"
    assert d.source == "VOLODYA"
    assert d.confidence >= 0.42


def test_holds_on_weak_votes(tmp_path):
    vol, _ = _brain(tmp_path)
    m = MarketAnalysis()
    votes = {"a": {"vote": "BUY", "strength": 0.3},
             "b": {"vote": "SELL", "strength": 0.3}}
    d = vol.decide(votes, m, None, None, None, {}, None, None, None, [])
    assert d.action == "HOLD" and d.source == "VOLODYA"


def test_respects_confident_counter_trend(tmp_path):
    vol, _ = _brain(tmp_path)
    m = MarketAnalysis(signal="SELL", confidence=0.8)
    d = vol.decide(_votes(), m, None, None, None, {}, None, None, None, [])
    assert d.action == "HOLD"


def test_closes_position_at_target(tmp_path):
    vol, _ = _brain(tmp_path)
    pos = PositionView(ticket=1, symbol="AUDCAD", side="BUY", lot=0.01,
                       open_price=0.9, open_time=0, profit=0.6)
    d = vol.decide({}, MarketAnalysis(), None, None, None,
                   {"profit_target_usd": 0.5}, None, pos, None, [])
    assert d.action == "CLOSE"


def test_closes_on_stop_limit(tmp_path):
    vol, _ = _brain(tmp_path)
    pos = PositionView(ticket=1, symbol="AUDCAD", side="BUY", lot=0.01,
                       open_price=0.9, open_time=0, profit=-1.2)
    d = vol.decide({}, MarketAnalysis(), None, None, None,
                   {"profit_target_usd": 0.5, "stop_loss_usd": 1.0},
                   None, pos, None, [])
    assert d.action == "CLOSE"


def test_self_tuning_threshold(tmp_path):
    _, mem = _brain(tmp_path)
    before = mem.get_params()["entry_net"]
    mem.on_entry(1, ["ema_cross"])
    mem.on_close(1, -0.80)
    assert mem.get_params()["entry_net"] > before   # loss -> more cautious
    mid = mem.get_params()["entry_net"]
    mem.on_entry(2, ["ema_cross"])
    mem.on_close(2, +0.50)
    assert mem.get_params()["entry_net"] < mid      # win -> more active
