"""Уведомления: консоль + (опционально) Telegram.

Уведомления не должны ронять агента: любая ошибка отправки только логируется.
Секреты (токен, chat_id) берутся из переменных окружения, а не из конфига.
"""

from __future__ import annotations

import os
from typing import Any

from .config import NotificationsConfig
from .logging_setup import get_logger, log_event
from .utils import fmt_money

log = get_logger("notify")


class Notifier:
    def __init__(self, cfg: NotificationsConfig) -> None:
        self.cfg = cfg
        tg = cfg.telegram
        self.token = os.getenv(tg.bot_token_env, "").strip() if tg.enabled else ""
        self.chat_id = os.getenv(tg.chat_id_env, "").strip() if tg.enabled else ""
        self.enabled = bool(tg.enabled and self.token and self.chat_id)
        if tg.enabled and not self.enabled:
            log.warning(
                "Telegram включён в конфиге, но %s/%s не заданы в .env — уведомления только в лог",
                tg.bot_token_env, tg.chat_id_env,
            )

    # -- низкий уровень ---------------------------------------------------------
    def send(self, text: str, *, level: str = "info") -> bool:
        log_event("notify", level=level, text=text)
        if level == "error":
            log.error("УВЕДОМЛЕНИЕ: %s", text)
        elif level == "warning":
            log.warning("УВЕДОМЛЕНИЕ: %s", text)
        else:
            log.info("УВЕДОМЛЕНИЕ: %s", text)
        if not self.enabled:
            return False
        try:
            import requests

            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4000], "parse_mode": "HTML"},
                timeout=10,
            )
            if response.status_code != 200:
                log.warning("Telegram ответил %s: %s", response.status_code, response.text[:200])
                return False
            return True
        except Exception as exc:  # pragma: no cover - сеть
            log.warning("Не удалось отправить уведомление в Telegram: %s", exc)
            return False

    # -- события ------------------------------------------------------------------
    def started(self, mode: str, account_desc: str) -> None:
        self.send(f"🤖 Агент запущен в режиме <b>{mode}</b>\n{account_desc}")

    def stopped(self, reason: str) -> None:
        self.send(f"🛑 Агент остановлен: {reason}")

    def halted(self, reason: str) -> None:
        self.send(f"⛔️ Торговля остановлена: {reason}", level="error")

    def trade_opened(self, plan_desc: str) -> None:
        self.send(f"📈 Вход\n{plan_desc}")

    def trade_closed(self, symbol: str, pnl: float, day_pnl: float) -> None:
        emoji = "✅" if pnl >= 0 else "🔻"
        self.send(
            f"{emoji} Закрытие {symbol}: {'+' if pnl >= 0 else ''}{fmt_money(pnl)} USD\n"
            f"Итог дня: {'+' if day_pnl >= 0 else ''}{fmt_money(day_pnl)} USD"
        )

    def error(self, text: str) -> None:
        self.send(f"⚠️ {text}", level="error")

    def daily_summary(self, payload: dict[str, Any]) -> None:
        self.send(
            "📊 Итоги дня\n"
            f"equity: {fmt_money(payload.get('equity'))}\n"
            f"результат: {'+' if (payload.get('pnl') or 0) >= 0 else ''}{fmt_money(payload.get('pnl'))}\n"
            f"входов: {payload.get('trades', 0)}\n"
            f"просадка: {payload.get('dd_pct', 0):.2f}%"
        )
