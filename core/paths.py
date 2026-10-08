"""Filesystem layout of the application.

Everything is resolved relative to the repository root so the app can be
started from any working directory.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_SETTINGS_FILE = CONFIG_DIR / "settings.default.json"
DB_FILE = DATA_DIR / "workday.db"
LOG_FILE = LOGS_DIR / "app.log"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
