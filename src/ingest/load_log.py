"""Журнал загрузок: каждая операция загрузки оставляет запись в таблице load_log."""
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime

log = logging.getLogger(__name__)


def done_periods(con, source: str) -> set[str]:
    rows = con.execute(
        "select distinct period from load_log where source = ? and status = 'success'", [source]
    ).fetchall()
    return {r[0] for r in rows}


def log_skip(con, source: str, period: str, message: str) -> None:
    now = datetime.now()
    con.execute(
        "insert into load_log values (?, ?, ?, ?, ?, 'skipped', 0, ?)",
        [uuid.uuid4().hex[:12], source, period, now, now, message],
    )


@contextmanager
def logged_run(con, source: str, period: str):
    """Пишет в журнал running -> success/failed. В блоке нужно заполнить stats['rows']."""
    run_id = uuid.uuid4().hex[:12]
    con.execute(
        "insert into load_log values (?, ?, ?, ?, null, 'running', null, null)",
        [run_id, source, period, datetime.now()],
    )
    stats = {"rows": 0, "message": None}
    try:
        yield stats
    except Exception as exc:
        con.execute(
            "update load_log set finished_at = ?, status = 'failed', message = ? where run_id = ?",
            [datetime.now(), f"{type(exc).__name__}: {exc}"[:1000], run_id],
        )
        log.error("[%s %s] ошибка: %s", source, period, exc)
        raise
    else:
        con.execute(
            "update load_log set finished_at = ?, status = 'success', rows_loaded = ?, message = ? "
            "where run_id = ?",
            [datetime.now(), stats["rows"], stats["message"], run_id],
        )
        log.info("[%s %s] загружено строк: %s", source, period, stats["rows"])
