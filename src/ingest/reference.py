"""Справочники: границы районов Чикаго (GeoJSON, Chicago Data Portal) и праздники США/Иллинойса."""
import logging
from datetime import date

import holidays
import pandas as pd
from shapely.geometry import shape

from src.http_client import get
from src.ingest.load_log import log_skip, logged_run

log = logging.getLogger(__name__)


def load_areas(con, cfg: dict, force: bool = False) -> None:
    if not force and con.execute("select count(*) from areas").fetchone()[0] > 0:
        log_skip(con, "areas", "all", "справочник районов уже загружен")
        return
    with logged_run(con, "areas", "all") as stats:
        gj = get(cfg["areas"]["geojson_url"], params={"$limit": 1000},
                 timeout=cfg["http"]["timeout"], retries=cfg["http"]["retries"]).json()
        rows = [{
            "area_num": int(f["properties"]["area_numbe"]),
            "name": f["properties"]["community"].title(),
            "geometry": shape(f["geometry"]).wkt,
        } for f in gj["features"]]
        df = pd.DataFrame(rows)
        con.register("areas_new", df)
        con.execute("insert or replace into areas select * from areas_new")
        con.unregister("areas_new")
        stats["rows"] = len(df)


def load_holidays(con, cfg: dict) -> None:
    start_year = int(cfg["weather"]["start_date"][:4])
    years = range(start_year, date.today().year + 2)
    with logged_run(con, "holidays", f"{years.start}..{years.stop - 1}") as stats:
        hol = holidays.country_holidays("US", subdiv="IL", years=years)
        df = pd.DataFrame(sorted(hol.items()), columns=["date", "name"])
        con.register("hol_new", df)
        con.execute("insert or replace into holidays select * from hol_new")
        con.unregister("hol_new")
        stats["rows"] = len(df)
