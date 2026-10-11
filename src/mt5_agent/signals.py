"""Генерация торгового сигнала.

Сигнал — это НЕ ордер. Это ответ на вопрос «есть ли сейчас статистическое
преимущество, и если да, то какое движение мы ожидаем и где ставим стоп».

Пайплайн решения:
    1. данные (бары) → признаки (только прошлое)
    2. ML-модель → вероятность движения вверх
    3. порог вероятности → сторона сделки (лонг/шорт/нет)
    4. рыночные фильтры → сессия, спред, волатильность
    5. новостной фильтр (LLM/эвристика) → блокировка против «красных» новостей
    6. риск-модуль → объём, лимиты

Только после шага 6 формируется заявка в терминал.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from .config import SymbolConfig
from .exceptions import DataError
from .features import atr, build_features, validate_ohlcv
from .logging_setup import get_logger, log_event
from .model import SignalModel
from .utils import in_session_windows, utc_now

log = get_logger("signals")


@dataclass
class Signal:
    symbol: str
    side: str                      # "buy" | "sell" | "none"
    probability: float             # вероятность движения вверх
    price: float = 0.0             # цена, на которой считался сигнал (bid/mid баров)
    atr: float = 0.0
    sl_distance: float = 0.0       # расстояние до стопа в цене
    tp_distance: float = 0.0       # расстояние до тейка в цене
    spread_points: float = 0.0
    reason: str = ""
    filters: dict[str, bool] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=utc_now)

    @property
    def is_entry(self) -> bool:
        return self.side in ("buy", "sell")

    def describe(self) -> str:
        if self.side == "none":
            return f"{self.symbol}: НЕТ ВХОДА ({self.reason})"
        return (
            f"{self.symbol}: {self.side.upper()} p={self.probability:.3f} "
            f"ATR={self.atr:.5f} SL={self.sl_distance:.5f} TP={self.tp_distance:.5f} "
            f"({self.reason})"
        )


@dataclass
class SignalConfigView:
    min_probability: float = 0.58
    allow_short: bool = True


def market_filters(
    symbol_cfg: SymbolConfig,
    *,
    now: datetime,
    spread_points: float,
    atr_value: float,
    point: float,
) -> dict[str, bool]:
    """Быстрые проверки «стоит ли вообще смотреть на этот бар»."""
    windows = getattr(symbol_cfg, "windows", None) or [
        (symbol_cfg.session_start_utc, symbol_cfg.session_end_utc)
    ]
    return {
        "session": in_session_windows(now, windows),
        "spread": spread_points <= symbol_cfg.max_spread_points,
        "volatility": atr_value / point >= symbol_cfg.min_atr_points,
    }


def generate_signal(
    df: pd.DataFrame,
    model: SignalModel | None,
    symbol_cfg: SymbolConfig,
    *,
    now: datetime | None = None,
    spread_points: float = 0.0,
    min_probability: float = 0.58,
    allow_short: bool = True,
    require_model: bool = True,
    sentiment_block: bool = False,
    sentiment_note: str = "",
) -> Signal:
    """Полный сигнальный пайплайн (шаги 1–5)."""
    now = now or utc_now()
    symbol = symbol_cfg.name
    result = Signal(symbol=symbol, side="none", probability=0.0, spread_points=spread_points,
                    generated_at=now)

    # --- 1. данные и признаки -------------------------------------------------
    try:
        df = validate_ohlcv(df, symbol)
    except DataError as exc:
        result.reason = f"данные непригодны: {exc}"
        return result

    features = build_features(df, lookbacks=None, atr_period=symbol_cfg.atr_period)
    last = features.index[-1]
    atr_value = float(atr(df, symbol_cfg.atr_period).iloc[-1])
    point = float(df.attrs.get("point") or 0.0)
    if point <= 0:
        # точка не передана — оценим по числу знаков (для фильтров достаточно)
        point = float(10 ** -int(df.attrs.get("digits", 5)))

    result.atr = atr_value
    result.price = float(df["close"].iloc[-1])
    result.filters = market_filters(
        symbol_cfg, now=now, spread_points=spread_points, atr_value=atr_value, point=point
    )

    # --- 4. рыночные фильтры ---------------------------------------------------
    if not result.filters["session"]:
        describe = getattr(symbol_cfg, "session_text", None)
        session_text = describe() if callable(describe) else (
            f"{symbol_cfg.session_start_utc}–{symbol_cfg.session_end_utc}"
        )
        result.reason = f"вне торговых сессий ({session_text} UTC)"
        return result
    if not result.filters["spread"]:
        result.reason = (
            f"спред {spread_points:.1f} пт шире лимита {symbol_cfg.max_spread_points} пт"
        )
        return result
    if not result.filters["volatility"]:
        result.reason = (
            f"волатильность слишком низкая (ATR {atr_value / point:.1f} пт < "
            f"{symbol_cfg.min_atr_points} пт)"
        )
        return result

    # --- 2–3. модель ------------------------------------------------------------
    if model is None:
        if require_model:
            result.reason = "модель не загружена, а require_model_trained=true"
            return result
        result.reason = "модель не загружена (сигнал без ML)"
        return result

    proba = model.predict_proba_up(features)
    p_up = float(proba.iloc[-1])
    result.probability = round(p_up, 4)

    if p_up >= min_probability:
        side = "buy"
    elif allow_short and p_up <= (1.0 - min_probability):
        side = "sell"
    else:
        side = "none"
        result.reason = (
            f"вероятность {p_up:.3f} в «серой зоне» "
            f"(порог {min_probability:.2f} / {1 - min_probability:.2f})"
        )
        return result

    # --- 5. новостной фильтр ------------------------------------------------------
    if sentiment_block:
        result.side = "none"
        result.reason = f"новостной фильтр заблокировал вход: {sentiment_note or 'негативный фон'}"
        log_event("signal_blocked_by_news", symbol=symbol, side=side,
                  probability=result.probability, note=sentiment_note)
        return result

    # --- стопы -------------------------------------------------------------------
    sl_distance = symbol_cfg.sl_atr_mult * atr_value
    tp_distance = symbol_cfg.tp_atr_mult * atr_value
    result.side = side
    result.sl_distance = float(sl_distance)
    result.tp_distance = float(tp_distance)
    result.reason = (
        f"p={p_up:.3f}, ATR={atr_value:.5f}, R:R={symbol_cfg.reward_risk:.2f}"
    )
    log_event(
        "signal", symbol=symbol, side=side, probability=result.probability,
        atr=atr_value, sl_distance=sl_distance, tp_distance=tp_distance,
        bar_time=str(last),
    )
    return result


def stops_for_side(signal: Signal, side: str, entry_price: float) -> tuple[float, float]:
    """Посчитать абсолютные цены SL и TP от цены входа."""
    if side == "buy":
        return entry_price - signal.sl_distance, entry_price + signal.tp_distance
    return entry_price + signal.sl_distance, entry_price - signal.tp_distance


def signal_as_dict(signal: Signal) -> dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "side": signal.side,
        "probability": signal.probability,
        "price": signal.price,
        "atr": signal.atr,
        "sl_distance": signal.sl_distance,
        "tp_distance": signal.tp_distance,
        "reason": signal.reason,
        "filters": signal.filters,
        "generated_at": signal.generated_at.isoformat(),
    }
