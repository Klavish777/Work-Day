"""Командный интерфейс агента.

    python -m mt5_agent check          # проверка окружения и связи
    python -m mt5_agent fetch          # скачать бары и спецификации символов
    python -m mt5_agent train          # обучить модель сигнала
    python -m mt5_agent backtest       # прогнать бэктест и получить вердикт
    python -m mt5_agent run            # запустить торговый цикл (демо!)
    python -m mt5_agent status         # что происходит сейчас
    python -m mt5_agent panel          # графическая панель управления
    python -m mt5_agent stop           # мягкая остановка (позиции не закрываются)
    python -m mt5_agent kill           # аварийный стоп (создать KILL_SWITCH)
    python -m mt5_agent unkill         # снять аварийный стоп

Каждая команда безопасна по умолчанию: real-деньги требуют трёх независимых
подтверждений (см. ``docs/SAFETY.md``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from .config import Config, ConfigError, ensure_runtime_dirs, load_config
from .exceptions import AgentError, SafetyError
from .logging_setup import setup_logging
from .utils import UTC, fmt_money, utc_now

BOLD, RESET, GREEN, YELLOW, RED, DIM = "\033[1m", "\033[0m", "\033[92m", "\033[93m", "\033[91m", "\033[2m"


def _utf8_console() -> None:
    """Не дать Windows-консоли упасть на русских буквах и рамках."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover
                pass


# -----------------------------------------------------------------------------
# Общие помощники
# -----------------------------------------------------------------------------

def _load(args) -> Config:
    cfg = load_config(args.config)
    ensure_runtime_dirs(cfg)
    logger = setup_logging(
        cfg.path(cfg.notifications.log_file),
        level=args.log_level or cfg.notifications.log_level,
        max_bytes=cfg.notifications.log_max_bytes,
        backup_count=cfg.notifications.log_backup_count,
        events_file=cfg.root_dir / "logs" / "events.jsonl",
    )
    for warning in cfg.validate():
        logger.warning("Конфиг: %s", warning)
    return cfg


def _print_header(text: str) -> None:
    print(f"\n{BOLD}{'=' * 72}\n{text}\n{'=' * 72}{RESET}")


def _ask(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [yes/нет]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("yes", "y", "да", "д")


def _specs_for(cfg: Config, args, store) -> dict:
    """Спецификации символов: из data/symbol_specs.yaml, иначе fallback."""
    from .specs import fallback_spec

    specs = store.load_specs()
    wanted = {s.name for s in cfg.trading.enabled_symbols}
    if args.symbol:
        wanted = {args.symbol.upper()}
    result = {}
    for name in wanted:
        if name in specs:
            result[name] = specs[name]
        else:
            result[name] = fallback_spec(name)
    return result, bool(specs)


def _load_or_make_bars(cfg: Config, store, symbol: str, timeframe: str, args):
    """Бары: локальный CSV или синтетика (для проверки кода без терминала)."""
    from .data_io import synthetic_bars

    if args.synthetic or not store.exists(symbol, timeframe):
        if not args.synthetic:
            print(f"{YELLOW}Нет локального файла данных для {symbol} {timeframe} — "
                  f"использую синтетические бары (это проверка кода, не стратегии!).{RESET}")
        frame = synthetic_bars(symbol, timeframe, bars=args.bars or 20_000)
        return frame, True
    return store.load_bars(symbol, timeframe), False


# -----------------------------------------------------------------------------
# check
# -----------------------------------------------------------------------------

def cmd_check(args) -> int:
    cfg = _load(args)
    _print_header("ПРОВЕРКА ОКРУЖЕНИЯ")

    print(f"Проект: {cfg.root_dir}")
    print(f"Режим: {cfg.agent.mode.upper()}")
    print(f"Демо-счёт обязателен (safety.demo_only): {cfg.safety.demo_only}")
    print(f"Kill switch: {'АКТИВЕН ⛔️' if cfg.path(cfg.safety.kill_switch_file).exists() else 'снят ✅'}")
    print(f"Символы: {', '.join(s.name for s in cfg.trading.enabled_symbols) or 'нет'}")

    print(f"\n{BOLD}Риск-модуль{RESET}")
    print(f"  риск на сделку: {cfg.risk.risk_per_trade_pct}% | день: -{cfg.risk.max_daily_loss_pct}% "
          f"| неделя: -{cfg.risk.max_weekly_loss_pct}% | просадка: -{cfg.risk.max_drawdown_pct}%")
    print(f"  максимум позиций: {cfg.risk.max_open_positions} | сделок в день: {cfg.risk.max_daily_trades}")
    print("  мартингейл/сетки: запрещены архитектурно ✅")

    print(f"\n{BOLD}Python{RESET}")
    print(f"  {sys.version.split()[0]}")
    try:
        import MetaTrader5  # noqa: F401

        print(f"  MetaTrader5: установлен ✅ ({MetaTrader5.__version__})")
        mt5_available = True
    except ImportError:
        print("  MetaTrader5: НЕ установлен (это нормально вне Windows) ⚠️")
        mt5_available = False

    print(f"\n{BOLD}Модели{RESET}")
    from .agent import model_path_for

    for symbol_cfg in cfg.trading.enabled_symbols:
        path = model_path_for(cfg, symbol_cfg)
        mark = "есть ✅" if path.exists() else "нет ⛔️"
        age = ""
        if path.exists():
            seconds = (utc_now() - datetime.fromtimestamp(path.stat().st_mtime, UTC)).total_seconds()
            age = f", возраст {seconds / 86400:.1f} дн."
        print(f"  {symbol_cfg.name}: {path.name} — {mark}{age}")

    if mt5_available:
        _print_header("ПОДКЛЮЧЕНИЕ К ТЕРМИНАЛУ")
        from .broker import MT5Broker, MT5Credentials

        prefix = "MT5_REAL" if args.mode == "live" else "MT5"
        credentials = MT5Credentials.from_env(prefix).merged(cfg.mt5)
        if not credentials.login:
            print(f"{RED}Не задан {prefix}_LOGIN в .env — пропускаю проверку связи{RESET}")
        else:
            broker = MT5Broker(credentials, reconnect_attempts=1)
            try:
                snapshot = broker.connect()
                print(snapshot.describe())
                print(f"  Демо-счёт: {'да ✅' if snapshot.is_demo else 'НЕТ ⛔️ (это реальные деньги)'}")
                print(f"  Автоторговля разрешена: {'да ✅' if snapshot.trade_allowed else 'НЕТ ⛔️'}")
                for symbol_cfg in cfg.trading.enabled_symbols:
                    try:
                        spec = broker.symbol_spec(symbol_cfg.name)
                        tick = broker.tick(symbol_cfg.name, spec)
                        print(f"  {symbol_cfg.name}: bid={tick.bid} ask={tick.ask} "
                              f"спред={tick.spread_points} пт | digits={spec.digits} "
                              f"step={spec.volume_step} tick_value_loss={spec.tick_value_loss}")
                    except Exception as exc:
                        print(f"  {symbol_cfg.name}: ошибка — {exc}")
            except AgentError as exc:
                print(f"{RED}{exc}{RESET}")
            finally:
                broker.shutdown()

    print(f"\n{GREEN}Готово.{RESET} Если что-то помечено ⛔️ — исправь до запуска торговли.")
    return 0


# -----------------------------------------------------------------------------
# fetch
# -----------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    cfg = _load(args)
    from .broker import MT5Broker, MT5Credentials
    from .data_io import DataStore

    store = DataStore(cfg.root_dir)
    credentials = MT5Credentials.from_env("MT5").merged(cfg.mt5)
    if not credentials.login:
        print(f"{RED}В .env не задан MT5_LOGIN — скачать данные не получится.{RESET}")
        return 2
    broker = MT5Broker(credentials)
    broker.connect()
    try:
        symbols = ([cfg.trading.by_name(args.symbol)] if args.symbol else cfg.trading.enabled_symbols)
        specs = {}
        for symbol_cfg in symbols:
            if symbol_cfg is None:
                print(f"{RED}Символ {args.symbol} не описан в config.yaml{RESET}")
                return 2
            # --timeframe позволяет скачать «запасной» таймфрейм, не меняя конфиг
            timeframe = (args.timeframe or symbol_cfg.timeframe).upper()
            if args.timeframe and args.timeframe.upper() != symbol_cfg.timeframe:
                print(f"{DIM}{symbol_cfg.name}: таймфрейм {timeframe} (в конфиге "
                      f"{symbol_cfg.timeframe} — сигналы считаются по нему){RESET}")
            spec = broker.symbol_spec(symbol_cfg.name)
            specs[symbol_cfg.name] = spec
            frame = broker.rates(symbol_cfg.name, timeframe, args.bars)
            path = store.save_bars(symbol_cfg.name, timeframe, frame)
            print(f"{symbol_cfg.name} {timeframe}: {len(frame)} баров → {path.name} "
                  f"({frame.index[0]} .. {frame.index[-1]})")
        if args.save_specs:
            path = store.save_specs(specs)
            print(f"Спецификации символов сохранены: {path}")
            print(f"{DIM}Эти значения сняты с твоего брокера — используй их в бэктесте.{RESET}")
    finally:
        broker.shutdown()
    return 0


# -----------------------------------------------------------------------------
# train
# -----------------------------------------------------------------------------

def cmd_train(args) -> int:
    cfg = _load(args)
    from .data_io import DataStore
    from .features import build_dataset, feature_columns
    from .model import train_signal_model

    store = DataStore(cfg.root_dir)
    specs, from_broker = _specs_for(cfg, args, store)
    symbols = ([cfg.trading.by_name(args.symbol)] if args.symbol else cfg.trading.enabled_symbols)
    symbols = [s for s in symbols if s is not None]
    if not symbols:
        print(f"{RED}Нечего обучать: проверь --symbol и config.yaml{RESET}")
        return 2

    reports_dir = cfg.root_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0

    for symbol_cfg in symbols:
        spec = specs.get(symbol_cfg.name)
        print(f"\n{BOLD}Обучение модели: {symbol_cfg.name} {symbol_cfg.timeframe}{RESET}")
        frame, is_synthetic = _load_or_make_bars(cfg, store, symbol_cfg.name, symbol_cfg.timeframe, args)
        if is_synthetic:
            print(f"{YELLOW}Данные синтетические → выводы о стратегии делать НЕЛЬЗЯ. "
                  f"Это проверка того, что код работает.{RESET}")
        if len(frame) < cfg.signal.horizon_bars + 300:
            print(f"{RED}Слишком мало баров: {len(frame)}{RESET}")
            exit_code = 2
            continue

        # издержки в долях цены — их должна перекрывать целевая переменная
        price = float(frame["close"].iloc[-1])
        spread_points = spec.spread_points if spec.spread_points > 0 else cfg.backtest.spread_points
        from .features import cost_pct_from_spec

        cost_pct = cost_pct_from_spec(
            spread_points, spec.point, price,
            commission_per_lot=cfg.backtest.commission_per_lot,
            spec_value_per_point_per_lot=spec.value_per_point_per_lot,
        )

        dataset = build_dataset(
            frame,
            lookbacks=cfg.signal.feature_lookbacks,
            atr_period=symbol_cfg.atr_period,
            horizon_bars=cfg.signal.horizon_bars,
            cost_pct=cost_pct,
            atr_mult=0.15,          # требуем движение «чуть больше» издержек
            symbol=symbol_cfg.name,
        )
        if len(dataset) < 1500:
            print(f"{YELLOW}Мало данных после расчёта фич: {len(dataset)} строк — результат будет "
                  f"неустойчивым.{RESET}")
        if args.features_only:
            print(f"Фич: {len(feature_columns(dataset))} | строк: {len(dataset)}")
            print(", ".join(feature_columns(dataset)))
            continue

        model, report = train_signal_model(
            dataset,
            symbol=symbol_cfg.name,
            timeframe=symbol_cfg.timeframe,
            horizon_bars=cfg.signal.horizon_bars,
            kind=cfg.signal.model,
            train_fraction=cfg.signal.train_test_split,
            embargo_bars=cfg.signal.embargo_bars,
            folds=cfg.signal.walk_forward_folds,
        )
        from .agent import model_path_for

        model_path = model_path_for(cfg, symbol_cfg)
        model.meta.notes = report.notes
        model.meta.recommended_threshold = report.recommended_threshold
        model.save(model_path)

        report_path = reports_dir / f"train_{symbol_cfg.name}_{symbol_cfg.timeframe}.txt"
        report_path.write_text(report.to_text(), encoding="utf-8")
        print(report.to_text())
        print(f"{DIM}Модель: {model_path}\nОтчёт: {report_path}{RESET}")

        if report.holdout_auc < 0.53:
            print(f"{RED}AUC на отложенном тесте {report.holdout_auc:.3f} — модель почти не "
                  f"отличает движение от шума. Торговать по ней нельзя.{RESET}")
            exit_code = 1
        recommended = report.recommended_threshold
        if abs(recommended - cfg.signal.min_probability) > 0.05:
            print(f"{YELLOW}Рекомендуемый порог {recommended:.2f} отличается от текущего "
                  f"{cfg.signal.min_probability:.2f} в config.yaml — подумай, прежде чем менять: "
                  f"порог, подобранный на истории, легко переподогнать.{RESET}")
        if from_broker:
            print(f"{DIM}Спецификации взяты из data/symbol_specs.yaml (с твоего брокера).{RESET}")
        else:
            print(f"{YELLOW}Спецификации символов — примерные (fallback). Для реальных расчётов "
                  f"сними их с брокера: python -m mt5_agent fetch --save-specs{RESET}")
    return exit_code


# -----------------------------------------------------------------------------
# backtest
# -----------------------------------------------------------------------------

def cmd_backtest(args) -> int:
    cfg = _load(args)
    from .backtest import Backtester
    from .data_io import DataStore
    from .features import build_dataset, cost_pct_from_spec
    from .model import train_signal_model

    store = DataStore(cfg.root_dir)
    specs, from_broker = _specs_for(cfg, args, store)
    symbols = ([cfg.trading.by_name(args.symbol)] if args.symbol else cfg.trading.enabled_symbols)
    symbols = [s for s in symbols if s is not None]
    reports_dir = cfg.root_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    overall_ok = True

    for symbol_cfg in symbols:
        spec = specs.get(symbol_cfg.name)
        print(f"\n{BOLD}Бэктест: {symbol_cfg.name} {symbol_cfg.timeframe}{RESET}")
        frame, is_synthetic = _load_or_make_bars(cfg, store, symbol_cfg.name, symbol_cfg.timeframe, args)
        if args.days and not is_synthetic and len(frame):
            cutoff = frame.index[-1] - timedelta(days=int(args.days))
            frame = frame[frame.index >= cutoff]
        if is_synthetic:
            print(f"{YELLOW}Данные синтетические — вердикт по метрикам относится к КОДУ, "
                  f"а не к стратегии.{RESET}")

        price = float(frame["close"].iloc[-1])
        spread_points = spec.spread_points if spec.spread_points > 0 else cfg.backtest.spread_points
        cost_pct = cost_pct_from_spec(
            spread_points, spec.point, price,
            commission_per_lot=cfg.backtest.commission_per_lot,
            spec_value_per_point_per_lot=spec.value_per_point_per_lot,
        )
        dataset = build_dataset(
            frame,
            lookbacks=cfg.signal.feature_lookbacks,
            atr_period=symbol_cfg.atr_period,
            horizon_bars=cfg.signal.horizon_bars,
            cost_pct=cost_pct,
            atr_mult=0.15,
            symbol=symbol_cfg.name,
        )
        if args.features_only:
            columns = [c for c in dataset.columns
                       if c not in ("target", "forward_return", "threshold", "close", "atr")]
            print(f"Фич: {len(columns)} | строк: {len(dataset)}")
            print(", ".join(columns))
            continue

        # Модель обучаем на первой части, симулируем — на последней (out-of-sample).
        model, train_report = train_signal_model(
            dataset,
            symbol=symbol_cfg.name,
            timeframe=symbol_cfg.timeframe,
            horizon_bars=cfg.signal.horizon_bars,
            kind=cfg.signal.model,
            train_fraction=cfg.signal.train_test_split,
            embargo_bars=cfg.signal.embargo_bars,
            folds=cfg.signal.walk_forward_folds,
        )
        features = dataset.drop(columns=["target", "forward_return", "threshold"])
        probabilities = model.predict_proba_up(features.drop(columns=["close", "atr"], errors="ignore"))

        split_at = 0 if args.in_sample else int(len(dataset) * cfg.signal.train_test_split)
        if args.in_sample:
            print(f"{YELLOW}--in-sample: модель проверяется на тех же данных, где училась. "
                  f"Результат будет завышен — доверять ему нельзя.{RESET}")

        backtester = Backtester(
            cfg.backtest,
            spec=spec,
            symbol_cfg=symbol_cfg,
        )
        result = backtester.run(
            frame.loc[dataset.index],
            probabilities.reindex(dataset.index),
            atr_series=dataset["atr"],
            min_probability=cfg.signal.min_probability,
            allow_short=cfg.signal.allow_short,
            apply_filters=not args.no_filters,
            start_index=split_at,
        )

        # Реалистичный спред для бэктеста: берём худший из конфига и данных
        text = result.to_text()
        if not from_broker:
            text += ("\n\n⚠️ Спецификации символов примерные (fallback). Сними реальные с брокера: "
                     "python -m mt5_agent fetch --save-specs")
        print(text)
        overall_ok = overall_ok and result.verdict_ok

        (reports_dir / f"backtest_{symbol_cfg.name}_{symbol_cfg.timeframe}.txt").write_text(text, encoding="utf-8")
        (reports_dir / f"backtest_{symbol_cfg.name}_{symbol_cfg.timeframe}.json").write_text(
            json.dumps({**result.to_dict(), "synthetic_data": is_synthetic}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (reports_dir / f"train_{symbol_cfg.name}_{symbol_cfg.timeframe}_backtest.txt").write_text(
            train_report.to_text(), encoding="utf-8"
        )
    if not overall_ok:
        print(f"\n{RED}{BOLD}ИТОГ: хотя бы один символ не прошёл пороги. На демо в таком виде выходить "
              f"нельзя — правь гипотезу, а не пороги.{RESET}")
        return 1
    print(f"\n{GREEN}{BOLD}ИТОГ: метрики в пределах порогов. Следующий шаг — демо-счёт на 1–3 месяца.{RESET}")
    return 0


# -----------------------------------------------------------------------------
# run
# -----------------------------------------------------------------------------

def cmd_run(args) -> int:
    cfg = _load(args)
    from .agent import TradingAgent
    from .notifications import Notifier

    mode = args.mode or cfg.agent.mode
    if mode == "live":
        print(f"\n{RED}{BOLD}!!! РЕЖИМ REAL !!!{RESET}")
        print("Агент будет отправлять ордера на РЕАЛЬНЫЙ счёт.")
        print("Правила, которые нельзя обойти: только отдельный счёт; только сумма, "
              "которую готов потерять; ежемесячная сверка проскальзывания и издержек.")
        if not args.i_understand_live:
            print(f"{YELLOW}Нужен явный флаг --i-understand-live.{RESET}")
            return 2
        if not _ask("Точно запускаем на реальном счёте?"):
            print("Отменено.")
            return 0

    notifier = Notifier(cfg.notifications)
    agent = TradingAgent(cfg, dry_run=args.dry_run, mode_override=mode, once=args.once)
    try:
        agent.start()
    except SafetyError as exc:
        print(f"{RED}{BOLD}ОСТАНОВКА ПО БЕЗОПАСНОСТИ:{RESET} {exc}")
        notifier.error(f"Остановка по безопасности: {exc}")
        return 3
    except AgentError as exc:
        print(f"{RED}Ошибка: {exc}{RESET}")
        return 2
    return 0


# -----------------------------------------------------------------------------
# status / kill / unkill
# -----------------------------------------------------------------------------

def cmd_stop(args) -> int:
    """Мягкая остановка агента: доработает цикл и выйдет, позиции не закрываются."""
    from .control import AgentController

    cfg = load_config(args.config)
    controller = AgentController(config=cfg)
    path = controller.request_stop(args.reason or "остановка из консоли")
    print(f"{YELLOW}Запрошена мягкая остановка.{RESET}")
    print(f"Файл: {path}")
    print("Агент завершит текущий цикл и выйдет. Открытые позиции остаются под защитой "
          "стоп-лоссов у брокера.")
    print(f"{DIM}Если нужно закрыть позиции немедленно — используй kill (аварийный стоп).{RESET}")
    if not controller.snapshot().running:
        print(f"{DIM}Процесс агента сейчас не запущен: запрос будет учтён при следующем старте "
              f"(агент снимет его сам) — но проще просто не запускать агента.{RESET}")
    return 0


def cmd_panel(args) -> int:
    """Запустить панель управления (графическое окно, встроенный tkinter)."""
    script = load_config(args.config).root_dir / "tools" / "agent_gui.py"
    if not script.exists():
        print(f"{RED}Не найден {script}{RESET}")
        return 2
    try:
        import tkinter  # noqa: F401  (проверяем наличие, не используем напрямую)
    except ImportError:
        print(f"{RED}В этой сборке Python нет tkinter, поэтому окно панели недоступно.{RESET}")
        print("На Windows tkinter входит в официальный установщик Python — переустанови Python "
              "с галочкой «tcl/tk and IDLE».")
        return 2
    import subprocess
    import sys as _sys

    print(f"{GREEN}Открываю панель управления…{RESET} {DIM}(закрытие окна завершает панель){RESET}")
    return subprocess.call([_sys.executable, str(script)])


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    from .state import AgentState
    from .utils import file_age_sec, read_json

    dashboard = read_json(cfg.root_dir / "state" / "dashboard.json")
    state = AgentState.load(cfg.root_dir / "state" / "agent_state.json")
    _print_header("СОСТОЯНИЕ АГЕНТА")

    heartbeat = cfg.path(cfg.agent.heartbeat_file)
    age = file_age_sec(heartbeat)
    if age is None:
        print("Агент: не запускался (нет heartbeat-файла)")
    else:
        stale = age > cfg.agent.heartbeat_stale_sec
        color = RED if stale else GREEN
        print(f"Агент: {color}{'НЕ ОТВЕЧАЕТ' if stale else 'работает'}{RESET} "
              f"(последний цикл {age:.0f} сек. назад)")

    print(f"Kill switch: {'АКТИВЕН ⛔️' if cfg.path(cfg.safety.kill_switch_file).exists() else 'снят ✅'}")
    print(f"Остановка (halt): {'ДА: ' + state.halt_reason if state.halted else 'нет'}")
    if (cfg.root_dir / "state" / "STOP_REQUEST").exists():
        print(f"{YELLOW}Запрошена мягкая остановка{RESET} (state/STOP_REQUEST) — агент завершит цикл и выйдет")

    if dashboard:
        account = dashboard.get("account", {})
        print(f"\n{BOLD}Счёт{RESET} ({dashboard.get('mode')})")
        print(f"  {account.get('desc', '')}")
        print(f"  equity {fmt_money(account.get('equity'))} | баланс {fmt_money(account.get('balance'))} "
              f"| день {account.get('pnl_today_pct', 0):+.2f}% | позиций {account.get('open_positions')}")
        risk = dashboard.get("risk", {})
        print(f"\n{BOLD}Риск{RESET}")
        print(f"  {risk.get('summary', '')}")
        positions = dashboard.get("positions") or []
        if positions:
            print(f"\n{BOLD}Открытые позиции{RESET}")
            for pos in positions:
                print(f"  #{pos['ticket']} {pos['symbol']} {pos['side']} {pos['volume']} @ "
                      f"{pos['price_open']} SL {pos['sl']} TP {pos['tp']} P/L {pos['profit']:+.2f}")
        signals = dashboard.get("signals") or []
        if signals:
            print(f"\n{BOLD}Последние сигналы{RESET}")
            for sig in signals:
                print(f"  {sig['symbol']}: {sig['side']} p={sig.get('probability', 0):.3f} — {sig.get('reason', '')}")
        blocked = dashboard.get("blocked") or []
        if blocked:
            print(f"\n{BOLD}Блокировки{RESET}")
            for item in blocked:
                print(f"  {item['symbol']}: {item['reason']}")
        errors = dashboard.get("errors") or []
        if errors:
            print(f"\n{RED}Ошибки последнего цикла{RESET}")
            for error in errors:
                print(f"  {error}")
    else:
        print(f"\n{DIM}Дашборда ещё нет — агент ни разу не завершил полный цикл.{RESET}")
    return 0


def cmd_kill(args) -> int:
    cfg = load_config(args.config)
    path = cfg.path(cfg.safety.kill_switch_file)
    reason = args.reason or "ручная остановка"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{utc_now().isoformat()} | {reason}\n", encoding="utf-8")
    print(f"{RED}{BOLD}KILL SWITCH АКТИВИРОВАН{RESET}")
    print(f"Файл: {path}")
    print("Агент на следующем цикле закроет все свои позиции и остановится.")
    print(f"{DIM}Если агент не запущен — просто не запускай его, пока файл на месте.{RESET}")
    return 0


def cmd_unkill(args) -> int:
    cfg = load_config(args.config)
    path = cfg.path(cfg.safety.kill_switch_file)
    if path.exists():
        path.unlink()
        print(f"{GREEN}Kill switch снят. Остановка (halt) в state тоже сбрасывается.{RESET}")
    else:
        print("Kill switch и так не активен.")
    state_path = cfg.root_dir / "state" / "agent_state.json"
    from .state import AgentState

    state = AgentState.load(state_path)
    if state.halted:
        state.resume()
        state.consecutive_errors = 0
        state.save(state_path)
        print("Флаг остановки (halt) сброшен — агент снова может торговать.")
    return 0


# -----------------------------------------------------------------------------
# Парсер
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mt5-agent",
        description="Контролируемый AI-агент для MetaTrader 5 (ML-сигнал + риск-модуль). "
                    "По умолчанию работает только с ДЕМО-счётом.",
    )
    parser.add_argument("--config", help="путь к config.yaml (по умолчанию config/config.yaml)")
    parser.add_argument("--log-level", help="DEBUG | INFO | WARNING | ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="проверить окружение, конфиг и связь с терминалом")
    p_check.add_argument("--mode", default="paper", choices=["paper", "shadow", "live"])
    p_check.set_defaults(func=cmd_check)

    p_fetch = sub.add_parser("fetch", help="скачать бары и спецификации символов из MT5 (нужен Windows)")
    p_fetch.add_argument("--symbol")
    p_fetch.add_argument("--timeframe")
    p_fetch.add_argument("--bars", type=int, default=20000)
    p_fetch.add_argument("--save-specs", action="store_true",
                         help="сохранить параметры символов (tick_value и др.) в data/symbol_specs.yaml")
    p_fetch.set_defaults(func=cmd_fetch)

    for name, func, help_text in (
        ("train", cmd_train, "обучить модель сигнала (walk-forward, отчёт в reports/)"),
        ("backtest", cmd_backtest, "прогнать бэктест и получить вердикт GO/NO-GO"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--symbol")
        p.add_argument("--bars", type=int, default=20000)
        p.add_argument("--synthetic", action="store_true",
                       help="использовать синтетические данные (проверка кода без терминала)")
        p.add_argument("--features-only", action="store_true",
                       help="только показать набор фич, без обучения")
        p.set_defaults(func=func)

    p_backtest = sub.choices["backtest"]
    p_backtest.add_argument("--days", type=int, help="сколько последних дней истории взять")
    p_backtest.add_argument("--in-sample", action="store_true",
                            help="(не рекомендуется) тест на обучающих данных")
    p_backtest.add_argument("--no-filters", action="store_true",
                            help="отключить фильтры сессии/спреда")

    p_run = sub.add_parser("run", help="запустить торговый цикл")
    p_run.add_argument("--mode", choices=["paper", "shadow", "live"], help="переопределить режим из конфига")
    p_run.add_argument("--once", action="store_true", help="выполнить один цикл и выйти")
    p_run.add_argument("--dry-run", action="store_true", help="считать всё, но НЕ отправлять ордера")
    p_run.add_argument("--i-understand-live", action="store_true", dest="i_understand_live",
                       help="подтверждение запуска на реальном счёте")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="показать текущее состояние агента")
    p_status.set_defaults(func=cmd_status)

    p_panel = sub.add_parser("panel", help="открыть графическую панель управления (Windows, tkinter)")
    p_panel.set_defaults(func=cmd_panel)

    p_stop = sub.add_parser("stop", help="мягкая остановка: доработать цикл и выйти (позиции не закрываются)")
    p_stop.add_argument("--reason", help="причина остановки")
    p_stop.set_defaults(func=cmd_stop)

    p_kill = sub.add_parser("kill", help="аварийный стоп: создать KILL_SWITCH")
    p_kill.add_argument("--reason", help="причина остановки")
    p_kill.set_defaults(func=cmd_kill)

    p_unkill = sub.add_parser("unkill", help="снять аварийный стоп")
    p_unkill.set_defaults(func=cmd_unkill)

    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"{RED}Ошибка конфигурации: {exc}{RESET}")
        return 2
    except SafetyError as exc:
        print(f"{RED}{BOLD}Остановка по безопасности:{RESET} {exc}")
        return 3
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 130
    except AgentError as exc:
        print(f"{RED}Ошибка: {exc}{RESET}")
        return 2
