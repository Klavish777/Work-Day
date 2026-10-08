"""Shared data models of the trading system.

These dataclasses are the ONLY way data moves between modules:
market gateway -> agents -> central LLM -> risk manager -> execution engine.
Nothing here touches the MT5 API directly.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"


class EngineState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class Mode(str, Enum):
    DEMO = "DEMO"
    REAL = "REAL"


def _ts() -> float:
    return time.time()


def new_cycle_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    spread_points: float
    time: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AccountView:
    login: int = 0
    server: str = ""
    currency: str = ""
    balance: float = 0.0
    equity: float = 0.0
    margin_free: float = 0.0
    profit: float = 0.0
    is_demo: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PositionView:
    ticket: int
    symbol: str
    side: str            # "BUY" | "SELL"
    lot: float
    open_price: float
    open_time: int
    profit: float        # floating P/L in account currency (profit + swap)
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolView:
    symbol: str
    digits: int = 5
    point: float = 0.00001
    tick_size: float = 0.00001
    tick_value: float = 0.0   # account currency per 1 lot per 1 tick
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: int = 0
    trade_allowed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Timeframe level analysis produced by the deterministic quant layer.
# --------------------------------------------------------------------------- #
@dataclass
class TimeframeAnalysis:
    timeframe: str
    candles: int = 0
    last_close: float = 0.0
    ema_fast: float = 0.0
    ema_mid: float = 0.0
    ema_slow: float = 0.0
    rsi: float = 50.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    momentum_pct: float = 0.0
    score: float = 0.0            # -1 .. +1 bullishness of this timeframe
    trend: str = "FLAT"           # BULLISH / BEARISH / FLAT

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketAnalysis:
    """Output of AI Agent #1 — Market Analyst."""
    signal: str = "HOLD"                # BUY / SELL / HOLD
    buy_probability: float = 0.5
    sell_probability: float = 0.5
    trend: str = "FLAT"                 # BULLISH / BEARISH / FLAT
    trend_strength: str = "Weak"        # Weak / Moderate / Strong
    volatility: str = "NORMAL"          # LOW / NORMAL / HIGH
    momentum: str = "NEUTRAL"           # UP / DOWN / NEUTRAL
    reversal_risk: str = "LOW"          # LOW / MEDIUM / HIGH
    support: List[float] = field(default_factory=list)
    resistance: List[float] = field(default_factory=list)
    entry_zones: List[Dict[str, float]] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    per_timeframe: List[TimeframeAnalysis] = field(default_factory=list)
    source: str = "DETERMINISTIC"       # LLM / DETERMINISTIC / LLM_FALLBACK_DETERMINISTIC
    ts: float = field(default_factory=_ts)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class StrategySignal:
    """Output of AI Agent #2 — Strategy Trader."""
    action: str = "HOLD"                # BUY / SELL / HOLD
    entry: float = 0.0
    take_profit: float = 0.0            # price
    stop_loss: float = 0.0              # price
    tp_usd: float = 0.0                 # expected profit in account currency
    sl_usd: float = 0.0                 # risked amount in account currency
    risk_reward: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    exit_conditions: str = ""
    source: str = "DETERMINISTIC"
    ts: float = field(default_factory=_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RiskVerdict:
    """Output of AI Agent #3 — Risk & Decision Analyst."""
    approved: bool = False
    risk: str = "HIGH"                  # LOW / MEDIUM / HIGH
    confidence: float = 0.0
    recommendation: str = "REJECT"      # EXECUTE / WAIT / REJECT
    reason: str = ""
    checks: List[str] = field(default_factory=list)
    source: str = "DETERMINISTIC"
    ts: float = field(default_factory=_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConsensusResult:
    executable: bool = False
    side: Optional[str] = None          # "BUY" / "SELL" / None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMDecision:
    """Structured decision of the central LLM Trading Manager."""
    action: str = "HOLD"                # BUY / SELL / HOLD / CLOSE
    confidence: float = 0.0
    reason: str = ""
    take_profit_usd: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)
    source: str = "DETERMINISTIC"       # LLM / DETERMINISTIC
    ts: float = field(default_factory=_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RiskGateResult:
    """Deterministic hard gate. The LLM can NEVER bypass it."""
    approved: bool = False
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CycleReport:
    """Everything that happened in one decision cycle (for journal + UI)."""
    cycle_id: str = field(default_factory=new_cycle_id)
    ts: float = field(default_factory=_ts)
    engine_state: str = "STOPPED"
    mt5_status: str = "DISCONNECTED"
    market: Optional[MarketAnalysis] = None
    strategy: Optional[StrategySignal] = None
    risk: Optional[RiskVerdict] = None
    consensus: Optional[ConsensusResult] = None
    llm_decision: Optional[LLMDecision] = None
    gate: Optional[RiskGateResult] = None
    executed: Optional[Dict[str, Any]] = None
    skipped_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "ts": self.ts,
            "engine_state": self.engine_state,
            "mt5_status": self.mt5_status,
            "market": self.market.to_dict() if self.market else None,
            "strategy": self.strategy.to_dict() if self.strategy else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "llm_decision": self.llm_decision.to_dict() if self.llm_decision else None,
            "gate": self.gate.to_dict() if self.gate else None,
            "executed": self.executed,
            "skipped_reason": self.skipped_reason,
        }
