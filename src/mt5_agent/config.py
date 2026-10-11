"""Загрузка и валидация конфигурации.

Секреты сюда не попадают: логин/пароль читаются из окружения (.env) в
:class:`~mt5_agent.broker.MT5Credentials`. В YAML лежат только правила.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError

# -----------------------------------------------------------------------------
# Вспомогательные функции
# -----------------------------------------------------------------------------

TIMEFRAME_ALIASES = {
    "M1": "TIMEFRAME_M1", "M2": "TIMEFRAME_M2", "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4", "M5": "TIMEFRAME_M5", "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10", "M12": "TIMEFRAME_M12", "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4", "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1", "MN1": "TIMEFRAME_MN1",
}

VALID_MODES = ("paper", "shadow", "live")


def _get(data: dict[str, Any], key: str, default: Any, cast=None) -> Any:
    value = data.get(key, default)
    if value is None:
        value = default
    if cast is not None and value is not None:
        try:
            value = cast(value)
        except (TypeError, ValueError) as exc:  # pragma: no cover - защита
            raise ConfigError(f"Не могу привести {key}={value!r} к {cast}: {exc}") from exc
    return value


def _dataclass_from(cls, data: dict[str, Any] | None):
    """Заполнить dataclass значениями из словаря, игнорируя неизвестные ключи."""
    data = dict(data or {})
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"Неизвестные параметры в секции {cls.__name__}: {sorted(unknown)}. "
            "Проверь опечатки в config.yaml — молча игнорировать их небезопасно."
        )
    kwargs = {k: v for k, v in data.items() if v is not None}
    return cls(**kwargs)


def _norm_timeframe(value: str) -> str:
    key = str(value).strip().upper()
    if key not in TIMEFRAME_ALIASES:
        raise ConfigError(
            f"Неизвестный таймфрейм {value!r}. Допустимо: {sorted(TIMEFRAME_ALIASES)}"
        )
    return key


# -----------------------------------------------------------------------------
# Секции конфига
# -----------------------------------------------------------------------------

@dataclass
class AgentConfig:
    mode: str = "paper"
    timezone: str = "UTC"
    loop_interval_sec: int = 60
    heartbeat_file: str = "state/heartbeat.txt"
    heartbeat_stale_sec: int = 180


@dataclass
class SymbolConfig:
    name: str = ""
    enabled: bool = True
    timeframe: str = "M15"
    history_bars: int = 5000
    max_spread_points: int = 20
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.25
    min_atr_points: float = 5.0
    session_start_utc: str = "07:00"
    session_end_utc: str = "19:00"
    # Если заданы окна — они важнее пары start/end. Нужно для инструментов с двумя
    # пиками ликвидности (пример: AUDCAD — Токио для AUD и Нью-Йорк для CAD).
    session_windows: list[list[str]] | None = None
    news_blackout_min: int = 30
    magic: int = 20260000

    def __post_init__(self) -> None:
        self.name = str(self.name).upper().strip()
        self.timeframe = _norm_timeframe(self.timeframe)
        if self.session_windows:
            normalized: list[list[str]] = []
            for window in self.session_windows:
                if len(window) != 2:
                    raise ConfigError(
                        f"{self.name}: каждое окно сессии задаётся парой [начало, конец], "
                        f"получено {window!r}"
                    )
                # parse_hhmm бросит понятную ошибку на некорректное время
                from .utils import parse_hhmm

                parse_hhmm(window[0])
                parse_hhmm(window[1])
                normalized.append([str(window[0]), str(window[1])])
            self.session_windows = normalized
        if not self.name:
            raise ConfigError("У символа не указано имя (name)")
        if self.sl_atr_mult <= 0 or self.tp_atr_mult <= 0:
            raise ConfigError(f"{self.name}: sl_atr_mult/tp_atr_mult должны быть > 0")
        if self.tp_atr_mult < self.sl_atr_mult:
            raise ConfigError(
                f"{self.name}: тейк ({self.tp_atr_mult}) меньше стопа ({self.sl_atr_mult}) — "
                "риск/прибыль хуже 1:1, это небезопасно"
            )

    @property
    def reward_risk(self) -> float:
        return self.tp_atr_mult / self.sl_atr_mult

    @property
    def windows(self) -> list[tuple[str, str]]:
        """Торговые окна: либо явные, либо единственное из start/end."""
        if self.session_windows:
            return [(window[0], window[1]) for window in self.session_windows]
        return [(self.session_start_utc, self.session_end_utc)]

    def session_text(self) -> str:
        """Описание сессии для сообщений и отчётов."""
        from .utils import describe_windows

        return describe_windows(self.windows)


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_open_positions: int = 2
    max_positions_per_symbol: int = 1
    max_daily_trades: int = 6
    min_balance_floor: float = 100.0
    cooldown_after_loss_min: int = 30
    max_leverage_used: float = 5.0
    allow_hedging: bool = False
    allow_martingale: bool = False
    allow_grid: bool = False
    unknown_position_action: str = "halt"

    def __post_init__(self) -> None:
        if self.allow_martingale or self.allow_grid:
            raise ConfigError(
                "Мартингейл и сетки запрещены архитектурно. Убери эти флаги из конфига."
            )
        if not (0.0 < self.risk_per_trade_pct <= 1.0):
            raise ConfigError(
                f"risk_per_trade_pct={self.risk_per_trade_pct} вне допустимого диапазона "
                "(0, 1.0]. Риск больше 1% на сделку противоречит правилам проекта."
            )
        if self.max_daily_loss_pct > 5.0:
            raise ConfigError("max_daily_loss_pct > 5% — слишком много. Разрешено до 5%.")
        if self.max_drawdown_pct > 25.0:
            raise ConfigError("max_drawdown_pct > 25% — недопустимо.")
        if self.unknown_position_action not in ("halt", "ignore", "warn"):
            raise ConfigError("unknown_position_action: halt | ignore | warn")


@dataclass
class SignalConfig:
    model: str = "gradient_boosting"
    horizon_bars: int = 4
    min_probability: float = 0.58
    allow_short: bool = True
    retrain_every_days: int = 7
    train_test_split: float = 0.75
    feature_lookbacks: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 8, 16, 32])
    embargo_bars: int = 4
    walk_forward_folds: int = 4
    model_path: str = "models/signal_model.joblib"
    require_model_trained: bool = True

    def __post_init__(self) -> None:
        if self.model not in ("gradient_boosting", "logistic"):
            raise ConfigError("signal.model: gradient_boosting | logistic")
        if not (0.5 < self.min_probability < 0.95):
            raise ConfigError("min_probability должна быть в (0.5, 0.95)")
        if self.horizon_bars < 1:
            raise ConfigError("horizon_bars >= 1")


@dataclass
class LLMConfig:
    """Настройки LLM-фильтра (только фильтр, не источник сигнала!)."""
    enabled: bool = False
    base_url: str = "http://localhost:11434/v1"   # локальный Ollama/llama.cpp — идеально
    model: str = "gpt-oss:20b"
    api_key_env: str = "LLM_API_KEY"              # для локальных серверов может быть пустым
    timeout_sec: int = 20
    max_tokens: int = 300
    temperature: float = 0.0


@dataclass
class SentimentConfig:
    enabled: bool = False
    provider: str = "none"           # none | headlines (файл+HTTP) | http_json
    url: str = ""                    # свой HTTP-источник заголовков (JSON)
    api_key_env: str = "NEWS_API_KEY"
    cache_minutes: int = 30
    fetch_timeout_sec: int = 10
    headlines_file: str = "state/headlines.txt"
    negative_block_threshold: float = 0.6
    positive_boost: float = 0.02
    llm: LLMConfig = field(default_factory=LLMConfig)

    def __post_init__(self) -> None:
        if self.provider not in ("none", "headlines", "http_json"):
            raise ConfigError("sentiment.provider: none | headlines | http_json")


@dataclass
class ExecutionConfig:
    order_comment: str = "mt5-agent"
    deviation_points: int = 10
    filling_fallback: list[str] = field(default_factory=lambda: ["IOC", "FOK", "RETURN"])
    max_slippage_points: int = 15
    retry_attempts: int = 3
    retry_backoff_sec: int = 2
    send_sl_tp_with_order: bool = True
    close_partial_enabled: bool = False
    breakeven_at_r: float = 0.0     # 1.0 = перевести SL в безубыток при +1R (0 = выключено)
    trail_atr_mult: float = 0.0     # сопровождение стопом по ATR (0 = выключено)

    def __post_init__(self) -> None:
        valid = {"IOC", "FOK", "RETURN", "BOC"}
        bad = [f for f in self.filling_fallback if f.upper() not in valid]
        if bad:
            raise ConfigError(f"execution.filling_fallback: недопустимые режимы {bad}")
        self.filling_fallback = [f.upper() for f in self.filling_fallback]


@dataclass
class BacktestConfig:
    days: int = 180
    initial_balance: float = 1000.0
    spread_points: float = 12.0
    commission_per_lot: float = 7.0
    slippage_points: float = 2.0
    leverage: int = 100
    risk_per_trade_pct: float = 0.5
    max_hold_bars: int = 48
    min_trades: int = 60
    min_profit_factor: float = 1.25
    max_allowed_dd_pct: float = 15.0
    min_win_rate_with_rr: float = 0.38


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token_env: str = "TG_BOT_TOKEN"
    chat_id_env: str = "TG_CHAT_ID"


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log_level: str = "INFO"
    log_file: str = "logs/agent.log"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5


@dataclass
class SafetyConfig:
    demo_only: bool = True
    require_confirmation_for_live: bool = True
    kill_switch_file: str = "state/KILL_SWITCH"
    max_consecutive_errors: int = 10
    verify_account_number: bool = True


@dataclass
class TradingConfig:
    symbols: list[SymbolConfig] = field(default_factory=list)

    def by_name(self, name: str) -> SymbolConfig | None:
        name = name.upper()
        for sym in self.symbols:
            if sym.name == name:
                return sym
        return None

    @property
    def enabled_symbols(self) -> list[SymbolConfig]:
        return [s for s in self.symbols if s.enabled]


# -----------------------------------------------------------------------------
# Корневой конфиг
# -----------------------------------------------------------------------------

@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    mt5: dict[str, Any] = field(default_factory=dict)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    # Производные (не из YAML)
    root_dir: Path = field(default_factory=Path.cwd)
    source_path: Path | None = None

    # -- удобные пути относительно корня проекта ------------------------------
    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.root_dir / p)

    @property
    def is_live_mode(self) -> bool:
        return self.agent.mode == "live"

    # -- валидация ------------------------------------------------------------
    def validate(self) -> list[str]:
        """Проверить смысловые ограничения. Возвращает список предупреждений."""
        warnings: list[str] = []
        if self.agent.mode not in VALID_MODES:
            raise ConfigError(f"agent.mode={self.agent.mode!r}; допустимо {VALID_MODES}")
        if not self.trading.symbols:
            raise ConfigError("В config.yaml не описано ни одного символа (trading.symbols)")
        if not self.trading.enabled_symbols:
            raise ConfigError("Все символы выключены (enabled: false) — агенту нечего делать")

        magics = [s.magic for s in self.trading.symbols]
        if len(set(magics)) != len(magics):
            raise ConfigError("magic-номера должны быть уникальны для каждого символа")

        if self.risk.max_positions_per_symbol > self.risk.max_open_positions:
            warnings.append(
                "max_positions_per_symbol > max_open_positions — бессмысленно, уменьши"
            )
        if self.agent.mode == "live" and self.safety.demo_only:
            warnings.append(
                "agent.mode=live, но safety.demo_only=true — агент всё равно не выйдет "
                "на реальный счёт, пока не изменишь конфиг осознанно."
            )
        if self.risk.risk_per_trade_pct > 1.0:  # pragma: no cover - уже проверено
            warnings.append("Риск на сделку > 1% — опасно")
        if not self.signal.require_model_trained:
            warnings.append(
                "signal.require_model_trained=false — агент может торговать без модели. "
                "Включай только для ручных экспериментов."
            )
        return warnings


# -----------------------------------------------------------------------------
# Загрузка
# -----------------------------------------------------------------------------

def find_project_root(start: Path | None = None) -> Path:
    """Найти корень проекта (папку, где лежит config/config.yaml).

    Ищем в порядке приоритета:

    1. явно переданный путь (тесты, ``--root``);
    2. каталог модуля пакета — обычный запуск из исходников;
    3. каталог самого ``.exe`` и его родителей — запуск собранного приложения
       (PyInstaller распаковывает код во временную папку, поэтому искать «от
       модуля» там бессмысленно, зато рядом с .exe лежит config/);
    4. текущий рабочий каталог.
    """
    candidates: list[Path] = []
    if start is not None:
        candidates.append(Path(start))
    else:
        candidates.append(Path(__file__))
        if getattr(sys, "frozen", False):  # собрано PyInstaller'ом
            candidates.append(Path(sys.executable))
        candidates.append(Path.cwd())

    seen: set[Path] = set()
    for candidate in candidates:
        for folder in [candidate.resolve(), *candidate.resolve().parents]:
            if folder in seen:
                continue
            seen.add(folder)
            if (folder / "config" / "config.yaml").exists():
                return folder
    return Path.cwd()


def load_dotenv_files(root: Path) -> None:
    """Прочитать .env (если он есть) в переменные окружения.

    Используется свой минимальный парсер, чтобы не зависеть от python-dotenv
    на этапе, когда он ещё не установлен.
    """
    for name in (".env", ".env.real"):
        env_path = root / name
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_config(path: str | Path | None = None, *, root: str | Path | None = None) -> Config:
    """Загрузить config.yaml (+ .env), вернуть провалидированный :class:`Config`.

    ``root`` обычно определяется автоматически; параметр нужен тестам, чтобы
    собрать изолированный «мини-проект» во временной папке.
    """
    root = Path(root) if root is not None else find_project_root()
    load_dotenv_files(root)

    config_path = Path(path) if path else root / "config" / "config.yaml"
    if path is not None and not Path(path).is_absolute():
        config_path = root / Path(path)
    if not config_path.exists():
        raise ConfigError(f"Не найден файл конфигурации: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml должен быть словарём верхнего уровня")

    allowed_top = {
        "agent", "mt5", "trading", "risk", "signal",
        "sentiment", "execution", "backtest", "notifications", "safety",
    }
    unknown = set(raw) - allowed_top
    if unknown:
        raise ConfigError(f"Неизвестные секции в config.yaml: {sorted(unknown)}")

    trading_raw = raw.get("trading") or {}
    symbols_raw = trading_raw.get("symbols") or []
    symbols = [_dataclass_from(SymbolConfig, s) for s in symbols_raw]

    cfg = Config(
        agent=_dataclass_from(AgentConfig, raw.get("agent")),
        mt5=dict(raw.get("mt5") or {}),
        trading=TradingConfig(symbols=symbols),
        risk=_dataclass_from(RiskConfig, raw.get("risk")),
        signal=_dataclass_from(SignalConfig, raw.get("signal")),
        sentiment=_dataclass_from(
            SentimentConfig,
            {
                **(raw.get("sentiment") or {}),
                "llm": _dataclass_from(LLMConfig, (raw.get("sentiment") or {}).get("llm")),
            },
        ),
        execution=_dataclass_from(ExecutionConfig, raw.get("execution")),
        backtest=_dataclass_from(BacktestConfig, raw.get("backtest")),
        notifications=NotificationsConfig(
            telegram=_dataclass_from(TelegramConfig, (raw.get("notifications") or {}).get("telegram")),
            **{k: v for k, v in (raw.get("notifications") or {}).items() if k != "telegram"},
        ),
        safety=_dataclass_from(SafetyConfig, raw.get("safety")),
        root_dir=root,
        source_path=config_path,
    )
    cfg.validate()
    return cfg


def ensure_runtime_dirs(cfg: Config) -> None:
    """Создать каталоги, которые нужны агенту в рантайме."""
    for rel in ("state", "logs", "reports", "models", "data"):
        (cfg.root_dir / rel).mkdir(parents=True, exist_ok=True)
