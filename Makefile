PY ?= .venv/bin/python

.PHONY: venv all ingest gbfs mart research train search monitor replay forecast explain dashboard notebooks report status test

venv:            ## окружение и зависимости
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

all:             ## полный цикл эксплуатации: загрузка → витрина → мониторинг → прогноз → объяснения → снимок для дашборда
	$(PY) -m src.pipeline

ingest:          ## загрузка всех источников (инкрементально)
	$(PY) -m src.ingest all

gbfs:            ## снимок состояния станций (cron каждые 15 минут)
	$(PY) -m src.ingest gbfs

mart:            ## очистка, контроль качества, витрина район × день
	$(PY) -m src.processing.build_mart

research: train search replay  ## исследование: сравнение моделей, автоподбор, прогон мониторинга по истории

train:           ## сравнение моделей: CV по времени, отложенная выборка, важность и отбор признаков
	$(PY) -m src.train

search:          ## автоматизированный подбор моделей и гиперпараметров + журнал экспериментов
	$(PY) -m src.experiments search

monitor:         ## проверка production-модели: качество, дрейф, переобучение, вывод, откат
	$(PY) -m src.monitor run

replay:          ## демонстрация мониторинга на истории (с переобучением и без)
	$(PY) -m src.monitor replay

forecast:        ## прогноз на следующий день
	$(PY) -m src.production forecast

explain:         ## глобальные объяснения модели (SHAP, PDP, лето/зима)
	$(PY) -m src.explain global

dashboard:       ## дашборд http://localhost:8501
	$(PY) -m streamlit run src/dashboard/app.py

notebooks:       ## пересчитать ноутбуки
	$(PY) -m jupyter nbconvert --to notebook --execute --inplace notebooks/01_eda.ipynb notebooks/02_models.ipynb

report:          ## рисунки и скриншоты для отчёта, сборка отчёта и презентации
	$(PY) report/figures.py && $(PY) report/screenshots.py && $(PY) report/build_report.py && $(PY) report/build_slides.py

status:          ## сводка: загрузки, качество данных, модели
	$(PY) -m src.status

test:
	$(PY) -m pytest -q tests
