"""Демонстрационный прогон агента на виртуальном рынке (без MT5).

Цель — увидеть, как агент думает и считает, ещё до того, как ты поднимешь
MetaTrader 5. Цены берутся из синтетического ряда, но **весь код агента
настоящий**: состояние, лимиты, размер позиции, безубыток, учёт закрытых сделок,
отчёты и дашборд.

    python tools/demo_cycle.py                      # 120 циклов, шаг 2 бара
    python tools/demo_cycle.py --cycles 40 --step 4 --quiet
    python tools/demo_cycle.py --model logistic     # быстро и без обучения на GB

ЧЕГО ЭТО НЕ ДОКАЗЫВАЕТ: ничего про доходность на реальном рынке. Виртуальный
рынок не знает про реквоты, свопы, изменение спреда и проскальзывание. Это
проверка программы, а не стратегии.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import mt5_agent.agent as agent_module  # noqa: E402
from mt5_agent.agent import TradingAgent  # noqa: E402
from mt5_agent.config import ensure_runtime_dirs, load_config  # noqa: E402
from mt5_agent.execution import OrderExecutor  # noqa: E402
from mt5_agent.features import build_dataset, cost_pct_from_spec  # noqa: E402
from mt5_agent.logging_setup import setup_logging  # noqa: E402
from mt5_agent.model import train_signal_model  # noqa: E402
from mt5_agent.virtual_broker import VirtualBroker  # noqa: E402


def train_demo_model(cfg, broker: VirtualBroker):
    """Быстро обучить модель на той же синтетике (только для демонстрации работы)."""
    symbol_cfg = cfg.trading.enabled_symbols[0]
    spec = broker.spec
    frame = broker.bars.iloc[: broker.warmup_bars].copy()
    frame.attrs["point"] = spec.point
    frame.attrs["digits"] = spec.digits
    price = float(frame["close"].iloc[-1])
    cost_pct = cost_pct_from_spec(
        broker.spread_points, spec.point, price,
        commission_per_lot=cfg.backtest.commission_per_lot,
        spec_value_per_point_per_lot=spec.value_per_point_per_lot,
    )
    dataset = build_dataset(
        frame, lookbacks=cfg.signal.feature_lookbacks, atr_period=symbol_cfg.atr_period,
        horizon_bars=cfg.signal.horizon_bars, cost_pct=cost_pct, atr_mult=0.15,
        symbol=symbol_cfg.name,
    )
    model, report = train_signal_model(
        dataset, symbol=symbol_cfg.name, timeframe=symbol_cfg.timeframe,
        horizon_bars=cfg.signal.horizon_bars, kind="logistic",
        train_fraction=0.75, embargo_bars=4, folds=2,
    )
    print(f"[демо] модель обучена: {report.n_samples} примеров, "
          f"AUC(holdout)={report.holdout_auc:.3f}, порог в отчёте "
          f"{report.recommended_threshold:.2f}")
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Демо-прогон агента на виртуальном рынке")
    parser.add_argument("--cycles", type=int, default=120, help="сколько циклов агента выполнить")
    parser.add_argument("--step", type=int, default=2, help="на сколько баров двигать рынок за цикл")
    parser.add_argument("--bars", type=int, default=6000, help="длина синтетического ряда")
    parser.add_argument("--balance", type=float, default=1000.0, help="стартовый виртуальный баланс")
    parser.add_argument("--model", default="logistic", choices=["logistic", "gradient_boosting"],
                        help="какую модель быстро обучить для демонстрации")
    parser.add_argument("--quiet", action="store_true", help="печатать короче")
    parser.add_argument("--workdir", default="state/demo",
                        help="изолированная папка для состояния демо-прогона "
                             "(по умолчанию state/demo: реальное состояние агента не затрагивается)")
    args = parser.parse_args(argv)

    cfg = load_config()
    # Демонстрационный прогон живёт в своей папке: state/agent_state.json настоящего
    # агента, лимиты и halt — не трогаем. Иначе виртуальные сделки могли бы
    # заблокировать реальную торговлю.
    workdir = Path(args.workdir)
    if not workdir.is_absolute():
        workdir = cfg.root_dir / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    cfg.root_dir = workdir
    ensure_runtime_dirs(cfg)
    setup_logging(cfg.path("logs/demo.log"), level="WARNING",
                  events_file=cfg.root_dir / "logs" / "events.jsonl")

    from mt5_agent.data_io import synthetic_bars

    broker = VirtualBroker(
        symbol=cfg.trading.enabled_symbols[0].name,
        timeframe=cfg.trading.enabled_symbols[0].timeframe,
        balance=args.balance,
        bars=synthetic_bars(cfg.trading.enabled_symbols[0].name,
                            cfg.trading.enabled_symbols[0].timeframe,
                            bars=args.bars, seed=11),
        warmup_bars=min(800, args.bars // 4),
    )

    print("=" * 78)
    print(" ДЕМО-ПРОГОН НА ВИРТУАЛЬНОМ РЫНКЕ")
    print(" Внимание: синтетические цены! Это проверка работы программы, а не стратегии.")
    print(f" Состояние прогона: {workdir} (реальное состояние агента не затрагивается)")
    print("=" * 78)

    model = train_demo_model(cfg, broker)

    agent = TradingAgent(cfg)
    agent.broker = broker
    agent.executor = OrderExecutor(broker, exec_cfg=cfg.execution, risk_cfg=cfg.risk, dry_run=False)
    agent.account_desc = broker.account().describe()
    agent._model_for = lambda symbol_cfg: model  # noqa: SLF001 - демонстрационный прогон

    # Единые часы: агент должен видеть время виртуального рынка, а не системное,
    # иначе смена дня/недели и паузы после убытков не сработают.
    agent_module.utc_now = lambda: broker.current_time  # type: ignore[assignment]

    for cycle in range(1, args.cycles + 1):
        report = agent.run_once()
        events = broker.advance(args.step)
        if not args.quiet:
            print(report.summary_text())
            for event in events:
                print(f"    рынок: {event}")
        elif cycle % 20 == 0:
            print(f"[{cycle:>4}/{args.cycles}] {broker.summary()}")

    print("=" * 78)
    print(" ИТОГИ ДЕМО-ПРОГОНА")
    print("=" * 78)
    print(broker.summary())
    print(agent.risk.summary(broker.equity))
    print(f"\nАртефакты: {workdir}/state/dashboard.json, {workdir}/state/agent_state.json, "
          f"{workdir}/reports/last_cycle.txt, {workdir}/logs/events.jsonl")
    print(f"Дашборд: .venv\\Scripts\\python.exe tools\\dashboard.py --root \"{workdir}\"")
    print("\nПомни: на синтетике доходность ничего не значит. Следующий шаг —"
          " подключить ДЕМО-счёт MT5 (check → fetch → train → backtest → run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
