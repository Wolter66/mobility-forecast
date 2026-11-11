"""Проверки инкрементальности, идемпотентности и журнала загрузок (без сети)."""
from datetime import date, timedelta

import duckdb
import pytest

from src.db import SCHEMA
from src.ingest import load_log, weather


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute(SCHEMA)
    yield c
    c.close()


@pytest.fixture
def cfg():
    return {
        "project": {"timezone": "America/Chicago"},
        "http": {"timeout": 1, "retries": 1, "backoff": 1},
        "weather": {"archive_url": "http://x", "latitude": 0, "longitude": 0,
                    "start_date": (date.today() - timedelta(days=10)).isoformat(),
                    "archive_lag_days": 6, "daily_vars": ["temperature_2m_mean"]},
    }


class FakeResp:
    def __init__(self, params):
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        self._json = {"daily": {"time": days, "temperature_2m_mean": [10.0] * len(days)}}

    def json(self):
        return self._json


def test_weather_incremental_and_idempotent(con, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(weather, "get", lambda url, params, **kw: calls.append(params) or FakeResp(params))

    weather.run(con, cfg)
    first = con.execute("select count(*) from weather_daily").fetchone()[0]
    weather.run(con, cfg)  # повторный запуск: новых дат нет
    second = con.execute("select count(*) from weather_daily").fetchone()[0]

    assert first == second == 5
    assert len(calls) == 1
    statuses = [r[0] for r in con.execute("select status from load_log order by started_at").fetchall()]
    assert statuses == ["success", "skipped"]


def test_failed_run_is_logged(con):
    with pytest.raises(RuntimeError):
        with load_log.logged_run(con, "test", "p1"):
            raise RuntimeError("источник недоступен")
    status, message = con.execute("select status, message from load_log").fetchone()
    assert status == "failed" and "источник недоступен" in message
    assert load_log.done_periods(con, "test") == set()
