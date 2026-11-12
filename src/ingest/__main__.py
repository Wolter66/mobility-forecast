"""Запуск загрузки: python -m src.ingest [all|divvy|weather|gbfs|reference]."""
import argparse
import logging

from src.config import load_config
from src.db import connect
from src.ingest import divvy, gbfs, reference, weather

STEPS = {
    "reference": lambda con, cfg: (reference.load_areas(con, cfg), reference.load_holidays(con, cfg)),
    "weather": weather.run,
    "divvy": divvy.run,
    "gbfs": gbfs.run,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="all", choices=["all", *STEPS])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    con = connect(cfg)

    failed = []
    for name, step in STEPS.items():
        if args.source not in ("all", name):
            continue
        try:
            step(con, cfg)
        except Exception as exc:  # noqa: BLE001 — сбой одного источника не роняет остальные
            # недоступность одного источника не роняет загрузку остальных, ошибка уже в load_log
            logging.error("Источник %s пропущен: %s", name, exc)
            failed.append(name)
    con.close()
    if failed:
        raise SystemExit(f"Не загружены: {', '.join(failed)}")


if __name__ == "__main__":
    main()
