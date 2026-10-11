"""Тесты локального хранилища и синтетических данных."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from mt5_agent.data_io import DataStore, synthetic_bars
from mt5_agent.exceptions import DataError
from mt5_agent.features import validate_ohlcv
from mt5_agent.specs import SymbolSpec, spec_from_dict


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    bars = synthetic_bars("EURUSD", "M15", bars=500, seed=1)
    store.save_bars("EURUSD", "M15", bars)
    assert store.exists("EURUSD", "M15")

    loaded = store.load_bars("EURUSD", "M15")
    assert len(loaded) == len(bars)
    assert loaded.index.tz is not None
    assert loaded.index.is_monotonic_increasing
    # данные «переживают» круг: цены совпадают
    assert loaded["close"].iloc[-1] == pytest.approx(bars["close"].iloc[-1], rel=1e-6)


def test_load_missing_file_gives_instruction(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    with pytest.raises(DataError, match="fetch"):
        store.load_bars("EURUSD", "M15")


def test_synthetic_bars_are_usable_and_deterministic() -> None:
    first = synthetic_bars("EURUSD", "M15", bars=1000, seed=42)
    second = synthetic_bars("EURUSD", "M15", bars=1000, seed=42)
    pd.testing.assert_frame_equal(first, second)
    # данные обязаны проходить базовую валидацию OHLC
    validate_ohlcv(first, "EURUSD")
    assert first.attrs["synthetic"] is True
    # high >= low и цены положительные
    assert (first["high"] >= first["low"]).all()
    assert (first["close"] > 0).all()


def test_synthetic_bars_different_seeds_differ() -> None:
    a = synthetic_bars("EURUSD", "M15", bars=300, seed=1)
    b = synthetic_bars("EURUSD", "M15", bars=300, seed=2)
    assert not a["close"].equals(b["close"])


def test_specs_roundtrip(tmp_path: Path) -> None:
    store = DataStore(tmp_path)
    specs = {
        "EURUSD": SymbolSpec(name="EURUSD", digits=5, point=1e-5, tick_size=1e-5,
                             tick_value_loss=1.0, volume_min=0.01, volume_step=0.01),
        "XAUUSD": SymbolSpec(name="XAUUSD", digits=2, point=0.01, tick_size=0.01,
                             tick_value_loss=1.0, volume_min=0.01, volume_step=0.01),
    }
    path = store.save_specs(specs)
    assert path.exists()

    loaded = store.load_specs()
    assert set(loaded) == {"EURUSD", "XAUUSD"}
    assert loaded["XAUUSD"].digits == 2
    assert loaded["EURUSD"].value_per_point_per_lot == pytest.approx(1.0)


def test_spec_from_dict_rejects_unknown_fields() -> None:
    from mt5_agent.exceptions import ConfigError

    with pytest.raises(ConfigError, match="Неизвестные поля"):
        spec_from_dict({"name": "EURUSD", "magic_number_of_doom": 1})
