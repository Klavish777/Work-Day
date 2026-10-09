"""REST API + dashboard serving.

The UI is the ONLY way the user talks to the system. Settings changes go
through the SettingsManager validation; switching to REAL requires the
explicit confirmation phrase.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import strategy_library as slib
from core.paths import STATIC_DIR
from core.settings import REAL_CONFIRMATION_PHRASE
from core.version import APP_NAME, APP_VERSION


class SettingsPatch(BaseModel):
    settings: Dict[str, Any] = {}
    real_confirm: str = ""


class MT5ConnectBody(BaseModel):
    terminal_path: str = ""
    login: str = ""
    password: str = ""
    server: str = ""


class ManualTradeBody(BaseModel):
    side: str = "BUY"


def create_app(engine, settings_mgr, journal, gateway,
               update_state=None) -> FastAPI:
    app = FastAPI(title="Work-Day AI Trader", version=APP_VERSION)

    # ------------------------------------------------------------------ #
    @app.get("/api/status")
    def status():
        s = settings_mgr.get()
        snap = engine.state.snapshot()
        masked = dict(s)
        mt5cfg = dict(masked.get("mt5", {}))
        mt5cfg["password_saved"] = bool(mt5cfg.get("password"))
        mt5cfg["password"] = ""
        masked["mt5"] = mt5cfg
        llm_cfg = dict(masked.get("llm", {}))
        if llm_cfg.get("api_key"):
            llm_cfg["api_key"] = "******"
        masked["llm"] = llm_cfg
        return {
            "engine_state": snap.get("engine_state"),
            "mt5": {"status": snap.get("mt5_status"),
                    "detail": snap.get("mt5_detail"),
                    "available": snap.get("mt5_available")},
            "account": snap.get("account"),
            "tick": snap.get("tick"),
            "position": snap.get("position"),
            "symbol": snap.get("symbol") or s.get("symbol"),
            "last_cycle": snap.get("last_cycle"),
            "skipped_reason": snap.get("skipped_reason"),
            "stats": journal.stats(),
            "settings": masked,
            "llm_provider": engine.provider.name,
            "real_phrase": REAL_CONFIRMATION_PHRASE,
            "app_name": APP_NAME,
            "version": APP_VERSION,
            "last_connect": getattr(engine, "last_connect", None),
            "agent_memory": getattr(engine, "memory", None) and
                            engine.memory.summary(),
            "strategy_catalog": slib.CATALOG,
            "update": dict(update_state) if update_state is not None else
                      {"checked": False, "has_update": False},
        }

    @app.get("/api/version")
    def version():
        return {"app_name": APP_NAME, "version": APP_VERSION,
                "update": dict(update_state) if update_state is not None
                else None}

    # ------------------------------------------------------------------ #
    @app.post("/api/engine/start")
    def engine_start():
        engine.cmd_start()
        return {"ok": True, "engine_state": "RUNNING"}

    @app.post("/api/engine/stop")
    def engine_stop():
        engine.cmd_stop()
        return {"ok": True, "engine_state": "STOPPED"}

    @app.post("/api/engine/pause")
    def engine_pause():
        engine.cmd_pause()
        return {"ok": True, "engine_state": "PAUSED"}

    @app.post("/api/position/close")
    def close_position():
        engine.cmd_close_position()
        return {"ok": True}

    @app.post("/api/position/close-all")
    def close_all():
        engine.cmd_close_all()
        return {"ok": True}

    @app.get("/api/quick")
    def quick():
        """Ultra-light snapshot for the 100 ms dashboard refresh loop:
        live tick, balance/equity and the open position — no heavy work."""
        snap = engine.state.snapshot()
        return {
            "engine_state": snap.get("engine_state"),
            "mt5_status": snap.get("mt5_status"),
            "tick": snap.get("tick"),
            "account": snap.get("account"),
            "position": snap.get("position"),
            "mode": (settings_mgr.get().get("mode") or "DEMO"),
        }

    @app.get("/api/chart")
    def chart():
        """Live mid-price history for the dashboard chart."""
        hist = getattr(engine, "tick_history", None)
        t: list = []
        p: list = []
        if hist:
            for ts, mid in list(hist):
                t.append(round(ts, 2))
                p.append(round(mid, 5))
        snap = engine.state.snapshot()
        pos = snap.get("position") or {}
        return {
            "t": t,
            "p": p,
            "entry": pos.get("open_price"),
            "side": pos.get("side"),
            "symbol": snap.get("symbol") or "AUDCAD",
        }

    @app.get("/api/chart/candles")
    def chart_candles(tf: str = "S5", count: int = 400):
        """Candles for the chart timeframe selector.
        S1/S5/S15/S30 are aggregated from live ticks; M1..H4 come from MT5."""
        return engine.chart_candles(tf, count)

    @app.post("/api/trade/manual")
    def manual_trade(body: ManualTradeBody):
        """Manual BUY/SELL trading button of the trading bot."""
        side = (body.side or "").upper()
        if side not in ("BUY", "SELL"):
            raise HTTPException(400, "side must be BUY or SELL")
        return engine.cmd_manual_trade(side)

    # ------------------------------------------------------------------ #
    @app.post("/api/mt5/connect")
    def mt5_connect(body: MT5ConnectBody):
        # merge with saved credentials: empty fields reuse the remembered
        # values; a non-empty password is remembered for next launches.
        saved = dict(settings_mgr.get().get("mt5", {}))
        if body.terminal_path:
            saved["terminal_path"] = body.terminal_path
        if body.login:
            saved["login"] = body.login
        if body.server:
            saved["server"] = body.server
        if body.password:
            saved["password"] = body.password
        ok, errors, _ = settings_mgr.update({"mt5": saved})
        if not ok:
            raise HTTPException(400, "; ".join(errors))
        engine.cmd_connect_mt5(settings_mgr.get()["mt5"])
        return {"ok": True, "queued": True}

    @app.post("/api/mt5/disconnect")
    def mt5_disconnect():
        engine.cmd_disconnect_mt5()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    @app.get("/api/settings")
    def get_settings():
        return settings_mgr.get()

    @app.post("/api/settings")
    def post_settings(patch: SettingsPatch):
        payload = dict(patch.settings or {})
        if patch.real_confirm:
            payload["real_confirm"] = patch.real_confirm
        ok, errors, applied = settings_mgr.update(payload)
        if not ok:
            return JSONResponse({"ok": False, "errors": errors}, status_code=400)
        # keep the engine interval live; LLM provider is rebuilt on the fly
        return {"ok": True, "settings": applied}

    # ------------------------------------------------------------------ #
    @app.get("/api/journal/trades")
    def journal_trades(limit: int = 50):
        return journal.recent_trades(limit)

    @app.get("/api/journal/decisions")
    def journal_decisions(limit: int = 30):
        return journal.recent_decisions(limit)

    @app.get("/api/journal/stats")
    def journal_stats():
        return journal.stats()

    # ------------------------------------------------------------------ #
    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(os.path.join(STATIC_DIR, "manifest.webmanifest"),
                            media_type="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        return FileResponse(os.path.join(STATIC_DIR, "sw.js"),
                            media_type="application/javascript")

    return app
