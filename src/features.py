"""Признаки для модели. Один и тот же код используется при обучении и при прогнозе.

Правило против утечки целевой переменной: для строки (район, дата D) всё, что считается
из истории поездок, использует только даты строго раньше D (shift >= 1).
Погода на дату D — внешний фактор, известный заранее: при обучении берётся фактическая,
в эксплуатации — прогноз погоды на D. Календарь и праздники известны заранее.

Прогноз в эксплуатации: к витрине добавляется строка (район, D) с пустым n_trips и прогнозом
погоды, затем вызывается та же build_features — признаки этой строки считаются только из прошлого.
"""
import numpy as np
import pandas as pd

KEYS = ["date", "area_num", "area_name"]


def feature_groups(cfg: dict) -> dict[str, list[str]]:
    m = cfg["model"]
    return {
        "lags": [f"lag_{l}" for l in m["lags"]] + ["same_dow_mean_4w"],
        "rolling": [f"roll_mean_{w}" for w in m["windows"]] + ["roll_std_7", "trend_7_28"],
        "trip_log": ["share_member_7", "share_electric_7", "median_duration_7", "active_stations_28"],
        "calendar": ["dow", "month", "is_weekend", "is_holiday", "holiday_near", "doy_sin", "doy_cos"],
        "weather": list(m["weather_features"]),
        "area": ["area_num"],
    }


def feature_columns(cfg: dict) -> list[str]:
    return [c for cols in feature_groups(cfg).values() for c in cols]


def _past_rolling(s: pd.Series, by: pd.Series, window: int, how: str) -> pd.Series:
    """Окно по прошлым дням: значения за D-window .. D-1 (текущий день не входит)."""
    prev = s.groupby(by).shift(1)
    return prev.groupby(by).transform(lambda x: getattr(x.rolling(window, min_periods=window), how)())


def build_features(mart: pd.DataFrame, cfg: dict, area_categories: list[int]) -> pd.DataFrame:
    m = cfg["model"]
    df = mart.sort_values(["area_num", "date"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    area, y = df["area_num"], df[m["target"]].astype(float)
    out = df[KEYS].copy()
    out["target"] = y

    # лаги и тот же день недели за 4 прошлые недели
    for lag in m["lags"]:
        out[f"lag_{lag}"] = y.groupby(area).shift(lag)
    out["same_dow_mean_4w"] = sum(y.groupby(area).shift(k) for k in (7, 14, 21, 28)) / 4

    # оконные статистики по прошлым дням
    for w in m["windows"]:
        out[f"roll_mean_{w}"] = _past_rolling(y, area, w, "mean")
    out["roll_std_7"] = _past_rolling(y, area, 7, "std")
    out["trend_7_28"] = (out["roll_mean_7"] + 1) / (out["roll_mean_28"] + 1)

    # признаки из журнала поездок (слабоструктурированный источник) за прошлые 7/28 дней
    trips_7 = _past_rolling(y, area, 7, "sum")
    out["share_member_7"] = _past_rolling(df["n_member"].astype(float), area, 7, "sum") / trips_7.replace(0, np.nan)
    out["share_electric_7"] = _past_rolling(df["n_electric"].astype(float), area, 7, "sum") / trips_7.replace(0, np.nan)
    out["median_duration_7"] = _past_rolling(df["median_duration_min"].astype(float), area, 7, "median")
    out["active_stations_28"] = _past_rolling(df["n_stations"].astype(float), area, 28, "max")

    # календарь (известен заранее)
    d = df["date"]
    out["dow"] = d.dt.dayofweek
    out["month"] = d.dt.month
    out["is_weekend"] = (out["dow"] >= 5).astype(int)
    out["is_holiday"] = df["is_holiday"].astype(int)
    holiday_dates = set(d[df["is_holiday"]])
    out["holiday_near"] = (d - pd.Timedelta(days=1)).isin(holiday_dates) | (d + pd.Timedelta(days=1)).isin(holiday_dates)
    out["holiday_near"] = out["holiday_near"].astype(int)
    doy = d.dt.dayofyear
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

    # погода на дату прогноза
    for col in m["weather_features"]:
        out[col] = df[col].astype(float)

    # район: категориальный признак с фиксированным набором категорий
    out["area_num"] = pd.Categorical(df["area_num"], categories=area_categories)
    return out


def complete_rows(features: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Отбрасывает первые 28 дней каждого района, где ещё нет полной истории для лагов."""
    required = feature_groups(cfg)["lags"] + feature_groups(cfg)["rolling"] + ["active_stations_28"]
    return features.dropna(subset=required).reset_index(drop=True)
