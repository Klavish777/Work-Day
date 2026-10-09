"""AI Agent #1 — MARKET ANALYST.

Responsibility: analyze the AUD/CAD market on multiple timeframes and return
a structured report (trend, strength, probabilities, volatility, levels,
entry zones). It NEVER opens trades.

Pipeline: real candles from MT5 -> deterministic indicator engine -> LLM
synthesis (when configured). If the LLM is off or fails, the deterministic
result is used and clearly labelled as such — no fake data is ever produced.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

from agents import indicators as ta
from agents.common import ask_llm, compact
from core.models import Candle, MarketAnalysis, Tick, TimeframeAnalysis
from llm.prompts import MARKET_ANALYST_SYSTEM

LOG = logging.getLogger("workday.market_analyst")

TF_WEIGHTS = {"M1": 0.5, "M5": 0.75, "M15": 1.0, "M30": 1.0,
              "H1": 1.25, "H4": 1.25, "D1": 1.0}


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def analyze_timeframe(tf: str, candles: List[Candle]) -> Optional[TimeframeAnalysis]:
    if len(candles) < 40:
        return None
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    ema_f = ta.ema(closes, 20)[-1]
    ema_m = ta.ema(closes, 50)[-1]
    ema_s = ta.ema(closes, 200)[-1] if len(closes) >= 200 else None
    rsi_v = ta.rsi(closes, 14)[-1]
    adx_v, pdi, mdi = ta.adx(highs, lows, closes, 14)
    atr_v = ta.atr(highs, lows, closes, 14)[-1]
    _, _, hist = ta.macd(closes)

    close = closes[-1]
    score = 0.0
    weight_sum = 0.0

    if ema_f is not None and ema_m is not None:
        weight_sum += 1.0
        if close > ema_f > ema_m:
            score += 1.0
        elif close < ema_f < ema_m:
            score -= 1.0
        elif close > ema_f:
            score += 0.4
        elif close < ema_f:
            score -= 0.4

    if ema_s is not None:
        weight_sum += 0.5
        score += 0.5 if close > ema_s else -0.5

    if hist[-1] is not None and atr_v:
        weight_sum += 0.6
        score += _clamp(hist[-1] / (atr_v * 0.5)) * 0.6

    if rsi_v is not None:
        weight_sum += 0.5
        score += ((rsi_v - 50.0) / 50.0) * 0.5

    mom = ta.momentum_pct(closes, 12)
    weight_sum += 0.4
    score += _clamp(mom / 0.30) * 0.4

    score = _clamp(score / weight_sum) if weight_sum else 0.0
    trend = "BULLISH" if score >= 0.15 else ("BEARISH" if score <= -0.15 else "FLAT")

    return TimeframeAnalysis(
        timeframe=tf,
        candles=len(candles),
        last_close=close,
        ema_fast=round(ema_f, 6) if ema_f else 0.0,
        ema_mid=round(ema_m, 6) if ema_m else 0.0,
        ema_slow=round(ema_s, 6) if ema_s else 0.0,
        rsi=round(rsi_v, 2) if rsi_v is not None else 50.0,
        adx=round(adx_v[-1], 2) if adx_v[-1] is not None else 0.0,
        plus_di=round(pdi[-1], 2) if pdi[-1] is not None else 0.0,
        minus_di=round(mdi[-1], 2) if mdi[-1] is not None else 0.0,
        macd_hist=round(hist[-1], 6) if hist[-1] is not None else 0.0,
        atr=round(atr_v, 6) if atr_v else 0.0,
        atr_pct=round(atr_v / close * 100.0, 4) if (atr_v and close) else 0.0,
        momentum_pct=round(mom, 4),
        score=round(score, 4),
        trend=trend,
    )


class MarketAnalystAgent:
    name = "market_analyst"

    def __init__(self, provider, llm_flags: Dict[str, bool]):
        self._provider = provider
        self._llm_enabled = bool(llm_flags.get("market_analyst", True))

    # ------------------------------------------------------------------ #
    def analyze(self, symbol: str, tick: Optional[Tick],
                tf_candles: Dict[str, List[Candle]]) -> MarketAnalysis:
        per_tf: List[TimeframeAnalysis] = []
        for tf, candles in tf_candles.items():
            a = analyze_timeframe(tf, candles)
            if a:
                per_tf.append(a)

        if not per_tf:
            return MarketAnalysis(
                signal="HOLD", trend="FLAT", trend_strength="Weak",
                confidence=0.0,
                reason="Not enough candle data from MT5 for analysis.",
                source="DETERMINISTIC",
            )

        det = self._deterministic(symbol, tick, per_tf, tf_candles)

        llm_answer = ask_llm(
            self._provider, self._llm_enabled,
            MARKET_ANALYST_SYSTEM,
            self._build_user_prompt(symbol, tick, per_tf),
            self.name,
        )
        if llm_answer is not None:
            merged = self._merge_llm(det, llm_answer)
            if merged is not None:
                merged.source = "LLM"
                return merged
            det.source = "LLM_FALLBACK_DETERMINISTIC"
        return det

    # ------------------------------------------------------------------ #
    def _deterministic(self, symbol: str, tick: Optional[Tick],
                       per_tf: List[TimeframeAnalysis],
                       tf_candles: Dict[str, List[Candle]]) -> MarketAnalysis:
        w_sum = 0.0
        comp = 0.0
        adx_sum = 0.0
        adx_n = 0
        for a in per_tf:
            w = TF_WEIGHTS.get(a.timeframe, 1.0)
            damp = max(0.35, min(1.0, a.adx / 25.0)) if a.adx else 0.5
            comp += a.score * w * damp
            w_sum += w
            if a.adx:
                adx_sum += a.adx
                adx_n += 1
        composite = _clamp(comp / w_sum) if w_sum else 0.0
        avg_adx = adx_sum / adx_n if adx_n else 0.0

        flatness = max(0.0, min(1.0, 1.0 - avg_adx / 25.0))
        raw_p = 1.0 / (1.0 + math.exp(-2.0 * composite))
        buy_prob = flatness * 0.5 + (1.0 - flatness) * raw_p
        sell_prob = 1.0 - buy_prob

        # demo-friendly signal thresholds (v1.6): the bot must actually
        # trade on demo while still ignoring pure noise
        if avg_adx < 15:
            signal = "HOLD"
        elif composite >= 0.20:
            signal = "BUY"
        elif composite <= -0.20:
            signal = "SELL"
        else:
            signal = "HOLD"

        trend = "BULLISH" if composite >= 0.15 else (
            "BEARISH" if composite <= -0.15 else "FLAT")
        strength = ("Strong" if avg_adx >= 30 else
                    "Moderate" if avg_adx >= 20 else "Weak")

        # volatility + momentum from the reference timeframe (M15 preferred)
        ref = next((a for a in per_tf if a.timeframe == "M15"), per_tf[-1])
        volatility = ("HIGH" if ref.atr_pct > 0.10 else
                      "LOW" if ref.atr_pct < 0.035 else "NORMAL")
        momentum = ("UP" if ref.momentum_pct > 0.05 else
                    "DOWN" if ref.momentum_pct < -0.05 else "NEUTRAL")

        reversal = "LOW"
        if ref.rsi >= 75 or ref.rsi <= 25:
            reversal = "HIGH"
        elif ref.rsi >= 68 or ref.rsi <= 32:
            reversal = "MEDIUM"

        # support/resistance from the reference timeframe candles
        candles_ref = tf_candles.get(ref.timeframe, [])
        levels = {"support": [], "resistance": []}
        if len(candles_ref) >= 30:
            levels = ta.pivot_levels(
                [c.high for c in candles_ref],
                [c.low for c in candles_ref],
                [c.close for c in candles_ref],
                cluster_atr=ref.atr or None,
            )

        entry_zones: List[Dict[str, float]] = []
        atr_v = ref.atr or 0.0
        if signal == "BUY" and levels["support"]:
            s = max(levels["support"])
            entry_zones.append({"from": round(s, 6),
                                "to": round(s + 0.3 * atr_v, 6), "side": "BUY"})
        if signal == "SELL" and levels["resistance"]:
            r = min(levels["resistance"])
            entry_zones.append({"from": round(r - 0.3 * atr_v, 6),
                                "to": round(r, 6), "side": "SELL"})

        price_note = ""
        if tick:
            price_note = (f" Bid/Ask {tick.bid}/{tick.ask}, "
                          f"spread {tick.spread_points:.1f} pts.")

        confidence = round(min(0.95, abs(composite) * 1.4 + avg_adx / 100.0), 3)
        reason = (
            f"{symbol}: composite score {composite:+.2f} across "
            f"{len(per_tf)} timeframes, avg ADX {avg_adx:.1f}, trend {trend} "
            f"({strength}), volatility {volatility}.{price_note}"
        )

        return MarketAnalysis(
            signal=signal,
            buy_probability=round(buy_prob, 3),
            sell_probability=round(sell_prob, 3),
            trend=trend,
            trend_strength=strength,
            volatility=volatility,
            momentum=momentum,
            reversal_risk=reversal,
            support=levels.get("support", []),
            resistance=levels.get("resistance", []),
            entry_zones=entry_zones,
            confidence=confidence,
            reason=reason,
            per_timeframe=per_tf,
            source="DETERMINISTIC",
        )

    # ------------------------------------------------------------------ #
    def _build_user_prompt(self, symbol: str, tick: Optional[Tick],
                           per_tf: List[TimeframeAnalysis]) -> str:
        payload = {
            "symbol": symbol,
            "tick": tick.to_dict() if tick else None,
            "timeframes": [a.to_dict() for a in per_tf],
        }
        return ("Real market data (indicator values computed from MT5 "
                f"candles):\n{compact(payload)}\n\nProduce the JSON report.")

    @staticmethod
    def _merge_llm(base: MarketAnalysis, ans: Dict) -> Optional[MarketAnalysis]:
        signal = str(ans.get("signal", "")).upper()
        if signal not in ("BUY", "SELL", "HOLD"):
            return None
        try:
            bp = float(ans.get("buy_probability", base.buy_probability))
            sp = float(ans.get("sell_probability", base.sell_probability))
            conf = float(ans.get("confidence", base.confidence))
        except (TypeError, ValueError):
            return None
        bp = max(0.0, min(1.0, bp))
        sp = max(0.0, min(1.0, sp))
        total = bp + sp
        if total <= 0:
            bp, sp = 0.5, 0.5
        else:
            bp, sp = bp / total, sp / total

        trend = str(ans.get("trend", base.trend)).upper()
        if trend not in ("BULLISH", "BEARISH", "FLAT"):
            trend = base.trend
        strength = ans.get("trend_strength", base.trend_strength)
        if strength not in ("Weak", "Moderate", "Strong"):
            strength = base.trend_strength
        volatility = str(ans.get("volatility", base.volatility)).upper()
        if volatility not in ("LOW", "NORMAL", "HIGH"):
            volatility = base.volatility

        def _levels(key):
            vals = ans.get(key, [])
            out = []
            if isinstance(vals, list):
                for v in vals[:5]:
                    try:
                        out.append(round(float(v), 6))
                    except (TypeError, ValueError):
                        continue
            return out or getattr(base, key)

        return MarketAnalysis(
            signal=signal,
            buy_probability=round(bp, 3),
            sell_probability=round(sp, 3),
            trend=trend,
            trend_strength=strength,
            volatility=volatility,
            momentum=str(ans.get("momentum", base.momentum)).upper()
            if str(ans.get("momentum", "")).upper() in ("UP", "DOWN", "NEUTRAL")
            else base.momentum,
            reversal_risk=str(ans.get("reversal_risk", base.reversal_risk)).upper()
            if str(ans.get("reversal_risk", "")).upper() in ("LOW", "MEDIUM", "HIGH")
            else base.reversal_risk,
            support=_levels("support"),
            resistance=_levels("resistance"),
            entry_zones=base.entry_zones,
            confidence=round(max(0.0, min(1.0, conf)), 3),
            reason=str(ans.get("reason", base.reason))[:500],
            per_timeframe=base.per_timeframe,
            source="LLM",
        )
