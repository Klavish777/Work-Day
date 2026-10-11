"""Расчёт объёма позиции. Математика, от которой зависят деньги.

Формула одна и та же и в бэктесте, и в live:

    loss на 1.0 лот  = (SL_в_цене / point) * value_per_point_per_lot
    объём            = equity * risk_pct / 100 / loss_на_1_лот

Затем объём **округляется вниз** к шагу лота (округление вверх увеличило бы
риск сверх лимита) и проверяется на кучу ограничений брокера. Если даже
минимальный лот рискует больше разрешённого — сделка ОТКЛОНЯЕТСЯ, а не
«округляется до минимального»: это одна из самых частых дыр в самодельных
ботах.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .specs import SymbolSpec
from .utils import floor_to_step

# Допустимое превышение лимита риска из-за округления и проскальзывания.
RISK_TOLERANCE = 1.05


@dataclass
class SizingResult:
    approved: bool
    volume: float = 0.0
    risk_money: float = 0.0
    risk_pct: float = 0.0
    sl_price_distance: float = 0.0
    tp_price_distance: float = 0.0
    reward_money: float = 0.0
    reward_risk: float = 0.0
    margin_required: float | None = None
    leverage_used: float | None = None
    notes: list[str] = field(default_factory=list)
    reject_reason: str | None = None

    def describe(self) -> str:
        if not self.approved:
            return f"ОТКАЗ: {self.reject_reason}"
        return (
            f"{self.volume:.2f} лот | риск {self.risk_money:.2f} "
            f"({self.risk_pct:.2f}% equity) | SL {self.sl_price_distance:.5f} "
            f"| TP {self.tp_price_distance:.5f} | R:R {self.reward_risk:.2f}"
        )


def normalize_volume(volume: float, spec: SymbolSpec) -> float:
    """Округлить объём ВНИЗ к шагу и загнать в [min, max]."""
    step = spec.volume_step
    vol = floor_to_step(volume, step)
    # Защита от накопления ошибки float (0.30000000000000004 -> 0.30)
    digits = max(0, len(str(step).split(".")[-1])) if "." in str(step) else 0
    vol = round(vol, digits + 1)
    vol = min(vol, spec.volume_max)
    return max(vol, 0.0)


def loss_for_volume(spec: SymbolSpec, sl_price_distance: float, volume: float) -> float:
    """Убыток в валюте депозита, если цена пройдёт SL при данном объёме."""
    return spec.money_for_price_distance(sl_price_distance, volume, for_loss=True)


def size_position(
    spec: SymbolSpec,
    *,
    equity: float,
    risk_pct: float,
    sl_price_distance: float,
    tp_price_distance: float | None = None,
    max_leverage: float | None = None,
    margin_per_lot: float | None = None,
    comment: str = "",
) -> SizingResult:
    """Подобрать объём под риск ``risk_pct`` от ``equity``.

    ``margin_per_lot`` берётся из ``mt5.order_calc_margin`` (в бэктесте —
    моделируется). Если он неизвестен, проверка на плечо пропускается, но об
    этом пишется note.
    """
    notes: list[str] = []
    result = SizingResult(approved=False, sl_price_distance=sl_price_distance)

    # --- базовые проверки ----------------------------------------------------
    if equity <= 0:
        result.reject_reason = "equity <= 0"
        return result
    if not (0 < risk_pct <= 1.0):
        result.reject_reason = f"risk_pct={risk_pct} вне (0, 1]"
        return result
    if sl_price_distance <= 0:
        result.reject_reason = "нулевой или отрицательный стоп-лосс"
        return result
    if not spec.trade_allowed:
        result.reject_reason = f"{spec.name}: торговля по инструменту запрещена брокером"
        return result

    min_dist = spec.min_stop_distance_price()
    if sl_price_distance < min_dist:
        result.reject_reason = (
            f"SL ближе минимально допустимого брокером "
            f"({spec.to_points(sl_price_distance):.1f} < {spec.to_points(min_dist):.1f} пт)"
        )
        return result
    if tp_price_distance is not None and 0 < tp_price_distance < min_dist:
        result.reject_reason = (
            f"TP ближе минимально допустимого брокером "
            f"({spec.to_points(tp_price_distance):.1f} < {spec.to_points(min_dist):.1f} пт)"
        )
        return result

    # --- собственно расчёт ---------------------------------------------------
    risk_money = equity * risk_pct / 100.0
    loss_per_lot = loss_for_volume(spec, sl_price_distance, 1.0)
    if loss_per_lot <= 0:
        result.reject_reason = (
            f"{spec.name}: не удалось посчитать стоимость пункта "
            "(tick_value_loss=0?) — проверь спецификацию символа"
        )
        return result

    raw_volume = risk_money / loss_per_lot
    volume = normalize_volume(raw_volume, spec)

    # Ограничение по плечу (марже).
    if margin_per_lot is not None and max_leverage is not None and margin_per_lot > 0:
        max_volume_by_leverage = equity * max_leverage / margin_per_lot
        capped = normalize_volume(max_volume_by_leverage, spec)
        if capped < volume:
            notes.append(
                f"объём урезан плечом: {volume:.2f} -> {capped:.2f} "
                f"(маржа {margin_per_lot:.2f}/лот, лимит x{max_leverage:g})"
            )
            volume = capped
        result.leverage_used = round(
            (volume * margin_per_lot) / equity, 2
        ) if equity else None
    else:
        notes.append("проверка плеча не выполнялась (нет margin_per_lot)")

    if volume < spec.volume_min:
        min_loss = loss_for_volume(spec, sl_price_distance, spec.volume_min)
        min_loss_pct = min_loss / equity * 100.0
        result.reject_reason = (
            f"{spec.name}: под риск {risk_pct:.2f}% ({risk_money:.2f}) объём получается "
            f"{raw_volume:.4f}, минимум у брокера {spec.volume_min:.2f} лот, что даёт риск "
            f"{min_loss:.2f} ({min_loss_pct:.2f}%). Сделка отклонена — не раздуваем риск."
        )
        result.notes = notes
        return result

    actual_risk = loss_for_volume(spec, sl_price_distance, volume)
    if actual_risk > risk_money * RISK_TOLERANCE:
        result.reject_reason = (
            f"после округления риск {actual_risk:.2f} превышает лимит "
            f"{risk_money:.2f} более чем на {(RISK_TOLERANCE - 1) * 100:.0f}%"
        )
        result.notes = notes
        return result

    result.approved = True
    result.volume = volume
    result.risk_money = round(actual_risk, 2)
    result.risk_pct = round(actual_risk / equity * 100.0, 3)
    result.tp_price_distance = tp_price_distance or 0.0
    if tp_price_distance:
        result.reward_money = round(
            spec.money_for_price_distance(tp_price_distance, volume, for_loss=False), 2
        )
        result.reward_risk = round(tp_price_distance / sl_price_distance, 2)
    result.margin_required = round(margin_per_lot * volume, 2) if margin_per_lot else None
    result.notes = notes + ([comment] if comment else [])
    return result


def estimate_margin_per_lot(
    spec: SymbolSpec, price: float, leverage: int | None
) -> float | None:
    """Грубая оценка маржи на 1.0 лот, если брокер не отдал order_calc_margin.

    Для Forex: contract_size * price / leverage (для котируемых в USD пар).
    Для прочих инструментов формула иная — поэтому оценка помечается как
    приблизительная и используется только для грубого предохранителя.
    """
    if leverage and leverage > 0:
        return spec.contract_size * price / leverage
    return None
