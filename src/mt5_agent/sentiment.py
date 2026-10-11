"""Новостной / сентимент-фильтр.

Роль в системе ровно одна: **запретить вход в плохой для торговли момент**
(важная новость, паника, «красный» фон). LLM здесь не предсказывает цену и не
является источником сигнала — только фильтром, как ты и выбрал.

Два уровня:

1. **Эвристика (работает всегда, офлайн).** Словарь ключевых слов и событий
   («fomc», «nonfarm», «cpi», «rate decision», «war», «default» ...). Считает
   риск 0..1. Полностью детерминирован и покрыт тестами — это важно: фильтр
   не должен быть чёрным ящиком, от которого зависят деньги.
2. **Локальная LLM (опционально).** Если ``sentiment.llm.enabled: true``,
   агент спрашивает модель с OpenAI-совместимым API (например, Ollama на
   localhost) и просит выдать строгий JSON: ``{"risk": 0..1, "block": bool,
   "reason": "..."}``. Модель локальная — данные и счёт никуда не уходят.
   Ответ LLM валидируется; при любой ошибке используется эвристика.

Важный принцип: **любое сомнение = блокировка**. Пропущенная сделка стоит
ноль, плохая сделка стоит денег.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import SentimentConfig
from .logging_setup import get_logger, log_event
from .utils import UTC, read_json, utc_now, write_json

log = get_logger("sentiment")

# --- словари эвристики -------------------------------------------------------
# Вес = вклад в «негативный фон». Ключи — подстроки в нижнем регистре.
NEGATIVE_TERMS: dict[str, float] = {
    "war": 0.35, "invasion": 0.4, "attack": 0.35, "strike": 0.2, "missile": 0.4,
    "default": 0.4, "debt ceiling": 0.35, "shutdown": 0.25, "bankruptcy": 0.4,
    "crisis": 0.4, "panic": 0.4, "crash": 0.5, "plunge": 0.3, "selloff": 0.25,
    "sell-off": 0.25, "recession": 0.35, "layoffs": 0.15, "downgrade": 0.3,
    "sanctions": 0.2, "tariffs": 0.2, "inflation surge": 0.3, "hawkish": 0.25,
    "hike": 0.2, "rate hike": 0.3, "emergency": 0.3, "collapse": 0.45,
    "bank run": 0.5, "contagion": 0.4, "outage": 0.2, "halted": 0.25,
    "investigation": 0.15, "fraud": 0.35, "capitulation": 0.35,
}

POSITIVE_TERMS: dict[str, float] = {
    "dovish": 0.25, "rate cut": 0.3, "cuts rates": 0.3, "stimulus": 0.25,
    "rally": 0.2, "record high": 0.25, "surge": 0.15, "beat expectations": 0.25,
    "upgrade": 0.2, "recovery": 0.2, "optimism": 0.2, "ceasefire": 0.35,
    "deal reached": 0.25, "soft landing": 0.25, "strong earnings": 0.2,
}

# События, вокруг которых вход вообще запрещён (волатильность непредсказуема).
HIGH_IMPACT_EVENTS: dict[str, float] = {
    "fomc": 0.9, "federal reserve decision": 0.9, "rate decision": 0.9,
    "nonfarm payroll": 0.9, "non-farm payroll": 0.9, "nfp": 0.85,
    "cpi": 0.85, "inflation data": 0.8, "ppi": 0.7, "gdp": 0.6,
    "ecb decision": 0.9, "boe decision": 0.85, "boj decision": 0.85,
    "press conference": 0.6, "jackson hole": 0.7, "election": 0.6,
    "opec meeting": 0.6, "powell speaks": 0.75, "lagarde speaks": 0.7,
    "jobs report": 0.85, "unemployment": 0.6, "retail sales": 0.6,
}


@dataclass
class SentimentResult:
    score: float = 0.0            # -1 (очень плохо) .. +1 (очень хорошо)
    risk: float = 0.0             # 0..1 «опасность входа»
    blocked: bool = False
    provider: str = "none"
    note: str = ""
    headlines: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    llm_used: bool = False
    fetched_at: datetime = field(default_factory=utc_now)

    def describe(self) -> str:
        state = "БЛОК" if self.blocked else "ок"
        return (f"[{state}] risk={self.risk:.2f} score={self.score:+.2f} "
                f"({self.provider}, LLM={'да' if self.llm_used else 'нет'}) — {self.note}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "risk": self.risk, "blocked": self.blocked,
            "provider": self.provider, "note": self.note, "llm_used": self.llm_used,
            "matched_terms": self.matched_terms, "headlines": self.headlines[:10],
            "fetched_at": self.fetched_at.isoformat(),
        }


# -----------------------------------------------------------------------------
# Эвристика
# -----------------------------------------------------------------------------

def score_headlines(headlines: Iterable[str], *, symbol: str | None = None) -> tuple[float, float, list[str]]:
    """Вернуть (risk 0..1, score -1..1, список совпавших терминов).

    Логика простая и намеренно консервативная: риск — максимум по заголовкам,
    а не среднее, потому что одна «красная» новость перевешивает десять
    нейтральных.
    """
    text = " \n ".join(h.lower() for h in headlines if h)
    if not text.strip():
        return 0.0, 0.0, []

    matched: list[str] = []

    def combine(weights: list[float]) -> float:
        """Вероятностное «ИЛИ»: несколько сигналов вместе весомее одного."""
        product = 1.0
        for weight in weights:
            product *= (1.0 - weight)
        return 1.0 - product

    negative_weights: list[float] = []
    event_weights: list[float] = []
    positive_weights: list[float] = []
    for terms, bucket in (
        (NEGATIVE_TERMS, negative_weights),
        (HIGH_IMPACT_EVENTS, event_weights),
        (POSITIVE_TERMS, positive_weights),
    ):
        for term, weight in terms.items():
            if term in text:
                bucket.append(weight)
                matched.append(term)

    risk = min(1.0, combine(negative_weights + event_weights))
    score = max(-1.0, min(1.0, combine(positive_weights) - risk))
    return round(risk, 3), round(score, 3), sorted(set(matched))


# -----------------------------------------------------------------------------
# Локальная LLM (OpenAI-совместимый API)
# -----------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = (
    "Ты — фильтр риска для торгового робота. Тебе дают список новостных "
    "заголовков. Твоя задача — оценить, опасно ли СЕЙЧАС открывать новые "
    "спекулятивные позиции. Ты не предсказываешь цену. "
    "Ответь СТРОГО JSON без пояснений: "
    '{"risk": <число 0..1>, "block": <true|false>, "reason": "<до 200 символов>"}'
)


def llm_verdict(
    headlines: list[str],
    cfg,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any] | None:
    """Спросить локальную LLM. Возвращает dict или None, если не получилось.

    Никогда не бросает исключение наружу: без LLM агент продолжает работать на
    эвристике (фильтр — вспомогательный, не критичный путь).
    """
    import os

    try:
        import requests
    except ImportError:  # pragma: no cover
        log.warning("requests не установлен — LLM-фильтр пропущен")
        return None

    base_url = (base_url or cfg.base_url).rstrip("/")
    model = model or cfg.model
    api_key = api_key if api_key is not None else os.getenv(cfg.api_key_env, "")
    timeout = timeout or cfg.timeout_sec

    payload = {
        "model": model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": "Заголовки:\n" + "\n".join(f"- {h}" for h in headlines[:30])},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.post(
            f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # pragma: no cover - зависит от окружения
        log.warning("LLM-фильтр недоступен (%s) — работаю на эвристике", exc)
        return None

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        log.warning("LLM вернула не-JSON: %s", content[:120])
        return None
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if "risk" not in verdict:
        return None
    try:
        verdict["risk"] = max(0.0, min(1.0, float(verdict["risk"])))
    except (TypeError, ValueError):
        return None
    verdict["block"] = bool(verdict.get("block", verdict["risk"] >= 0.6))
    verdict["reason"] = str(verdict.get("reason", ""))[:300]
    return verdict


# -----------------------------------------------------------------------------
# Фильтр
# -----------------------------------------------------------------------------

class SentimentFilter:
    """Кэширующий фильтр: собирает заголовки, считает риск, включает LLM."""

    def __init__(self, cfg: SentimentConfig, *, state_dir: Path, logger=None) -> None:
        self.cfg = cfg
        self.state_dir = Path(state_dir)
        self.cache_path = self.state_dir / "sentiment_cache.json"
        self.log = logger or log

    # -- сбор заголовков ---------------------------------------------------------
    def _headlines_from_file(self) -> list[str]:
        path = self.cfg.headlines_file
        p = Path(path)
        if not p.is_absolute():
            p = self.state_dir.parent / path
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        result = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result.append(line)
        return result

    def _headlines_from_http(self) -> list[str]:
        if self.cfg.provider != "http_json" or not self.cfg.url:
            return []
        try:
            import requests
        except ImportError:  # pragma: no cover
            return []
        try:
            response = requests.get(self.cfg.url, timeout=self.cfg.fetch_timeout_sec)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self.log.warning("Не удалось получить заголовки (%s)", exc)
            return []
        if isinstance(data, dict):
            for key in ("headlines", "items", "news", "titles"):
                if key in data and isinstance(data[key], list):
                    return [str(h) for h in data[key]]
            return []
        if isinstance(data, list):
            return [str(h) for h in data]
        return []

    def collect_headlines(self) -> list[str]:
        return self._headlines_from_file() + self._headlines_from_http()

    # -- кэш ----------------------------------------------------------------------
    def _cached(self, now: datetime) -> SentimentResult | None:
        raw = read_json(self.cache_path, default=None)
        if not raw:
            return None
        try:
            fetched = datetime.fromisoformat(raw["fetched_at"])
        except (KeyError, ValueError):
            return None
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        if now - fetched > timedelta(minutes=self.cfg.cache_minutes):
            return None
        return SentimentResult(
            score=float(raw.get("score", 0.0)),
            risk=float(raw.get("risk", 0.0)),
            blocked=bool(raw.get("blocked", False)),
            provider=str(raw.get("provider", "cache")),
            note=str(raw.get("note", "")),
            headlines=list(raw.get("headlines") or []),
            matched_terms=list(raw.get("matched_terms") or []),
            llm_used=bool(raw.get("llm_used", False)),
            fetched_at=fetched,
        )

    # -- основной метод --------------------------------------------------------------
    def evaluate(self, *, symbol: str | None = None, now: datetime | None = None,
                 force: bool = False) -> SentimentResult:
        now = (now or utc_now()).astimezone(UTC)

        if not self.cfg.enabled or self.cfg.provider == "none":
            return SentimentResult(provider="none", note="новостной фильтр выключен")

        if not force:
            cached = self._cached(now)
            if cached is not None:
                return cached

        headlines = self.collect_headlines()
        if not headlines:
            result = SentimentResult(provider=self.cfg.provider,
                                     note="заголовков нет — фон нейтральный")
            self._store(result, now)
            return result

        risk, score, matched = score_headlines(headlines, symbol=symbol)
        note = f"эвристика: {len(headlines)} заголовков, совпадения: {', '.join(matched[:6]) or 'нет'}"
        llm_used = False

        if self.cfg.llm.enabled:
            verdict = llm_verdict(headlines, self.cfg.llm)
            if verdict is not None:
                llm_used = True
                risk = max(risk, float(verdict["risk"]))
                note = f"LLM: {verdict.get('reason') or 'без пояснения'}"
                if verdict.get("block"):
                    risk = max(risk, self.cfg.negative_block_threshold)

        blocked = risk >= self.cfg.negative_block_threshold
        if blocked:
            note += " → входы ЗАБЛОКИРОВАНЫ"

        result = SentimentResult(
            score=score, risk=risk, blocked=blocked, provider=self.cfg.provider,
            note=note, headlines=headlines[:20], matched_terms=matched, llm_used=llm_used,
            fetched_at=now,
        )
        self._store(result, now)
        log_event("sentiment", **result.to_dict())
        self.log.info("Новостной фильтр: %s", result.describe())
        return result

    def _store(self, result: SentimentResult, now: datetime) -> None:
        payload = result.to_dict()
        payload["fetched_at"] = now.isoformat()
        write_json(self.cache_path, payload)
