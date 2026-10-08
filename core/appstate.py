"""Thread-safe snapshot of the live system state for the UI/API."""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional


class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "engine_state": "STOPPED",
            "mt5_status": "DISCONNECTED",
            "mt5_detail": "",
            "mt5_available": False,
            "account": None,
            "tick": None,
            "position": None,
            "symbol": "AUDCAD",
            "last_cycle": None,
            "skipped_reason": None,
        }

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)
