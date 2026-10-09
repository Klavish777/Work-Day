"""Filesystem layout of the application.

Two modes:
  * normal (python main.py)   — everything lives in the repository root;
  * frozen (PyInstaller .exe) — bundled assets (ui/static) come from the
    temporary extraction dir, while user data (config/data/logs) is kept
    NEXT TO THE EXE so it survives updates.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if is_frozen():
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    BASE_DIR = BUNDLE_DIR

CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BUNDLE_DIR / "ui" / "static"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_SETTINGS_FILE = CONFIG_DIR / "settings.default.json"
DB_FILE = DATA_DIR / "workday.db"
MEMORY_FILE = DATA_DIR / "agent_memory.json"
LOG_FILE = LOGS_DIR / "app.log"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
