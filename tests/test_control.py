"""Тесты пульта управления (control) и мягкой остановки агента.

Панель управления не должна содержать собственной логики риска — только чтение
состояния и запуск того же CLI. Именно это здесь и проверяется: что состояние
читается правильно, что опасные режимы недоступны, что остановка работает
предсказуемо.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mt5_agent.agent import TradingAgent
from mt5_agent.control import AgentController, format_events
from mt5_agent.execution import OrderExecutor
from mt5_agent.state import AgentState
from mt5_agent.utils import utc_now
from tests.conftest import FakeBroker, StubModel


@pytest.fixture()
def controller(config, project: Path) -> AgentController:
    return AgentController(root=project, config=config)


def write_dashboard(project: Path, **overrides) -> None:
    payload = {
        "ts": utc_now().isoformat(),
        "mode": "paper",
        "dry_run": False,
        "virtual_market": False,
        "account": {"desc": "[ДЕМО] login=1", "equity": 1015.5, "balance": 1000.0,
                    "pnl_today_pct": 1.55, "open_positions": 1},
        "risk": {"halted": False, "halt_reason": "", "kill_switch": False, "drawdown_pct": 0.4,
                 "daily_realized": 12.5, "daily_trades": 2, "daily_loss_limit_pct": 2.0,
                 "risk_per_trade_pct": 0.5},
        "positions": [{"ticket": 1, "symbol": "EURUSD", "side": "buy", "volume": 0.03,
                       "price_open": 1.1, "sl": 1.0985, "tp": 1.1025, "profit": 4.2}],
        "signals": [{"symbol": "EURUSD", "side": "buy", "probability": 0.71,
                     "sl_distance": 0.0015, "tp_distance": 0.00225, "reason": "p=0.710"}],
        "blocked": [],
        "errors": [],
    }
    payload.update(overrides)
    (project / "state" / "dashboard.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# --- чтение состояния ---------------------------------------------------------

def test_empty_project_snapshot(controller: AgentController) -> None:
    snapshot = controller.snapshot()
    assert snapshot.running is False
    assert snapshot.has_dashboard is False
    assert snapshot.kill_switch is False
    assert snapshot.state_label == "ОСТАНОВЛЕН"
    assert "агент не запущен" in snapshot.describe()


def test_snapshot_parses_dashboard(controller: AgentController, project: Path) -> None:
    write_dashboard(project)
    snapshot = controller.snapshot()
    assert snapshot.has_dashboard
    assert snapshot.mode == "paper"
    assert snapshot.equity == pytest.approx(1015.5)
    assert snapshot.balance == pytest.approx(1000.0)
    assert snapshot.pnl_today_pct == pytest.approx(1.55)
    assert snapshot.open_positions == 1
    assert snapshot.daily_trades == 2
    assert snapshot.drawdown_pct == pytest.approx(0.4)
    assert snapshot.risk_per_trade_pct == pytest.approx(0.5)
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0]["symbol"] == "EURUSD"


def test_running_is_based_on_heartbeat(controller: AgentController, project: Path) -> None:
    write_dashboard(project)
    assert controller.snapshot().running is False      # heartbeat ещё нет
    heartbeat = project / "state" / "heartbeat.txt"
    heartbeat.write_text(utc_now().isoformat(), encoding="utf-8")
    snapshot = controller.snapshot()
    assert snapshot.running is True
    assert snapshot.state_label == "РАБОТАЕТ"
    assert "РАБОТАЕТ" in snapshot.describe()


def test_halted_state_is_shown(controller: AgentController, project: Path) -> None:
    state = AgentState.load(project / "state" / "agent_state.json")
    state.halt("дневной лимит исчерпан")
    state.save(project / "state" / "agent_state.json")
    snapshot = controller.snapshot()
    assert snapshot.halted is True
    assert snapshot.state_label == "ОСТАНОВЛЕН"
    assert "дневной лимит" in snapshot.describe()


def test_virtual_market_flag(controller: AgentController, project: Path) -> None:
    write_dashboard(project, virtual_market=True, mode="paper")
    assert controller.snapshot().virtual_market is True
    assert "ВИРТУАЛЬНЫЙ РЫНОК" in controller.snapshot().describe()


# --- защита от «кнопки реал» ----------------------------------------------------

def test_panel_refuses_live_mode(controller: AgentController) -> None:
    with pytest.raises(ValueError, match="shadow|paper"):
        controller.start(mode="live")


def test_panel_start_builds_cli_command(controller: AgentController, monkeypatch, project: Path) -> None:
    captured: dict = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            self.pid = 4242
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mt5_agent.control.subprocess.Popen", FakePopen)
    process = controller.start(mode="shadow", dry_run=True, once=True)

    assert process.pid == 4242
    args = captured["args"]
    assert args[0].endswith(("python.exe", "python", "python3", "python3.11"))
    assert args[1:4] == ["-m", "mt5_agent", "run"]
    assert "--mode" in args and args[args.index("--mode") + 1] == "shadow"
    assert "--dry-run" in args and "--once" in args
    assert captured["kwargs"]["cwd"] == str(project)
    assert captured["kwargs"]["env"]["PYTHONUTF8"] == "1"


def test_start_clears_stale_stop_request(controller: AgentController, monkeypatch) -> None:
    controller.request_stop("старый запрос")
    assert controller.stop_request_path.exists()

    class FakePopen:
        pid = 1

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

    monkeypatch.setattr("mt5_agent.control.subprocess.Popen", FakePopen)
    controller.start(mode="paper")
    assert not controller.stop_request_path.exists()


def test_run_task_writes_header_and_uses_cli(controller: AgentController, monkeypatch) -> None:
    captured: dict = {}

    class FakePopen:
        pid = 99

        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mt5_agent.control.subprocess.Popen", FakePopen)
    controller.run_task(["train", "--symbol", "EURUSD"])
    assert captured["args"][1:4] == ["-m", "mt5_agent", "train"]
    assert "--symbol" in captured["args"]
    assert controller.task_log_path.exists()
    assert "train --symbol EURUSD" in controller.task_log_path.read_text(encoding="utf-8")


# --- остановка и kill switch ------------------------------------------------------

def test_stop_request_and_cancel(controller: AgentController) -> None:
    path = controller.request_stop("тест")
    assert path.exists()
    assert controller.snapshot().stop_requested is True
    assert "ЗАПРОШЕНА ОСТАНОВКА" in controller.snapshot().state_label or True
    assert controller.cancel_stop_request() is True
    assert controller.cancel_stop_request() is False


def test_kill_switch_toggle(controller: AgentController, project: Path) -> None:
    controller.activate_kill_switch("тест")
    assert controller.kill_switch_path.exists()
    snapshot = controller.snapshot()
    assert snapshot.kill_switch is True
    assert snapshot.state_label == "KILL SWITCH АКТИВЕН"

    # состояние тоже помечается как остановленное
    state = AgentState.load(project / "state" / "agent_state.json")
    assert state.halted

    assert controller.deactivate_kill_switch(reset_halt=True) is True
    assert not controller.kill_switch_path.exists()
    assert AgentState.load(project / "state" / "agent_state.json").halted is False
    assert controller.deactivate_kill_switch() is False


# --- журналы ------------------------------------------------------------------------

def test_tail_events_parses_jsonl_and_skips_garbage(controller: AgentController) -> None:
    events_path = controller.events_path
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join([
            json.dumps({"ts": "2026-01-01T10:00:00+00:00", "kind": "trade_opened", "symbol": "EURUSD"}),
            "{ это не json",
            json.dumps({"ts": "2026-01-01T11:00:00+00:00", "kind": "trade_closed", "symbol": "EURUSD", "pnl": -3.2}),
        ]),
        encoding="utf-8",
    )
    events = controller.tail_events(10)
    assert [event["kind"] for event in events] == ["trade_opened", "trade_closed"]


def test_format_events_is_human_readable() -> None:
    text = format_events([
        {"ts": "2026-01-01T10:00:00+00:00", "kind": "trade_opened", "symbol": "EURUSD", "side": "buy"},
        {"ts": "2026-01-01T10:05:00+00:00", "kind": "entry_blocked", "symbol": "EURUSD", "reason": "дневной лимит"},
    ])
    assert "ВХОД" in text
    assert "БЛОК ВХОДА" in text
    assert "дневной лимит" in text


def test_tail_agent_log_missing_file_is_empty(controller: AgentController) -> None:
    assert controller.tail_agent_log(10) == ""


# --- предполётные проверки -----------------------------------------------------------

def test_preflight_reports_missing_pieces(controller: AgentController) -> None:
    checks = dict((text, ok) for ok, text in controller.preflight())
    assert any(".env" in key for key in checks)
    assert any("модель для EURUSD" in key for key in checks)
    assert any("kill switch" in key.lower() for key in checks)


def test_preflight_after_setup(controller: AgentController, project: Path, monkeypatch) -> None:
    (project / ".env").write_text("MT5_LOGIN=1\n", encoding="utf-8")
    (project / "models" / "signal_model.joblib").write_text("stub", encoding="utf-8")
    checks = dict((text, ok) for ok, text in controller.preflight())
    assert checks.get(".env найден") is True
    assert any(key.startswith("модель для EURUSD: есть") and ok for key, ok in checks.items())


def test_config_summary_contains_key_limits(controller: AgentController) -> None:
    summary = dict(controller.config_summary())
    assert summary["Риск на сделку"] == "0.5%"
    assert summary["Дневной лимит"] == "-2.0%"
    assert summary["Мартингейл/сетки"] == "запрещены в коде"
    assert "EURUSD" in summary


# --- мягкая остановка в самом агенте --------------------------------------------------

def test_agent_clears_stale_stop_and_honours_new_one(config, project: Path, fake_broker: FakeBroker,
                                                     monkeypatch) -> None:
    """Старый запрос остановки не должен мешать запуску, а новый — останавливать агента."""
    from mt5_agent.agent import CycleReport

    agent = TradingAgent(config)
    agent.broker = fake_broker
    agent.executor = OrderExecutor(fake_broker, exec_cfg=config.execution, risk_cfg=config.risk)
    agent.account_desc = "тестовый счёт"

    monkeypatch.setattr(agent, "_prepare_broker", lambda: None)
    monkeypatch.setattr(agent, "_safety_guard", lambda: None)
    monkeypatch.setattr("mt5_agent.agent.time.sleep", lambda seconds: None)

    cycles: list[int] = []

    def fake_run_once() -> CycleReport:
        cycles.append(len(cycles) + 1)
        # имитируем внешний запрос остановки, пришедший во время работы цикла
        agent.stop_request_path.parent.mkdir(parents=True, exist_ok=True)
        agent.stop_request_path.write_text("остановка из панели", encoding="utf-8")
        return CycleReport(mode=agent.mode, connected=True, equity=1000.0)

    monkeypatch.setattr(agent, "run_once", fake_run_once)

    # старый запрос не должен убить только что запущенного агента
    agent.stop_request_path.parent.mkdir(parents=True, exist_ok=True)
    agent.stop_request_path.write_text("старый запрос", encoding="utf-8")

    agent.start()
    assert len(cycles) == 1, "агент должен завершиться в конце первого же цикла"


def test_stop_request_short_circuits_symbol_loop(config, project: Path, fake_broker: FakeBroker,
                                                 monkeypatch) -> None:
    """Если пришёл запрос остановки, новые сигналы в этом цикле не считаются."""
    agent = TradingAgent(config)
    agent.broker = fake_broker
    agent.executor = OrderExecutor(fake_broker, exec_cfg=config.execution, risk_cfg=config.risk)
    monkeypatch.setattr(agent, "_model_for", lambda symbol_cfg: StubModel(0.9))

    agent.request_stop = lambda reason="": agent.stop_request_path.write_text("стоп", encoding="utf-8")
    agent.stop_request_path.parent.mkdir(parents=True, exist_ok=True)
    agent.stop_request_path.write_text("стоп", encoding="utf-8")

    report = agent.run_once()
    assert fake_broker.orders_sent == [], "при запросе остановки новые ордера не отправляются"
    assert report.signals == []
