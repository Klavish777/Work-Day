"""Тесты риск-модуля и состояния: лимиты обязаны срабатывать всегда."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from mt5_agent.broker import Position
from mt5_agent.risk import RiskManager
from mt5_agent.state import AgentState
from mt5_agent.utils import utc_now


@pytest.fixture()
def risk_setup(config, project: Path):
    state = AgentState.load(project / "state" / "agent_state.json")
    state.roll_periods(utc_now(), 1000.0)
    state.update_equity(1000.0)
    manager = RiskManager(
        config.risk,
        state,
        state_path=project / "state" / "agent_state.json",
        kill_switch_path=project / "state" / "KILL_SWITCH",
        own_magics=(111111,),
    )
    return manager, state, project


def _check(manager: RiskManager, config, equity: float = 1000.0, positions=None,
           foreign=None, now=None):
    return manager.check_entry(
        equity=equity,
        balance=equity,
        symbol_cfg=config.trading.enabled_symbols[0],
        positions=positions or [],
        foreign_positions=foreign or [],
        now=now,
    )


def test_fresh_state_allows_entry(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    decision = _check(manager, config)
    assert decision.approved, decision.report()


def test_kill_switch_blocks_everything(risk_setup, config) -> None:
    manager, state, project = risk_setup
    manager.activate_kill_switch("тест")
    assert (project / "state" / "KILL_SWITCH").exists()
    decision = _check(manager, config)
    assert not decision.approved
    assert "kill switch" in decision.reason


def test_daily_loss_limit_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    # equity упала на 2.5% от значения на начало дня
    decision = _check(manager, config, equity=975.0)
    assert not decision.approved
    assert "дневной лимит" in decision.reason


def test_weekly_loss_limit_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    state.daily.start_equity = 1000.0          # день «сброшен», чтобы проверить неделю
    state.weekly.start_equity = 1000.0
    decision = _check(manager, config, equity=940.0)
    assert not decision.approved
    assert "дневной лимит" in decision.reason or "недельный" in decision.reason


def test_drawdown_from_peak_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    state.equity_peak = 1200.0
    state.daily.start_equity = 1100.0
    state.weekly.start_equity = 1100.0
    decision = _check(manager, config, equity=1080.0)
    assert not decision.approved
    assert "просадка" in decision.reason


def test_max_open_positions_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    positions = [
        Position(ticket=1, symbol="EURUSD", side="buy", volume=0.01, price_open=1.1, magic=111111),
        Position(ticket=2, symbol="GBPUSD", side="buy", volume=0.01, price_open=1.3, magic=111111),
    ]
    decision = _check(manager, config, positions=positions)
    assert not decision.approved
    assert "максимум открытых позиций" in decision.reason


def test_hedging_is_blocked(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    positions = [Position(ticket=1, symbol="EURUSD", side="buy", volume=0.01, price_open=1.1, magic=111111)]
    decision = _check(manager, config, positions=positions)
    assert not decision.approved
    assert "позиций по символу" in decision.reason or "хеджирование" in decision.reason


def test_daily_trade_cap_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    for _ in range(config.risk.max_daily_trades):
        state.register_open()
    decision = _check(manager, config)
    assert not decision.approved
    assert "сделок за день" in decision.reason


def test_cooldown_after_loss_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    state.register_close(-5.0, "EURUSD", utc_now())
    decision = _check(manager, config)
    assert not decision.approved
    assert "пауза после убытка" in decision.reason
    # после истечения паузы вход снова разрешён
    later = utc_now() + timedelta(minutes=config.risk.cooldown_after_loss_min + 1)
    assert _check(manager, config, now=later).approved


def test_foreign_positions_halt_by_default(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    foreign = [Position(ticket=99, symbol="EURUSD", side="buy", volume=1.0, price_open=1.1, magic=777)]
    decision = _check(manager, config, foreign=foreign)
    assert not decision.approved
    assert "посторонние позиции" in decision.reason


def test_min_balance_floor_blocks(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    manager.cfg.min_balance_floor = 500.0
    state.daily.start_equity = 400.0
    decision = _check(manager, config, equity=400.0)
    assert not decision.approved
    assert "минимальный equity" in decision.reason


def test_state_rolls_daily_and_weekly(config, project: Path) -> None:
    state = AgentState.load(project / "state" / "agent_state.json")
    state.roll_periods(utc_now(), 1000.0)
    state.register_open()
    state.register_close(-3.0, "EURUSD")
    assert state.daily.realized_pnl == pytest.approx(-3.0)

    tomorrow = utc_now() + timedelta(days=1)
    events = state.roll_periods(tomorrow, 990.0)
    assert any("новый день" in e for e in events)
    assert state.daily.realized_pnl == 0.0
    assert state.daily.trades_opened == 0


def test_state_survives_restart(config, project: Path) -> None:
    path = project / "state" / "agent_state.json"
    state = AgentState.load(path)
    state.roll_periods(utc_now(), 1000.0)
    state.register_open()
    state.halt("тестовая остановка")
    state.track_open(1234, "EURUSD", 0.01, magic=111111)
    state.save(path)

    restored = AgentState.load(path)
    assert restored.halted is True
    assert restored.halt_reason == "тестовая остановка"
    assert restored.daily.trades_opened == 1
    assert "1234" in restored.open_tickets


def test_error_counter_and_halt(risk_setup, config) -> None:
    manager, state, _ = risk_setup
    limit = 3
    for _ in range(limit - 1):
        assert manager.register_error(limit, "ошибка") is False
    assert manager.register_error(limit, "последняя ошибка") is True
    assert state.halted
    manager.register_success()
    assert state.consecutive_errors == 0
