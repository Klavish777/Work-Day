"""Agent memory: learning strategy weights from the trade history."""
import os

from core.memory import AgentMemory


def _mem(tmp_path):
    return AgentMemory(path=str(tmp_path / "mem.json"))


def test_memory_persists_across_restarts(tmp_path):
    p = str(tmp_path / "mem.json")
    m = AgentMemory(path=p)
    m.on_entry(101, ["ema_cross", "donchian"])
    m.on_close(101, +0.50)
    assert os.path.exists(p)

    m2 = AgentMemory(path=p)
    s = m2.summary()
    assert s["baseline"]["closed"] == 1
    assert s["baseline"]["wins"] == 1
    assert s["strategies"]["ema_cross"]["trades"] == 1
    assert s["strategies"]["ema_cross"]["weight"] > 1.0


def test_losing_trades_lower_weight(tmp_path):
    m = _mem(tmp_path)
    m.on_entry(1, ["rsi_reversion"])
    m.on_close(1, -0.80)
    m.on_entry(2, ["rsi_reversion"])
    m.on_close(2, -0.80)
    w_loss = m.weights()["rsi_reversion"]
    assert w_loss < 1.0

    m2 = _mem(tmp_path)
    for i in range(4):
        m2.on_entry(i, ["momentum"])
        m2.on_close(i, +0.50)
    assert m2.weights()["momentum"] > 1.0


def test_weight_clamped(tmp_path):
    m = _mem(tmp_path)
    for i in range(50):
        m.on_entry(i, ["adx_di"])
        m.on_close(i, +1.0)
    assert m.weights()["adx_di"] <= 2.0


def test_backfill_from_journal(tmp_path):
    m = _mem(tmp_path)
    m.backfill_from_journal([
        {"profit": 0.5}, {"profit": -0.2}, {"profit": 0.1}, {"profit": None},
    ])
    b = m.summary()["baseline"]
    assert b["closed"] == 3 and b["wins"] == 2 and b["losses"] == 1
    assert abs(b["pnl"] - 0.4) < 1e-9
