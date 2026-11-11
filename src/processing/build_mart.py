"""Очистка поездок, контроль качества и витрина «район × день».

python -m src.processing.build_mart
Витрина пересобирается целиком (create or replace), поэтому повторный запуск даёт тот же результат.
"""
import logging
from datetime import datetime
from pathlib import Path

from src.config import load_config
from src.db import connect
from src.processing.geo import update_point_area

log = logging.getLogger(__name__)


def create_trips_clean(con, cfg: dict, trips_glob: str) -> None:
    c, p = cfg["cleaning"], cfg["cleaning"]["coord_precision"]
    # ride_id, встречающиеся больше одного раза, — все копии отбрасываются (их доля в dq_report)
    con.execute(f"""
        create or replace table duplicate_rides as
        select ride_id from read_parquet('{trips_glob}') group by ride_id having count(*) > 1
    """)
    con.execute(f"""
        create or replace table trips_clean as
        select cast(t.started_at as date) as trip_date,
               pa.area_num::utinyint as area_num,
               t.start_station_id as station_id,
               t.member_casual = 'member' as is_member,
               t.rideable_type like 'electric%' as is_electric,
               (date_diff('second', t.started_at, t.ended_at) / 60.0)::float as duration_min
        from read_parquet('{trips_glob}') t
        join point_area pa
          on pa.lat = round(t.start_lat, {p}) and pa.lng = round(t.start_lng, {p})
        where t.ride_id not in (select ride_id from duplicate_rides)  -- дубликаты ride_id
          and pa.area_num is not null                          -- старт за пределами города
          and t.ended_at > t.started_at
          and date_diff('second', t.started_at, t.ended_at) between {c['min_duration_min'] * 60}
                                                              and {c['max_duration_hours'] * 3600}
    """)


def quality_checks(con, trips_glob: str) -> list[tuple]:
    raw = con.execute(f"""
        select count(*) as n,
               avg(case when start_lat is null or start_lng is null then 1 else 0 end) as null_coords,
               avg(case when ride_id in (select ride_id from duplicate_rides) then 1 else 0 end)
                                                                                        as dup_ride_id,
               avg(case when ended_at <= started_at then 1 else 0 end)                  as bad_duration,
               avg(case when start_station_id is null then 1 else 0 end)                as no_station
        from read_parquet('{trips_glob}')
    """).fetchone()
    clean_n = con.execute("select count(*) from trips_clean").fetchone()[0]
    gaps = con.execute("""
        with d as (select distinct trip_date from trips_clean)
        select date_diff('day', min(trip_date), max(trip_date)) + 1 - count(*) from d
    """).fetchone()[0]

    # (слой, проверка, значение, порог, пройдено)
    checks = [
        ("raw", "rows", raw[0], 1, raw[0] >= 1),
        ("raw", "share_null_coords", raw[1], 0.01, raw[1] <= 0.01),
        ("raw", "share_duplicate_ride_id", raw[2], 0.001, raw[2] <= 0.001),
        ("raw", "share_non_positive_duration", raw[3], 0.01, raw[3] <= 0.01),
        ("raw", "share_without_station", raw[4], 0.5, raw[4] <= 0.5),
        ("clean", "share_rows_kept", clean_n / raw[0], 0.9, clean_n / raw[0] >= 0.9),
        ("clean", "missing_dates", gaps, 0, gaps == 0),
    ]
    now = datetime.now()
    con.executemany("insert into dq_report values (?, ?, ?, ?, ?, ?)",
                    [(now, *c) for c in checks])
    for layer, name, value, thr, ok in checks:
        log.log(logging.INFO if ok else logging.WARNING, "DQ %-5s %-28s %10.4f (порог %s) %s",
                layer, name, value, thr, "OK" if ok else "ВНИМАНИЕ")
    return checks


def build_daily_area(con) -> None:
    con.execute("""
        create or replace table daily_area as
        with agg as (
            select trip_date as date, area_num,
                   count(*)                                                  as n_trips,
                   count(*) filter (where is_member)                         as n_member,
                   count(*) filter (where not is_member)                     as n_casual,
                   count(*) filter (where is_electric)                       as n_electric,
                   count(distinct station_id)                                as n_stations,
                   median(duration_min)                                      as median_duration_min
            from trips_clean
            group by all
        ),
        grid as (
            select d.date, a.area_num, a.name as area_name
            from (select distinct date from agg) d cross join areas a
        )
        select g.date, g.area_num, g.area_name,
               coalesce(agg.n_trips, 0)    as n_trips,
               coalesce(agg.n_member, 0)   as n_member,
               coalesce(agg.n_casual, 0)   as n_casual,
               coalesce(agg.n_electric, 0) as n_electric,
               coalesce(agg.n_stations, 0) as n_stations,
               agg.median_duration_min,
               w.* exclude (date),
               h.name is not null          as is_holiday
        from grid g
        left join agg            on agg.date = g.date and agg.area_num = g.area_num
        left join weather_daily w on w.date = g.date
        left join holidays h      on h.date = g.date
        order by g.date, g.area_num
    """)
    n, d0, d1 = con.execute("select count(*), min(date), max(date) from daily_area").fetchone()
    log.info("Витрина daily_area: %d строк, период %s — %s", n, d0, d1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    con = connect(cfg)
    trips_glob = str(Path(cfg["paths"]["data_dir"]) / "raw" / "divvy" / "*.parquet")

    update_point_area(con, cfg, trips_glob)
    create_trips_clean(con, cfg, trips_glob)
    quality_checks(con, trips_glob)
    build_daily_area(con)
    con.close()


if __name__ == "__main__":
    main()
