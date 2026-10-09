"""Settings manager.

All user-editable parameters live in config/settings.json. The manager
validates and clamps every value; AI agents and the LLM have NO write
access to settings — only the UI / REST API (i.e. the human user) does.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, List, Tuple

from core.paths import SETTINGS_FILE, DEFAULT_SETTINGS_FILE, ensure_dirs

ALLOWED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

REAL_CONFIRMATION_PHRASE = "REAL TRADING USES REAL MONEY."

# v2: demo-friendly permissions (the user explicitly granted full
# autonomy on demo accounts): lower AI confidence bar, wider spread limit.
SETTINGS_SCHEMA_VERSION = 2

DEFAULT_SETTINGS: Dict[str, Any] = {
    "settings_schema": SETTINGS_SCHEMA_VERSION,
    # --- instrument ---
    "symbol": "AUDCAD",
    # --- account mode ---
    "mode": "DEMO",                    # DEMO | REAL
    "real_confirmed": False,           # must be explicitly confirmed by the user
    # --- engine ---
    "auto_trading_enabled": True,      # master permission for automated trading
    "engine_cycle_sec": 5,
    # --- position sizing (user controlled only; AI never changes these) ---
    "lot_size": 0.01,
    "max_lot": 0.10,
    # --- profit / loss targets (account currency) ---
    "min_profit_usd": 0.30,            # lower bound of the target range
    "max_profit_usd": 0.80,            # upper bound of the default range
    "profit_target_usd": 0.50,         # auto-close when floating profit >= this
    "stop_loss_usd": 1.00,             # equity protection: close when loss >= this (0 = off)
    # --- optional explicit price TP/SL in points (0 = derived from $ targets) ---
    "take_profit_points": 0,
    "stop_loss_points": 0,
    # --- market filters ---
    "max_spread_points": 60,
    "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
    "min_confidence": 0.35,            # required AI confidence level
    # NOTE: the core strategy is profit-target scalping ($0.30-$0.80 target,
    # ~$1 protective stop), so R:R is normally < 1. Set 1.0+ only if you
    # switch to classic trend entries with price-based TP/SL.
    "min_risk_reward": 0.3,
    "require_sl": True,
    # --- daily limits ---
    "max_trades_per_day": 10,
    "max_daily_loss_usd": 5.0,
    # --- execution ---
    "deviation_points": 20,
    "magic_number": 20261008,
    # --- MT5 connection ---
    "mt5": {
        "terminal_path": "",           # e.g. C:\\Program Files\\MetaTrader 5\\terminal64.exe
        "login": "",
        "password": "",
        "server": "",
    },
    # --- Kristina: the auto-trading avatar widget ---
    "kristina": {
        "background": "mountains",     # mountains | cave | snow
        "tool": "pickaxe",             # pickaxe | shovel | drill
    },
    # --- LLM configuration (swappable provider) ---
    "llm": {
        "provider": "off",             # off | openai | openai_compatible
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "api_key_env": "",             # alternative: name of env var holding the key
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 700,
        "timeout_sec": 30,
        "agents": {
            "market_analyst": True,
            "strategy_trader": True,
            "risk_analyst": True,
            "central": True,
        },
    },
    # --- UI server ---
    "ui": {"host": "0.0.0.0", "port": 8080},
}


class SettingsManager:
    """Thread-safe load / validate / save of user settings."""

    def __init__(self, path=None, default_path=None):
        self._path = path or SETTINGS_FILE
        self._default_path = default_path or DEFAULT_SETTINGS_FILE
        self._lock = threading.RLock()
        self._settings: Dict[str, Any] = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        ensure_dirs()
        with self._lock:
            if os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    legacy = "settings_schema" not in data
                    merged = copy.deepcopy(DEFAULT_SETTINGS)
                    self._deep_update(merged, data)
                    self._settings, _ = self._validate(merged)
                    if legacy:
                        self._migrate()
                except (json.JSONDecodeError, OSError):
                    self._settings = copy.deepcopy(DEFAULT_SETTINGS)
            else:
                # first run: seed from bundled defaults (or built-in defaults)
                if os.path.exists(self._default_path):
                    try:
                        with open(self._default_path, "r", encoding="utf-8") as f:
                            seed = json.load(f)
                        merged = copy.deepcopy(DEFAULT_SETTINGS)
                        self._deep_update(merged, seed)
                        self._settings = merged
                    except (json.JSONDecodeError, OSError):
                        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
                self.save()

    def _migrate(self) -> None:
        """One-time upgrade of pre-v2 user settings to the demo-friendly
        permissions: lower confidence bar, wider spread limit.
        The user explicitly asked for full bot autonomy on demo accounts."""
        self._settings["min_confidence"] = 0.35
        self._settings["max_spread_points"] = 60.0
        self._settings["settings_schema"] = SETTINGS_SCHEMA_VERSION
        self.save()

    def save(self) -> None:
        ensure_dirs()
        with self._lock:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    # ------------------------------------------------------------------ #
    def update(self, patch: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Apply a user patch. Returns (ok, errors, applied_settings).

        Mode switching to REAL additionally requires the confirmation phrase.
        """
        with self._lock:
            candidate = copy.deepcopy(self._settings)
            self._deep_update(candidate, patch)

            # REAL mode requires explicit confirmation phrase in the patch.
            if candidate.get("mode") == "REAL" and self._settings.get("mode") != "REAL":
                phrase = patch.get("real_confirm", "")
                if phrase != REAL_CONFIRMATION_PHRASE:
                    return False, [
                        "Switching to REAL requires confirmation phrase: "
                        f"'{REAL_CONFIRMATION_PHRASE}'"
                    ], self.get()
            if candidate.get("mode") == "DEMO":
                candidate["real_confirmed"] = False

            # Demo/live servers are hard-linked to the account mode:
            # any server name containing "demo" => DEMO,
            # any server name containing "live" => REAL (confirmed).
            # Covers Bybit-Demo / Bybit-Live*, MetaQuotes-Demo, etc.
            server_name = str(candidate.get("mt5", {}).get("server", "")
                              ).strip().lower()
            if "live" in server_name and "demo" not in server_name:
                if candidate.get("mode") != "REAL":
                    return False, [
                        "Сервер реальный (в имени есть 'Live'): переключите "
                        "режим в REAL с подтверждением."
                    ], self.get()
            elif "demo" in server_name:
                candidate["mode"] = "DEMO"
                candidate["real_confirmed"] = False

            validated, errors = self._validate(candidate)
            if errors:
                return False, errors, self.get()
            if validated.get("mode") == "REAL":
                validated["real_confirmed"] = True
            self._settings = validated
            self.save()
            return True, [], self.get()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                SettingsManager._deep_update(dst[k], v)
            else:
                dst[k] = v

    @staticmethod
    def _validate(s: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        errors: List[str] = []

        def num(key: str, lo: float, hi: float) -> None:
            try:
                v = float(s.get(key, 0))
            except (TypeError, ValueError):
                errors.append(f"{key}: must be a number")
                return
            if not (lo <= v <= hi):
                errors.append(f"{key}: must be within [{lo}, {hi}]")
            s[key] = v

        if s.get("symbol"):
            s["symbol"] = str(s["symbol"]).upper().replace("/", "")[:16]
        else:
            errors.append("symbol: required")

        if s.get("mode") not in ("DEMO", "REAL"):
            errors.append("mode: must be DEMO or REAL")

        num("lot_size", 0.01, 100.0)
        num("max_lot", 0.01, 100.0)
        if not errors and s["lot_size"] > s["max_lot"]:
            errors.append("lot_size: cannot exceed max_lot")

        num("profit_target_usd", 0.05, 10000.0)
        num("min_profit_usd", 0.05, 10000.0)
        num("max_profit_usd", 0.05, 10000.0)
        num("stop_loss_usd", 0.0, 100000.0)
        num("take_profit_points", 0, 100000)
        num("stop_loss_points", 0, 100000)
        num("max_spread_points", 1, 1000)
        num("min_confidence", 0.0, 1.0)
        num("min_risk_reward", 0.0, 10.0)
        num("engine_cycle_sec", 2, 600)
        num("deviation_points", 0, 200)
        num("max_trades_per_day", 1, 1000)
        num("max_daily_loss_usd", 0.0, 1000000.0)

        tfs = s.get("timeframes")
        if isinstance(tfs, list) and tfs and all(t in ALLOWED_TIMEFRAMES for t in tfs):
            # keep canonical order
            s["timeframes"] = [t for t in ALLOWED_TIMEFRAMES if t in tfs]
        else:
            errors.append(f"timeframes: must be a non-empty subset of {ALLOWED_TIMEFRAMES}")

        s["auto_trading_enabled"] = bool(s.get("auto_trading_enabled", False))
        s["require_sl"] = bool(s.get("require_sl", True))

        kr = s.get("kristina") or {}
        s["kristina"] = {
            "background": kr.get("background")
            if kr.get("background") in ("mountains", "cave", "snow")
            else "mountains",
            "tool": kr.get("tool")
            if kr.get("tool") in ("pickaxe", "shovel", "drill")
            else "pickaxe",
        }

        mt5cfg = s.get("mt5") or {}
        s["mt5"] = {
            "terminal_path": str(mt5cfg.get("terminal_path", "")),
            "login": str(mt5cfg.get("login", "")),
            "password": str(mt5cfg.get("password", "")),
            "server": str(mt5cfg.get("server", "")),
        }

        llm = s.get("llm") or {}
        if llm.get("provider") not in ("off", "openai", "openai_compatible"):
            errors.append("llm.provider: must be one of off | openai | openai_compatible")
        agents = llm.get("agents") or {}
        s["llm"] = {
            "provider": llm.get("provider", "off"),
            "base_url": str(llm.get("base_url", "")),
            "api_key": str(llm.get("api_key", "")),
            "api_key_env": str(llm.get("api_key_env", "")),
            "model": str(llm.get("model", "gpt-4o-mini")),
            "temperature": max(0.0, min(2.0, float(llm.get("temperature", 0.1) or 0.0))),
            "max_tokens": int(max(100, min(4000, int(llm.get("max_tokens", 700) or 700)))),
            "timeout_sec": int(max(5, min(300, int(llm.get("timeout_sec", 30) or 30)))),
            "agents": {
                "market_analyst": bool(agents.get("market_analyst", True)),
                "strategy_trader": bool(agents.get("strategy_trader", True)),
                "risk_analyst": bool(agents.get("risk_analyst", True)),
                "central": bool(agents.get("central", True)),
            },
        }

        try:
            s["settings_schema"] = int(s.get("settings_schema",
                                             SETTINGS_SCHEMA_VERSION))
        except (TypeError, ValueError):
            s["settings_schema"] = SETTINGS_SCHEMA_VERSION

        ui = s.get("ui") or {}
        s["ui"] = {
            "host": str(ui.get("host", "0.0.0.0")),
            "port": int(max(1, min(65535, int(ui.get("port", 8080) or 8080)))),
        }

        return s, errors
