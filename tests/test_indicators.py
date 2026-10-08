import math

from agents import indicators as ta


def make_trend(n=120, start=0.9000, step=0.0004, noise=0.00005):
    highs, lows, closes = [], [], []
    price = start
    for i in range(n):
        o = price
        price = start + step * i + (0.0001 if i % 7 == 0 else 0.0)
        c = price
        highs.append(max(o, c) + noise)
        lows.append(min(o, c) - noise)
        closes.append(c)
    return highs, lows, closes


def test_ema_converges_to_constant():
    vals = [1.0] * 50
    out = ta.ema(vals, 10)
    assert out[-1] is not None
    assert abs(out[-1] - 1.0) < 1e-9


def test_sma_simple():
    out = ta.sma([1, 2, 3, 4, 5], 3)
    assert out[2] == 2.0 and out[4] == 4.0 and out[0] is None


def test_rsi_uptrend_high():
    closes = [100 + i * 0.5 for i in range(40)]
    out = ta.rsi(closes, 14)
    assert out[-1] is not None and out[-1] > 90


def test_rsi_downtrend_low():
    closes = [200 - i * 0.5 for i in range(40)]
    out = ta.rsi(closes, 14)
    assert out[-1] is not None and out[-1] < 10


def test_macd_hist_positive_in_uptrend():
    _, _, closes = make_trend()
    _, _, hist = ta.macd(closes)
    assert hist[-1] is not None and hist[-1] > 0


def test_atr_positive():
    highs, lows, closes = make_trend()
    out = ta.atr(highs, lows, closes, 14)
    assert out[-1] is not None and out[-1] > 0


def test_adx_strong_in_trend():
    highs, lows, closes = make_trend(160)
    adx, pdi, mdi = ta.adx(highs, lows, closes, 14)
    assert adx[-1] is not None
    assert adx[-1] > 20
    assert pdi[-1] > mdi[-1]


def test_pivot_levels_structure():
    highs = [1.0, 1.1, 1.0, 1.2, 1.0, 1.15, 1.0, 1.1, 1.05, 1.08]
    lows = [0.9, 0.95, 0.9, 0.98, 0.9, 0.97, 0.9, 0.95, 0.93, 0.96]
    closes = [0.95, 1.0, 0.95, 1.05, 0.95, 1.02, 0.95, 1.0, 0.98, 1.0]
    out = ta.pivot_levels(highs, lows, closes, fractal=1, lookback=20)
    assert set(out.keys()) == {"support", "resistance"}
    for lvl in out["support"]:
        assert lvl < closes[-1]
    for lvl in out["resistance"]:
        assert lvl > closes[-1]
