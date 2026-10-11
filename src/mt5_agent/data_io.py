"""Хранение данных: CSV-снимки котировок и режим «песочницы» без терминала.

Зачем это нужно:

* CSV-снимок позволяет **перепроверять сигналы и модели офлайн**, не подключаясь
  к терминалу (и, что важно, не рискуя ничем).
* Синтетические данные (:func:`synthetic_bars`) дают возможность прогнать весь
  пайплайн (фичи → модель → бэктест → отчёты) на любом компьютере ещё до того,
  как ты купишь VPS. Это проверка КОДА, а не стратегии: на выдуманных данных
  делать выводы о доходности нельзя — в отчётах это всегда указано явно.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .exceptions import DataError
from .specs import SymbolSpec, spec_from_dict
from .utils import UTC

CSV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]

# Типовые уровни цен — нужны только синтетике, чтобы числа были «похожи на правду»
# (иначе модель увидит нереалистичные относительные движения).
_TYPICAL_PRICES = {
    "EURUSD": 1.10, "GBPUSD": 1.27, "USDJPY": 150.0, "USDCHF": 0.88,
    "AUDUSD": 0.66, "NZDUSD": 0.61, "USDCAD": 1.36,
    "AUDCAD": 0.91, "AUDNZD": 1.08, "NZDCAD": 0.83, "EURGBP": 0.85,
    "XAUUSD": 2350.0,
}

_TIMEFRAME_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12,
    "M15": 15, "M20": 20, "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "H12": 720, "D1": 1440, "W1": 10080,
}


class DataStore:
    """Локальное хранилище баров в ``data/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -- пути ---------------------------------------------------------------------
    def csv_path(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / f"{symbol.upper()}_{timeframe.upper()}.csv"

    def specs_path(self) -> Path:
        return self.data_dir / "symbol_specs.yaml"

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self.csv_path(symbol, timeframe).exists()

    # -- бары -----------------------------------------------------------------------
    def save_bars(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Path:
        path = self.csv_path(symbol, timeframe)
        out = df.copy()
        out = out.reset_index().rename(columns={out.index.name or "index": "time"})
        if "time" not in out.columns:
            out = out.rename(columns={out.columns[0]: "time"})
        out["time"] = pd.to_datetime(out["time"], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
        for column in CSV_COLUMNS:
            if column not in out.columns:
                out[column] = 0.0
        out = out[CSV_COLUMNS]
        out.to_csv(path, index=False)
        return path

    def load_bars(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.csv_path(symbol, timeframe)
        if not path.exists():
            raise DataError(
                f"Нет файла с барами {path}. Сначала скачай данные командой "
                f"`python -m mt5_agent fetch --symbol {symbol} --timeframe {timeframe}` "
                "или запусти песочницу с синтетическими данными (--synthetic)."
            )
        frame = pd.read_csv(path)
        if "time" not in frame.columns:
            raise DataError(f"{path}: нет колонки time")
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame = frame.set_index("time").sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        for column in ("tick_volume", "spread", "real_volume"):
            if column not in frame.columns:
                frame[column] = 0.0
        frame.attrs["symbol"] = symbol.upper()
        frame.attrs["timeframe"] = timeframe.upper()
        return frame

    # -- спецификации символов ---------------------------------------------------------
    def load_specs(self) -> dict[str, SymbolSpec]:
        path = self.specs_path()
        if not path.exists():
            return {}
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        symbols = raw.get("symbols") or {}
        return {name.upper(): spec_from_dict({"name": name.upper(), **data})
                for name, data in symbols.items()}

    def save_specs(self, specs: dict[str, SymbolSpec]) -> Path:
        import yaml

        path = self.specs_path()
        payload = {
            "comment": "Снято с твоего брокера командой `python -m mt5_agent fetch --save-specs`. "
                       "Не подменяй значения вручную — от них зависит размер риска.",
            "symbols": {
                name: {k: v for k, v in spec.as_dict().items() if k != "name"}
                for name, spec in specs.items()
            },
        }
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path


def to_mt5_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Привести DataFrame из MT5 (copy_rates) к нашему виду (на случай иных названий)."""
    frame = df.copy()
    if isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "time"})
    if "time" not in frame.columns and "time_" in frame.columns:
        frame = frame.rename(columns={"time_": "time"})
    return frame


def synthetic_bars(
    symbol: str,
    timeframe: str,
    bars: int = 20_000,
    *,
    spec: SymbolSpec | None = None,
    seed: int = 7,
    start: datetime | None = None,
    trend_strength: float = 0.0,
) -> pd.DataFrame:
    """Синтетические бары для проверки КОДА (не стратегии!).

    Модель: случайное блуждание с кластеризацией волатильности, внутридневной
    сезонностью, честным спредом и округлением до ``digits``. В конце данных
    намеренно добавляется смена режима волатильности — так проще увидеть, как
    стратегия ломается при изменении рынка.
    """
    spec = spec or SymbolSpec(name=symbol.upper())
    minutes = _TIMEFRAME_MINUTES.get(timeframe.upper(), 15)
    rng = np.random.default_rng(seed)
    start = start or (datetime(2023, 1, 2, tzinfo=UTC))

    # волатильность кластерами + смена режима на 2/3 истории
    base_vol = 0.00045 if "JPY" not in symbol.upper() else 0.0005
    vol_process = np.empty(bars)
    vol = base_vol
    for i in range(bars):
        target = base_vol * (1.0 if i < bars * 2 / 3 else 1.9)
        vol = 0.97 * vol + 0.03 * target + rng.normal(0, base_vol * 0.05)
        vol_process[i] = abs(vol)

    returns = rng.normal(trend_strength, vol_process)
    # внутридневная сезонность (лондон+нью-йорк активнее)
    index = pd.date_range(start=start, periods=bars, freq=f"{minutes}min", tz="UTC")
    hours = index.hour.to_numpy()
    session = 1.0 + 0.5 * np.exp(-((hours - 10) ** 2) / 12.0) + 0.4 * np.exp(-((hours - 15) ** 2) / 10.0)
    returns = returns * session

    prices = _TYPICAL_PRICES.get(symbol.upper(), 1.0)
    closes = prices * np.exp(np.cumsum(returns))
    opens = np.concatenate([[prices], closes[:-1]])
    wick = np.abs(rng.normal(0, 1, bars)) * vol_process * prices
    highs = np.maximum(opens, closes) + wick
    lows = np.minimum(opens, closes) - wick * 0.9
    digits = spec.digits
    frame = pd.DataFrame(
        {
            "open": np.round(opens, digits),
            "high": np.round(highs, digits),
            "low": np.round(lows, digits),
            "close": np.round(closes, digits),
            "tick_volume": rng.integers(200, 3000, bars).astype(float),
            "spread": np.full(bars, 12.0),
            "real_volume": 0.0,
        },
        index=index,
    )
    # иногда спред расширяется (азиатская сессия) — проверка фильтра спреда
    frame.loc[(frame.index.hour < 6), "spread"] = 28.0
    frame.attrs["symbol"] = symbol.upper()
    frame.attrs["timeframe"] = timeframe.upper()
    frame.attrs["synthetic"] = True
    return frame
