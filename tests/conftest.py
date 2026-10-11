"""Общие фикстуры тестов.

Главная идея: **все тесты идут без MetaTrader5 и без интернета**. Это не
экономия, а требование: денежные расчёты должны быть проверяемы в любой момент,
а не только на Windows-машине с терминалом. Поэтому здесь живёт
:class:`FakeBroker` — полноценная замена брокера с управляемыми ценами.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from mt5_agent.broker import AccountSnapshot, Position, Tick, TradeResult
from mt5_agent.config import load_config
from mt5_agent.specs import SymbolSpec
from mt5_agent.utils import UTC

CONFIG_YAML = textwrap.dedent(
    """
    agent:
      mode: "paper"
      loop_interval_sec: 60
      heartbeat_file: "state/heartbeat.txt"
      heartbeat_stale_sec: 180

    mt5:
      terminal_path: ""
      server: "Test-Demo"
      login: 0
      password: ""
      timeout_ms: 60000
      reconnect_attempts: 2
      reconnect_backoff_sec: 1

    trading:
      symbols:
        - name: "EURUSD"
          enabled: true
          timeframe: "M15"
          history_bars: 1500
          max_spread_points: 20
          atr_period: 14
          sl_atr_mult: 1.5
          tp_atr_mult: 2.25
          min_atr_points: 2
          session_start_utc: "00:00"
          session_end_utc: "23:59"
          news_blackout_min: 30
          magic: 111111

        - name: "AUDCAD"
          enabled: false
          timeframe: "M15"
          history_bars: 1500
          max_spread_points: 30
          atr_period: 14
          sl_atr_mult: 1.5
          tp_atr_mult: 2.25
          min_atr_points: 2
          session_windows:
            - ["00:00", "06:00"]
            - ["13:00", "19:00"]
          news_blackout_min: 30
          magic: 111112

    risk:
      risk_per_trade_pct: 0.5
      max_daily_loss_pct: 2.0
      max_weekly_loss_pct: 5.0
      max_drawdown_pct: 10.0
      max_open_positions: 2
      max_positions_per_symbol: 1
      max_daily_trades: 3
      min_balance_floor: 100.0
      cooldown_after_loss_min: 30
      max_leverage_used: 5.0
      unknown_position_action: "halt"

    signal:
      model: "gradient_boosting"
      horizon_bars: 4
      min_probability: 0.58
      allow_short: true
      retrain_every_days: 7
      train_test_split: 0.75
      embargo_bars: 4
      walk_forward_folds: 2
      model_path: "models/signal_model.joblib"
      require_model_trained: true

    sentiment:
      enabled: false
      provider: "none"

    execution:
      order_comment: "test"
      deviation_points: 10
      filling_fallback: ["IOC", "FOK", "RETURN"]
      max_slippage_points: 15
      send_sl_tp_with_order: true
      breakeven_at_r: 1.0
      trail_atr_mult: 0.0

    backtest:
      days: 60
      initial_balance: 1000.0
      spread_points: 12
      commission_per_lot: 7.0
      slippage_points: 2
      leverage: 100
      risk_per_trade_pct: 0.5
      max_hold_bars: 48
      min_trades: 10
      min_profit_factor: 1.1
      max_allowed_dd_pct: 15.0

    notifications:
      telegram:
        enabled: false
      log_level: "WARNING"
      log_file: "logs/agent.log"

    safety:
      demo_only: true
      require_confirmation_for_live: true
      kill_switch_file: "state/KILL_SWITCH"
      max_consecutive_errors: 10
      verify_account_number: true
    """
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Изолированный «мини-проект»: config/config.yaml + пустые каталоги."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    for name in ("state", "logs", "reports", "models", "data"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture()
def config(project: Path):
    return load_config(project / "config" / "config.yaml", root=project)


@pytest.fixture()
def symbol_spec() -> SymbolSpec:
    return SymbolSpec(
        name="EURUSD", digits=5, point=1e-5, tick_size=1e-5,
        tick_value_loss=1.0, tick_value_profit=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level_points=0,
    )


@pytest.fixture()
def bars() -> pd.DataFrame:
    """Простой, предсказуемый ряд цен (не синтетика — чтобы тесты были точными)."""
    index = pd.date_range("2024-01-01", periods=400, freq="15min", tz="UTC")
    base = 1.10000
    # плавный тренд + пила, детерминированно
    values = [base + 0.0001 * (i % 10) + 0.00002 * i for i in range(len(index))]
    frame = pd.DataFrame(
        {
            "open": values,
            "high": [v + 0.0003 for v in values],
            "low": [v - 0.0003 for v in values],
            "close": [v + 0.0001 for v in values],
            "tick_volume": [1000.0] * len(index),
            "spread": [12.0] * len(index),
            "real_volume": [0.0] * len(index),
        },
        index=index,
    )
    frame.attrs["symbol"] = "EURUSD"
    frame.attrs["point"] = 1e-5
    frame.attrs["digits"] = 5
    return frame


# -----------------------------------------------------------------------------
# Фейковый брокер
# -----------------------------------------------------------------------------

@dataclass
class FakeBroker:
    """Замена MT5Broker для тестов: управляемые цены, позиции и сделки."""

    spec: SymbolSpec
    equity: float = 1000.0
    balance: float = 1000.0
    is_demo: bool = True
    bid: float = 1.10000
    ask: float = 1.10012
    bars: pd.DataFrame | None = None
    positions_list: list[Position] = field(default_factory=list)
    deals_frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    margin_per_lot: float = 1000.0
    fail_orders: bool = False
    orders_sent: list[dict] = field(default_factory=list)
    closed_tickets: list[int] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)
    ticket_counter: int = 5000

    # -- жизненный цикл -------------------------------------------------------
    def ensure_connected(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def terminal_ready(self):
        return True, "ok"

    # -- данные --------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            login=12345678, server="Test-Demo", balance=self.balance, equity=self.equity,
            margin=0.0, margin_free=self.equity, margin_level=None, leverage=100,
            trade_allowed=True, trade_expert=True, is_demo=self.is_demo,
        )

    def symbol_spec(self, name: str = "EURUSD") -> SymbolSpec:
        return self.spec

    def tick(self, name: str = "EURUSD", spec=None) -> Tick:
        return Tick(
            symbol=name, time=datetime(2024, 1, 1, tzinfo=UTC), bid=self.bid, ask=self.ask,
            spread_points=round((self.ask - self.bid) / self.spec.point, 1),
        )

    def rates(self, name: str, timeframe: str, count: int) -> pd.DataFrame:
        frame = self.bars.copy() if self.bars is not None else pd.DataFrame()
        frame = frame.iloc[-int(count):]
        frame.attrs["symbol"] = name
        frame.attrs["point"] = self.spec.point
        frame.attrs["digits"] = self.spec.digits
        return frame

    def calc_margin(self, side: str, symbol: str, volume: float, price: float) -> float:
        return self.margin_per_lot * float(volume)

    def deals(self, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        return self.deals_frame

    def positions(self, symbol: str | None = None, magic: int | None = None):
        result = list(self.positions_list)
        if symbol:
            result = [p for p in result if p.symbol == symbol]
        if magic is not None:
            result = [p for p in result if p.magic == magic]
        return result

    # -- торговля -------------------------------------------------------------
    def market_order(self, symbol: str, side: str, volume: float, *, sl: float = 0.0,
                     tp: float = 0.0, magic: int = 0, comment: str = "", deviation_points: int = 10,
                     filling_preference=None, spec=None, dry_run: bool = False) -> TradeResult:
        self.orders_sent.append(
            {"symbol": symbol, "side": side, "volume": volume, "sl": sl, "tp": tp, "magic": magic}
        )
        if not sl:
            return TradeResult(False, 10016, "no stop loss")
        if self.fail_orders:
            return TradeResult(False, 10019, "no money")
        self.ticket_counter += 1
        price = self.ask if side == "buy" else self.bid
        self.positions_list.append(
            Position(
                ticket=self.ticket_counter, symbol=symbol,
                side=side, volume=volume, price_open=price, sl=sl, tp=tp,
                profit=0.0, magic=magic, comment=comment,
            )
        )
        return TradeResult(True, 10009, "done", order_ticket=self.ticket_counter,
                           price=price, volume=volume)

    def modify_position(self, ticket: int, *, sl: float | None = None, tp: float | None = None) -> TradeResult:
        for pos in self.positions_list:
            if pos.ticket == ticket:
                if sl is not None:
                    pos.sl = sl
                if tp is not None:
                    pos.tp = tp
        self.modified.append({"ticket": ticket, "sl": sl, "tp": tp})
        return TradeResult(True, 10009, "done")

    def close_position(self, position: Position, **kwargs) -> TradeResult:
        self.positions_list = [p for p in self.positions_list if p.ticket != position.ticket]
        self.closed_tickets.append(position.ticket)
        return TradeResult(True, 10009, "closed", price=self.bid, volume=position.volume)

    # -- вспомогательное --------------------------------------------------------
    def add_position(self, ticket: int, side: str = "buy", magic: int = 111111,
                     symbol: str = "EURUSD", volume: float = 0.01, price_open: float = 1.1,
                     sl: float = 1.09, tp: float = 1.12, profit: float = 0.0) -> Position:
        pos = Position(ticket=ticket, symbol=symbol, side=side, volume=volume,
                       price_open=price_open, sl=sl, tp=tp, profit=profit, magic=magic)
        self.positions_list.append(pos)
        return pos


@pytest.fixture()
def fake_broker(symbol_spec: SymbolSpec, bars: pd.DataFrame) -> FakeBroker:
    return FakeBroker(spec=symbol_spec, bars=bars)


# -----------------------------------------------------------------------------
# Заглушка модели
# -----------------------------------------------------------------------------

class StubModel:
    """Модель, которая всегда выдаёт заданную вероятность — для тестов логики."""

    def __init__(self, probability: float = 0.9, feature_names: list[str] | None = None) -> None:
        self.probability = probability
        self.meta = type("Meta", (), {
            "feature_names": feature_names or [],
            "trained_at": datetime.now(UTC).isoformat(),
            "recommended_threshold": 0.58,
            "symbol": "EURUSD",
        })()

    @property
    def feature_names(self) -> list[str]:
        return list(self.meta.feature_names)

    def predict_proba_up(self, features: pd.DataFrame) -> pd.Series:
        return pd.Series(self.probability, index=features.index, name="p_up")

    def needs_retrain(self, every_days: int) -> bool:
        return False
