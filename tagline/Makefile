# Tagline
#
# Stage 1 is the tagged site; Stage 2 turns GA4 export data into stitched, enriched tables
# in BigQuery; Stage 3 runs multi-touch attribution in PySpark on Dataproc Serverless and
# orchestrates everything with Airflow (locally, in Docker). The project id and the Stage 3
# settings come from tagline/.env (copy .env.example; gitignored).
#
#   make setup && make test && make reference && make build && make numbers
#   make airflow-up && make airflow-check && make airflow-test && make airflow-down

SHELL := /bin/bash

PIPELINE := pipeline
CLI      := cd $(PIPELINE) && .venv/bin/python -m tagline_pipeline

# The first Python 3.12+ on PATH (macOS's /usr/bin/python3 is 3.9). make venv PYTHON=... to choose.
PYTHON ?= $(shell for p in python3.14 python3.13 python3.12 python3; do \
            command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null \
            && { echo $$p; break; }; done)

# make build COSTS_JSON=pipeline/runs/build.json also writes the cost table as JSON
COSTS_JSON ?=
COSTS_ARG  := $(if $(COSTS_JSON),--costs-json $(abspath $(COSTS_JSON)),)

# make simulate SIM_ARGS="--people 10 --pace 0.3"
SIM_ARGS ?=

.PHONY: help setup venv site test test-site test-pipeline test-simulator reference build build-dry check \
        numbers fixture simulate simulate-plan simulate-live simulate-e2e

help:
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# --- setup --------------------------------------------------------------------------

venv: ## create pipeline/.venv (Python 3.12+) with the pipeline and pytest
	@test -n "$(PYTHON)" || { echo "no Python 3.12+ on PATH (try: brew install python@3.14)"; exit 2; }
	cd $(PIPELINE) && $(PYTHON) -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e '.[dev]'
	@echo "created $(PIPELINE)/.venv with $$($(PIPELINE)/.venv/bin/python --version)"

setup: venv ## venv + npm ci for the site and the simulator
	cd site && npm ci
	cd simulator && npm ci

# --- Stage 1 ------------------------------------------------------------------------

site: ## run the site's dev server (http://localhost:5173/?debug=1)
	cd site && npm run dev

test-site: ## Stage 1 unit tests
	cd site && npm test

# --- Stage 2: BigQuery --------------------------------------------------------------

reference: ## load products (synthetic unit_cost) and SYNTHETIC campaign_costs into tagline_raw
	$(CLI) reference

build: ## build every model in order, then run the data checks (fails if any check fails)
	$(CLI) build $(COSTS_ARG)

build-dry: ## dry-run the models: bytes each would scan; creates nothing; needs a previous build
	$(CLI) build --dry-run

check: ## run the data checks against the built tables
	$(CLI) check $(COSTS_ARG)

numbers: ## print the reconciled numbers (events, sessions, orders, revenue, identity, cost)
	$(CLI) numbers

fixture: ## temporary fake GA4 export tables in tagline_raw: build with them, verify, rebuild, delete them
	cd $(PIPELINE) && .venv/bin/python tests/site_export_fixture.py

test-pipeline: ## pytest: config, cost guard, cost table, CLI exit codes, dry run, sources, SQL files, reference data, fixture rows
	cd $(PIPELINE) && .venv/bin/python -m pytest -q

# --- Stage 2: traffic simulator -----------------------------------------------------

simulate: ## dry run: 50 seeded synthetic shoppers in Chrome; no hits sent to Google
	cd simulator && npm run simulate -- $(SIM_ARGS)

simulate-plan: ## print the plan's counts without a browser
	cd simulator && npm run plan -- $(SIM_ARGS)

simulate-live: ## LIVE: send synthetic traffic to GA4 (needs VITE_GA4_MEASUREMENT_ID=G-...)
	@test -n "$$VITE_GA4_MEASUREMENT_ID" || { echo "set VITE_GA4_MEASUREMENT_ID=G-XXXXXXXXXX (the GA4 web stream's measurement id)"; exit 2; }
	cd simulator && npm run simulate -- --live $(SIM_ARGS)

test-simulator: ## simulator unit tests (plan, hit parsing, summary)
	cd simulator && npm test

simulate-e2e: ## simulator end-to-end: a short dry run in Chrome, asserting the summary
	cd simulator && npm run test:e2e

test: test-pipeline test-simulator ## every unit test that needs no browser and no BigQuery (plus: make test-site)

# --- Stage 3: Airflow (local, Docker) -------------------------------------------------

# Compose reads tagline/.env (project, bucket, service account) and airflow/.env (generated on the
# first airflow-up by airflow/scripts/init-secrets.sh). Both are gitignored.
AIRFLOW_COMPOSE := docker compose -f airflow/docker-compose.yaml --env-file .env --env-file airflow/.env
AIRFLOW_INIT    := airflow/scripts/init-secrets.sh

# make airflow-test AIRFLOW_DATE=2026-09-28 AIRFLOW_CONF='{"attribution": false}'
AIRFLOW_DATE ?= $(shell date -u +%F)
AIRFLOW_CONF ?=

# make airflow-orphans CANCEL=1 cancels what it lists
CANCEL ?=

.PHONY: airflow-up airflow-down airflow-check airflow-test airflow-orphans

airflow-up: ## start Airflow 3 in Docker (http://localhost:8080); the first run generates the admin login
	@$(AIRFLOW_INIT)
	$(AIRFLOW_COMPOSE) up -d --wait
	@echo "Airflow: http://localhost:$${AIRFLOW_PORT:-8080}  (user admin; password in airflow/.secrets/simple_auth_manager_passwords.json)"
	@echo "tagline_daily is created paused; unpausing it starts the latest 10:00 UTC run at once (Stage 2 rebuild + Spark batch)"

airflow-down: ## stop Airflow; the metadata database (runs, pause state) is kept, and nothing restarts on its own
	$(AIRFLOW_COMPOSE) down

airflow-orphans: ## Dataproc batches tagline_daily left running (after a crash); CANCEL=1 cancels them
	@CANCEL='$(CANCEL)' airflow/scripts/orphan-batches.sh

airflow-check: ## DagBag import test for tagline_daily, in the Airflow image: no Google Cloud calls
	@$(AIRFLOW_INIT)
	$(AIRFLOW_COMPOSE) run --rm --no-deps airflow-cli python /opt/airflow/tests/test_dag.py

airflow-test: ## LIVE: airflow dags test tagline_daily end to end (BigQuery + Dataproc; costs cents); needs airflow-up
	$(AIRFLOW_COMPOSE) exec airflow-scheduler airflow dags test tagline_daily $(AIRFLOW_DATE) $(if $(AIRFLOW_CONF),--conf '$(AIRFLOW_CONF)',)

# --- Stage 3: the Spark attribution job (Dataproc Serverless) ------------------------------

# The batch (runtime 3.0 = Spark 4.0.2, TTL, labels, resources, code URIs) is spark/tagline_spark/batch.py,
# the same definition the DAG submits. Project, region, bucket and service account: tagline/.env.
SPARK_DIR := spark
SPARK_PY  := .venv/bin/python
# Spark 4.0 runs on Java 17 or 21, not the newer default JDK: the tests use a Java 21 (macOS java_home).
SPARK_JAVA_HOME ?= $(shell /usr/libexec/java_home -v 21 2>/dev/null)
SPARK_ENV := $(if $(SPARK_JAVA_HOME),JAVA_HOME=$(SPARK_JAVA_HOME),)
# make spark-status BATCH=tagline-attr-...  (default: the latest attribution batch)
BATCH ?=

.PHONY: spark-venv spark-test spark-upload spark-submit spark-status spark-report spark-cancel

spark-venv: ## create spark/.venv (Python 3.12+): pyspark pinned to the runtime's Spark, pytest, Google Cloud clients
	@test -n "$(PYTHON)" || { echo "no Python 3.12+ on PATH (try: brew install python@3.14)"; exit 2; }
	cd $(SPARK_DIR) && $(PYTHON) -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e '.[dev,submit]'
	@echo "created $(SPARK_DIR)/.venv with $$($(SPARK_DIR)/.venv/bin/python --version)"

spark-test: ## Spark unit tests on a local SparkSession (Java 21): journeys, the six models, invariants, the batch
	cd $(SPARK_DIR) && $(SPARK_ENV) $(SPARK_PY) -m pytest -q

spark-upload: ## upload the job's code to gs://<bucket>/code/attribution/<version>/ (the DAG runs that version)
	cd $(SPARK_DIR) && $(SPARK_PY) -m tagline_spark.submit upload

spark-submit: ## LIVE: upload, run the attribution batch on Dataproc Serverless, wait; wall time, DCU usage, cost
	cd $(SPARK_DIR) && $(SPARK_PY) -m tagline_spark.submit submit

spark-status: ## a batch's state, wall time, DCU usage and cost (BATCH=<id>, default the latest); any still running
	cd $(SPARK_DIR) && $(SPARK_PY) -m tagline_spark.submit status $(BATCH)

spark-report: ## channel credit by model (all orders, complete-lookback orders), last_click vs Stage 2 (3 small queries)
	cd $(SPARK_DIR) && $(SPARK_PY) -m tagline_spark.report

spark-cancel: ## cancel a batch: make spark-cancel BATCH=<id>
	@test -n "$(BATCH)" || { echo "usage: make spark-cancel BATCH=<batch id>"; exit 2; }
	cd $(SPARK_DIR) && $(SPARK_PY) -m tagline_spark.submit cancel $(BATCH)
