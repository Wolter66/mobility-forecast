"""Источник 3 (слабоструктурированный): GBFS-фид Divvy в реальном времени (JSON + геокоординаты).

station_information — справочник станций с координатами и вместимостью;
station_status — снимок состояния каждой станции («показания датчиков»: свободные велосипеды/доки).
Запускается по расписанию (cron каждые 15 минут), снимки копятся в gbfs_status.
"""
import logging
from datetime import datetime, timezone

import pandas as pd

from src.http_client import get
from src.ingest.load_log import logged_run

log = logging.getLogger(__name__)
SOURCE = "gbfs"


def _fetch(url: str, cfg: dict) -> dict:
    return get(url, timeout=cfg["http"]["timeout"], retries=cfg["http"]["retries"],
               backoff=cfg["http"]["backoff"]).json()


def run(con, cfg: dict) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with logged_run(con, SOURCE, "station_information") as stats:
        info = _fetch(cfg["gbfs"]["station_information"], cfg)
        st = pd.json_normalize(info["data"]["stations"])
        st = pd.DataFrame({
            "station_id": st["station_id"].astype(str),
            "name": st.get("name"),
            "lat": st["lat"].astype(float),
            "lng": st["lon"].astype(float),
            "capacity": st.get("capacity"),
            "updated_at": now,
        })
        con.register("st_new", st)
        con.execute("insert or replace into stations select * from st_new")
        con.unregister("st_new")
        stats["rows"] = len(st)

    with logged_run(con, SOURCE, "station_status") as stats:
        status = _fetch(cfg["gbfs"]["station_status"], cfg)
        snapshot_ts = datetime.fromtimestamp(status["last_updated"], timezone.utc).replace(tzinfo=None)
        df = pd.json_normalize(status["data"]["stations"])
        df = pd.DataFrame({
            "snapshot_ts": snapshot_ts,
            "station_id": df["station_id"].astype(str),
            "num_bikes_available": df.get("num_bikes_available"),
            "num_ebikes_available": df.get("num_ebikes_available"),
            "num_bikes_disabled": df.get("num_bikes_disabled"),
            "num_docks_available": df.get("num_docks_available"),
            "is_renting": df.get("is_renting"),
            "last_reported": pd.to_datetime(df.get("last_reported"), unit="s"),
        })
        con.register("status_new", df)
        # тот же снимок (тот же last_updated фида) повторно не добавится
        con.execute("insert or ignore into gbfs_status select * from status_new")
        con.unregister("status_new")
        stats["rows"] = len(df)
        stats["message"] = f"snapshot {snapshot_ts:%Y-%m-%d %H:%M} UTC"
