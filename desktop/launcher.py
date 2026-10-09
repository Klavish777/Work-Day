"""Work-Day AI Trader — Windows desktop application entry point (prototip).

What the .exe does:
  1. starts the FULL trading system in-process (agents, risk manager,
     MT5 gateway, journal);
  2. serves the dashboard on 127.0.0.1;
  3. checks GitHub for a newer "prototip" release (startup update check);
  4. opens the dashboard in a native window (pywebview) or, if that is
     unavailable, in the default browser.

CLI:
  WorkDayTrader-prototip-vX.Y.exe              normal start
  WorkDayTrader-prototip-vX.Y.exe --selftest   CI smoke test (no GUI)
  WorkDayTrader-prototip-vX.Y.exe --version    print version and exit
  --port N --host H                            override bind address
  --no-window                                  server + browser only
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

# allow running from source: python desktop/launcher.py
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Work-Day AI Trader (prototip)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--selftest", action="store_true",
                    help="start, verify the API answers, then exit")
    ap.add_argument("--no-window", action="store_true",
                    help="do not open a native window")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()

    from core.version import APP_NAME, APP_VERSION
    if args.version:
        print(f"{APP_NAME} — prototip v{APP_VERSION}")
        return

    import logging
    from core import paths
    from core.engine import TradingEngine
    from core.settings import SettingsManager
    from core.updater import UPDATE_STATE, start_update_check
    from database.storage import Storage
    from journal.trade_journal import TradeJournal
    from llm.factory import build_provider
    from mt5.gateway import MT5Gateway
    from ui.api import create_app

    paths.ensure_dirs()
    handlers = [logging.FileHandler(paths.LOG_FILE, encoding="utf-8")]
    if not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        handlers=handlers)
    log = logging.getLogger("workday.desktop")
    log.info("%s prototip v%s starting (frozen=%s)", APP_NAME, APP_VERSION,
             getattr(sys, "frozen", False))

    # --- startup update check (background, never blocks) --------------- #
    start_update_check()

    # --- trading system ------------------------------------------------- #
    settings_mgr = SettingsManager()
    settings = settings_mgr.get()
    host = args.host or "127.0.0.1"
    port = args.port or int(settings.get("ui", {}).get("port", 8080))

    storage = Storage()
    journal = TradeJournal(storage)
    gateway = MT5Gateway()
    provider = build_provider(settings.get("llm", {}))
    engine = TradingEngine(settings_mgr, gateway, journal, provider)
    engine.start()

    import uvicorn
    app = create_app(engine, settings_mgr, journal, gateway,
                     update_state=UPDATE_STATE)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="UIServer").start()

    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    if not wait_for_port(probe_host, port, 25):
        log.error("Dashboard server did not start on port %s", port)
        if args.selftest:
            os._exit(2)
        # fall through: window will show the error screen with retry

    url = f"http://127.0.0.1:{port}"

    # --- CI smoke test --------------------------------------------------- #
    if args.selftest:
        import requests as _rq
        try:
            r = _rq.get(f"{url}/api/status", timeout=10)
            assert r.status_code == 200, f"status {r.status_code}"
            d = r.json()
            assert "engine_state" in d and "mt5" in d and "version" in d
            assert _rq.get(f"{url}/", timeout=10).status_code == 200
            print(f"SELFTEST OK: dashboard 200, engine={d['engine_state']}, "
                  f"mt5={d['mt5']['status']}, version={d['version']}, "
                  f"update_checked={d['update']['checked']}", flush=True)
            os._exit(0)
        except Exception as exc:  # noqa: BLE001
            print(f"SELFTEST FAILED: {exc}", flush=True)
            os._exit(1)

    # --- GUI --------------------------------------------------------------- #
    opened = False
    if not args.no_window:
        try:
            import webview  # pywebview — native window (WebView2 on Windows)
            webview.create_window(f"{APP_NAME} — prototip v{APP_VERSION}",
                                  url, width=1240, height=860)
            webview.start()   # blocks until the window is closed
            opened = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Native window unavailable (%s) — opening browser", exc)
    if not opened:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    engine.shutdown()
    server.should_exit = True
    os._exit(0)


if __name__ == "__main__":
    main()
