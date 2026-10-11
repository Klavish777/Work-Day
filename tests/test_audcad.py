"""Тесты специфики AUDCAD: котировка в CAD, два окна ликвидности, ширина спреда.

AUDCAD — не «ещё один EURUSD». Здесь проверяются именно те отличия, которые
влияют на деньги: стоимость пункта (валюта прибыли CAD), допустимый спред и
торговые окна (Токио для AUD, Нью-Йорк для CAD).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from mt5_agent.config import ConfigError, SymbolConfig
from mt5_agent.data_io import synthetic_bars
from mt5_agent.sizing import size_position
from mt5_agent.specs import FALLBACK_SPECS, fallback_spec
from mt5_agent.utils import UTC, describe_windows, in_session_windows
from tests.conftest import StubModel


# --- сессионные окна ----------------------------------------------------------

def test_single_window_still_works() -> None:
    assert in_session_windows(datetime(2024, 6, 3, 10, tzinfo=UTC), [("07:00", "19:00")])
    assert not in_session_windows(datetime(2024, 6, 3, 3, tzinfo=UTC), [("07:00", "19:00")])


def test_two_windows_cover_both_peaks() -> None:
    windows = [("00:00", "06:00"), ("13:00", "19:00")]
    # токийская сессия (AUD активен)
    assert in_session_windows(datetime(2024, 6, 3, 3, 30, tzinfo=UTC), windows)
    # нью-йоркская (CAD активен)
    assert in_session_windows(datetime(2024, 6, 3, 15, 0, tzinfo=UTC), windows)
    # «мёртвая зона» между сессиями — вход запрещён
    assert not in_session_windows(datetime(2024, 6, 3, 9, 0, tzinfo=UTC), windows)
    # ролловер и вечер пятницы — тоже вне окон
    assert not in_session_windows(datetime(2024, 6, 3, 22, 30, tzinfo=UTC), windows)


def test_empty_windows_means_always_open() -> None:
    assert in_session_windows(datetime(2024, 6, 3, 22, 30, tzinfo=UTC), [])


def test_describe_windows_is_human_readable() -> None:
    text = describe_windows([("00:00", "06:00"), ("13:00", "19:00")])
    assert text == "00:00–06:00, 13:00–19:00"
    assert describe_windows(None, "07:00", "19:00") == "07:00–19:00"


def test_config_accepts_session_windows_and_validates() -> None:
    symbol = SymbolConfig(name="AUDCAD", session_windows=[["00:00", "06:00"], ["13:00", "19:00"]])
    assert symbol.windows == [("00:00", "06:00"), ("13:00", "19:00")]
    assert symbol.session_text() == "00:00–06:00, 13:00–19:00"

    with pytest.raises(ConfigError, match="окно сессии"):
        SymbolConfig(name="AUDCAD", session_windows=[["00:00"]])

    with pytest.raises(ValueError):
        SymbolConfig(name="AUDCAD", session_windows=[["25:00", "06:00"]])


def test_config_windows_override_start_end() -> None:
    symbol = SymbolConfig(name="AUDCAD", session_start_utc="07:00", session_end_utc="19:00",
                          session_windows=[["00:00", "06:00"]])
    assert symbol.windows == [("00:00", "06:00")]


# --- стоимость пункта: валюта прибыли CAD ---------------------------------------

def test_audcad_fallback_spec_is_cad_based() -> None:
    spec = fallback_spec("AUDCAD")
    assert spec.name == "AUDCAD"
    assert spec.digits == 5
    assert spec.point == pytest.approx(1e-5)
    assert spec.currency_profit == "CAD"
    # стоимость пункта НЕ 1.0 USD: CAD переводится в USD по курсу USDCAD
    assert 0.5 < spec.tick_value_loss < 1.0


def test_audcad_position_size_uses_cad_tick_value() -> None:
    """1000 USD, риск 0.5% = 5 USD, стоп 150 пунктов, тик 0.72 USD."""
    spec = fallback_spec("AUDCAD")
    result = size_position(spec, equity=1000.0, risk_pct=0.5,
                           sl_price_distance=0.0015, tp_price_distance=0.00225)
    assert result.approved
    # 1 лот при стопе 150 пунктов = 108 USD риска → 0.04 лота (5 / 108 = 0.046)
    assert result.volume == pytest.approx(0.04)
    assert result.risk_money <= 5.0 * 1.05
    assert result.reward_risk == pytest.approx(1.5, rel=1e-6)


def test_audcad_risk_is_smaller_than_eurusd_for_same_stop() -> None:
    """При одинаковом стопе лот по AUDCAD больше — потому что пункт дешевле.

    Это важная ловушка: «лот как у EURUSD» дал бы здесь другой риск.
    """
    audcad = fallback_spec("AUDCAD")
    eurusd = fallback_spec("EURUSD")
    size_audcad = size_position(audcad, equity=1000.0, risk_pct=0.5, sl_price_distance=0.0015)
    size_eurusd = size_position(eurusd, equity=1000.0, risk_pct=0.5, sl_price_distance=0.0015)
    assert size_audcad.volume > size_eurusd.volume
    # но денежный риск у обеих сделок одинаковый — это и есть смысл расчёта от риска
    assert size_audcad.risk_money == pytest.approx(size_eurusd.risk_money, abs=0.3)


def test_all_fallback_specs_are_valid() -> None:
    for name, spec in FALLBACK_SPECS.items():
        assert spec.name == name
        assert spec.point > 0 and spec.tick_size > 0
        assert spec.tick_value_loss > 0, f"{name}: без стоимости пункта риск не посчитать"


# --- синтетика и сигнал по AUDCAD ------------------------------------------------

def test_synthetic_audcad_price_level_is_realistic() -> None:
    bars = synthetic_bars("AUDCAD", "M15", bars=500, seed=5)
    # уровень цен AUDCAD ~0.9, а не 1.1 как у EURUSD
    assert 0.5 < bars["close"].iloc[0] < 1.2
    assert bars.attrs["symbol"] == "AUDCAD"


def test_signal_blocked_in_dead_zone_and_allowed_in_peak() -> None:
    """AUDCAD: в «мёртвой зоне» 09:00 UTC входа нет, в 15:00 UTC — есть."""
    from mt5_agent.signals import generate_signal

    symbol_cfg = SymbolConfig(
        name="AUDCAD", timeframe="M15", max_spread_points=30, min_atr_points=1,
        session_windows=[["00:00", "06:00"], ["13:00", "19:00"]], magic=20260011,
    )
    bars = synthetic_bars("AUDCAD", "M15", bars=600, seed=7)

    in_peak = generate_signal(bars, StubModel(0.9), symbol_cfg,
                              now=datetime(2024, 6, 3, 15, 0, tzinfo=UTC), spread_points=12.0)
    assert in_peak.filters["session"] is True
    assert in_peak.side == "buy"

    in_dead_zone = generate_signal(bars, StubModel(0.9), symbol_cfg,
                                   now=datetime(2024, 6, 3, 9, 0, tzinfo=UTC), spread_points=12.0,
                                   allow_short=False)
    assert in_dead_zone.filters["session"] is False
    assert in_dead_zone.side == "none"
    assert "вне торговых сессий" in in_dead_zone.reason


def test_wide_audcad_spread_blocks_entry() -> None:
    """У AUDCAD спред шире: лимит 30 пунктов пропускает обычный спред и режет ролловер."""
    from mt5_agent.signals import generate_signal

    symbol_cfg = SymbolConfig(name="AUDCAD", timeframe="M15", max_spread_points=30, min_atr_points=1,
                              session_windows=[["00:00", "06:00"], ["13:00", "19:00"]])
    bars = synthetic_bars("AUDCAD", "M15", bars=600, seed=7)
    signal = generate_signal(bars, StubModel(0.9), symbol_cfg,
                             now=datetime(2024, 6, 3, 15, 0, tzinfo=UTC), spread_points=48.0)
    assert signal.side == "none"
    assert signal.filters["spread"] is False


def test_project_config_targets_audcad() -> None:
    """Проверяем не тестовый, а настоящий config.yaml проекта: рабочая пара — AUDCAD."""
    from pathlib import Path as _Path

    from mt5_agent.config import load_config

    root = _Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config" / "config.yaml", root=root)

    enabled = [symbol.name for symbol in cfg.trading.enabled_symbols]
    assert enabled == ["AUDCAD"], f"ожидался только AUDCAD, а включено: {enabled}"

    audcad = cfg.trading.by_name("AUDCAD")
    assert audcad is not None
    assert audcad.windows == [("00:00", "06:00"), ("13:00", "19:00")]
    assert audcad.max_spread_points == 30
    assert audcad.magic == 20260011
    # magic-номера уникальны, иначе агент перепутает свои и чужие сделки
    magics = [symbol.magic for symbol in cfg.trading.symbols]
    assert len(set(magics)) == len(magics)
