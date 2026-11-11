"""Подключение к DuckDB и схема хранилища."""
from pathlib import Path

import duckdb

SCHEMA = """
create table if not exists load_log (
    run_id      varchar primary key,
    source      varchar not null,
    period      varchar,
    started_at  timestamp not null,
    finished_at timestamp,
    status      varchar not null,      -- running / success / skipped / failed
    rows_loaded bigint,
    message     varchar
);

create table if not exists areas (
    area_num  integer primary key,
    name      varchar not null,
    geometry  varchar not null          -- WKT
);

create table if not exists point_area (
    lat       double not null,
    lng       double not null,
    area_num  integer,                  -- null = точка вне города
    primary key (lat, lng)
);

create table if not exists weather_daily (
    date                      date primary key,
    temperature_2m_mean       double,
    temperature_2m_max        double,
    temperature_2m_min        double,
    apparent_temperature_mean double,
    precipitation_sum         double,
    rain_sum                  double,
    snowfall_sum              double,
    precipitation_hours       double,
    wind_speed_10m_max        double,
    sunshine_duration         double
);

create table if not exists stations (
    station_id varchar primary key,
    name       varchar,
    lat        double,
    lng        double,
    capacity   integer,
    updated_at timestamp
);

create table if not exists gbfs_status (
    snapshot_ts          timestamp not null,
    station_id           varchar not null,
    num_bikes_available  integer,
    num_ebikes_available integer,
    num_bikes_disabled   integer,
    num_docks_available  integer,
    is_renting           integer,
    last_reported        timestamp,
    primary key (snapshot_ts, station_id)
);

create table if not exists holidays (
    date date primary key,
    name varchar
);

create table if not exists dq_report (
    checked_at timestamp,
    layer      varchar,
    check_name varchar,
    value      double,
    threshold  double,
    passed     boolean
);
"""


def connect(cfg: dict) -> duckdb.DuckDBPyConnection:
    Path(cfg["paths"]["db"]).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(cfg["paths"]["db"])
    # ограничение памяти: тяжёлые операции сбрасываются на диск, а не падают по OOM
    con.execute(f"set memory_limit = '{cfg['duckdb']['memory_limit']}'")
    con.execute(f"set temp_directory = '{Path(cfg['paths']['data_dir']) / 'tmp'}'")
    con.execute(f"set threads = {cfg['duckdb']['threads']}")
    con.execute("set preserve_insertion_order = false")
    con.execute(SCHEMA)
    return con
