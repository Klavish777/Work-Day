"""Тесты генерации сигнала: фильтры должны работать раньше модели."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from mt5_agent.signals import generate_signal, signal_as_dict, stops_for_side
from mt5_agent.utils import UTC
from tests.conftest import StubModel

NOW = datetime(2024, 6, 3, 12, 0, tzinfo=UTC)


def test_buy_signal_with_stops(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(
        bars, StubModel(0.90), symbol_cfg, now=NOW, spread_points=12.0, min_probability=0.58,
    )
    assert signal.is_entry
    assert signal.side == "buy"
    assert signal.sl_distance > 0 and signal.tp_distance > signal.sl_distance
    assert signal.tp_distance / signal.sl_distance == pytest.approx(symbol_cfg.reward_risk, rel=1e-6)


def test_short_signal_when_probability_low(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.05), symbol_cfg, now=NOW, spread_points=5.0,
                             min_probability=0.58, allow_short=True)
    assert signal.side == "sell"


def test_shorts_disabled_blocks_low_probability(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.05), symbol_cfg, now=NOW, spread_points=5.0,
                             min_probability=0.58, allow_short=False)
    assert signal.side == "none"
    assert "серой зоне" in signal.reason


def test_gray_zone_is_no_trade(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.55), symbol_cfg, now=NOW, spread_points=5.0,
                             min_probability=0.58)
    assert signal.side == "none"
    assert "серой зоне" in signal.reason


def test_wide_spread_blocks_entry(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.99), symbol_cfg, now=NOW,
                             spread_points=symbol_cfg.max_spread_points + 10)
    assert signal.side == "none"
    assert "спред" in signal.reason
    assert signal.filters["spread"] is False


def test_outside_session_blocks_entry(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    symbol_cfg.session_start_utc = "08:00"
    symbol_cfg.session_end_utc = "10:00"
    signal = generate_signal(bars, StubModel(0.99), symbol_cfg, now=NOW, spread_points=5.0)
    assert signal.side == "none"
    assert "сесси" in signal.reason


def test_low_volatility_blocks_entry(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    symbol_cfg.min_atr_points = 10_000   # заведомо недостижимо
    signal = generate_signal(bars, StubModel(0.99), symbol_cfg, now=NOW, spread_points=5.0)
    assert signal.side == "none"
    assert "волатильность" in signal.reason


def test_sentiment_blocks_even_strong_signal(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.99), symbol_cfg, now=NOW, spread_points=5.0,
                             sentiment_block=True, sentiment_note="FOMC через 10 минут")
    assert signal.side == "none"
    assert "новостной фильтр" in signal.reason


def test_missing_model_blocks_when_required(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, None, symbol_cfg, now=NOW, spread_points=5.0, require_model=True)
    assert signal.side == "none"
    assert "require_model_trained" in signal.reason


def test_stops_for_side_prices(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.90), symbol_cfg, now=NOW, spread_points=5.0)
    sl, tp = stops_for_side(signal, "buy", 1.10000)
    assert sl == pytest.approx(1.10000 - signal.sl_distance)
    assert tp == pytest.approx(1.10000 + signal.tp_distance)
    sl_s, tp_s = stops_for_side(signal, "sell", 1.10000)
    assert sl_s == pytest.approx(1.10000 + signal.sl_distance)
    assert tp_s == pytest.approx(1.10000 - signal.tp_distance)


def test_signal_serialises(bars: pd.DataFrame, config) -> None:
    symbol_cfg = config.trading.enabled_symbols[0]
    signal = generate_signal(bars, StubModel(0.90), symbol_cfg, now=NOW, spread_points=5.0)
    payload = signal_as_dict(signal)
    assert payload["symbol"] == "EURUSD"
    assert payload["side"] == "buy"
    assert "generated_at" in payload
