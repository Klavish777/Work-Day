"""Тесты новостного фильтра: эвристика детерминирована и понятна."""

from __future__ import annotations

from pathlib import Path

from mt5_agent.config import SentimentConfig
from mt5_agent.sentiment import SentimentFilter, score_headlines


def test_neutral_headlines_no_risk() -> None:
    risk, score, matched = score_headlines(["EUR/USD trades sideways", "Market awaits data"])
    assert risk == 0.0
    assert matched == []


def test_high_impact_event_raises_risk() -> None:
    risk, _, matched = score_headlines(["FOMC rate decision tonight at 19:00"])
    assert risk >= 0.6
    assert any("fomc" in m for m in matched)


def test_negative_news_blocks() -> None:
    risk, score, _ = score_headlines(["Bank run fears: contagion spreads across region"])
    assert risk >= 0.6
    assert score < 0


def test_one_red_headline_dominates_many_neutral() -> None:
    headlines = ["quiet session"] * 10 + ["Emergency rate hike announced"]
    risk, _, _ = score_headlines(headlines)
    assert risk >= 0.3


def test_positive_terms_give_positive_score() -> None:
    risk, score, matched = score_headlines(["Ceasefire reached, markets rally on soft landing hopes"])
    assert score > 0
    assert risk < 0.6
    assert matched


def test_filter_disabled_returns_neutral(tmp_path: Path) -> None:
    cfg = SentimentConfig(enabled=False, provider="none")
    result = SentimentFilter(cfg, state_dir=tmp_path).evaluate()
    assert result.blocked is False
    assert result.provider == "none"


def test_filter_reads_headlines_file_and_caches(tmp_path: Path) -> None:
    headlines = tmp_path / "headlines.txt"
    headlines.write_text("# комментарий\nFOMC decision today\nТихо на рынке\n", encoding="utf-8")
    cfg = SentimentConfig(enabled=True, provider="headlines",
                          headlines_file=str(headlines), negative_block_threshold=0.6)
    sent_filter = SentimentFilter(cfg, state_dir=tmp_path)
    result = sent_filter.evaluate()
    assert result.blocked is True
    assert "FOMC" in result.headlines[0]
    # второй вызов берёт из кэша (файл уже не нужен)
    headlines.unlink()
    cached = sent_filter.evaluate()
    assert cached.blocked is True
    assert cached.provider == "headlines"


def test_missing_headlines_file_is_neutral(tmp_path: Path) -> None:
    cfg = SentimentConfig(enabled=True, provider="headlines",
                          headlines_file=str(tmp_path / "нет-такого.txt"))
    result = SentimentFilter(cfg, state_dir=tmp_path).evaluate()
    assert result.blocked is False
    assert "нет" in result.note


def test_llm_verdict_never_raises_without_server(monkeypatch, tmp_path: Path) -> None:
    """Если локальная LLM недоступна — работаем на эвристике, а не падаем."""
    from mt5_agent.config import LLMConfig
    from mt5_agent.sentiment import llm_verdict

    cfg = LLMConfig(enabled=True, base_url="http://127.0.0.1:9/v1", timeout_sec=1)
    verdict = llm_verdict(["quiet session"], cfg)
    assert verdict is None
