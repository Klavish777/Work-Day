"""Интеграционный тест полного цикла агента на фейковом брокере.

Это «генеральная репетиция»: сигнал → риск → размер → заявка → учёт — всё,
кроме самого терминала.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from mt5_agent.agent import TradingAgent
from mt5_agent.exceptions import SafetyError
from mt5_agent.execution import OrderExecutor
from mt5_agent.utils import day_key, utc_now
from tests.conftest import FakeBroker, StubModel


@pytest.fixture()
def agent(config, project: Path, fake_broker: FakeBroker, monkeypatch) -> TradingAgent:
    instance = TradingAgent(config)
    instance.broker = fake_broker
    instance.executor = OrderExecutor(
        fake_broker, exec_cfg=config.execution, risk_cfg=config.risk, dry_run=False
    )
    instance.account_desc = fake_broker.account().describe()
    monkeypatch.setattr(instance, "_model_for", lambda symbol_cfg: StubModel(0.90))
    return instance


def test_cycle_opens_trade_and_writes_state(agent, fake_broker, project: Path) -> None:
    report = agent.run_once()

    assert report.connected
    assert len(fake_broker.orders_sent) == 1
    sent = fake_broker.orders_sent[0]
    assert sent["side"] == "buy"
    assert sent["sl"] > 0 and sent["tp"] > sent["sl"]
    # риск в пределах 0.5% + допуск на округление
    assert sent["volume"] <= 0.06

    # учёт: сделка записана в состояние и позиция отслеживается
    assert agent.state.daily.trades_opened == 1
    assert len(agent.state.open_tickets) == 1
    ticket = next(iter(agent.state.open_tickets))
    assert agent.state.open_tickets[ticket]["symbol"] == "EURUSD"

    # артефакты для дашборда/сторожа
    dashboard = json.loads((project / "state" / "dashboard.json").read_text(encoding="utf-8"))
    assert dashboard["account"]["open_positions"] == 1
    assert dashboard["positions"][0]["symbol"] == "EURUSD"
    assert (project / "state" / "heartbeat.txt").exists()
    assert (project / "reports" / "last_cycle.txt").exists()
    state_on_disk = json.loads((project / "state" / "agent_state.json").read_text(encoding="utf-8"))
    assert state_on_disk["daily"]["trades_opened"] == 1


def test_cycle_respects_daily_loss_limit(agent, fake_broker) -> None:
    agent.state.roll_periods(utc_now(), 1000.0)
    agent.state.daily.key = day_key(utc_now())
    agent.state.daily.start_equity = 1000.0
    agent.state.equity_peak = 1000.0
    fake_broker.equity = 970.0        # -3% за день → лимит 2% пробит

    report = agent.run_once()
    assert fake_broker.orders_sent == []
    assert any("дневной лимит" in item["reason"] for item in report.blocked)


def test_cycle_registers_closed_trade(agent, fake_broker) -> None:
    agent.run_once()
    ticket = int(next(iter(agent.state.open_tickets)))

    # позиция закрылась (стоп/тейк) — убираем её и даём историю сделок
    fake_broker.positions_list = []
    fake_broker.deals_frame = pd.DataFrame(
        [{"ticket": 900, "position_id": ticket, "profit": 12.5, "swap": -0.4, "commission": -0.7}]
    )
    report = agent.run_once()

    assert report.closed and report.closed[0]["pnl"] == pytest.approx(11.4)
    assert agent.state.daily.realized_pnl == pytest.approx(11.4)
    # старая сделка больше не отслеживается (следующий цикл может открыть новую)
    assert str(ticket) not in agent.state.open_tickets


def test_kill_switch_closes_positions_and_halts(agent, fake_broker, project: Path) -> None:
    fake_broker.add_position(ticket=77, side="buy", magic=111111)
    agent.state.track_open(77, "EURUSD", 0.01, magic=111111)
    (project / "state" / "KILL_SWITCH").write_text("стоп", encoding="utf-8")

    report = agent.run_once()

    assert fake_broker.closed_tickets == [77]
    assert agent.state.halted is True
    assert "kill switch" in agent.state.halt_reason.lower()
    assert report.halted


def test_foreign_position_blocks_new_entry(agent, fake_broker) -> None:
    fake_broker.add_position(ticket=123, side="buy", magic=999999)   # чужой magic
    report = agent.run_once()
    assert fake_broker.orders_sent == []
    assert any("посторонние" in item["reason"] for item in report.blocked)


def test_no_model_blocks_trading(config, project, fake_broker, monkeypatch) -> None:
    instance = TradingAgent(config)
    instance.broker = fake_broker
    instance.executor = OrderExecutor(fake_broker, exec_cfg=config.execution, risk_cfg=config.risk)
    monkeypatch.setattr(instance, "_model_for", lambda symbol_cfg: None)
    report = instance.run_once()
    assert fake_broker.orders_sent == []
    assert any("модел" in item["reason"].lower() for item in report.blocked)


def test_paper_mode_refuses_real_account(config, fake_broker) -> None:
    instance = TradingAgent(config)
    instance.broker = fake_broker
    fake_broker.is_demo = False
    with pytest.raises(SafetyError, match="ДЕМО-счёт"):
        instance._safety_guard()


def test_live_mode_requires_both_barriers(config, fake_broker, monkeypatch) -> None:
    instance = TradingAgent(config, mode_override="live")
    instance.broker = fake_broker
    monkeypatch.delenv("I_UNDERSTAND_REAL_MONEY_RISK", raising=False)
    with pytest.raises(SafetyError, match="demo_only|I_UNDERSTAND"):
        instance._safety_guard()

    monkeypatch.setenv("I_UNDERSTAND_REAL_MONEY_RISK", "TRUE")
    config.safety.demo_only = False
    instance._safety_guard()   # теперь барьеры сняты — исключения быть не должно


def test_errors_halt_after_limit(agent, fake_broker, monkeypatch) -> None:
    agent.cfg.safety.max_consecutive_errors = 2

    def boom(_symbol_cfg):
        raise RuntimeError("данные недоступны")

    monkeypatch.setattr(agent, "_process_symbol", lambda *a, **k: boom(None))
    agent.run_once()
    report = agent.run_once()
    assert agent.state.halted
    assert report.halted


def test_breakeven_moves_stop_in_cycle(agent, fake_broker) -> None:
    agent.run_once()
    # цена ушла на +1R — на следующем цикле стоп должен переехать в безубыток
    position = fake_broker.positions_list[0]
    entry = position.price_open
    risk = entry - position.sl
    fake_broker.bid = entry + risk * 1.2
    fake_broker.ask = fake_broker.bid + 0.00012
    agent.run_once()
    assert fake_broker.modified
    assert fake_broker.modified[-1]["sl"] == pytest.approx(entry, abs=1e-9)
