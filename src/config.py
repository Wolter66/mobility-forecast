"""Загрузка config.yaml с переопределением через переменные окружения / .env."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# переменная окружения -> путь к ключу в config.yaml
ENV_OVERRIDES = {
    "MOBILITY_DB": ("paths", "db"),
    "MOBILITY_DATA_DIR": ("paths", "data_dir"),
    "DIVVY_START_MONTH": ("divvy", "start_month"),
    "DIVVY_END_MONTH": ("divvy", "end_month"),
    "WEATHER_START_DATE": ("weather", "start_date"),
    "DUCKDB_MEMORY_LIMIT": ("duckdb", "memory_limit"),
    "DUCKDB_THREADS": ("duckdb", "threads"),
    "HTTP_TIMEOUT": ("http", "timeout"),
    "HTTP_RETRIES": ("http", "retries"),
}


def _read_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path: Path | None = None) -> dict:
    _read_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            value = os.environ[env]
            cfg[section][key] = int(value) if value.isdigit() else value
    for key in ("data_dir", "db"):
        cfg["paths"][key] = str(ROOT / cfg["paths"][key])
    return cfg
