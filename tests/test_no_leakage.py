"""Доказательство отсутствия утечки целевой переменной.

Идея: если признаки строки (район, D) зависят только от прошлого, то любое изменение данных
о поездках за даты >= D не должно менять признаки строк с датой <= D.
"""
import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.features import build_features, feature_columns, feature_groups

TRIP_COLS = ["n_trips", "n_member", "n_casual", "n_electric", "n_stations", "median_duration_min"]


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def mart(cfg):
    rng = np.random.default_rng(0)
    dates = pd.date_range("2025-01-01", periods=90)
    rows = []
    for area in (1, 2, 3):
        for d in dates:
            n = int(rng.poisson(50 * area))
            rows.append({"date": d, "area_num": area, "area_name": f"A{area}", "n_trips": n,
                         "n_member": n // 2, "n_casual": n - n // 2, "n_electric": n // 3,
                         "n_stations": int(rng.integers(1, 10)), "median_duration_min": float(rng.uniform(5, 20)),
                         "is_holiday": d.day == 1,
                         **{w: float(rng.normal()) for w in cfg["model"]["weather_features"]}})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("cutoff", ["2025-02-15", "2025-03-01", "2025-03-30"])
def test_features_do_not_see_future(cfg, mart, cutoff):
    cutoff = pd.Timestamp(cutoff)
    before = build_features(mart, cfg, [1, 2, 3])

    future = mart.copy()
    mask = future.date >= cutoff
    for col in TRIP_COLS:  # «портим» все данные о поездках начиная с даты cutoff
        future.loc[mask, col] = future.loc[mask, col] * 7 + 1000
    after = build_features(future, cfg, [1, 2, 3])

    cols = [c for c in feature_columns(cfg) if c != "area_num"]
    upto = before.date <= cutoff
    pd.testing.assert_frame_equal(before.loc[upto, cols], after.loc[upto, cols])

    # контроль, что тест вообще чувствителен: на следующий день после cutoff лаг 1 обязан измениться
    nxt = before.date == cutoff + pd.Timedelta(days=1)
    assert not before.loc[nxt, "lag_1"].equals(after.loc[nxt, "lag_1"])


def test_target_is_not_a_feature(cfg):
    assert cfg["model"]["target"] not in feature_columns(cfg)
    assert not set(TRIP_COLS) & set(feature_columns(cfg))


def test_groups_cover_all_features(cfg):
    cols = feature_columns(cfg)
    assert len(cols) == len(set(cols))
    assert sum(len(v) for v in feature_groups(cfg).values()) == len(cols)
