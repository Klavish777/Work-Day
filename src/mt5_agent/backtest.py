"""Бэктест на исторических барах — без MT5 и без интернета.

Честность бэктеста — это не про красоту графика, а про три вещи:

1. **Издержки.** Спред + комиссия + проскальзывание вычитаются из каждого
   трейда. Именно они убивают большинство «сверхприбыльных» стратегий.
2. **Асимметричная оценка исполнения стопов.** Если внутри одного бара цена
   дошла и до стопа, и до тейка — мы считаем, что сработал СТОП. Реальный
   порядок тиков нам неизвестен, значит берём худший вариант.
3. **Только out-of-sample.** По умолчанию симуляция идёт по последней части
   истории, которую модель не видела при обучении (см. CLI ``backtest``).

Результат — отчёт с метриками и явным вердиктом «GO / NO-GO» по порогам из
``config.yaml`` (раздел ``backtest``). Если вердикт NO-GO — в торговлю по этой
модели идти нельзя, что бы ни показывал красивый график.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .logging_setup import get_logger
from .signals import market_filters
from .sizing import size_position
from .specs import SymbolSpec
from .utils import UTC

log = get_logger("backtest")


@dataclass
class TradeRecord:
    entry_time: datetime
    exit_time: datetime
    side: str
    volume: float
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str          # "tp" | "sl" | "time" | "end"
    equity_after: float


@dataclass
class BacktestResult:
    symbol: str
    initial_balance: float
    final_equity: float
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    verdict_ok: bool = False
    verdict_notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        m = self.metrics
        lines = [
            f"=========== БЭКТЕСТ {self.symbol} ===========",
            f"Сделок: {m.get('trades', 0)} | win rate: {m.get('win_rate', 0) * 100:.1f}% | "
            f"profit factor: {m.get('profit_factor', 0):.2f}",
            f"Итог: {self.initial_balance:.2f} → {self.final_equity:.2f} "
            f"({m.get('total_return_pct', 0):+.2f}%)",
            f"Макс. просадка: {m.get('max_drawdown_pct', 0):.2f}% | "
            f"макс. серия убытков: {m.get('max_consecutive_losses', 0)}",
            f"Средний выигрыш: {m.get('avg_win', 0):+.2f} | средний проигрыш: {m.get('avg_loss', 0):+.2f} | "
            f"матожидание: {m.get('expectancy', 0):+.2f} на сделку",
            f"Издержки всего: {m.get('costs_total', 0):.2f} (спред+комиссия+проскальзывание)",
            "",
            "Распределение выходов: " + ", ".join(
                f"{k}={v}" for k, v in (m.get("exit_reasons") or {}).items()
            ) or "—",
            "",
            f"ВЕРДИКТ: {'✅ можно тестировать дальше на демо' if self.verdict_ok else '⛔️ НЕ ГОДИТСЯ для торговли'}",
        ]
        lines += [f"  • {note}" for note in self.verdict_notes]
        lines += [
            "",
            "Напоминание: бэктест не знает про новости, реквоты, свопы и изменение спреда.",
            "Даже хороший бэктест — это только разрешение проверить идею на демо 1–3 месяца.",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "initial_balance": self.initial_balance,
            "final_equity": self.final_equity,
            "metrics": self.metrics,
            "verdict_ok": self.verdict_ok,
            "verdict_notes": self.verdict_notes,
            "trades": [
                {
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "side": t.side,
                    "volume": t.volume,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": round(t.pnl, 2),
                    "r_multiple": round(t.r_multiple, 2),
                    "bars_held": t.bars_held,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
        }


class Backtester:
    """Симуляция «бар за баром» с теми же правилами риска, что и в live."""

    def __init__(
        self,
        cfg: BacktestConfig,
        *,
        spec: SymbolSpec,
        symbol_cfg,
        logger=None,
    ) -> None:
        self.cfg = cfg
        self.spec = spec
        self.symbol_cfg = symbol_cfg
        self.log = logger or log

    # -- основной прогон -----------------------------------------------------------
    def run(
        self,
        df: pd.DataFrame,
        probabilities: pd.Series,
        *,
        atr_series: pd.Series | None = None,
        min_probability: float = 0.58,
        allow_short: bool = True,
        apply_filters: bool = True,
        start_index: int = 0,
    ) -> BacktestResult:
        """Прогнать симуляцию.

        ``probabilities`` — вероятность «вверх» для каждого бара (рассчитана
        заранее моделью). ``start_index`` — с какого бара начинать (для
        out-of-sample режима передаётся индекс конца train-части).
        """
        from .features import atr as atr_fn

        cfg = self.cfg
        point = self.spec.point
        equity = float(cfg.initial_balance)
        atr_series = atr_series if atr_series is not None else atr_fn(df, self.symbol_cfg.atr_period)

        result = BacktestResult(
            symbol=self.symbol_cfg.name,
            initial_balance=equity,
            final_equity=equity,
        )
        equity_curve: list[tuple[datetime, float]] = []
        costs_total = 0.0
        trades: list[TradeRecord] = []

        n = len(df)
        closes = df["close"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        times = df.index.to_list()
        proba = probabilities.reindex(df.index).to_numpy(dtype=float)
        atr_values = atr_series.reindex(df.index).to_numpy(dtype=float)

        i = max(1, int(start_index))
        while i < n - 1:
            equity_curve.append((times[i], equity))
            p_up = proba[i]
            atr_value = atr_values[i]
            if not np.isfinite(p_up) or not np.isfinite(atr_value) or atr_value <= 0:
                i += 1
                continue

            # --- решение о входе (тот же пайплайн, что и в live) --------------------
            side = "none"
            if p_up >= min_probability:
                side = "buy"
            elif allow_short and p_up <= (1.0 - min_probability):
                side = "sell"
            if side == "none":
                i += 1
                continue

            if apply_filters:
                filters = market_filters(
                    self.symbol_cfg,
                    now=times[i].to_pydatetime().astimezone(UTC),
                    spread_points=cfg.spread_points,
                    atr_value=atr_value,
                    point=point,
                )
                if not all(filters.values()):
                    i += 1
                    continue

            # --- стопы ---------------------------------------------------------------
            sl_distance = self.symbol_cfg.sl_atr_mult * atr_value
            tp_distance = self.symbol_cfg.tp_atr_mult * atr_value
            min_dist = self.spec.min_stop_distance_price(cfg.spread_points)
            if sl_distance < min_dist or tp_distance < min_dist:
                i += 1
                continue

            # --- цена входа с издержками ------------------------------------------------
            half_spread = cfg.spread_points / 2.0 * point
            slip = cfg.slippage_points * point
            add_costs = half_spread + slip
            entry = closes[i] + add_costs if side == "buy" else closes[i] - add_costs

            # --- размер позиции ---------------------------------------------------------
            margin_per_lot = self.spec.contract_size * entry / max(1, cfg.leverage)
            sizing = size_position(
                self.spec,
                equity=equity,
                risk_pct=cfg.risk_per_trade_pct,
                sl_price_distance=sl_distance,
                tp_price_distance=tp_distance,
                max_leverage=None,
                margin_per_lot=margin_per_lot,
            )
            if not sizing.approved:
                i += 1
                continue
            volume = sizing.volume

            sl = entry - sl_distance if side == "buy" else entry + sl_distance
            tp = entry + tp_distance if side == "buy" else entry - tp_distance

            # --- ожидание выхода ---------------------------------------------------------
            exit_price = None
            exit_reason = "end"
            exit_index = min(i + cfg.max_hold_bars, n - 1)
            for j in range(i + 1, min(i + 1 + cfg.max_hold_bars, n)):
                if side == "buy":
                    if lows[j] <= sl:                     # сначала проверяем стоп (консервативно)
                        exit_price, exit_reason, exit_index = sl - slip, "sl", j
                        break
                    if highs[j] >= tp:
                        exit_price, exit_reason, exit_index = tp - slip, "tp", j
                        break
                else:
                    if highs[j] >= sl:
                        exit_price, exit_reason, exit_index = sl + slip, "sl", j
                        break
                    if lows[j] <= tp:
                        exit_price, exit_reason, exit_index = tp + slip, "tp", j
                        break
            if exit_price is None:
                exit_price = closes[exit_index]
                exit_reason = "time" if exit_index < n - 1 else "end"

            # --- деньги --------------------------------------------------------------------
            # Знак результата: для лонга прибыль, если вышли выше входа; для шорта — ниже.
            is_profit = (exit_price > entry) if side == "buy" else (exit_price < entry)
            gross = self.spec.money_for_price_distance(
                abs(exit_price - entry), volume, for_loss=not is_profit
            )
            gross = gross if is_profit else -gross
            commission = cfg.commission_per_lot * volume
            pnl = gross - commission
            costs_total += commission + self.spec.money_for_price_distance(
                (half_spread + slip) * 2, volume
            )
            equity += pnl
            if equity <= 0:
                equity = 0.0

            r_multiple = pnl / sizing.risk_money if sizing.risk_money else 0.0
            trades.append(
                TradeRecord(
                    entry_time=times[i], exit_time=times[exit_index], side=side, volume=volume,
                    entry_price=float(entry), exit_price=float(exit_price), sl=float(sl), tp=float(tp),
                    pnl=float(pnl), r_multiple=float(r_multiple), bars_held=exit_index - i,
                    exit_reason=exit_reason, equity_after=float(equity),
                )
            )
            # Одна позиция за раз: следующая сделка не раньше выхода.
            i = exit_index + 1
            if equity <= 0:
                break

        result.trades = trades
        result.final_equity = float(equity)
        result.equity_curve = equity_curve
        result.metrics = self._metrics(trades, equity, costs_total)
        result.verdict_ok, result.verdict_notes = self._verdict(result.metrics)
        return result

    # -- метрики ------------------------------------------------------------------------
    def _metrics(self, trades: list[TradeRecord], final_equity: float, costs_total: float) -> dict[str, Any]:
        cfg = self.cfg
        if not trades:
            return {
                "trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0, "expectancy": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "max_consecutive_losses": 0, "costs_total": 0.0, "exit_reasons": {},
                "avg_r": 0.0, "sharpe_trade": 0.0, "trades_per_day": 0.0,
            }
        pnls = np.array([t.pnl for t in trades], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())

        # просадка по кривой equity сделок
        curve = np.concatenate([[cfg.initial_balance], np.array([t.equity_after for t in trades])])
        peak = np.maximum.accumulate(curve)
        drawdowns = (peak - curve) / np.where(peak > 0, peak, 1.0) * 100.0
        max_dd = float(drawdowns.max())

        consecutive = worst = 0
        for pnl in pnls:
            if pnl <= 0:
                consecutive += 1
                worst = max(worst, consecutive)
            else:
                consecutive = 0

        span = trades[-1].exit_time - trades[0].entry_time
        days = max(span.total_seconds() / 86400.0, 1e-6)

        return {
            "trades": len(trades),
            "win_rate": float(len(wins) / len(trades)),
            "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
            "total_return_pct": (final_equity / cfg.initial_balance - 1.0) * 100.0,
            "max_drawdown_pct": max_dd,
            "expectancy": float(pnls.mean()),
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "max_consecutive_losses": worst,
            "costs_total": float(costs_total),
            "exit_reasons": _count_by(trades, "exit_reason"),
            "avg_r": float(np.mean([t.r_multiple for t in trades])),
            "sharpe_trade": float(pnls.mean() / pnls.std(ddof=1)) if len(pnls) > 2 and pnls.std(ddof=1) > 0 else 0.0,
            "trades_per_day": len(trades) / days,
            "max_hold_bars": int(np.max([t.bars_held for t in trades])),
        }

    def _verdict(self, metrics: dict[str, Any]) -> tuple[bool, list[str]]:
        """Пороги «можно ли вообще идти дальше»."""
        cfg = self.cfg
        notes: list[str] = []
        ok = True

        if metrics["trades"] < cfg.min_trades:
            ok = False
            notes.append(
                f"Сделок {metrics['trades']} < минимума {cfg.min_trades}: статистики нет, "
                "результат может быть случайностью."
            )
        if metrics["profit_factor"] < cfg.min_profit_factor:
            ok = False
            notes.append(
                f"Profit factor {metrics['profit_factor']:.2f} < {cfg.min_profit_factor}: "
                "доход не покрывает убытки с запасом."
            )
        if metrics["max_drawdown_pct"] > cfg.max_allowed_dd_pct:
            ok = False
            notes.append(
                f"Просадка {metrics['max_drawdown_pct']:.1f}% > {cfg.max_allowed_dd_pct}%: "
                "психологически и финансово неприемлемо."
            )
        if metrics["win_rate"] < cfg.min_win_rate_with_rr and metrics["trades"] > 0:
            notes.append(
                f"Win rate {metrics['win_rate'] * 100:.1f}% ниже ориентира "
                f"{cfg.min_win_rate_with_rr * 100:.0f}% — проверь, покрывает ли R:R этот уровень."
            )
        if metrics["expectancy"] <= 0:
            ok = False
            notes.append("Матожидание на сделку ≤ 0 — стратегия теряет деньги.")
        if metrics["trades_per_day"] > 20:
            notes.append(
                f"Очень много сделок ({metrics['trades_per_day']:.1f} в день) — на реале "
                "вырастут проскальзывание и комиссии."
            )
        if metrics["costs_total"] > abs(metrics["expectancy"] * metrics["trades"]) * 0.5 and metrics["trades"] > 0:
            notes.append("Издержки съедают больше половины прибыли — ищи более редкие, но качественные входы.")
        if not notes:
            notes.append("Метрики в норме. Следующий шаг — демо-счёт на 1–3 месяца.")
        return ok, notes


def _count_by(trades: list[TradeRecord], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in trades:
        key = str(getattr(trade, field_name))
        counts[key] = counts.get(key, 0) + 1
    return counts


def compare_to_reality(result: BacktestResult, real_pnl: float, real_trades: int) -> str:
    """Сравнить бэктест с реальными результатами — ловим расхождение.

    Если на демо результаты заметно хуже бэктеста — значит в бэктесте есть
    оптимизм (спред, проскальзывание, режим рынка). Это нормальный сигнал, а не
    повод «подкрутить бэктест».
    """
    if real_trades == 0:
        return "Реальных сделок ещё нет — сравнивать нечего."
    expected = result.metrics.get("expectancy", 0.0) * real_trades
    ratio = real_pnl / expected if expected else float("inf")
    text = (
        f"Ожидалось (по бэктесту): {expected:+.2f} | факт: {real_pnl:+.2f} "
        f"на {real_trades} сделках"
    )
    if expected and ratio < 0.5:
        text += " → факт хуже бэктеста: увеличь запас по издержкам и проверь исполнение."
    elif expected and ratio > 1.5:
        text += " → факт лучше бэктеста: будь осторожен, это может быть везение."
    else:
        text += " → расхождение в пределах нормы."
    return text
