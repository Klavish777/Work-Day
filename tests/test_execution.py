"""Тесты исполнения: план, отправка, безубыток и учёт закрытых сделок."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from mt5_agent.broker import Position
from mt5_agent.execution import OrderExecutor
from mt5_agent.sizing import size_position
from mt5_agent.signals import Signal
from mt5_agent.utils import utc_now


@pytest.fixture()
def executor(config, fake_broker):
    return OrderExecutor(fake_broker, exec_cfg=config.execution, risk_cfg=config.risk, dry_run=False)


def make_signal(side: str = "buy", sl: float = 0.0015, tp: float = 0.00225) -> Signal:
    return Signal(symbol="EURUSD", side=side, probability=0.9, price=1.1,
                  atr=0.001, sl_distance=sl, tp_distance=tp)


def test_build_plan_prices_and_rounding(config, fake_broker, symbol_spec, executor) -> None:
    signal = make_signal("buy")
    sizing = size_position(symbol_spec, equity=1000.0, risk_pct=0.5,
                           sl_price_distance=signal.sl_distance, tp_price_distance=signal.tp_distance)
    plan = executor.build_plan(signal=signal, sizing=sizing, spec=symbol_spec,
                               tick=fake_broker.tick(), magic=111111)
    assert plan.entry_price == pytest.approx(1.10012)
    assert plan.sl_price == pytest.approx(1.09862, abs=1e-5)
    assert plan.tp_price == pytest.approx(1.10237, abs=1e-5)
    assert plan.sl_price < plan.entry_price < plan.tp_price


def test_build_plan_for_sell(config, fake_broker, symbol_spec, executor) -> None:
    signal = make_signal("sell")
    sizing = size_position(symbol_spec, equity=1000.0, risk_pct=0.5,
                           sl_price_distance=signal.sl_distance, tp_price_distance=signal.tp_distance)
    plan = executor.build_plan(signal=signal, sizing=sizing, spec=symbol_spec,
                               tick=fake_broker.tick(), magic=111111)
    assert plan.tp_price < plan.entry_price < plan.sl_price


def test_plan_rejects_stop_inside_broker_minimum(config, fake_broker, executor) -> None:
    from mt5_agent.exceptions import ExecutionError
    from mt5_agent.specs import SymbolSpec

    strict = SymbolSpec(name="EURUSD", point=1e-5, tick_size=1e-5, tick_value_loss=1.0,
                        stops_level_points=500)
    signal = make_signal("buy")
    sizing = size_position(strict, equity=100_000.0, risk_pct=0.5,
                           sl_price_distance=signal.sl_distance, tp_price_distance=signal.tp_distance)
    # размер посчитан под «мягкую» спецификацию, а брокер требует стоп дальше
    with pytest.raises(ExecutionError, match="ближе"):
        executor.build_plan(signal=signal, sizing=sizing, spec=strict,
                            tick=fake_broker.tick(), magic=111111)


def test_dry_run_does_not_send_orders(config, fake_broker, symbol_spec) -> None:
    dry = OrderExecutor(fake_broker, exec_cfg=config.execution, risk_cfg=config.risk, dry_run=True)
    signal = make_signal("buy")
    sizing = size_position(symbol_spec, equity=1000.0, risk_pct=0.5,
                           sl_price_distance=signal.sl_distance, tp_price_distance=signal.tp_distance)
    plan = dry.build_plan(signal=signal, sizing=sizing, spec=symbol_spec,
                          tick=fake_broker.tick(), magic=111111)
    result = dry.execute(plan, spec=symbol_spec)
    assert result.ok
    assert fake_broker.orders_sent == []
    assert "dry-run" in result.comment


def test_execute_sends_sl_tp_and_magic(executor, config, fake_broker, symbol_spec) -> None:
    signal = make_signal("buy")
    sizing = size_position(symbol_spec, equity=1000.0, risk_pct=0.5,
                           sl_price_distance=signal.sl_distance, tp_price_distance=signal.tp_distance)
    plan = executor.build_plan(signal=signal, sizing=sizing, spec=symbol_spec,
                               tick=fake_broker.tick(), magic=222222)
    result = executor.execute(plan, spec=symbol_spec)
    assert result.ok
    sent = fake_broker.orders_sent[-1]
    assert sent["magic"] == 222222
    assert sent["sl"] == pytest.approx(plan.sl_price)
    assert sent["tp"] == pytest.approx(plan.tp_price)
    assert sent["volume"] == pytest.approx(plan.volume)


def test_execute_without_approved_sizing_is_rejected(executor, fake_broker, symbol_spec) -> None:
    from mt5_agent.exceptions import ExecutionError
    from mt5_agent.sizing import SizingResult

    rejected = SizingResult(approved=False, reject_reason="мало денег")
    with pytest.raises(ExecutionError, match="мало денег"):
        executor.build_plan(signal=make_signal(), sizing=rejected, spec=symbol_spec,
                            tick=fake_broker.tick(), magic=1)


def test_breakeven_stop_is_moved_at_one_r(executor, config, fake_broker, symbol_spec) -> None:
    # длинная позиция: вход 1.10000, стоп 1.09850 (риск 15 пт), цена выросла до 1.10160 (+1R)
    position = Position(ticket=1, symbol="EURUSD", side="buy", volume=0.02,
                        price_open=1.10000, sl=1.09850, tp=1.10400, magic=111111)
    fake_broker.bid, fake_broker.ask = 1.10160, 1.10172
    actions = executor.manage_positions([position], specs={"EURUSD": symbol_spec})
    assert actions and "SL" in actions[0]
    assert fake_broker.modified[-1]["ticket"] == 1
    assert fake_broker.modified[-1]["sl"] == pytest.approx(1.10000)


def test_stop_not_moved_before_one_r(executor, fake_broker, symbol_spec) -> None:
    position = Position(ticket=2, symbol="EURUSD", side="buy", volume=0.02,
                        price_open=1.10000, sl=1.09850, tp=1.10400, magic=111111)
    fake_broker.bid, fake_broker.ask = 1.10050, 1.10062   # прибыль меньше 1R
    actions = executor.manage_positions([position], specs={"EURUSD": symbol_spec})
    assert actions == []
    assert fake_broker.modified == []


def test_position_without_stop_is_closed_immediately(executor, fake_broker, symbol_spec) -> None:
    position = Position(ticket=3, symbol="EURUSD", side="buy", volume=0.02,
                        price_open=1.10000, sl=0.0, tp=0.0, magic=111111)
    actions = executor.manage_positions([position], specs={"EURUSD": symbol_spec})
    assert actions and "закрыта" in actions[0]
    assert fake_broker.closed_tickets == [3]


def test_stop_is_never_moved_against_position(executor, fake_broker, symbol_spec) -> None:
    position = Position(ticket=4, symbol="EURUSD", side="buy", volume=0.02,
                        price_open=1.10000, sl=1.10400, tp=1.11000, magic=111111)
    fake_broker.bid, fake_broker.ask = 1.10200, 1.10212
    executor.manage_positions([position], specs={"EURUSD": symbol_spec})
    # стоп уже выше безубытка и новое значение «хуже» — двигать нельзя
    assert fake_broker.modified == []


def test_closed_trade_pnl_sums_deal_fields(executor, fake_broker) -> None:
    fake_broker.deals_frame = pd.DataFrame(
        [
            {"ticket": 1, "position_id": 5001, "profit": 12.0, "swap": -0.3, "commission": -0.7},
            {"ticket": 2, "position_id": 5001, "profit": 0.0, "swap": 0.0, "commission": 0.0},
            {"ticket": 3, "position_id": 9999, "profit": 50.0, "swap": 0.0, "commission": 0.0},
        ]
    )
    assert executor.closed_trade_pnl(5001, utc_now() - timedelta(hours=1)) == pytest.approx(11.0)


def test_closed_trade_pnl_is_zero_when_history_empty(executor, fake_broker) -> None:
    fake_broker.deals_frame = pd.DataFrame()
    assert executor.closed_trade_pnl(1) == pytest.approx(0.0)
