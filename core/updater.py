"""Update check performed at application startup.

Compares the running APP_VERSION with the latest `prototip-vX.Y` release
published on GitHub. The check runs in a background thread, never blocks
startup and never breaks the app when there is no internet — the result is
simply shown in the dashboard ("доступно обновление").
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import requests

from core.version import APP_VERSION

REPO = "Klavish777/Work-Day"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"


def parse_version(text: str) -> tuple:
    """'prototip-v1.0.2' / 'v1.1' / '1.0' -> (1, 0, 2)."""
    t = (text or "").strip().lower()
    for prefix in ("prototip-v", "prototip v", "prototip-", "prototip", "v"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    t = t.strip(" -_")
    parts: List[int] = []
    for chunk in t.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest_tag: str, current: str = APP_VERSION) -> bool:
    try:
        return parse_version(latest_tag) > parse_version(current)
    except Exception:  # noqa: BLE001 — never crash on version strings
        return False


def extract_latest(releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First (newest) release whose tag starts with 'prototip'."""
    for rel in releases or []:
        tag = str(rel.get("tag_name", ""))
        if tag.lower().startswith("prototip"):
            return rel
    return None


def check_update(timeout: int = 8) -> Dict[str, Any]:
    info: Dict[str, Any] = {"checked": True, "has_update": False,
                            "current": APP_VERSION, "latest": None,
                            "url": None, "error": None}
    try:
        resp = requests.get(RELEASES_URL, timeout=timeout,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            info["error"] = f"HTTP {resp.status_code}"
            return info
        rel = extract_latest(resp.json())
        if rel is not None:
            tag = rel["tag_name"]
            info["latest"] = tag
            info["has_update"] = is_newer(tag)
            info["url"] = rel.get("html_url")
    except requests.RequestException as exc:
        info["error"] = str(exc)
    return info


# Shared mutable state, refreshed by the background thread at startup.
UPDATE_STATE: Dict[str, Any] = {"checked": False, "has_update": False,
                                "current": APP_VERSION, "latest": None,
                                "url": None, "error": None}


def start_update_check() -> Dict[str, Any]:
    def worker() -> None:
        UPDATE_STATE.update(check_update())

    threading.Thread(target=worker, daemon=True,
                     name="UpdateCheck").start()
    return UPDATE_STATE
