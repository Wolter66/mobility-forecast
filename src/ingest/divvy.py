"""Источник 1: помесячные выгрузки поездок Divvy (CSV в zip на S3) -> data/raw/divvy/YYYYMM.parquet.

Инкрементальность: месяц, успешно загруженный ранее (есть в load_log и есть parquet), повторно не качается.
Идемпотентность: файл пишется во временный и атомарно переименовывается, повторный запуск даёт тот же результат.
"""
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from src.http_client import SourceUnavailable, get
from src.ingest.load_log import done_periods, logged_run

log = logging.getLogger(__name__)
SOURCE = "divvy"
KEY_RE = re.compile(r"^(\d{6})-divvy-(?:public)?tripdata\.zip$")
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def list_available_months(cfg: dict) -> dict[str, str]:
    """Список опубликованных месяцев: {'202409': '202409-divvy-tripdata.zip', ...}."""
    months, token = {}, None
    while True:
        params = {"list-type": "2"}
        if token:
            params["continuation-token"] = token
        resp = get(cfg["divvy"]["bucket_url"], params=params, **_http(cfg))
        root = ElementTree.fromstring(resp.content)
        for key in root.iter(f"{S3_NS}Key"):
            m = KEY_RE.match(key.text)
            if m:
                months[m.group(1)] = key.text
        token_el = root.find(f"{S3_NS}NextContinuationToken")
        if token_el is None:
            return months
        token = token_el.text


def _http(cfg: dict) -> dict:
    return {"timeout": cfg["http"]["timeout"], "retries": cfg["http"]["retries"],
            "backoff": cfg["http"]["backoff"]}


def _in_range(month: str, cfg: dict) -> bool:
    start = cfg["divvy"]["start_month"].replace("-", "")
    end = (cfg["divvy"]["end_month"] or "999999").replace("-", "")
    return start <= month <= end


def _load_month(con, cfg: dict, month: str, key: str, out: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / key
        resp = get(f"{cfg['divvy']['bucket_url']}/{key}", stream=True, **_http(cfg))
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = next(n for n in zf.namelist()
                            if n.endswith(".csv") and not n.startswith("__MACOSX"))
            csv_path = Path(zf.extract(csv_name, tmp))

        tmp_out = out.with_suffix(".parquet.tmp")
        con.execute(f"""
            copy (
                select ride_id, rideable_type,
                       cast(started_at as timestamp) as started_at,
                       cast(ended_at as timestamp)   as ended_at,
                       start_station_id, start_station_name, end_station_id, end_station_name,
                       cast(start_lat as double) as start_lat, cast(start_lng as double) as start_lng,
                       cast(end_lat as double)   as end_lat,   cast(end_lng as double)   as end_lng,
                       member_casual
                from read_csv('{csv_path}', header = true, all_varchar = true)
            ) to '{tmp_out}' (format parquet, compression zstd)
        """)
        tmp_out.replace(out)
    return con.execute(f"select count(*) from read_parquet('{out}')").fetchone()[0]


def run(con, cfg: dict) -> None:
    out_dir = Path(cfg["paths"]["data_dir"]) / "raw" / "divvy"
    out_dir.mkdir(parents=True, exist_ok=True)

    with logged_run(con, SOURCE, "listing") as stats:
        available = list_available_months(cfg)
        stats["rows"] = len(available)

    done = done_periods(con, SOURCE)
    todo = [m for m in sorted(available)
            if _in_range(m, cfg) and not (m in done and (out_dir / f"{m}.parquet").exists())]
    log.info("Divvy: опубликовано месяцев %d, к загрузке %d", len(available), len(todo))

    for month in todo:
        try:
            with logged_run(con, SOURCE, month) as stats:
                stats["rows"] = _load_month(con, cfg, month, available[month],
                                            out_dir / f"{month}.parquet")
        except (SourceUnavailable, zipfile.BadZipFile, StopIteration):
            # один битый/недоступный месяц не останавливает загрузку остальных
            continue
