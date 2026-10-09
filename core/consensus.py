"""AI Consensus — agreement rules between the agents.

Rules (from the specification):
  * Market Analyst BUY  + Strategy Trader BUY  + Risk APPROVED -> EXECUTE BUY
  * Market Analyst SELL + Strategy Trader SELL + Risk APPROVED -> EXECUTE SELL
  * Market Analyst BUY  + Strategy Trader SELL (conflict)      -> HOLD
  * Anything less than full agreement                           -> HOLD
"""
from __future__ import annotations

from agents import strategy_library as slib
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

    # v2.0: consensus can also be reached through the strategy library —
    # when the Market Analyst is not directional but the weighted votes of
    # the classic strategies show a clear dominance matching the Strategy
    # Trader's plan, and the Risk Analyst approves.
    votes = getattr(market, "strategies", None)
    if votes and s_act in ("BUY", "SELL"):
        side, agg = slib.dominant_side(votes)
        if side == s_act:
            if not risk.approved:
                return ConsensusResult(
                    executable=False,
                    side=None,
                    reason=(f"Библиотека стратегий за {side}, но Риск-аналитик "
                            f"не одобрил ({risk.recommendation})."),
                )
            return ConsensusResult(
                executable=True,
                side=side,
                reason=(f"Консенсус библиотеки стратегий: {agg['buy_n']} за "
                        f"BUY / {agg['sell_n']} за SELL, взвешенный счёт "
                        f"{agg['net']:+.2f}, Риск-аналитик одобрил."),
            )

    return ConsensusResult(
        executable=False,
        side=None,
        reason=f"No actionable agreement (market={m_sig}, strategy={s_act}).",
    )
