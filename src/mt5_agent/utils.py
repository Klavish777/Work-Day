"""Мелкие утилиты: время, форматирование, атомарная запись файлов, JSON-состояние."""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


# -----------------------------------------------------------------------------
# Время
# -----------------------------------------------------------------------------

def utc_now() -> datetime:
    """Текущее время в UTC (aware)."""
    return datetime.now(UTC)


def parse_hhmm(value: str) -> tuple[int, int]:
    """'07:30' -> (7, 30)."""
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Ожидался формат HH:MM, получено {value!r}")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Некорректное время: {value!r}")
    return hh, mm


def in_session(now_utc: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    """Внутри ли ``now`` торговой сессии [start, end) в UTC.

    Поддерживает сессии через полночь (например 22:00 -> 03:00).
    """
    sh, sm = parse_hhmm(start_hhmm)
    eh, em = parse_hhmm(end_hhmm)
    minutes_now = now_utc.hour * 60 + now_utc.minute
    minutes_start = sh * 60 + sm
    minutes_end = eh * 60 + em
    if minutes_start == minutes_end:
        return True
    if minutes_start < minutes_end:
        return minutes_start <= minutes_now < minutes_end
    # через полночь
    return minutes_now >= minutes_start or minutes_now < minutes_end


def in_session_windows(now_utc: datetime, windows: list[tuple[str, str]]) -> bool:
    """Внутри ли ``now`` хотя бы одного из торговых окон.

    Нужно для инструментов, у которых ликвидность приходит «волнами» из разных
    регионов. Пример — AUDCAD: активная торговля идёт в токийскую сессию (со стороны
    AUD) и в нью-йоркскую (со стороны CAD), а между ними и во время ролловера спред
    расширяется. Одним окном это не описать, поэтому их несколько.
    """
    if not windows:
        return True
    return any(in_session(now_utc, start, end) for start, end in windows)


def describe_windows(windows: list[tuple[str, str]] | None, start: str = "", end: str = "") -> str:
    """Человекочитаемое описание торговых окон для логов и сообщений."""
    if not windows:
        return f"{start}–{end}"
    return ", ".join(f"{window_start}–{window_end}" for window_start, window_end in windows)


def session_ends_at(now_utc: datetime, start_hhmm: str, end_hhmm: str) -> datetime | None:
    """Когда закончится текущая сессия (для тайм-стопа по времени)."""
    if not in_session(now_utc, start_hhmm, end_hhmm):
        return None
    eh, em = parse_hhmm(end_hhmm)
    candidate = now_utc.replace(hour=eh, minute=em, second=0, microsecond=0)
    if candidate <= now_utc:
        candidate += timedelta(days=1)
    return candidate


def day_key(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%d")


def iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.astimezone(UTC).isoformat()


def fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ")


def fmt_pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}%"


# -----------------------------------------------------------------------------
# Файлы / состояние
# -----------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str) -> None:
    """Запись файла через временный файл + os.replace (не оставит половину файла)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:  # pragma: no cover
                pass


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(utc_now().isoformat(), encoding="utf-8")


def file_age_sec(path: Path) -> float | None:
    if not path.exists():
        return None
    return (utc_now() - datetime.fromtimestamp(path.stat().st_mtime, UTC)).total_seconds()


# -----------------------------------------------------------------------------
# Числа
# -----------------------------------------------------------------------------

def floor_to_step(value: float, step: float) -> float:
    """Округлить ВНИЗ к шагу объёма (округление вверх увеличит риск!)."""
    if step <= 0:
        return value
    return math.floor(round(value / step, 8)) * step


def round_price(price: float, digits: int) -> float:
    return round(price, int(digits))


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return default if not b else a / b


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
