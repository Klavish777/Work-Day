"""Тесты слоя брокера на поддельном пакете MetaTrader5.

Здесь проверяется именно то, что чаще всего ломается в самодельных ботах:
маппинг полей символа, обязательность стоп-лосса, разбор retcode'ов,
защита от «не того» счёта.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from mt5_agent.broker import MT5Broker, MT5Credentials
from mt5_agent.exceptions import BrokerError, ExecutionError
from mt5_agent.utils import utc_now
from tests import fake_mt5


@pytest.fixture(autouse=True)
def fake_module(monkeypatch):
    """Подменяем настоящий MetaTrader5 нашей подделкой на время теста."""
    fake_mt5.reset()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    yield fake_mt5


@pytest.fixture()
def broker():
    instance = MT5Broker(
        MT5Credentials(login=12345678, password="secret", server="Test-Demo"),
        reconnect_attempts=3, reconnect_backoff_sec=0.01,
    )
    yield instance
    instance.shutdown()


def test_connect_returns_snapshot(broker) -> None:
    snapshot = broker.connect()
    assert snapshot.login == 12345678
    assert snapshot.is_demo is True
    assert snapshot.equity == pytest.approx(1000.0)
    assert "ДЕМО" in snapshot.describe()


def test_connect_rejects_wrong_account(broker) -> None:
    """Подключение не к тому счёту должно быть фатально, а не «ну ладно»."""
    broker.expected_login = 99999999
    with pytest.raises(BrokerError, match="ожидался"):
        broker.connect()


def test_connect_retries_then_succeeds(fake_module) -> None:
    fake_mt5.reset(init_fail_times=2)
    broker = MT5Broker(MT5Credentials(login=12345678), reconnect_attempts=3, reconnect_backoff_sec=0.001)
    snapshot = broker.connect()
    assert snapshot.login == 12345678
    assert fake_mt5.state()["init_calls"] == 3
    broker.shutdown()


def test_connect_gives_up_with_clear_error(fake_module, monkeypatch) -> None:
    fake_mt5.reset(init_fail_times=99)
    broker = MT5Broker(MT5Credentials(login=12345678), reconnect_attempts=2, reconnect_backoff_sec=0.001)
    with pytest.raises(BrokerError, match="Не удалось подключиться"):
        broker.connect()


def test_symbol_spec_maps_real_fields(broker) -> None:
    broker.connect()
    spec = broker.symbol_spec("EURUSD")
    assert spec.digits == 5
    assert spec.point == pytest.approx(1e-5)
    assert spec.tick_value_loss == pytest.approx(1.0)
    assert spec.value_per_point_per_lot == pytest.approx(1.0)
    assert spec.volume_step == pytest.approx(0.01)
    assert spec.trade_allowed is True


def test_filling_modes_respects_broker_mask(broker) -> None:
    broker.connect()
    # в подделке filling_mode = 2 → разрешён только IOC
    assert broker.filling_modes("EURUSD") == ["IOC", "RETURN"]


def test_rates_are_time_indexed(broker) -> None:
    broker.connect()
    frame = broker.rates("EURUSD", "M15", 50)
    assert len(frame) == 50
    assert frame.index.is_monotonic_increasing
    assert frame.index.tz is not None
    assert {"open", "high", "low", "close"}.issubset(frame.columns)


def test_market_order_requires_stop_loss(broker) -> None:
    broker.connect()
    with pytest.raises(ExecutionError, match="без стоп-лосса"):
        broker.market_order("EURUSD", "buy", 0.01, sl=0.0)


def test_market_order_rejected_by_precheck(broker, fake_module) -> None:
    broker.connect()
    fake_mt5.reset(initialized=True, check_retcode=fake_mt5.TRADE_RETCODE_INVALID_STOPS)
    result = broker.market_order("EURUSD", "buy", 0.01, sl=1.0950, tp=1.1050)
    assert result.ok is False
    assert result.retcode == fake_mt5.TRADE_RETCODE_INVALID_STOPS
    assert "предпроверка" in result.comment
    # отправки не было
    assert all(order["kind"] != "send" for order in fake_mt5.state()["orders"])


def test_market_order_success_sends_sl_and_tp(broker, fake_module) -> None:
    broker.connect()
    result = broker.market_order("EURUSD", "buy", 0.02, sl=1.0950, tp=1.1050, magic=42,
                                 comment="unit-test")
    assert result.ok
    assert result.order_ticket == 7001
    sent = [o for o in fake_mt5.state()["orders"] if o["kind"] == "send"][-1]
    assert sent["sl"] == pytest.approx(1.0950)
    assert sent["tp"] == pytest.approx(1.1050)
    assert sent["magic"] == 42
    assert sent["action"] == fake_mt5.TRADE_ACTION_DEAL
    assert sent["type"] == fake_mt5.ORDER_TYPE_BUY


def test_market_order_returns_failure_on_broker_reject(broker, fake_module) -> None:
    broker.connect()
    fake_mt5.reset(initialized=True, send_retcode=fake_mt5.TRADE_RETCODE_NO_MONEY)
    result = broker.market_order("EURUSD", "sell", 1.0, sl=1.1050, tp=1.0950)
    assert result.ok is False
    assert result.retcode == fake_mt5.TRADE_RETCODE_NO_MONEY


def test_positions_filtered_by_magic(broker, fake_module) -> None:
    broker.connect()
    fake_mt5.reset(initialized=True)
    fake_mt5.state()["positions"] = [
        fake_mt5.make_position(1, magic=111111),
        fake_mt5.make_position(2, magic=777777),
    ]
    assert len(broker.positions()) == 2
    assert [p.ticket for p in broker.positions(magic=111111)] == [1]
    position = broker.positions(magic=111111)[0]
    assert position.side == "buy"
    assert position.is_long()


def test_close_position_sends_opposite_side(broker, fake_module) -> None:
    broker.connect()
    from mt5_agent.broker import Position

    pos = Position(ticket=5, symbol="EURUSD", side="buy", volume=0.03, price_open=1.1, magic=111111)
    result = broker.close_position(pos)
    assert result.ok
    sent = [o for o in fake_mt5.state()["orders"] if o["kind"] == "send"][-1]
    assert sent["type"] == fake_mt5.ORDER_TYPE_SELL
    assert sent["position"] == 5


def test_realized_pnl_sums_profit_swap_commission(broker, fake_module) -> None:
    broker.connect()
    fake_mt5.state()["deals"] = [
        fake_mt5.make_deal(1, position_id=5001, profit=10.0, commission=-0.7, swap=-0.2),
        fake_mt5.make_deal(2, position_id=5002, profit=-4.0),
    ]
    total = broker.realized_pnl(utc_now() - timedelta(days=1), utc_now())
    assert total == pytest.approx(5.1)
    assert broker.realized_pnl(utc_now() - timedelta(days=1), utc_now(), magic=111111) == pytest.approx(5.1)


def test_terminal_ready_reports_disabled_autotrading(broker, fake_module) -> None:
    broker.connect()
    fake_mt5.state()["trade_allowed"] = False
    ok, reason = broker.terminal_ready()
    assert ok is False
    assert "AutoTrading" in reason


def test_calc_margin_and_profit(broker) -> None:
    broker.connect()
    margin = broker.calc_margin("buy", "EURUSD", 0.1, 1.1)
    assert margin == pytest.approx(100.0)
    profit = broker.calc_profit("buy", "EURUSD", 0.1, 1.10000, 1.10100)
    assert profit == pytest.approx(10.0)
