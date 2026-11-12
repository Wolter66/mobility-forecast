"""Обучение и сравнение моделей: python -m src.train

Схема валидации (учитывает порядок во времени):
  - отложенная выборка: все даты >= model.holdout_start, в подборе и сравнении не участвует;
  - на остальных данных — cv_folds последовательных окон по cv_fold_days дней; для каждого окна
    модель обучается на всех датах до начала окна (расширяющееся окно) и проверяется на окне.
Метрики: MAE, RMSE, WAPE = sum|y - ŷ| / sum(y). Результаты пишутся в DuckDB:
model_runs, predictions, feature_importance, ablation.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from src.config import load_config
from src.db import connect
from src.features import build_features, complete_rows, feature_columns, feature_groups
from src.models import build_models, make_lightgbm, model_params, predict

log = logging.getLogger(__name__)


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    err = y - p
    return {"mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))),
            "wape": float(np.sum(np.abs(err)) / np.sum(y))}


def cv_windows(cfg: dict) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    m = cfg["model"]
    last = pd.Timestamp(m["holdout_start"]) - timedelta(days=1)
    windows = []
    for k in range(m["cv_folds"]):
        end = last - timedelta(days=k * m["cv_fold_days"])
        windows.append((end - timedelta(days=m["cv_fold_days"] - 1), end))
    return windows[::-1]


def data_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()[:16]


def area_categories(con) -> list[int]:
    return sorted(con.execute("select area_num from areas").df()["area_num"].tolist())


def research_dataset(con, cfg: dict) -> tuple[pd.DataFrame, str]:
    """Признаки по данным исследовательского среза (до holdout_end) и хеш версии этих данных.

    Данные после holdout_end — период эксплуатации, в обучении и выборе модели не участвуют.
    """
    tables = set(con.execute("select table_name from information_schema.tables").df()["table_name"])
    if "daily_area" not in tables:
        raise SystemExit("Нет витрины daily_area: сначала загрузите данные и соберите витрину —\n"
                         "  python -m src.ingest all\n  python -m src.processing.build_mart\n"
                         "(или сразу полный цикл: python -m src.pipeline)")
    mart = con.execute("select * from daily_area where date <= ? order by area_num, date",
                       [cfg["model"]["holdout_end"]]).df()
    feats = complete_rows(build_features(mart, cfg, area_categories(con)), cfg)
    return feats, data_hash(mart)


def split_dev_holdout(feats: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = pd.Timestamp(cfg["model"]["holdout_start"])
    end = pd.Timestamp(cfg["model"]["holdout_end"])
    return feats[feats.date < start], feats[(feats.date >= start) & (feats.date <= end)]


def save_table(con, table: str, df: pd.DataFrame) -> None:
    _save(con, table, df)


def _save(con, table: str, df: pd.DataFrame) -> None:
    # если у соединения остался недочитанный результат (fetchone), новые версии DuckDB не дают
    # регистрировать DataFrame — закрываем его пустым запросом
    con.execute("select 1").fetchall()
    con.register("tmp_df", df)
    con.execute(f"create table if not exists {table} as select * from tmp_df limit 0")
    con.execute(f"insert into {table} by name select * from tmp_df")
    con.unregister("tmp_df")


def run(cfg: dict) -> str:
    np.random.seed(cfg["project"]["seed"])
    con = connect(cfg)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    feats, dhash = research_dataset(con, cfg)
    cols = feature_columns(cfg)
    dev, hold = split_dev_holdout(feats, cfg)
    log.info("run_id=%s, данные %s: обучение+CV %d строк (%s — %s), отложенная %d строк (%s — %s)",
             run_id, dhash, len(dev), dev.date.min().date(), dev.date.max().date(),
             len(hold), hold.date.min().date(), hold.date.max().date())

    runs, preds = [], []

    def record(name, model, stage, fold, train, valid, y, p):
        runs.append({"run_id": run_id, "created_at": datetime.now(), "model": name, "stage": stage,
                     "fold": fold, "train_end": train.date.max(), "valid_start": valid.date.min(),
                     "valid_end": valid.date.max(), "n_valid": len(valid), **metrics(y, p),
                     "params": json.dumps(model_params(model), default=str), "features": json.dumps(cols),
                     "data_hash": dhash, "seed": cfg["project"]["seed"]})
        preds.append(pd.DataFrame({"run_id": run_id, "model": name, "stage": stage, "fold": fold,
                                   "date": valid.date.values, "area_num": valid.area_num.astype(int).values,
                                   "y_true": y, "y_pred": p}))

    # 1. кросс-валидация по времени
    for fold, (start, end) in enumerate(cv_windows(cfg), 1):
        train = dev[dev.date < start]
        valid = dev[(dev.date >= start) & (dev.date <= end)]
        for name, model in build_models(cfg, cols).items():
            model.fit(train[cols], train.target)
            p = predict(model, valid[cols])
            record(name, model, "cv", fold, train, valid, valid.target.to_numpy(), p)
        log.info("CV fold %d (%s — %s): %s", fold, start.date(), end.date(),
                 ", ".join(f"{r['model']} WAPE={r['wape']:.3f}" for r in runs[-3:]))

    # 2. финальное обучение на всех данных до отложенной выборки и проверка на ней
    model_dir = Path(cfg["paths"]["data_dir"]) / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    for name, model in build_models(cfg, cols).items():
        model.fit(dev[cols], dev.target)
        p = predict(model, hold[cols])
        record(name, model, "holdout", 0, dev, hold, hold.target.to_numpy(), p)
        joblib.dump(model, model_dir / f"{run_id}_{name}.joblib")

    _save(con, "model_runs", pd.DataFrame(runs))
    _save(con, "predictions", pd.concat(preds, ignore_index=True))

    # 3. важность признаков и отбор: на последнем окне CV (отложенная выборка не используется)
    start, end = cv_windows(cfg)[-1]
    train, valid = dev[dev.date < start], dev[(dev.date >= start) & (dev.date <= end)]
    lgbm = make_lightgbm(cfg).fit(train[cols], train.target)
    gain = pd.DataFrame({"feature": cols, "importance": lgbm.booster_.feature_importance("gain"),
                         "importance_std": 0.0, "method": "lightgbm_gain"})
    gain["importance"] /= gain["importance"].sum()
    perm = permutation_importance(lgbm, valid[cols], valid.target, n_repeats=5,
                                  random_state=cfg["project"]["seed"],
                                  scoring=lambda est, X, y: -metrics(y.to_numpy(), predict(est, X))["wape"])
    perm = pd.DataFrame({"feature": cols, "importance": perm.importances_mean,
                         "importance_std": perm.importances_std, "method": "permutation_wape"})
    _save(con, "feature_importance", pd.concat([gain, perm]).assign(run_id=run_id))

    base_wape = metrics(valid.target.to_numpy(), predict(lgbm, valid[cols]))["wape"]
    ablation = [{"run_id": run_id, "group": "все признаки", "n_features": len(cols),
                 "wape": base_wape, "delta_wape": 0.0}]
    for group, gcols in feature_groups(cfg).items():
        rest = [c for c in cols if c not in gcols]
        m = make_lightgbm(cfg).fit(train[rest], train.target)
        w = metrics(valid.target.to_numpy(), predict(m, valid[rest]))["wape"]
        ablation.append({"run_id": run_id, "group": f"без {group}", "n_features": len(rest),
                         "wape": w, "delta_wape": w - base_wape})
    _save(con, "ablation", pd.DataFrame(ablation))

    report(con, run_id)
    con.close()
    return run_id


def report(con, run_id: str) -> None:
    res = con.execute("""
        select model, stage, avg(wape) wape, stddev(wape) wape_std, avg(mae) mae, avg(rmse) rmse
        from model_runs where run_id = ? group by all order by stage, wape
    """, [run_id]).df()
    log.info("Итоги run_id=%s\n%s", run_id, res.to_string(index=False, float_format="%.3f"))
    abl = con.execute("select * exclude (run_id) from ablation where run_id = ?", [run_id]).df()
    log.info("Отбор признаков (LightGBM, последнее окно CV)\n%s",
             abl.to_string(index=False, float_format="%.4f"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(load_config())


if __name__ == "__main__":
    main()
