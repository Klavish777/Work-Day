"""Technical indicators computed on REAL candle data.

Pure-python implementations (no numpy dependency) so they run everywhere the
MT5 Python package runs. All functions take plain lists of floats and return
lists aligned to the input (None where the lookback window is not filled).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[List[Optional[float]],
                                    List[Optional[float]],
                                    List[Optional[float]]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    line: List[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            line[i] = ema_fast[i] - ema_slow[i]

    valid = [(i, v) for i, v in enumerate(line) if v is not None]
    sig: List[Optional[float]] = [None] * len(closes)
    hist: List[Optional[float]] = [None] * len(closes)
    if len(valid) >= signal:
        vals = [v for _, v in valid]
        sig_vals = ema(vals, signal)
        for (i, _), s in zip(valid, sig_vals):
            sig[i] = s
        for i in range(len(closes)):
            if line[i] is not None and sig[i] is not None:
                hist[i] = line[i] - sig[i]
    return line, sig, hist


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    trs: List[float] = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i + 1] = prev
    return out


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Tuple[List[Optional[float]],
                                   List[Optional[float]],
                                   List[Optional[float]]]:
    """Wilder ADX. Returns (adx, +DI, -DI) series."""
    n = len(closes)
    adx_out: List[Optional[float]] = [None] * n
    pdi_out: List[Optional[float]] = [None] * n
    mdi_out: List[Optional[float]] = [None] * n
    if n < period * 2 + 1:
        return adx_out, pdi_out, mdi_out

    trs: List[float] = []
    pdms: List[float] = []
    mdms: List[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdms.append(up if (up > down and up > 0) else 0.0)
        mdms.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))

    tr_s = sum(trs[:period])
    pdm_s = sum(pdms[:period])
    mdm_s = sum(mdms[:period])
    dx_window: List[float] = []

    for i in range(period, n):
        if i > period:
            tr_s = tr_s - tr_s / period + trs[i - 1]
            pdm_s = pdm_s - pdm_s / period + pdms[i - 1]
            mdm_s = mdm_s - mdm_s / period + mdms[i - 1]
        pdi = 100.0 * pdm_s / tr_s if tr_s > 0 else 0.0
        mdi = 100.0 * mdm_s / tr_s if tr_s > 0 else 0.0
        pdi_out[i] = pdi
        mdi_out[i] = mdi
        # guard: when the TOTAL directional movement is negligible relative
        # to the true range (perfect flat / dead market), the DI ratio is
        # numerically unstable and must be treated as no-direction (DX=0)
        if (pdi + mdi) > 1e-6 and (pdm_s + mdm_s) > tr_s * 1e-6:
            dx = 100.0 * abs(pdi - mdi) / (pdi + mdi)
        else:
            dx = 0.0
        dx_window.append(dx)
        if len(dx_window) == period:
            adx_out[i] = sum(dx_window) / period
        elif len(dx_window) > period and adx_out[i - 1] is not None:
            adx_out[i] = (adx_out[i - 1] * (period - 1) + dx) / period

    return adx_out, pdi_out, mdi_out


# --------------------------------------------------------------------------- #
def pivot_levels(highs: Sequence[float], lows: Sequence[float],
                 closes: Sequence[float], fractal: int = 3, lookback: int = 120,
                 cluster_atr: Optional[float] = None) -> Dict[str, List[float]]:
    """Pivot highs/lows in the recent window, clustered into levels.

    Returns {"support": [...], "resistance": [...]} sorted by touch count.
    """
    n = len(highs)
    if n < fractal * 2 + 3:
        return {"support": [], "resistance": []}
    start = max(fractal, n - lookback)
    piv_high: List[float] = []
    piv_low: List[float] = []
    for i in range(start, n - fractal):
        if all(highs[i] >= highs[i - k] for k in range(1, fractal + 1)) and \
           all(highs[i] >= highs[i + k] for k in range(1, fractal + 1)):
            piv_high.append(highs[i])
        if all(lows[i] <= lows[i - k] for k in range(1, fractal + 1)) and \
           all(lows[i] <= lows[i + k] for k in range(1, fractal + 1)):
            piv_low.append(lows[i])

    last_close = float(closes[-1])
    if cluster_atr and cluster_atr > 0:
        tol = cluster_atr * 0.5
    else:
        window_h = highs[-lookback:] if lookback <= n else list(highs)
        window_l = lows[-lookback:] if lookback <= n else list(lows)
        tol = (max(window_h) - min(window_l)) / 40.0

    def _cluster(points: List[float]) -> List[Tuple[float, int]]:
        pts = sorted(points)
        clusters: List[List[float]] = []
        for p in pts:
            if clusters and p - clusters[-1][-1] <= tol:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [(sum(c) / len(c), len(c)) for c in clusters]

    sup = [(lvl, cnt) for lvl, cnt in _cluster(piv_low) if lvl < last_close]
    res = [(lvl, cnt) for lvl, cnt in _cluster(piv_high) if lvl > last_close]
    sup.sort(key=lambda t: (-t[1], -t[0]))
    res.sort(key=lambda t: (-t[1], t[0]))
    return {
        "support": [round(l, 6) for l, _ in sup[:3]],
        "resistance": [round(l, 6) for l, _ in res[:3]],
    }


def momentum_pct(closes: Sequence[float], bars: int = 12) -> float:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return 0.0
    return (closes[-1] / closes[-bars - 1] - 1.0) * 100.0
