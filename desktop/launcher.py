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
    # windowed (--noconsole) Windows builds have no stdout/stderr attached
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

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
    # explicit implementations: avoid lazy imports that a frozen build
    # might not bundle (the dashboard uses polling, no websockets needed)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            loop="asyncio", http="h11", ws="none")
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
        import traceback
        result_file = os.path.join(os.path.dirname(os.path.abspath(sys.executable
                                                    if getattr(sys, "frozen", False)
                                                    else __file__)),
                                   "selftest-result.txt")

        def finish(ok: bool, text: str) -> None:
            try:
                with open(result_file, "w", encoding="utf-8") as f:
                    f.write(("SELFTEST OK: " if ok else "SELFTEST FAILED: ")
                            + text)
            except OSError:
                pass
            print(("SELFTEST OK: " if ok else "SELFTEST FAILED: ") + text,
                  flush=True)
            os._exit(0 if ok else 1)

        try:
            r = _rq.get(f"{url}/api/status", timeout=10)
            assert r.status_code == 200, f"status {r.status_code}"
            d = r.json()
            assert "engine_state" in d and "mt5" in d and "version" in d
            assert _rq.get(f"{url}/", timeout=10).status_code == 200
            # on Windows the MT5 package MUST be bundled inside the exe;
            # this turns the smoke test into a real packaging verification
            import platform as _pl
            if _pl.system() == "Windows":
                assert d["mt5"]["available"] is True, (
                    "MetaTrader5 package is NOT bundled/loaded in the exe: "
                    + (d["mt5"]["detail"] or "no detail"))
            finish(True, f"dashboard 200, engine={d['engine_state']}, "
                         f"mt5={d['mt5']['status']}, "
                         f"mt5_available={d['mt5']['available']}, "
                         f"version={d['version']}, "
                         f"update_checked={d['update']['checked']}")
        except Exception:  # noqa: BLE001
            finish(False, traceback.format_exc(limit=8))

    # --- GUI --------------------------------------------------------------- #
    opened = False
    if not args.no_window:
        try:
            import webview  # pywebview — native window (WebView2 on Windows)
            win = webview.create_window(
                f"{APP_NAME} — prototip v{APP_VERSION}", url,
                width=1240, height=860)

            def _on_started() -> None:
                # open maximized (full screen)
                try:
                    win.maximize()
                except Exception:  # noqa: BLE001
                    pass

            webview.start(_on_started)  # blocks until the window is closed
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


def _crash_report() -> None:
    """Write any unhandled exception next to the executable so that CI (and
    users) can see WHY a windowed build died."""
    import traceback
    text = traceback.format_exc()
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "crash-report.txt"), "w",
                  encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    try:
        print(text, flush=True)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _crash_report()
        os._exit(3)
