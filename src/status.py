"""Сводка по состоянию системы: python -m src.status

Журнал загрузок, проверки качества, витрина и метрики последнего обучения — в одном выводе.
"""
import duckdb
import pandas as pd

from src.config import load_config

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def section(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    print(df.to_string(index=False) if not df.empty else "(пусто)")


def main() -> None:
    cfg = load_config()
    con = duckdb.connect(cfg["paths"]["db"], read_only=True)
    tables = set(con.execute("select table_name from information_schema.tables").df()["table_name"])

    section("Журнал загрузок (по источникам)", con.execute("""
        select source, status, count(*) as runs, sum(rows_loaded)::bigint as rows_loaded,
               strftime(max(started_at), '%Y-%m-%d %H:%M') as last_run
        from load_log group by all order by source, status
    """).df())

    section("Последние 5 запусков", con.execute("""
        select source, period, status, rows_loaded, strftime(started_at, '%Y-%m-%d %H:%M:%S') as started, message
        from load_log order by started_at desc limit 5
    """).df())

    if "dq_report" in tables:
        section("Проверки качества (последний запуск)", con.execute("""
            select layer, check_name, printf('%.4f', value) as value, threshold,
                   case when passed then 'OK' else 'ВНИМАНИЕ' end as result
            from dq_report where checked_at = (select max(checked_at) from dq_report)
        """).df())

    if "daily_area" in tables:
        section("Витрина daily_area", con.execute("""
            select count(*) as rows, count(distinct area_num) as areas, min(date) as date_from,
                   max(date) as date_to, sum(n_trips)::bigint as trips
            from daily_area
        """).df())

    if "model_runs" in tables:
        section("Модели (последний запуск обучения)", con.execute("""
            select run_id, model, stage, round(avg(wape) * 100, 1) as wape_pct,
                   round(avg(mae), 1) as mae, round(avg(rmse), 1) as rmse, any_value(data_hash) as data_hash
            from model_runs where run_id = (select max(run_id) from model_runs)
            group by all order by stage, wape_pct
        """).df())
    con.close()


if __name__ == "__main__":
    main()
