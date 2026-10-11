"""Тесты конфигурации: главное — что опасные значения отсекаются на входе."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mt5_agent.config import ConfigError, load_config
from tests.conftest import CONFIG_YAML


def patched(old: str, new: str) -> str:
    """Заменить фрагмент в базовом конфиге тестов."""
    assert old in CONFIG_YAML, f"фрагмент не найден: {old}"
    return CONFIG_YAML.replace(old, new)


def write_config(project: Path, body: str) -> Path:
    path = project / "config" / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_config_loads_and_validates(project: Path) -> None:
    cfg = load_config(project / "config" / "config.yaml", root=project)
    assert cfg.agent.mode == "paper"
    assert cfg.risk.risk_per_trade_pct == 0.5
    assert cfg.trading.enabled_symbols[0].name == "EURUSD"
    assert cfg.signal.allow_short is True
    assert cfg.execution.breakeven_at_r == 1.0
    assert cfg.root_dir == project


def test_risk_above_one_percent_is_rejected(project: Path) -> None:
    path = write_config(project, patched("risk_per_trade_pct: 0.5", "risk_per_trade_pct: 5.0"))
    with pytest.raises(ConfigError, match="risk_per_trade_pct"):
        load_config(path, root=project)


def test_daily_loss_limit_too_high_is_rejected(project: Path) -> None:
    path = write_config(project, patched("max_daily_loss_pct: 2.0", "max_daily_loss_pct: 20.0"))
    with pytest.raises(ConfigError, match="max_daily_loss_pct"):
        load_config(path, root=project)


def test_martingale_is_rejected(project: Path) -> None:
    path = write_config(project, patched(
        'unknown_position_action: "halt"',
        'unknown_position_action: "halt"\n  allow_martingale: true',
    ))
    with pytest.raises(ConfigError, match="Мартингейл"):
        load_config(path, root=project)


def test_take_profit_less_than_stop_is_rejected(project: Path) -> None:
    path = write_config(project, patched("sl_atr_mult: 1.5\n      tp_atr_mult: 2.25",
                                         "sl_atr_mult: 2.5\n      tp_atr_mult: 2.25"))
    with pytest.raises(ConfigError, match="тейк"):
        load_config(path, root=project)


def test_unknown_section_is_rejected(project: Path) -> None:
    path = write_config(project, CONFIG_YAML + "\nstrategy:\n  foo: 1\n")
    with pytest.raises(ConfigError, match="Неизвестные секции"):
        load_config(path, root=project)


def test_unknown_key_inside_section_is_rejected(project: Path) -> None:
    path = write_config(project, patched(
        "risk_per_trade_pct: 0.5",
        "risk_per_trade_pct: 0.5\n  magic_bean: 1",
    ))
    with pytest.raises(ConfigError, match="Неизвестные параметры"):
        load_config(path, root=project)


def test_duplicate_magic_is_rejected(project: Path) -> None:
    extra = textwrap.indent(textwrap.dedent(
        """
        - name: "GBPUSD"
          enabled: true
          timeframe: "M15"
          sl_atr_mult: 1.5
          tp_atr_mult: 2.25
          magic: 111111
        """
    ).strip(), "    ")
    path = write_config(project, patched("      magic: 111111", "      magic: 111111\n" + extra))
    with pytest.raises(ConfigError, match="magic"):
        load_config(path, root=project)


def test_all_symbols_disabled_is_rejected(project: Path) -> None:
    path = write_config(project, patched('enabled: true', 'enabled: false'))
    with pytest.raises(ConfigError, match="выключены"):
        load_config(path, root=project)


def test_bad_timeframe_is_rejected(project: Path) -> None:
    path = write_config(project, patched('timeframe: "M15"', 'timeframe: "M7"'))
    with pytest.raises(ConfigError, match="таймфрейм"):
        load_config(path, root=project)
