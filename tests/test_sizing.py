"""Тесты размера позиции — это самая «денежная» математика в проекте."""

from __future__ import annotations

import pytest

from mt5_agent.sizing import loss_for_volume, normalize_volume, size_position
from mt5_agent.specs import SymbolSpec


def test_volume_matches_risk_budget(symbol_spec: SymbolSpec) -> None:
    """1000 USD, риск 0.5% = 5 USD, стоп 150 пунктов → 0.03 лота."""
    result = size_position(
        symbol_spec, equity=1000.0, risk_pct=0.5,
        sl_price_distance=0.0015, tp_price_distance=0.00225,
    )
    assert result.approved
    assert result.volume == pytest.approx(0.03)
    assert result.risk_money == pytest.approx(4.5, abs=0.01)
    assert result.risk_pct <= 0.5
    assert result.reward_risk == pytest.approx(1.5, abs=0.01)


def test_volume_is_rounded_down_not_up(symbol_spec: SymbolSpec) -> None:
    """Округление объёма вверх увеличило бы риск — этого быть не должно."""
    result = size_position(
        symbol_spec, equity=1000.0, risk_pct=0.5,
        sl_price_distance=0.0010, tp_price_distance=0.0020,
    )
    assert result.approved
    # 5 / (100 пунктов * 1 USD) = 0.05 лота ровно
    assert result.volume == pytest.approx(0.05)
    # сырой объём 0.0555 → вниз до 0.05
    smaller = size_position(
        symbol_spec, equity=1111.0, risk_pct=0.5,
        sl_price_distance=0.0010, tp_price_distance=0.0020,
    )
    assert smaller.volume == pytest.approx(0.05)


def test_too_small_risk_budget_is_rejected_not_rounded_up(symbol_spec: SymbolSpec) -> None:
    """Маленький счёт: минимальный лот рискует больше лимита → ОТКАЗ."""
    result = size_position(
        symbol_spec, equity=100.0, risk_pct=0.5,
        sl_price_distance=0.0020, tp_price_distance=0.0040,
    )
    assert not result.approved
    assert "минимум у брокера" in (result.reject_reason or "")


def test_stop_closer_than_broker_minimum_is_rejected(symbol_spec: SymbolSpec) -> None:
    tight = SymbolSpec(name="EURUSD", point=1e-5, tick_size=1e-5, tick_value_loss=1.0,
                       stops_level_points=200)
    result = size_position(tight, equity=1000.0, risk_pct=0.5, sl_price_distance=0.0010)
    assert not result.approved
    assert "ближе" in (result.reject_reason or "")


def test_leverage_cap_reduces_volume(symbol_spec: SymbolSpec) -> None:
    result = size_position(
        symbol_spec, equity=1000.0, risk_pct=1.0, sl_price_distance=0.0010,
        margin_per_lot=50_000.0, max_leverage=5.0,
    )
    # без ограничения было бы 10/100 = 0.1 лота; маржа 0.1*50000 = 5000 = x5 лимит
    assert result.approved
    assert result.margin_required is not None
    assert result.margin_required <= 1000.0 * 5.0 * 1.05


def test_zero_equity_and_bad_risk_are_rejected(symbol_spec: SymbolSpec) -> None:
    assert not size_position(symbol_spec, equity=0.0, risk_pct=0.5, sl_price_distance=0.001).approved
    assert not size_position(symbol_spec, equity=1000.0, risk_pct=0.0, sl_price_distance=0.001).approved
    assert not size_position(symbol_spec, equity=1000.0, risk_pct=5.0, sl_price_distance=0.001).approved


def test_normalize_volume_floors_to_step(symbol_spec: SymbolSpec) -> None:
    assert normalize_volume(0.037, symbol_spec) == pytest.approx(0.03)
    assert normalize_volume(0.0399999, symbol_spec) == pytest.approx(0.03)
    assert normalize_volume(1000.0, symbol_spec) == pytest.approx(100.0)  # потолок брокера


def test_loss_for_volume_is_proportional(symbol_spec: SymbolSpec) -> None:
    assert loss_for_volume(symbol_spec, 0.0010, 1.0) == pytest.approx(100.0)
    assert loss_for_volume(symbol_spec, 0.0010, 0.1) == pytest.approx(10.0)


def test_jpy_like_spec_uses_tick_value(symbol_spec: SymbolSpec) -> None:
    """Для USDJPY стоимость пункта другая — формула должна это учитывать."""
    jpy = SymbolSpec(name="USDJPY", digits=3, point=1e-3, tick_size=1e-3,
                     tick_value_loss=0.68, volume_min=0.01, volume_step=0.01)
    result = size_position(jpy, equity=1000.0, risk_pct=0.5, sl_price_distance=0.150)
    assert result.approved
    # риск = 5 USD; 1 лот при 150 «пунктах» по 0.68 USD/пункт = 102 USD → 0.04 лота
    assert result.volume == pytest.approx(0.04)
    assert result.risk_money <= 5.0 * 1.05


def test_missing_tick_value_gives_clear_error() -> None:
    broken = SymbolSpec(name="XXXUSD", point=1e-5, tick_size=1e-5, tick_value_loss=0.0)
    result = size_position(broken, equity=1000.0, risk_pct=0.5, sl_price_distance=0.001)
    assert not result.approved
    assert "стоимость пункта" in (result.reject_reason or "")
