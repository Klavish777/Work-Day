"""Entry point of Work-Day AI Trader.

Starts the trading engine thread and the dashboard/API server.

Usage:
    python main.py [--host 0.0.0.0] [--port 8080]
"""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from core import paths
from core.engine import TradingEngine
from core.settings import SettingsManager
from database.storage import Storage
from journal.trade_journal import TradeJournal
from llm.factory import build_provider
from mt5.gateway import MT5Gateway
from ui.api import create_app


def setup_logging() -> None:
    paths.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(paths.LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Work-Day AI Trader")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("workday.main")

    settings_mgr = SettingsManager()
    storage = Storage()
    journal = TradeJournal(storage)
    gateway = MT5Gateway()
    provider = build_provider(settings_mgr.get()["llm"])

    engine = TradingEngine(settings_mgr, gateway, journal, provider)
    engine.start()

    app = create_app(engine, settings_mgr, journal, gateway)

    ui_cfg = settings_mgr.get()["ui"]
    host = args.host or ui_cfg.get("host", "0.0.0.0")
    port = args.port or int(ui_cfg.get("port", 8080))

    log.info("Dashboard: http://%s:%s (LLM provider: %s)", host, port,
             provider.name)
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        engine.shutdown()


if __name__ == "__main__":
    main()
