"""Обучение и применение ML-модели сигнала.

Модель предсказывает не цену, а **вероятность того, что движение вперёд
превысит издержки** (см. :mod:`mt5_agent.features`). Это принципиально:
предсказание цены даёт красивые картинки, а такое бинарное предсказание с
порогом — измеримую ожидаемую доходность и честную метрику.

Валидация — только time-series walk-forward: обучение и проверка идут по
времени, между ними «забор» (embargo) из ``horizon_bars`` баров, чтобы метки
не протекали из будущего в прошлое.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .exceptions import DataError, ModelNotTrainedError
from .logging_setup import get_logger

log = get_logger("model")

MODEL_KINDS = ("gradient_boosting", "logistic")
NON_FEATURE_COLUMNS = {"target", "forward_return", "threshold", "close", "atr"}


# -----------------------------------------------------------------------------
# Обёртка модели
# -----------------------------------------------------------------------------

@dataclass
class ModelMetadata:
    symbol: str = ""
    timeframe: str = ""
    horizon_bars: int = 4
    model_kind: str = "gradient_boosting"
    feature_names: list[str] = field(default_factory=list)
    trained_at: str = ""
    n_samples: int = 0
    train_auc: float = 0.0
    walk_forward: list[dict] = field(default_factory=list)
    recommended_threshold: float = 0.58
    notes: list[str] = field(default_factory=list)
    version: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ModelMetadata":
        data = json.loads(text)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def make_estimator(kind: str, *, seed: int = 42) -> Pipeline:
    """Создать «сырую» модель внутри Pipeline со скейлером.

    Скейлер внутри Pipeline — принципиально: он обучается только на train-части
    каждого фолда, а не на всей истории (иначе будет утечка будущего).
    """
    if kind not in MODEL_KINDS:
        raise DataError(f"Неизвестный тип модели {kind!r}; допустимо {MODEL_KINDS}")
    if kind == "logistic":
        clf = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    else:
        clf = GradientBoostingClassifier(
            n_estimators=250,
            learning_rate=0.03,
            max_depth=3,
            subsample=0.8,
            min_samples_leaf=50,
            random_state=seed,
        )
    # SimpleImputer — страховка от редких NaN в живом потоке данных (пропущенный
    # бар, деление на ноль). Обучается внутри фолда, как и скейлер.
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", clf),
    ])


class SignalModel:
    """Модель + её метаданные. Загрузка/сохранение вместе (joblib + .json)."""

    def __init__(self, estimator: Pipeline, meta: ModelMetadata) -> None:
        self.estimator = estimator
        self.meta = meta

    # -- применение -----------------------------------------------------------
    @property
    def feature_names(self) -> list[str]:
        return list(self.meta.feature_names)

    def predict_proba_up(self, features: pd.DataFrame) -> pd.Series:
        """Вероятность класса «вверх» для последних баров."""
        if not self.meta.feature_names:
            raise ModelNotTrainedError("У модели нет списка признаков — переобучи её")
        missing = [c for c in self.meta.feature_names if c not in features.columns]
        if missing:
            raise DataError(f"В признаках не хватает колонок: {missing[:5]}")
        x = features[self.meta.feature_names]
        # NaN в живом потоке (пропущенный бар) обрабатывает SimpleImputer внутри
        # обученного пайплайна — та же статистика, что и на обучении.
        proba = self.estimator.predict_proba(x)
        return pd.Series(proba[:, 1], index=features.index, name="p_up")

    # -- файлы ----------------------------------------------------------------
    def save(self, path: str | Path, meta_path: str | Path | None = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.estimator, path)
        meta_target = Path(meta_path) if meta_path else path.with_suffix(".meta.json")
        meta_target.write_text(self.meta.to_json(), encoding="utf-8")
        log.info("Модель сохранена: %s (+ %s)", path, meta_target.name)

    @classmethod
    def load(cls, path: str | Path, meta_path: str | Path | None = None) -> "SignalModel":
        path = Path(path)
        if not path.exists():
            raise ModelNotTrainedError(
                f"Файл модели не найден: {path}. Сначала обучи модель: "
                "python -m mt5_agent train --symbol EURUSD"
            )
        estimator = joblib.load(path)
        meta_target = Path(meta_path) if meta_path else path.with_suffix(".meta.json")
        if meta_target.exists():
            meta = ModelMetadata.from_json(meta_target.read_text(encoding="utf-8"))
        else:
            meta = ModelMetadata(notes=["метаданные не найдены — модель без истории обучения"])
        return cls(estimator, meta)

    @property
    def age_days(self) -> float | None:
        from .utils import UTC, utc_now

        if not self.meta.trained_at:
            return None
        try:
            trained = datetime.fromisoformat(self.meta.trained_at)
        except ValueError:
            return None
        if trained.tzinfo is None:
            trained = trained.replace(tzinfo=UTC)
        return (utc_now() - trained).total_seconds() / 86400.0

    def needs_retrain(self, every_days: int) -> bool:
        age = self.age_days
        return age is None or age >= every_days


# -----------------------------------------------------------------------------
# Обучение
# -----------------------------------------------------------------------------

@dataclass
class TrainReport:
    symbol: str
    n_samples: int
    n_features: int
    train_auc: float
    holdout_auc: float
    folds: list[dict]
    threshold_table: list[dict]
    recommended_threshold: float
    notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            f"=== Отчёт об обучении: {self.symbol} ===",
            f"Примеров: {self.n_samples} | признаков: {self.n_features}",
            f"AUC на train: {self.train_auc:.4f} | AUC на отложенном тесте: {self.holdout_auc:.4f}",
            "",
            "Walk-forward фолды (обучение → проверка):",
            f"{'фолд':>5} {'train':>7} {'test':>6} {'AUC':>7} {'сигналов':>9} {'ср.доход':>10} {'hit':>7}",
        ]
        for fold in self.folds:
            lines.append(
                f"{fold['fold']:>5} {fold['n_train']:>7} {fold['n_test']:>6} "
                f"{fold['auc']:>7.4f} {fold['n_signals']:>9} "
                f"{fold['mean_return_pct']:>9.3f}% {fold['hit_rate']:>6.1%}"
            )
        lines += ["", "Порог входа → ожидаемый результат на holdout:",
                  f"{'порог':>7} {'сигналов':>9} {'ср.доход':>10} {'hit':>7} {'годных':>7}"]
        for row in self.threshold_table:
            lines.append(
                f"{row['threshold']:>7.3f} {row['n_signals']:>9} {row['mean_return_pct']:>9.3f}% "
                f"{row['hit_rate']:>6.1%} {'да' if row['acceptable'] else 'нет':>7}"
            )
        lines += ["", f"Рекомендуемый порог: {self.recommended_threshold:.3f}", ""]
        lines += [f"• {n}" for n in self.notes]
        return "\n".join(lines)


def _auc(y_true: np.ndarray, proba: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, proba))


def _fold_metrics(y_true: np.ndarray, proba: np.ndarray, forward_return: np.ndarray,
                  threshold: float) -> dict:
    signals = proba >= threshold
    n_signals = int(signals.sum())
    if n_signals == 0:
        return {"n_signals": 0, "mean_return_pct": 0.0, "hit_rate": 0.0,
                "median_return_pct": 0.0, "worst_pct": 0.0}
    selected = forward_return[signals]
    return {
        "n_signals": n_signals,
        "mean_return_pct": float(np.mean(selected) * 100.0),
        "median_return_pct": float(np.median(selected) * 100.0),
        "hit_rate": float(np.mean(selected > 0)),
        "worst_pct": float(np.min(selected) * 100.0),
    }


def walk_forward(
    dataset: pd.DataFrame,
    *,
    feature_names: list[str] | None = None,
    kind: str = "gradient_boosting",
    folds: int = 4,
    embargo_bars: int = 4,
    train_fraction: float = 0.75,
    threshold: float = 0.58,
    seed: int = 42,
) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray]:
    """Walk-forward валидация. Возвращает метрики фолдов и holdout-массивы."""
    feature_names = feature_names or [c for c in dataset.columns if c not in NON_FEATURE_COLUMNS]
    # Держим DataFrame (а не numpy) — тогда sklearn запоминает имена признаков,
    # и предсказание в live идёт по тем же колонкам без предупреждений.
    x = dataset[feature_names]
    y = dataset["target"].to_numpy(dtype=float)
    fwd = dataset["forward_return"].to_numpy(dtype=float)

    n = len(dataset)
    split_at = int(n * train_fraction)
    if split_at < 200 or n - split_at < 50:
        raise DataError(
            f"Слишком мало данных для честной валидации: {n} строк "
            f"(train={split_at}, holdout={n - split_at}). Нужно больше истории."
        )

    fold_metrics: list[dict] = []
    block = max(1, (split_at - embargo_bars) // (folds + 1))
    for fold in range(folds):
        train_end = block * (fold + 1)
        test_start = train_end + embargo_bars
        test_end = min(test_start + block, split_at)
        if test_end - test_start < 20 or train_end < 100:
            continue
        estimator = make_estimator(kind, seed=seed)
        estimator.fit(x.iloc[:train_end], y[:train_end])
        proba = estimator.predict_proba(x.iloc[test_start:test_end])[:, 1]
        metrics = {
            "fold": fold + 1,
            "n_train": train_end,
            "n_test": test_end - test_start,
            "auc": _auc(y[test_start:test_end], proba),
        }
        metrics.update(_fold_metrics(y[test_start:test_end], proba, fwd[test_start:test_end], threshold))
        fold_metrics.append(metrics)

    # Holdout — самая честная оценка: модель никогда не видела эти бары.
    estimator = make_estimator(kind, seed=seed)
    estimator.fit(x.iloc[:split_at], y[:split_at])
    holdout_proba = estimator.predict_proba(x.iloc[split_at:])[:, 1]
    return fold_metrics, holdout_proba, y[split_at:], fwd[split_at:]


def recommend_threshold(
    y_holdout: np.ndarray,
    proba_holdout: np.ndarray,
    fwd_holdout: np.ndarray,
    *,
    thresholds: tuple[float, ...] = (0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70),
    min_signals: int = 20,
) -> tuple[list[dict], float]:
    """Подобрать порог вероятности по holdout-части.

    Критерий: максимум средней доходности при достаточном числе сигналов и
    положительной доле прибыльных. Это ориентир, а не гарантия: holdout
    уже «видел» тюнинг порога, поэтому запас — обязательная часть процесса.
    """
    table: list[dict] = []
    best_threshold = 0.60
    best_score = -1e9
    for t in thresholds:
        metrics = _fold_metrics(y_holdout, proba_holdout, fwd_holdout, t)
        acceptable = metrics["n_signals"] >= min_signals and metrics["mean_return_pct"] > 0
        row = {"threshold": float(t), **metrics, "acceptable": bool(acceptable)}
        table.append(row)
        if acceptable:
            score = metrics["mean_return_pct"] * np.sqrt(max(metrics["n_signals"], 1))
            if score > best_score:
                best_score = score
                best_threshold = float(t)
    return table, best_threshold


def train_signal_model(
    dataset: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    horizon_bars: int,
    kind: str = "gradient_boosting",
    train_fraction: float = 0.75,
    embargo_bars: int = 4,
    folds: int = 4,
    seed: int = 42,
) -> tuple[SignalModel, TrainReport]:
    """Обучить финальную модель и собрать честный отчёт о её качестве."""
    feature_names = [c for c in dataset.columns if c not in NON_FEATURE_COLUMNS]
    if dataset["target"].nunique() < 2:
        raise DataError("В данных только один класс — модель обучать не на чем")

    fold_metrics, holdout_proba, y_holdout, fwd_holdout = walk_forward(
        dataset, feature_names=feature_names, kind=kind, folds=folds,
        embargo_bars=embargo_bars, train_fraction=train_fraction, seed=seed,
    )
    table, recommended = recommend_threshold(y_holdout, holdout_proba, fwd_holdout)

    # финальное обучение — на всей доступной истории
    estimator = make_estimator(kind, seed=seed)
    x_all = dataset[feature_names]
    y_all = dataset["target"].to_numpy(dtype=float)
    estimator.fit(x_all, y_all)
    train_auc = _auc(y_all, estimator.predict_proba(x_all)[:, 1])
    holdout_auc = _auc(y_holdout, holdout_proba)

    notes = []
    if np.isnan(holdout_auc) or holdout_auc < 0.53:
        notes.append(
            "AUC на отложенном тесте ниже 0.53 — у модели почти нет преимущества. "
            "Торговать по ней НЕЛЬЗЯ: доработай признаки/данные или не торгуй этим сигналом."
        )
    if len(dataset) < 2000:
        notes.append("Мало истории (<2000 примеров) — результат очень неустойчив.")
    notes.append(
        "Все метрики — на исторических данных. Это не обещание будущей доходности."
    )

    meta = ModelMetadata(
        symbol=symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        model_kind=kind,
        feature_names=feature_names,
        trained_at=datetime.now(timezone.utc).isoformat(),
        n_samples=len(dataset),
        train_auc=float(train_auc),
        walk_forward=fold_metrics,
        recommended_threshold=float(recommended),
        notes=notes,
    )
    report = TrainReport(
        symbol=symbol,
        n_samples=len(dataset),
        n_features=len(feature_names),
        train_auc=float(train_auc),
        holdout_auc=float(holdout_auc),
        folds=fold_metrics,
        threshold_table=table,
        recommended_threshold=float(recommended),
        notes=notes,
    )
    return SignalModel(estimator, meta), report


def load_or_fail(path: str | Path, *, max_age_days: int | None = None) -> SignalModel:
    """Загрузить модель, отказавшись работать со слишком старой."""
    model = SignalModel.load(path)
    if max_age_days is not None and model.needs_retrain(max_age_days):
        raise ModelNotTrainedError(
            f"Модель устарела (возраст {model.age_days and round(model.age_days, 1)} дн., "
            f"лимит {max_age_days}). Переобучи: python -m mt5_agent train"
        )
    return model
