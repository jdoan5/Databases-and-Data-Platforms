# Query Plan Forensics
#
# There is no psql on the host PATH, and that is fine: every SQL invocation
# goes through the container, which also guarantees client version == server
# version 17.
#
#   make up && make init && make load && make check && make bench

SHELL   := /bin/bash
PROFILE ?= full
STAGE   ?= baseline
ITER    ?= 15
CACHE   ?= warm

ifeq ($(PROFILE),full)
ROWS := 5000000
else ifeq ($(PROFILE),small)
ROWS := 500000
else
$(error PROFILE must be 'full' or 'small', got '$(PROFILE)')
endif

DC   := docker compose
PSQL := $(DC) exec -T db psql -U postgres -d inventory -X -v ON_ERROR_STOP=1

# Use the local venv if one exists, otherwise whatever python3 is on PATH.
# CI pip-installs pytest into the runner's python and needs no venv.
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

.PHONY: help up down nuke wait init load check bench baseline compare report \
        audit psql analyze indexes stats clean-results venv gate

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## start the pinned Postgres 17 container
	$(DC) up -d
	@$(MAKE) --no-print-directory wait

# pg_isready is NOT sufficient here: during first-boot initdb the entrypoint
# runs a temporary server that answers pg_isready and is then shut down, so a
# pg_isready loop returns "ready" and the next command dies with "the database
# system is shutting down". Poll an actual query instead.
wait:
	@printf 'waiting for postgres'
	@until $(DC) exec -T db psql -U postgres -d inventory -X -tAc 'SELECT 1' \
	  >/dev/null 2>&1; do printf '.'; sleep 1; done; echo ' ready'

down: ## stop the container, keep the volume
	$(DC) down

nuke: ## stop and DESTROY the data volume
	$(DC) down -v

init: ## extensions + base schema + views + triggers + procedures
	$(PSQL) -f /sql/00_extensions.sql
	$(PSQL) -f /sql/base/01_schema.sql
	$(PSQL) -f /sql/05_prepare.sql
	$(PSQL) -f /sql/base/04_views.sql
	$(PSQL) -f /sql/base/05_triggers.sql
	$(PSQL) -f /sql/base/06_procedures.sql
	@echo "schema ready"

load: ## generate the dataset (PROFILE=full|small)
	@echo "loading PROFILE=$(PROFILE) -> $(ROWS) movements"
	$(PSQL) -v rows=$(ROWS) -f /sql/10_generate.sql

check: ## Stage 1 acceptance gates — skew, correlation, determinism
	$(PSQL) -f /sql/90_acceptance.sql

audit: ## reproduce the three defects in the committed schema
	$(PSQL) -f /sql/91_reconcile.sql

analyze: ## refresh statistics
	$(PSQL) -c 'ANALYZE;'

venv: ## create .venv with pytest (only needed to run the gate locally)
	python3 -m venv .venv && .venv/bin/pip install -q pytest
	@echo "created .venv"

bench: ## run the benchmark (STAGE=label ITER=n CACHE=warm|prewarm)
	$(PY) bench/runner.py --stage $(STAGE) --iterations $(ITER) --cache $(CACHE)

baseline: ## run the benchmark AND commit it as the CI contract
	$(PY) bench/runner.py --stage baseline --iterations $(ITER) \
	  --cache $(CACHE) --write-baselines

gate: ## the regression gate, against bench/results/$(STAGE).json
	QPF_STAGE=$(STAGE) $(PY) -m pytest bench/test_no_plan_regression.py -v

report: ## markdown table for STAGE
	@$(PY) bench/report.py --stage $(STAGE)

compare: ## before/after table (BEFORE=baseline AFTER=s3_covering)
	@$(PY) bench/report.py --before $(BEFORE) --after $(AFTER)

indexes: ## which indexes are actually being used, and what they cost
	$(PSQL) -c "SELECT relname, indexrelname, idx_scan, \
	  pg_size_pretty(pg_relation_size(indexrelid)) AS size \
	  FROM pg_stat_user_indexes ORDER BY idx_scan ASC, 4 DESC;"

stats: ## the slowest statements this server has seen
	$(PSQL) -c "SELECT calls, round(mean_exec_time::numeric,2) AS mean_ms, \
	  round(total_exec_time::numeric,2) AS total_ms, left(query, 70) AS query \
	  FROM pg_stat_statements WHERE query NOT LIKE '%pg_stat%' \
	  ORDER BY total_exec_time DESC LIMIT 15;"

psql: ## interactive shell
	$(DC) exec -it db psql -U postgres -d inventory

clean-results: ## drop local benchmark runs (keeps committed baselines)
	rm -f bench/results/*.json
