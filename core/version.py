"""Application version — the single source of truth.

Convention (прототип):
  * APP_VERSION is bumped on EVERY fix (1.0 -> 1.1 -> 2.0 ...);
  * release tag on GitHub:        prototip-v<APP_VERSION>
  * Windows executable name:      WorkDayTrader-prototip-v<APP_VERSION>.exe
  * the app checks these releases at startup and offers an update.
"""

APP_NAME = "Work-Day AI Trader"
APP_VERSION = "1.7"


def label() -> str:
    return f"prototip v{APP_VERSION}"
