"""Логирование: в консоль (UTF-8) и в файл с ротацией.

Отдельно предусмотрен «журнал сделок» — events-лог в JSON Lines, который потом
легко читать скриптами (``logs/events.jsonl``).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

from .utils import UTC, atomic_write_text

_EVENTS_PATH: Path | None = None


def setup_logging(
    log_file: str | Path,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    events_file: str | Path | None = None,
) -> logging.Logger:
    """Настроить корневой логгер. Вызывать один раз при старте."""
    global _EVENTS_PATH
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("mt5_agent")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if events_file:
        _EVENTS_PATH = Path(events_file)
        _EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    return logger


def get_logger(name: str = "mt5_agent") -> logging.Logger:
    return logging.getLogger(name if name.startswith("mt5_agent") else f"mt5_agent.{name}")


def log_event(kind: str, **payload) -> None:
    """Записать событие (сделку, отказ, ошибку) в JSONL-журнал.

    Ничего не делает, если журнал не настроен — логи не должны ронять агента.
    """
    if _EVENTS_PATH is None:
        return
    record = {"ts": datetime.now(UTC).isoformat(), "kind": kind, **payload}
    try:
        with _EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:  # pragma: no cover
        pass


def write_report(path: str | Path, text: str) -> None:
    atomic_write_text(Path(path), text)
