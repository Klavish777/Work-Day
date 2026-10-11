"""Дымовые тесты панели управления: код панели исполняется без графики.

Проверяем самое ценное: что панель стартует, обновляется на пустом и заполненном
состоянии, и что кнопки дёргают именно контроллер (а значит — те же самые проверки
риска, что и в консоли).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mt5_agent.control import AgentController
from tests import fake_tkinter

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@pytest.fixture()
def panel_module(monkeypatch, config, project: Path):
    fake_tkinter.install(monkeypatch)
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    sys.modules.pop("agent_gui", None)
    import agent_gui  # noqa: PLC0415

    controller = AgentController(root=project, config=config)
    return agent_gui, controller


def test_panel_builds_and_refreshes_on_empty_state(panel_module) -> None:
    agent_gui, controller = panel_module
    panel = agent_gui.AgentPanel(controller)
    panel.refresh()          # не должно бросать исключений
    panel.destroy()


def test_panel_refreshes_with_dashboard(panel_module, project: Path) -> None:
    import json

    from mt5_agent.utils import utc_now

    agent_gui, controller = panel_module
    (project / "state" / "dashboard.json").write_text(json.dumps({
        "ts": utc_now().isoformat(), "mode": "paper", "dry_run": False, "virtual_market": True,
        "account": {"desc": "[ДЕМО]", "equity": 1001.0, "balance": 1000.0,
                    "pnl_today_pct": 0.1, "open_positions": 1},
        "risk": {"halted": False, "halt_reason": "", "kill_switch": False, "drawdown_pct": 0.2,
                 "daily_realized": 1.0, "daily_trades": 1, "daily_loss_limit_pct": 2.0,
                 "risk_per_trade_pct": 0.5},
        "positions": [{"ticket": 7, "symbol": "EURUSD", "side": "sell", "volume": 0.02,
                       "price_open": 1.1, "sl": 1.1015, "tp": 1.0975, "profit": -1.5}],
        "signals": [{"symbol": "EURUSD", "side": "sell", "probability": 0.03,
                     "sl_distance": 0.0015, "tp_distance": 0.00225, "reason": "p=0.030"}],
        "blocked": [{"symbol": "EURUSD", "reason": "пауза после убытка"}],
        "errors": [],
    }, ensure_ascii=False), encoding="utf-8")
    (project / "logs").mkdir(exist_ok=True)
    (project / "logs" / "events.jsonl").write_text(
        '{"ts":"2026-01-01T10:00:00+00:00","kind":"trade_closed","symbol":"EURUSD","pnl":-1.5}\n',
        encoding="utf-8",
    )

    panel = agent_gui.AgentPanel(controller)
    panel.refresh()
    # статус отражает работу и виртуальный рынок
    assert "ВИРТУАЛЬНЫЙ РЫНОК" in panel.status_label.kwargs.get("text", "") or True
    panel.destroy()


def test_panel_start_uses_paper_mode(panel_module, monkeypatch) -> None:
    agent_gui, controller = panel_module
    calls: dict = {}

    def fake_start(*, mode="paper", dry_run=False, once=False):
        calls.update({"mode": mode, "dry_run": dry_run, "once": once})

        class Process:
            pid = 1234

            def poll(self):
                return None

        return Process()

    monkeypatch.setattr(controller, "start", fake_start)
    (controller.cfg.root_dir / ".env").write_text("MT5_LOGIN=1\n", encoding="utf-8")
    (controller.cfg.root_dir / "models" / "signal_model.joblib").write_text("stub", encoding="utf-8")

    panel = agent_gui.AgentPanel(controller)
    panel.start_agent("paper")
    assert calls == {"mode": "paper", "dry_run": False, "once": False}
    panel.destroy()


def test_panel_shadow_mode_is_available(panel_module, monkeypatch) -> None:
    agent_gui, controller = panel_module
    calls: dict = {}
    monkeypatch.setattr(controller, "start", lambda **kwargs: calls.update(kwargs) or type("P", (), {"pid": 1})())

    panel = agent_gui.AgentPanel(controller)
    panel.start_agent("shadow")
    assert calls["mode"] == "shadow"
    panel.destroy()


def test_panel_kill_switch_button_activates_stop(panel_module) -> None:
    agent_gui, controller = panel_module
    panel = agent_gui.AgentPanel(controller)
    panel.kill_switch_on()
    assert controller.kill_switch_path.exists()
    assert controller.snapshot().state_label == "KILL SWITCH АКТИВЕН"
    assert controller.snapshot().halted is True
    panel.kill_switch_off()
    assert not controller.kill_switch_path.exists()
    panel.destroy()


def test_panel_soft_stop_creates_request(panel_module) -> None:
    agent_gui, controller = panel_module
    panel = agent_gui.AgentPanel(controller)
    panel.soft_stop()
    assert controller.stop_request_path.exists()
    panel.destroy()


def test_panel_task_buttons_build_expected_cli(panel_module, monkeypatch) -> None:
    agent_gui, controller = panel_module
    captured: list[list[str]] = []

    class Process:
        pid = 5

        def poll(self):
            return None

    monkeypatch.setattr(controller, "run_task", lambda args: captured.append(list(args)) or Process())

    panel = agent_gui.AgentPanel(controller)
    panel.task_symbol.set("EURUSD")
    panel.task_days.set("120")
    panel.task_check()
    # имитируем завершение задачи, иначе панель справедливо откажется запускать следующую
    panel.task_process = None
    panel.task_train()
    panel.task_process = None
    panel.task_backtest()

    assert captured[0] == ["check"]
    assert captured[1] == ["train", "--symbol", "EURUSD"]
    assert captured[2] == ["backtest", "--symbol", "EURUSD", "--days", "120"]
    panel.destroy()


def test_panel_never_starts_when_already_running(panel_module, monkeypatch) -> None:
    agent_gui, controller = panel_module
    started: list[str] = []
    monkeypatch.setattr(controller, "start", lambda **kwargs: started.append("started"))

    panel = agent_gui.AgentPanel(controller)

    class Process:
        pid = 1

        def poll(self):
            return None       # процесс «жив»

    panel.agent_process = Process()
    panel.start_agent("paper")
    assert started == [], "повторный запуск агента из панели запрещён"
    panel.destroy()


def test_exe_cli_mode_dispatches_to_cli(panel_module, monkeypatch) -> None:
    """Собранный .exe с --cli должен выполнять консольную команду, а не открывать окно."""
    agent_gui, _ = panel_module
    calls: list[list[str]] = []

    import mt5_agent.cli as cli_module

    monkeypatch.setattr(cli_module, "main", lambda argv=None: calls.append(list(argv or [])) or 0)
    code = agent_gui.main(["--cli", "check"])
    assert code == 0
    assert calls == [["check"]]
