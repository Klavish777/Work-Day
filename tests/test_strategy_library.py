"""Strategy library: deterministic votes from classic trading systems."""
import math

from agents import strategy_library as slib
from core.models import Candle


def _candles(n=150, drift=0.0004, base=0.9):
    out = []
    prev = base
    for i in range(n):
        c = base + drift * i + 0.0001 * math.sin(i / 3.0)
        o = prev
        out.append(Candle(time=i, open=o, high=max(o, c) + 0.0002,
                          low=min(o, c) - 0.0002, close=c))
        prev = c
    return out


def test_all_strategies_vote_on_real_candles():
    votes = slib.vote_all({"M15": _candles()})
    assert set(votes.keys()) == set(slib.CATALOG.keys())
    for name, v in votes.items():
        assert v["vote"] in ("BUY", "SELL", "NEUTRAL"), name
        assert 0.0 <= v["strength"] <= 1.0, name


def test_uptrend_majority_is_directional():
    votes = slib.vote_all({"M15": _candles(drift=0.0006)})
    agg = slib.aggregate(votes)
    assert agg["buy_n"] > 0
    assert agg["net"] > 0


def test_no_candles_gives_all_neutral():
    votes = slib.vote_all({})
    assert votes and all(v["vote"] == "NEUTRAL" for v in votes.values())


def test_aggregate_weights_shift_the_balance():
    votes = {
        "a": {"vote": "BUY", "strength": 0.5},
        "b": {"vote": "SELL", "strength": 0.5},
    }
    agg = slib.aggregate(votes, {"a": 2.0, "b": 1.0})
    assert agg["side"] == "BUY"
    agg2 = slib.aggregate(votes, {"a": 0.3, "b": 1.5})
    assert agg2["side"] == "SELL"
