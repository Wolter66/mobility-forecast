# Архитектура системы

![Архитектура](img/architecture.png)

```mermaid
flowchart LR
    subgraph S[Источники]
        D[Divvy trip data<br/>S3, CSV в zip, раз в месяц]
        W[Open-Meteo<br/>архив и прогноз, JSON]
        G[GBFS Divvy<br/>JSON + координаты, 15 мин]
        A[Chicago Data Portal<br/>GeoJSON районов]
        H[holidays]
    end

    subgraph I[src/ingest]
        L[(load_log)]
    end

    subgraph R[Хранение: DuckDB + Parquet]
        RAW[raw: divvy/*.parquet,<br/>weather, stations, gbfs, areas]
        STG[staging: point_area,<br/>trips_clean, dq_report]
        MART[(mart: daily_area)]
        SRV[(service: registry, experiments,<br/>monitoring, forecasts)]
    end

    subgraph P[Обработка и модели]
        F[features.py<br/>единый код признаков]
        M[train.py: наивная, Ridge, LightGBM<br/>CV по времени + отложенная]
        E[experiments.py: Optuna + журнал]
        MON[monitor.py + registry.py<br/>дрейф, качество, переобучение, откат]
        X[explain.py: SHAP, PDP/ICE]
        PR[production.py: прогноз на завтра]
    end

    SNAP[(dashboard.duckdb)]
    UI[Streamlit-дашборд]
    ORC[pipeline.py · make all · cron / Планировщик]

    D & W & G & A & H --> I --> RAW --> STG --> MART
    I --- L
    MART --> F --> M & E & MON & X & PR
    M & E & MON & X & PR --> SRV
    SRV & MART --> SNAP --> UI
    ORC -.запуск.-> I & STG & MON & PR & X & SNAP
```

| Слой | Где | Содержимое |
|---|---|---|
| raw | `data/raw/divvy/YYYYMM.parquet`, таблицы DuckDB | данные источников без изменений (только приведение типов) |
| staging | `point_area`, `trips_clean`, `dq_report` | привязка к районам, очищенные поездки, проверки качества |
| mart | `daily_area` | район × день: поездки, их структура, погода, праздники |
| service | `load_log`, `pipeline_runs`, `model_runs`, `experiments`, `model_registry`, `registry_events`, `monitoring_runs`, `monitoring_drift`, `monitoring_replay`, `live_predictions`, `forecasts`, `explain_*` | журналы, реестр моделей, мониторинг, прогнозы, объяснения |

Поток эксплуатации (`python -m src.pipeline`): загрузка → витрина и проверки качества → мониторинг production-модели (при срабатывании правил — претендент, вывод в эксплуатацию или отказ, откат) → прогноз на следующий день → объяснения → снимок базы для дашборда.

Исследовательский поток (`make research`): сравнение моделей → автоподбор с журналом → прогон мониторинга по истории.
