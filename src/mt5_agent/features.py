"""Построение признаков (фич) и целевой переменной.

Жёсткие правила, без которых ML в трейдинге превращается в генератор
красивых графиков на истории и убытков на реальных деньгах:

1. **Никаких данных из будущего.** Все признаки на баре ``t`` считаются
   только из баров ``<= t``. Целевая переменная — из будущего, но она
   отделяется от фич "забором" (``embargo``) при обучении.
2. **Никакого look-ahead в нормализации.** Масштабирование живёт внутри
   sklearn-Pipeline и обучается только на train-части фолда.
3. **Порог входа должен окупать издержки.** Цель помечается «1» только если
   движение вперёд превышает стоимость спреда и комиссии — иначе модель будет
   учиться ловить шум, а деньги уйдут брокеру.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .exceptions import DataError

OHLCV_COLUMNS = ("open", "high", "low", "close")


# -----------------------------------------------------------------------------
# Индикаторы
# -----------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder). Считается только по прошлым барам."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def validate_ohlcv(df: pd.DataFrame, symbol: str = "?") -> pd.DataFrame:
    """Проверить, что данные пригодны для расчёта фич."""
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"{symbol}: в данных нет колонок {missing}")
    if len(df) < 100:
        raise DataError(f"{symbol}: слишком мало данных ({len(df)} баров)")
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    if df[list(OHLCV_COLUMNS)].isna().any().any():
        raise DataError(f"{symbol}: в OHLC есть пропуски — данные битые")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataError(f"{symbol}: есть нулевые/отрицательные цены")
    # дырки в данных (пропущенные бары) — не фатально, но важно знать
    return df


# -----------------------------------------------------------------------------
# Фичи
# -----------------------------------------------------------------------------

def build_features(
    df: pd.DataFrame,
    *,
    lookbacks: list[int] | None = None,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Собрать матрицу признаков. Индекс сохраняется (время баров)."""
    lookbacks = sorted(set(lookbacks or [1, 2, 3, 4, 8, 16, 32]))
    close = df["close"]
    feats = pd.DataFrame(index=df.index)

    # 1. Доходности за разные окна
    for lb in lookbacks:
        feats[f"ret_{lb}"] = close.pct_change(lb)

    # 2. Волатильность и ATR (в долях от цены — сравнимо между инструментами)
    atr_series = atr(df, atr_period)
    feats["atr_pct"] = atr_series / close
    for lb in (4, 16, 64):
        feats[f"vol_{lb}"] = close.pct_change().rolling(lb, min_periods=lb).std()

    # 3. Положение относительно средних
    for lb in (8, 16, 32, 64, 128):
        ema = close.ewm(span=lb, adjust=False, min_periods=lb).mean()
        feats[f"dist_ema_{lb}"] = (close - ema) / close

    # 4. RSI и его изменения
    r = rsi(close, atr_period)
    feats["rsi"] = (r - 50.0) / 50.0
    feats["rsi_change"] = r.diff(3) / 50.0

    # 5. Форма свечей (тело/тени) — сглаженные, чтобы не шумели
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    body = (df["close"] - df["open"]) / rng
    upper = (df["high"] - df[["open", "close"]].max(axis=1)) / rng
    lower = (df[["open", "close"]].min(axis=1) - df["low"]) / rng
    feats["body_ma4"] = body.rolling(4, min_periods=2).mean()
    feats["upper_wick_ma4"] = upper.rolling(4, min_periods=2).mean()
    feats["lower_wick_ma4"] = lower.rolling(4, min_periods=2).mean()

    # 6. Объём относительно среднего
    if "tick_volume" in df.columns:
        vol = df["tick_volume"].astype(float)
        vol_ma = vol.rolling(32, min_periods=8).mean().replace(0.0, np.nan)
        feats["volume_ratio"] = vol / vol_ma - 1.0

    # 7. Спред (если пришёл из терминала) — издержки на входе
    if "spread" in df.columns:
        spread = df["spread"].astype(float)
        feats["spread_rel"] = (spread / close.replace(0.0, np.nan)).fillna(0.0)
        feats["spread_vs_ma"] = spread / spread.rolling(64, min_periods=8).mean().replace(0.0, np.nan) - 1.0

    # 8. Время суток и день недели (циклично) — в UTC
    if isinstance(df.index, pd.DatetimeIndex):
        hours = df.index.hour + df.index.minute / 60.0
        feats["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
        feats["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)
        dow = df.index.dayofweek
        feats["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
        feats["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)

    # 9. Дистанция до максимума/минимума окна (пробой/откат)
    for lb in (16, 64):
        roll_max = df["high"].rolling(lb, min_periods=lb).max()
        roll_min = df["low"].rolling(lb, min_periods=lb).min()
        feats[f"pos_in_range_{lb}"] = (close - roll_min) / (roll_max - roll_min).replace(0.0, np.nan)

    # Чистим: inf -> nan, никаких бесконечностей в модели
    feats = feats.replace([np.inf, -np.inf], np.nan)
    return feats


def build_labels(
    df: pd.DataFrame,
    *,
    horizon_bars: int = 4,
    cost_pct: float = 0.0,
    atr_mult: float = 0.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Целевая переменная: «выгодно ли входить в лонг».

    ``target = 1``, если доходность за ``horizon_bars`` вперёд превышает порог,
    где порог = ``cost_pct`` (издержки: спред+комиссия в долях цены) плюс,
    опционально, ``atr_mult`` * ATR (запас «настоящего» движения).

    Возвращает DataFrame с колонками:
        ``target`` — 0/1
        ``forward_return`` — фактическая доходность (для отчётов и бэктеста)
    """
    if horizon_bars < 1:
        raise DataError("horizon_bars должен быть >= 1")
    close = df["close"]
    forward_return = close.shift(-horizon_bars) / close - 1.0
    threshold = pd.Series(float(cost_pct), index=df.index)
    if atr_mult > 0:
        atr_pct = atr(df, atr_period) / close
        threshold = threshold + atr_mult * atr_pct
    target = (forward_return > threshold).astype("float")
    target[forward_return.isna()] = np.nan
    return pd.DataFrame(
        {"target": target, "forward_return": forward_return, "threshold": threshold},
        index=df.index,
    )


def build_dataset(
    df: pd.DataFrame,
    *,
    lookbacks: list[int] | None = None,
    atr_period: int = 14,
    horizon_bars: int = 4,
    cost_pct: float = 0.0,
    atr_mult: float = 0.0,
    symbol: str = "?",
) -> pd.DataFrame:
    """Фичи + цель, выровненные по времени, без NaN-строк."""
    df = validate_ohlcv(df, symbol)
    feats = build_features(df, lookbacks=lookbacks, atr_period=atr_period)
    labels = build_labels(
        df, horizon_bars=horizon_bars, cost_pct=cost_pct, atr_mult=atr_mult, atr_period=atr_period
    )
    dataset = feats.join(labels)
    dataset["close"] = df["close"]
    dataset["atr"] = atr(df, atr_period)

    # Первые бары всегда «неполные»: длинные окна (ema_128, rolling_64) ещё не
    # набрались. Их нельзя ни обучать, ни предсказывать по ним — выкидываем.
    columns = feature_columns(dataset)
    complete = dataset[columns].notna().all(axis=1)
    dropped = int((~complete).sum())
    dataset = dataset[complete & dataset["target"].notna()]

    if len(dataset) < 200:
        raise DataError(
            f"{symbol}: после расчёта фич осталось {len(dataset)} пригодных строк "
            f"(отброшено {dropped}). Нужна более длинная история."
        )
    return dataset


def feature_columns(dataset: pd.DataFrame) -> list[str]:
    """Список колонок-признаков (без служебных)."""
    service = {"target", "forward_return", "threshold", "close", "atr"}
    return [c for c in dataset.columns if c not in service]


def cost_pct_from_spec(spread_points: float, point: float, price: float, commission_per_lot: float = 0.0,
                       spec_value_per_point_per_lot: float | None = None, volume_for_commission: float = 1.0) -> float:
    """Оценка издержек входа в долях цены (спред + комиссия).

    Используется как минимальный порог для цели обучения: движение меньше
    издержек — не сдел, а подарок брокеру.
    """
    if price <= 0:
        return 0.0
    spread_cost = spread_points * point / price
    commission_cost = 0.0
    if commission_per_lot > 0 and spec_value_per_point_per_lot:
        # комиссия за круг переводится в «пункты» через стоимость пункта
        points_per_lot = commission_per_lot / spec_value_per_point_per_lot
        commission_cost = points_per_lot * point / price
    return float(spread_cost + commission_cost)
