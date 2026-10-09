"""AI consensus, including the strategy-library path added in v2.0."""
from core import consensus
from core.models import MarketAnalysis, RiskVerdict, StrategySignal


def _strong_buy_votes():
    return {f"s{i}": {"vote": "BUY", "strength": 0.8} for i in range(6)}


def test_classic_full_agreement():
    m = MarketAnalysis(signal="BUY", confidence=0.8)
    s = StrategySignal(action="BUY", confidence=0.8)
    r = RiskVerdict(approved=True, recommendation="EXECUTE")
    res = consensus.evaluate(m, s, r)
    assert res.executable and res.side == "BUY"


def test_library_dominance_consensus():
    m = MarketAnalysis(signal="HOLD", confidence=0.2,
                       strategies=_strong_buy_votes())
    s = StrategySignal(action="BUY", confidence=0.6)
    r = RiskVerdict(approved=True, recommendation="EXECUTE")
    res = consensus.evaluate(m, s, r)
    assert res.executable and res.side == "BUY"
    assert "библиотек" in res.reason.lower()


def test_library_consensus_needs_risk_approval():
    m = MarketAnalysis(signal="HOLD", strategies=_strong_buy_votes())
    s = StrategySignal(action="BUY", confidence=0.6)
    r = RiskVerdict(approved=False, recommendation="REJECT")
    res = consensus.evaluate(m, s, r)
    assert not res.executable
