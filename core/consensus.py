"""AI Consensus — agreement rules between the agents.

Rules (from the specification):
  * Market Analyst BUY  + Strategy Trader BUY  + Risk APPROVED -> EXECUTE BUY
  * Market Analyst SELL + Strategy Trader SELL + Risk APPROVED -> EXECUTE SELL
  * Market Analyst BUY  + Strategy Trader SELL (conflict)      -> HOLD
  * Anything less than full agreement                           -> HOLD
"""
from __future__ import annotations

from core.models import ConsensusResult, MarketAnalysis, RiskVerdict, StrategySignal


def evaluate(market: MarketAnalysis,
             strategy: StrategySignal,
             risk: RiskVerdict) -> ConsensusResult:
    m_sig = (market.signal or "HOLD").upper()
    s_act = (strategy.action or "HOLD").upper()

    if m_sig in ("BUY", "SELL") and s_act in ("BUY", "SELL") and m_sig != s_act:
        return ConsensusResult(
            executable=False,
            side=None,
            reason=f"Agent conflict: Market Analyst says {m_sig}, "
                   f"Strategy Trader says {s_act}. No new trade on conflict.",
        )

    if m_sig == s_act and m_sig in ("BUY", "SELL"):
        if not risk.approved:
            return ConsensusResult(
                executable=False,
                side=None,
                reason=f"Agents agree on {m_sig} but Risk & Decision Analyst "
                       f"did not approve ({risk.recommendation}).",
            )
        return ConsensusResult(
            executable=True,
            side=m_sig,
            reason=f"Consensus reached: Market Analyst {m_sig}, "
                   f"Strategy Trader {s_act}, Risk Analyst APPROVED.",
        )

    return ConsensusResult(
        executable=False,
        side=None,
        reason=f"No actionable agreement (market={m_sig}, strategy={s_act}).",
    )
