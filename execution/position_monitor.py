"""Position Monitor — automatic close on profit target (and protective stop
in account currency).

This is the primary exit condition of the system: when floating profit
reaches the user-configured target (e.g. $0.30 - $0.80) the position is
closed automatically.
"""
from __future__ import annotations

from typing import Dict, Optional

from core.models import PositionView


class PositionMonitor:
    def evaluate(self, position: PositionView,
                 settings: Dict) -> Optional[str]:
        """Return a close reason, or None to keep the position."""
        target = float(settings.get("profit_target_usd", 0.5))
        if target > 0 and position.profit >= target:
            return (f"PROFIT_TARGET: floating profit ${position.profit:.2f} "
                    f">= target ${target:.2f}")

        stop = float(settings.get("stop_loss_usd", 0) or 0)
        if stop > 0 and position.profit <= -stop:
            return (f"STOP_LOSS_USD: floating loss ${position.profit:.2f} "
                    f"reached protective stop ${stop:.2f}")
        return None
