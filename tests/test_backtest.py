"""Тесты бэктестера: издержки, консервативная обработка стопов, вердикт."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mt5_agent.backtest import Backtester, compare_to_reality
from mt5_agent.config import BacktestConfig
from mt5_agent.specs import SymbolSpec


def make_symbol_cfg():
    from types import SimpleNamespace

    return SimpleNamespace(
        name="EURUSD", atr_period=14, sl_atr_mult=1.5, tp_atr_mult=2.25,
        max_spread_points=100, min_atr_points=1, session_start_utc="00:00",
        session_end_utc="23:59", magic=111111, timeframe="M15",
    )


def make_bars(closes: list[float], wick: float = 0.0004, freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq=freq, tz="UTC")
    closes_array = np.array(closes, dtype=float)
    opens = np.concatenate([[closes_array[0]], closes_array[:-1]])
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes_array) + wick,
            "low": np.minimum(opens, closes_array) - wick,
            "close": closes_array,
            "tick_volume": 1000.0,
            "spread": 12.0,
            "real_volume": 0.0,
        },
        index=index,
    )
    frame.attrs["symbol"] = "EURUSD"
    return frame


@pytest.fixture()
def spec() -> SymbolSpec:
    return SymbolSpec(name="EURUSD", digits=5, point=1e-5, tick_size=1e-5,
                      tick_value_loss=1.0, volume_min=0.01, volume_step=0.01)


def backtester(spec, **overrides) -> Backtester:
    cfg = BacktestConfig(**{"initial_balance": 1000.0, "min_trades": 1, "spread_points": 10.0,
                            "commission_per_lot": 7.0, "slippage_points": 1.0,
                            "risk_per_trade_pct": 0.5, "max_hold_bars": 30, **overrides})
    return Backtester(cfg, spec=spec, symbol_cfg=make_symbol_cfg())


def test_uptrend_hits_take_profit(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.99, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=20)
    assert result.trades, "должна быть хотя бы одна сделка"
    first = result.trades[0]
    assert first.side == "buy"
    assert first.exit_reason == "tp"
    assert first.pnl > 0


def test_stop_is_checked_before_take_profit(spec) -> None:
    """Если бар закрыл и стоп, и тейк — считаем стоп (худший вариант)."""
    closes = [1.10000] * 40
    bars = make_bars(closes)
    # одна огромная свеча: и высоко, и низко
    idx = bars.index[26]
    bars.loc[idx, ["open", "high", "low", "close"]] = [1.10000, 1.13000, 1.07000, 1.10000]
    probabilities = pd.Series(0.99, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=20)
    assert result.trades
    assert result.trades[0].exit_reason == "sl"
    assert result.trades[0].pnl < 0


def test_costs_reduce_result(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.99, index=bars.index)
    cheap = backtester(spec, spread_points=0.0, commission_per_lot=0.0, slippage_points=0.0)
    pricey = backtester(spec, spread_points=40.0, commission_per_lot=20.0, slippage_points=5.0)
    cheap_result = cheap.run(bars, probabilities, min_probability=0.58, start_index=20)
    pricey_result = pricey.run(bars, probabilities, min_probability=0.58, start_index=20)
    assert cheap_result.metrics["costs_total"] == pytest.approx(0.0)
    assert pricey_result.metrics["costs_total"] > 0
    assert pricey_result.final_equity < cheap_result.final_equity


def test_no_signals_no_trades(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00010 for i in range(80)])
    probabilities = pd.Series(0.50, index=bars.index)   # серая зона
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=10)
    assert result.trades == []
    assert result.verdict_ok is False
    assert any("Сделок" in note for note in result.verdict_notes)


def test_short_signal_is_simulated(spec) -> None:
    bars = make_bars([1.10000 - i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.01, index=bars.index)   # модель уверена в падении
    result = backtester(spec).run(bars, probabilities, min_probability=0.58,
                                  allow_short=True, start_index=20)
    assert result.trades
    assert result.trades[0].side == "sell"
    assert result.trades[0].pnl > 0


def test_shorts_disabled_when_allow_short_false(spec) -> None:
    bars = make_bars([1.10000 - i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.01, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58,
                                  allow_short=False, start_index=20)
    assert result.trades == []


def test_position_size_respects_risk(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.99, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=20)
    first = result.trades[0]
    # риск на сделку ~0.5% от 1000 = 5 USD → лот ≤ 0.06 при ATR-стопе
    assert first.volume <= 0.07
    assert first.pnl > -4.0 * 2  # даже с проскальзыванием убыток не катастрофичен


def test_verdict_flags_weak_strategy(spec) -> None:
    closes = []
    price = 1.1
    rng = np.random.default_rng(3)
    for _ in range(300):
        price += rng.normal(0, 0.0003)
        closes.append(price)
    bars = make_bars(closes)
    probabilities = pd.Series(np.linspace(0.85, 0.95, len(bars)), index=bars.index)
    result = backtester(spec, min_trades=300).run(bars, probabilities, min_probability=0.58, start_index=30)
    assert result.verdict_ok is False
    assert any("Сделок" in note for note in result.verdict_notes)


def test_compare_to_reality_detects_divergence(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.99, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=20)
    worse = compare_to_reality(result, real_pnl=-20.0, real_trades=50)
    assert "хуже" in worse
    no_trades = compare_to_reality(result, real_pnl=0.0, real_trades=0)
    assert "нет" in no_trades.lower()


def test_result_serialises_to_dict(spec) -> None:
    bars = make_bars([1.10000 + i * 0.00040 for i in range(120)])
    probabilities = pd.Series(0.99, index=bars.index)
    result = backtester(spec).run(bars, probabilities, min_probability=0.58, start_index=20)
    payload = result.to_dict()
    assert payload["symbol"] == "EURUSD"
    assert "trades" in payload and "metrics" in payload
    assert isinstance(result.to_text(), str)
