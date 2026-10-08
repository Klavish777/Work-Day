from core import consensus
from core.models import MarketAnalysis, RiskVerdict, StrategySignal


def mk(signal, action, approved=True):
    return consensus.evaluate(
        MarketAnalysis(signal=signal, confidence=0.8),
        StrategySignal(action=action, confidence=0.8),
        RiskVerdict(approved=approved, recommendation="EXECUTE" if approved else "REJECT"),
    )


def test_full_agreement_buy():
    r = mk("BUY", "BUY", True)
    assert r.executable and r.side == "BUY"


def test_full_agreement_sell():
    r = mk("SELL", "SELL", True)
    assert r.executable and r.side == "SELL"


def test_conflict_gives_hold():
    r = mk("BUY", "SELL", True)
    assert not r.executable and r.side is None
    assert "conflict" in r.reason.lower()


def test_risk_veto_blocks():
    r = mk("BUY", "BUY", False)
    assert not r.executable


def test_no_signal_hold():
    r = mk("HOLD", "HOLD", True)
    assert not r.executable
