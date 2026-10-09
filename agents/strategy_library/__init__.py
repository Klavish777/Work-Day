"""Strategy Library — deterministic votes from classic trading systems.

Each strategy is a public, well-known system from the trading literature /
open trader communities, encoded as deterministic code (no internet scraping,
no fake data). Every cycle each strategy votes BUY / SELL / NEUTRAL on the
REAL candles coming from MT5. The Strategy Trader aggregates the weighted
votes; the weights themselves are learned from the trade history by
core.memory.AgentMemory (agents remember every closed trade).

Sources (public books / methods):
  1. EMA cross            — J. Murphy, "Technical Analysis of the Financial Markets"
  2. Triple EMA stack     — EMA 8/21/55 alignment, popular community system
  3. RSI reversion        — W. Wilder, "New Concepts in Technical Trading Systems"
  4. RSI-50 cross         — L. Connors, C. Alvarez, "Short Term Trading Strategies That Work"
  5. MACD cross           — G. Appel, "Technical Analysis: Power Tools for Active Investors"
  6. MACD histogram slope — G. Appel (histogram momentum)
  7. Bollinger breakout   — J. Bollinger, "Bollinger on Bollinger Bands"
  8. Donchian channel     — R. Donchian / Turtle traders (R. Dennis, M. Covel "Way of the Turtle")
  9. Stochastic extremes  — G. Lane, stochastic oscillator
 10. ADX + DI direction   — W. Wilder, ADX trend filter
 11. Momentum threshold   — G. Antonacci / momentum literature
 12. Engulfing price action — A. Brooks, "Trading Price Action"
 13. Volodya meta (proprietary) — original system built from the same
     candle data: rolling z-score, EMA slope, RSI position and the weighted
     agreement of strategies 1-12 (the meta-vote). Developed in-house.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from agents import indicators as ta
from core.models import Candle, Tick

#: name -> (display title, literature source)
CATALOG: Dict[str, tuple] = {
    "ema_cross": ("EMA 9/21 кросс", "Дж. Мёрфи — Технический анализ"),
    "triple_ema": ("Тройная EMA 8/21/55", "Система трейдерских сообществ"),
    "rsi_reversion": ("RSI возврат из крайности", "У. Уайлдер — Новые концепции"),
    "rsi_fifty": ("RSI пересечение 50", "Коннорс/Альварес — Краткосрочные стратегии"),
    "macd_cross": ("MACD пересечение", "Дж. Аппель — Технический анализ"),
    "macd_hist": ("Гистограмма MACD", "Дж. Аппель — импульс гистограммы"),
    "bollinger": ("Пробой Боллинджера", "Дж. Боллинджер — О полосах Боллинджера"),
    "donchian": ("Канал Дончиана 20", "Черепахи — Р. Деннис / М. Ковел"),
    "stochastic": ("Стохастик в крайностях", "Дж. Лейн — стохастический осциллятор"),
    "adx_di": ("ADX + направление DI", "У. Уайлдер — индекс тренда"),
    "momentum": ("Импульс 12 баров", "Г. Антонесси — импульсные системы"),
    "engulfing": ("Поглощение (прайс-экшн)", "Э. Брукс — Торговля по прайс-экшн"),
    "volodya_meta": ("Володя (авторская)", "Собственная мета-стратегия: статистика рынка + голоса библиотеки"),
}

REF_TF_ORDER = ("M15", "M30", "M5", "H1", "H4", "M1", "D1")


def _neut(strength: float = 0.0) -> Dict:
    return {"vote": "NEUTRAL", "strength": strength}


def _pick_reference(tf_candles: Dict[str, List[Candle]]) -> Optional[List[Candle]]:
    for tf in REF_TF_ORDER:
        if tf in tf_candles and len(tf_candles[tf]) >= 60:
            return tf_candles[tf]
    for tf, candles in tf_candles.items():
        if len(candles) >= 60:
            return candles
    return None


def _cross_recently(a_prev: List[float], b_prev: List[float], up: bool,
                    lookback: int = 3) -> bool:
    """True if series a crossed series b within the last `lookback` bars."""
    n = min(len(a_prev), len(b_prev))
    for k in range(1, min(lookback, n - 1) + 1):
        i = n - 1 - k + 1
        if i <= 0:
            break
        before = a_prev[i - 1] - b_prev[i - 1]
        after = a_prev[i] - b_prev[i]
        if (up and before <= 0 < after) or (not up and before >= 0 > after):
            return True
    return False


def vote_all(tf_candles: Dict[str, List[Candle]],
             tick: Optional[Tick] = None) -> Dict[str, Dict]:
    """Run every strategy on real candles; returns per-strategy votes."""
    votes: Dict[str, Dict] = {}
    candles = _pick_reference(tf_candles)
    if candles is None:
        return {name: _neut() for name in CATALOG}

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    n = len(closes)

    ema9 = ta.ema(closes, 9)
    ema21 = ta.ema(closes, 21)
    ema50 = ta.ema(closes, 50)
    ema8 = ta.ema(closes, 8)
    ema55 = ta.ema(closes, 55) if n >= 55 else None
    rsi = ta.rsi(closes, 14)
    macd_line, sig_line, hist = ta.macd(closes)
    mid, up, lo = ta.bollinger(closes, 20, 2.0)
    adx_s, pdi, mdi = ta.adx(highs, lows, closes, 14)
    k_val, d_val = ta.stochastic(highs, lows, closes)
    mom = ta.momentum_pct(closes, 12)
    close = closes[-1]

    def fin(v):
        return v is not None

    # 1. EMA 9/21 cross ------------------------------------------------------ #
    v = _neut()
    if fin(ema9[-1]) and fin(ema21[-1]):
        e9 = [x or 0.0 for x in ema9]
        e21 = [x or 0.0 for x in ema21]
        if _cross_recently(e9, e21, up=True):
            v = {"vote": "BUY", "strength": 0.9}
        elif _cross_recently(e9, e21, up=False):
            v = {"vote": "SELL", "strength": 0.9}
        elif e9[-1] > e21[-1]:
            v = {"vote": "BUY", "strength": 0.45}
        elif e9[-1] < e21[-1]:
            v = {"vote": "SELL", "strength": 0.45}
    votes["ema_cross"] = v

    # 2. Triple EMA stack ------------------------------------------------------ #
    v = _neut()
    if ema55 is not None and fin(ema8[-1]) and fin(ema21[-1]) and fin(ema55[-1]):
        if ema8[-1] > ema21[-1] > ema55[-1]:
            v = {"vote": "BUY", "strength": 0.8}
        elif ema8[-1] < ema21[-1] < ema55[-1]:
            v = {"vote": "SELL", "strength": 0.8}
    votes["triple_ema"] = v

    # 3. RSI reversion ------------------------------------------------------ #
    v = _neut()
    r = rsi[-1]
    if r is not None:
        if r <= 30:
            v = {"vote": "BUY", "strength": min(1.0, (30 - r) / 10 + 0.5)}
        elif r >= 70:
            v = {"vote": "SELL", "strength": min(1.0, (r - 70) / 10 + 0.5)}
    votes["rsi_reversion"] = v

    # 4. RSI-50 cross with trend filter ------------------------------------- #
    v = _neut()
    rr = [x if x is not None else 50.0 for x in rsi]
    trend_up = fin(ema50[-1]) and close > ema50[-1]
    trend_dn = fin(ema50[-1]) and close < ema50[-1]
    if _cross_recently(rr, [50.0] * len(rr), up=True) and trend_up:
        v = {"vote": "BUY", "strength": 0.65}
    elif _cross_recently(rr, [50.0] * len(rr), up=False) and trend_dn:
        v = {"vote": "SELL", "strength": 0.65}
    votes["rsi_fifty"] = v

    # 5. MACD cross ---------------------------------------------------------- #
    v = _neut()
    if fin(macd_line[-1]) and fin(sig_line[-1]):
        m = [x or 0.0 for x in macd_line]
        s = [x or 0.0 for x in sig_line]
        if _cross_recently(m, s, up=True):
            v = {"vote": "BUY", "strength": 0.85}
        elif _cross_recently(m, s, up=False):
            v = {"vote": "SELL", "strength": 0.85}
    votes["macd_cross"] = v

    # 6. MACD histogram slope ------------------------------------------------- #
    v = _neut()
    hs = [x if x is not None else 0.0 for x in hist]
    if len(hs) >= 3:
        if hs[-1] > hs[-2] > hs[-3] and hs[-1] > 0:
            v = {"vote": "BUY", "strength": 0.5}
        elif hs[-1] < hs[-2] < hs[-3] and hs[-1] < 0:
            v = {"vote": "SELL", "strength": 0.5}
    votes["macd_hist"] = v

    # 7. Bollinger breakout ---------------------------------------------------- #
    v = _neut()
    if fin(up[-1]) and fin(lo[-1]):
        if close > up[-1]:
            v = {"vote": "BUY", "strength": 0.75}
        elif close < lo[-1]:
            v = {"vote": "SELL", "strength": 0.75}
    votes["bollinger"] = v

    # 8. Donchian 20 breakout --------------------------------------------------- #
    v = _neut()
    if n >= 21:
        hh20 = max(highs[-21:-1])
        ll20 = min(lows[-21:-1])
        if close > hh20:
            v = {"vote": "BUY", "strength": 0.85}
        elif close < ll20:
            v = {"vote": "SELL", "strength": 0.85}
    votes["donchian"] = v

    # 9. Stochastic extremes ---------------------------------------------------- #
    v = _neut()
    if k_val is not None and d_val is not None:
        if k_val < 20 and k_val > d_val:
            v = {"vote": "BUY", "strength": 0.7}
        elif k_val > 80 and k_val < d_val:
            v = {"vote": "SELL", "strength": 0.7}
    votes["stochastic"] = v

    # 10. ADX + DI direction -------------------------------------------------- #
    v = _neut()
    if adx_s[-1] is not None and adx_s[-1] >= 20:
        strength = min(1.0, 0.4 + adx_s[-1] / 100.0)
        if pdi[-1] is not None and mdi[-1] is not None:
            if pdi[-1] > mdi[-1]:
                v = {"vote": "BUY", "strength": strength}
            else:
                v = {"vote": "SELL", "strength": strength}
    votes["adx_di"] = v

    # 11. Momentum ------------------------------------------------------------- #
    v = _neut()
    if mom >= 0.12:
        v = {"vote": "BUY", "strength": min(1.0, 0.5 + mom)}
    elif mom <= -0.12:
        v = {"vote": "SELL", "strength": min(1.0, 0.5 - mom)}
    votes["momentum"] = v

    # 12. Engulfing candle ------------------------------------------------------ #
    v = _neut()
    if n >= 2:
        o1, c1 = candles[-2].open, candles[-2].close
        o2, c2 = candles[-1].open, candles[-1].close
        if c1 < o1 and c2 > o2 and c2 > o1 and o2 < c1:
            v = {"vote": "BUY", "strength": 0.6}
        elif c1 > o1 and c2 < o2 and c2 < o1 and o2 > c1:
            v = {"vote": "SELL", "strength": 0.6}
    votes["engulfing"] = v

    # 13. VOLODYA meta-strategy (proprietary) -------------------------------- #
    # Built from the same real data: rolling z-score, EMA-20 slope, RSI
    # position and the agreement of the 12 classic strategies above.
    v = _neut()
    if n >= 25:
        win = closes[-20:]
        mean = sum(win) / 20.0
        std = (sum((x - mean) ** 2 for x in win) / 20.0) ** 0.5
        z = (close - mean) / std if std else 0.0
        ema20 = ta.ema(closes, 20)
        slope = 0.0
        if ema20[-1] is not None and ema20[-6] is not None and close:
            slope = (ema20[-1] - ema20[-6]) / close * 10000.0  # in points
        r_now = rsi[-1] if rsi[-1] is not None else 50.0
        agree = sum(1 for vv in votes.values() if vv["vote"] == "BUY") - \
            sum(1 for vv in votes.values() if vv["vote"] == "SELL")
        score = (0.40 * max(-1.0, min(1.0, agree / 6.0)) +
                 0.30 * max(-1.0, min(1.0, slope / 3.0)) +
                 0.20 * ((r_now - 50.0) / 50.0) -
                 0.10 * max(-1.0, min(1.0, z / 2.0)))
        if score >= 0.25:
            v = {"vote": "BUY", "strength": min(1.0, abs(score) + 0.2)}
        elif score <= -0.25:
            v = {"vote": "SELL", "strength": min(1.0, abs(score) + 0.2)}
    votes["volodya_meta"] = v

    return votes


def dominant_side(votes: Dict[str, Dict],
                  weights: Optional[Dict[str, float]] = None,
                  min_net: float = 0.9, min_votes: int = 4):
    """Return (side, agg) when the library shows a clear weighted dominance."""
    agg = aggregate(votes, weights)
    side = agg["side"]
    if side not in ("BUY", "SELL"):
        return None, agg
    n = agg["buy_n"] if side == "BUY" else agg["sell_n"]
    if abs(agg["net"]) >= min_net and n >= min_votes:
        return side, agg
    return None, agg


def aggregate(votes: Dict[str, Dict],
              weights: Optional[Dict[str, float]] = None) -> Dict:
    """Weighted aggregation of library votes."""
    weights = weights or {}
    buy_score = sell_score = 0.0
    buy_n = sell_n = neut_n = 0
    for name, vote in (votes or {}).items():
        w = float(weights.get(name, 1.0))
        s = float(vote.get("strength", 0.0)) * w
        if vote.get("vote") == "BUY":
            buy_score += s
            buy_n += 1
        elif vote.get("vote") == "SELL":
            sell_score += s
            sell_n += 1
        else:
            neut_n += 1
    net = buy_score - sell_score
    side = "BUY" if net > 0.35 else "SELL" if net < -0.35 else "NEUTRAL"
    return {"buy_n": buy_n, "sell_n": sell_n, "neut_n": neut_n,
            "buy_score": round(buy_score, 3), "sell_score": round(sell_score, 3),
            "net": round(net, 3), "side": side}
