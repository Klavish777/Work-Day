import time

from core.models import (AccountView, ConsensusResult, LLMDecision,
                         MarketAnalysis, PositionView, RiskVerdict,
                         StrategySignal, SymbolView, Tick)
from core.settings import DEFAULT_SETTINGS
from risk.manager import RiskManager


def ctx(**over):
    settings = dict(DEFAULT_SETTINGS)
    settings.update(over.get("settings", {}))
    d = dict(
        settings=settings,
        engine_state="RUNNING",
        decision=LLMDecision(action="BUY", confidence=0.8),
        market=MarketAnalysis(signal="BUY", confidence=0.8),
        strategy=StrategySignal(action="BUY", confidence=0.8, stop_loss=0.8990,
                                take_profit=0.9020, risk_reward=1.5),
        risk_verdict=RiskVerdict(approved=True, recommendation="EXECUTE"),
        consensus=ConsensusResult(executable=True, side="BUY"),
        account=AccountView(balance=1000, equity=1000),
        position=None,
        tick=Tick(symbol="AUDCAD", bid=0.9000, ask=0.90015,
                  spread_points=15, time=int(time.time())),
        sym=SymbolView(symbol="AUDCAD"),
        trades_today=0,
        realized_loss_today=0.0,
    )
    d.update({k: v for k, v in over.items() if k != "settings"})
    return d


def test_happy_path_approved():
    g = RiskManager().evaluate(**ctx())
    assert g.approved, g.reasons


def test_second_position_blocked():
    pos = PositionView(ticket=1, symbol="AUDCAD", side="BUY", lot=0.01,
                       open_price=0.9, open_time=0, profit=0.1)
    g = RiskManager().evaluate(**ctx(position=pos))
    assert not g.approved
    assert any("second" in r.lower() or "already" in r.lower() for r in g.reasons)


def test_excess_lot_blocked():
    g = RiskManager().evaluate(**ctx(settings={"lot_size": 0.5, "max_lot": 0.1}))
    assert not g.approved


def test_real_without_confirmation_blocked():
    g = RiskManager().evaluate(**ctx(settings={"mode": "REAL",
                                               "real_confirmed": False}))
    assert not g.approved


def test_low_confidence_blocked():
    g = RiskManager().evaluate(**ctx(
        decision=LLMDecision(action="BUY", confidence=0.3)))
    assert not g.approved


def test_high_spread_blocked():
    g = RiskManager().evaluate(**ctx(
        tick=Tick(symbol="AUDCAD", bid=0.9, ask=0.9010, spread_points=100,
                  time=int(time.time()))))
    assert not g.approved


def test_conflict_blocked():
    g = RiskManager().evaluate(**ctx(
        consensus=ConsensusResult(executable=False, side=None)))
    assert not g.approved


def test_daily_limits_blocked():
    g = RiskManager().evaluate(**ctx(trades_today=99))
    assert not g.approved
    g2 = RiskManager().evaluate(**ctx(realized_loss_today=99.0))
    assert not g2.approved
