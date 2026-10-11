"""Спецификация инструмента (символа) — «зеркало» ``mt5.symbol_info``.

Зачем отдельный класс: вся математика (риск, лот, стоимость пункта) должна
работать и в бэктесте, и на живом счёте, и в тестах — без установленного MT5.
Значения по умолчанию соответствуют обычному брокеру с 5-значными котировками.

ВАЖНО: цифры ниже — только пример. Реальные ``tick_value``/``point`` берутся
из твоего терминала (методом :meth:`MT5Broker.symbol_spec`), потому что у
разных брокеров они разные, и ошибка здесь = ошибка в размере риска.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import ConfigError


@dataclass(frozen=True)
class SymbolSpec:
    """Параметры инструмента, влияющие на деньги."""

    name: str
    digits: int = 5
    point: float = 0.00001
    tick_size: float = 0.00001          # trade_tick_size
    tick_value_loss: float = 1.0        # trade_tick_value_loss — USD на 1 тик на 1.0 лот
    tick_value_profit: float = 1.0      # trade_tick_value_profit
    contract_size: float = 100_000.0    # trade_contract_size
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: int = 0         # trade_stops_level
    freeze_level_points: int = 0        # trade_freeze_level
    spread_points: float = 0.0          # текущий спред в пунктах
    currency_profit: str = "USD"
    trade_allowed: bool = True

    def __post_init__(self) -> None:
        if self.point <= 0 or self.tick_size <= 0:
            raise ConfigError(f"{self.name}: point/tick_size должны быть > 0")
        if self.volume_step <= 0:
            raise ConfigError(f"{self.name}: volume_step должен быть > 0")
        if self.volume_min <= 0:
            raise ConfigError(f"{self.name}: volume_min должен быть > 0")
        if self.volume_min > self.volume_max:
            raise ConfigError(f"{self.name}: volume_min > volume_max")

    # -- деньги ---------------------------------------------------------------
    @property
    def value_per_point_per_lot(self) -> float:
        """Сколько USD (в валюте депозита) даёт 1 пункт движения на 1.0 лот."""
        return (self.point / self.tick_size) * self.tick_value_loss

    @property
    def value_per_point_per_lot_profit(self) -> float:
        return (self.point / self.tick_size) * self.tick_value_profit

    def money_for_price_distance(
        self, price_distance: float, volume: float, *, for_loss: bool = True
    ) -> float:
        """Денежный результат движения цены на ``price_distance`` для ``volume``.

        Используется и для расчёта риска (SL), и для оценки прибыли (TP).
        """
        per_point = self.value_per_point_per_lot if for_loss else self.value_per_point_per_lot_profit
        points = abs(price_distance) / self.point
        return points * per_point * volume

    def spread_cost(self, volume: float) -> float:
        """Стоимость спреда в деньгах (то, что ты платишь сразу при входе)."""
        return self.money_for_price_distance(self.spread_points * self.point, volume)

    # -- пункты/цены ----------------------------------------------------------
    def to_points(self, price_distance: float) -> float:
        return abs(price_distance) / self.point

    def to_price(self, points: float) -> float:
        return points * self.point

    def min_stop_distance_price(self, spread_points: float | None = None) -> float:
        """Минимально допустимое расстояние SL/TP от цены входа.

        Берём максимум из требования брокера (stops_level) и текущего спреда,
        плюс один пункт запаса — иначе брокер отклонит ордер (retcode 10016).
        """
        spread = self.spread_points if spread_points is None else spread_points
        level = max(float(self.stops_level_points), float(spread))
        return (level + 1.0) * self.point

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "digits": self.digits,
            "point": self.point,
            "tick_size": self.tick_size,
            "tick_value_loss": self.tick_value_loss,
            "tick_value_profit": self.tick_value_profit,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step,
            "stops_level_points": self.stops_level_points,
            "spread_points": self.spread_points,
        }


def spec_from_dict(data: dict) -> SymbolSpec:
    """Собрать SymbolSpec из словаря (бэктест / тесты / data/symbol_specs.yaml)."""
    known = {f for f in SymbolSpec.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Неизвестные поля спецификации символа: {sorted(unknown)}")
    return SymbolSpec(**data)


# Типовые SPEC-и для бэктеста и тестов. Это ПРИМЕРЫ: перед реальным
# использованием сверь tick_value_loss с терминалом (Спецификация контракта).
FALLBACK_SPECS: dict[str, SymbolSpec] = {
    "EURUSD": SymbolSpec(name="EURUSD", digits=5, point=1e-5, tick_size=1e-5,
                         tick_value_loss=1.0, tick_value_profit=1.0, volume_min=0.01,
                         volume_max=100.0, volume_step=0.01, stops_level_points=0),
    "GBPUSD": SymbolSpec(name="GBPUSD", digits=5, point=1e-5, tick_size=1e-5,
                         tick_value_loss=1.0, tick_value_profit=1.0, volume_min=0.01,
                         volume_max=100.0, volume_step=0.01, stops_level_points=0),
    "USDJPY": SymbolSpec(name="USDJPY", digits=3, point=1e-3, tick_size=1e-3,
                         tick_value_loss=0.68, tick_value_profit=0.68, volume_min=0.01,
                         volume_max=100.0, volume_step=0.01, stops_level_points=0),
    "XAUUSD": SymbolSpec(name="XAUUSD", digits=2, point=0.01, tick_size=0.01,
                         tick_value_loss=1.0, tick_value_profit=1.0, volume_min=0.01,
                         volume_max=50.0, volume_step=0.01, stops_level_points=0),
    "BTCUSD": SymbolSpec(name="BTCUSD", digits=2, point=0.01, tick_size=0.01,
                         tick_value_loss=1.0, tick_value_profit=1.0, volume_min=0.01,
                         volume_max=10.0, volume_step=0.01, stops_level_points=0),
}


def fallback_spec(name: str, **overrides) -> SymbolSpec:
    """Спецификация по имени с возможностью переопределить поля.

    Если символа нет в справочнике — делаем консервативное предположение и
    помечаем это в логе вызывающего кода (жёсткие значения не угадываем молча).
    """
    base = FALLBACK_SPECS.get(name.upper())
    if base is None:
        base = SymbolSpec(name=name.upper(), **{k: v for k, v in overrides.items()
                                                if k in SymbolSpec.__dataclass_fields__})
        return base
    if overrides:
        fields = {f: getattr(base, f) for f in SymbolSpec.__dataclass_fields__}
        fields.update({k: v for k, v in overrides.items() if k in fields})
        fields["name"] = name.upper()
        return SymbolSpec(**fields)
    return base
