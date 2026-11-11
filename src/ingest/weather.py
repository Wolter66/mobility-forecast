"""Источник 2: суточная погода Open-Meteo Archive API (JSON) -> таблица weather_daily.

Инкрементальность: запрашиваем только даты после последней загруженной.
Идемпотентность: insert or replace по первичному ключу date.
"""
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from src.http_client import get
from src.ingest.load_log import log_skip, logged_run

log = logging.getLogger(__name__)
SOURCE = "open-meteo"


def run(con, cfg: dict) -> None:
    w = cfg["weather"]
    last = con.execute("select max(date) from weather_daily").fetchone()[0]
    start = last + timedelta(days=1) if last else datetime.strptime(w["start_date"], "%Y-%m-%d").date()
    end = date.today() - timedelta(days=w["archive_lag_days"])
    period = f"{start}..{end}"
    if start > end:
        log_skip(con, SOURCE, period, "новых дат нет")
        log.info("Open-Meteo: новых дат нет")
        return

    with logged_run(con, SOURCE, period) as stats:
        resp = get(w["archive_url"], params={
            "latitude": w["latitude"], "longitude": w["longitude"],
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "daily": ",".join(w["daily_vars"]), "timezone": cfg["project"]["timezone"],
        }, timeout=cfg["http"]["timeout"], retries=cfg["http"]["retries"], backoff=cfg["http"]["backoff"])
        payload = resp.json()
        if "daily" not in payload:
            raise ValueError(f"неожиданный ответ API: {payload.get('reason', payload)}")

        df = pd.DataFrame(payload["daily"]).rename(columns={"time": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[["date", *w["daily_vars"]]]
        con.register("weather_new", df)
        con.execute(f"insert or replace into weather_daily ({', '.join(df.columns)}) "
                    f"select * from weather_new")
        con.unregister("weather_new")
        stats["rows"] = len(df)
