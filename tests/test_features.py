"""Тесты фич и меток.

Самый важный тест здесь — ``test_no_lookahead``: признаки на баре t не должны
меняться от того, что появились данные будущего. Если это ломается, весь
бэктест превращается в фантазию.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mt5_agent.exceptions import DataError
from mt5_agent.features import (
    atr,
    build_dataset,
    build_features,
    build_labels,
    cost_pct_from_spec,
    rsi,
    validate_ohlcv,
)


def test_no_lookahead_in_features(bars: pd.DataFrame) -> None:
    """Обрезаем будущее — признаки на общих барах обязаны совпасть."""
    full = build_features(bars)
    truncated = build_features(bars.iloc[:-25])
    common = truncated.index
    pd.testing.assert_frame_equal(
        full.loc[common], truncated, check_exact=False, atol=1e-12, rtol=1e-9
    )


def test_features_have_no_nan_after_warmup(bars: pd.DataFrame) -> None:
    features = build_features(bars)
    tail = features.iloc[-50:]
    assert not tail.isna().any().any(), tail.isna().sum()[tail.isna().sum() > 0]


def test_labels_use_future_but_are_marked(bars: pd.DataFrame) -> None:
    labels = build_labels(bars, horizon_bars=4, cost_pct=0.0)
    # последние 4 значения — NaN (будущего ещё нет)
    assert labels["target"].iloc[-4:].isna().all()
    # доходность совпадает с ручным расчётом
    close = bars["close"]
    manual = close.shift(-4) / close - 1.0
    pd.testing.assert_series_equal(
        labels["forward_return"], manual, check_names=False
    )


def test_cost_threshold_reduces_positive_labels(bars: pd.DataFrame) -> None:
    without_costs = build_labels(bars, horizon_bars=4, cost_pct=0.0)["target"].mean()
    with_costs = build_labels(bars, horizon_bars=4, cost_pct=0.01)["target"].mean()
    assert with_costs <= without_costs


def test_dataset_drops_warmup_rows(bars: pd.DataFrame) -> None:
    dataset = build_dataset(bars, lookbacks=[1, 2, 4, 8, 16, 32], horizon_bars=4)
    assert len(dataset) > 250  # 400 баров минус ~128 «прогрева» и минус горизонт
    feature_columns = [c for c in dataset.columns if c not in
                       ("target", "forward_return", "threshold", "close", "atr")]
    assert not dataset[feature_columns].isna().any().any()
    assert not dataset["target"].isna().any()
    # индекс возрастает (никакой перемешанности времени)
    assert dataset.index.is_monotonic_increasing


def test_validate_ohlcv_rejects_broken_data(bars: pd.DataFrame) -> None:
    with pytest.raises(DataError, match="нулевые|отрицательные"):
        broken = bars.copy()
        broken.loc[broken.index[10], "close"] = 0.0
        validate_ohlcv(broken, "EURUSD")

    with pytest.raises(DataError, match="пропуски"):
        broken = bars.copy()
        broken.loc[broken.index[5], "high"] = np.nan
        validate_ohlcv(broken, "EURUSD")

    with pytest.raises(DataError, match="мало данных"):
        validate_ohlcv(bars.iloc[:50], "EURUSD")


def test_atr_and_rsi_are_causal(bars: pd.DataFrame) -> None:
    atr_full = atr(bars, 14)
    atr_part = atr(bars.iloc[:-10], 14)
    pd.testing.assert_series_equal(
        atr_full.loc[atr_part.index], atr_part, check_names=False
    )
    rsi_full = rsi(bars["close"], 14)
    rsi_part = rsi(bars["close"].iloc[:-10], 14)
    pd.testing.assert_series_equal(
        rsi_full.loc[rsi_part.index], rsi_part, check_names=False
    )
    assert 0.0 <= rsi_full.dropna().iloc[-1] <= 100.0


def test_cost_pct_counts_spread_and_commission() -> None:
    # спред 12 пунктов по 0.00001 при цене 1.1 → 0.0109%
    spread_only = cost_pct_from_spec(12.0, 1e-5, 1.1)
    assert spread_only == pytest.approx(12 * 1e-5 / 1.1, rel=1e-6)
    # с комиссией $7 за лот (при $1 за пункт = 700 пунктов) издержки выше
    both = cost_pct_from_spec(12.0, 1e-5, 1.1, commission_per_lot=7.0,
                              spec_value_per_point_per_lot=1.0)
    assert both > spread_only


def test_position_in_range_within_bounds(bars: pd.DataFrame) -> None:
    features = build_features(bars)
    column = features["pos_in_range_64"].dropna()
    assert column.min() >= -0.01
    assert column.max() <= 1.01
