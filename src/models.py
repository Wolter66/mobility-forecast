"""Три модели разных классов, сравниваемые по единой схеме валидации.

1. SeasonalNaive — базовое решение: спрос в день D равен спросу ровно неделю назад.
2. Ridge — линейная регрессия с L2-регуляризацией на log(1 + y).
3. LightGBM — градиентный бустинг деревьев с пуассоновской функцией потерь (целевая — счётчик).

Кодирование категориальных признаков:
- район: Ridge — one-hot (у каждого района свой уровень спроса; 77 категорий — немного,
  а target encoding дублировал бы лаги и требовал бы отдельной защиты от утечки);
  LightGBM — встроенная поддержка категорий (группирует районы в разбиениях без утечки).
- день недели и месяц: Ridge — one-hot (зависимость нелинейная: пик в будни у подписчиков,
  в выходные у разовых пользователей); LightGBM — целым числом, деревья сами найдут пороги.
"""
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

COUNT_PREFIXES = ("lag_", "same_dow", "roll_", "active_stations")
RIDGE_ONEHOT = ["area_num", "dow", "month"]


class SeasonalNaive:
    name = "seasonal_naive"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X["lag_7"].to_numpy(dtype=float)

    def get_params(self):
        return {"rule": "y(D) = y(D-7)"}


def make_ridge(cfg: dict, features: list[str]) -> TransformedTargetRegressor:
    counts = [f for f in features if f.startswith(COUNT_PREFIXES)]
    onehot = [f for f in RIDGE_ONEHOT if f in features]
    numeric = [f for f in features if f not in counts and f not in onehot]
    prep = ColumnTransformer([
        ("counts", Pipeline([("log", FunctionTransformer(np.log1p)),
                             ("impute", SimpleImputer(strategy="median")),
                             ("scale", StandardScaler())]), counts),
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")),
                              ("scale", StandardScaler())]), numeric),
        ("onehot", OneHotEncoder(handle_unknown="ignore"), onehot),
    ])
    model = Pipeline([("prep", prep), ("ridge", Ridge(alpha=cfg["model"]["ridge"]["alpha"]))])
    return TransformedTargetRegressor(regressor=model, func=np.log1p, inverse_func=np.expm1)


def make_lightgbm(cfg: dict) -> LGBMRegressor:
    return LGBMRegressor(**cfg["model"]["lightgbm"], random_state=cfg["project"]["seed"])


def build_models(cfg: dict, features: list[str]) -> dict:
    return {
        "seasonal_naive": SeasonalNaive(),
        "ridge": make_ridge(cfg, features),
        "lightgbm": make_lightgbm(cfg),
    }


def model_params(model) -> dict:
    if isinstance(model, SeasonalNaive):
        return model.get_params()
    if isinstance(model, TransformedTargetRegressor):
        return {"alpha": model.regressor.named_steps["ridge"].alpha, "target": "log1p"}
    return {k: v for k, v in model.get_params().items() if v is not None}


def predict(model, X: pd.DataFrame) -> np.ndarray:
    return np.clip(model.predict(X), 0, None)


def make_model(spec: dict, cfg: dict, features: list[str]):
    """Модель по описанию конфигурации {"model": ..., "params": {...}} — из журнала экспериментов или реестра."""
    params = spec.get("params", {})
    if spec["model"] == "seasonal_naive":
        return SeasonalNaive()
    if spec["model"] == "ridge":
        local = {**cfg, "model": {**cfg["model"], "ridge": {"alpha": params.get("alpha", cfg["model"]["ridge"]["alpha"])}}}
        return make_ridge(local, features)
    if spec["model"] == "lightgbm":
        return LGBMRegressor(**{**cfg["model"]["lightgbm"], **params}, random_state=cfg["project"]["seed"])
    raise ValueError(f"неизвестная модель: {spec['model']}")
