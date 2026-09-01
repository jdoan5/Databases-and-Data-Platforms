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
        audit psql analyze indexes stats clean-results venv gate stage3 reset apply-stage3 \
        stage4 s4a-stats s4b-writes s4c-audit s4d-cleanup \
        stage5 s5-partition s5-ops s5-revert \
        s3a-composite s3a-covering-dirty s3a-covering s3b-partial s3b-denorm s3c-brin s3c-brin-only s3d-revert

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

# TRUNCATE is not a reset. `make load` replaces rows but leaves every index,
# added column and trigger from a later stage in place -- so a "baseline"
# captured after any Stage 3 work silently measures the tuned database. It cost
# a full remeasurement to notice: Q04's baseline read 2.1ms instead of 40.9ms.
# 01_schema.sql opens with DROP TABLE ... CASCADE, so init IS the reset.
reset: init load ## full teardown to base schema, then reload — start every experiment here

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

apply-stage3: ## jump straight to Stage 3's end state (no intermediate benchmarks)
	$(PSQL) -f /sql/39_apply_stage3.sql

# --- Stage 3: one change at a time, each with its own benchmark run ---------
# Never two at once. Each target applies exactly one migration and then
# measures, so every number in the writeup has a single cause.

s3a-composite: ## 3a/1 composite index (product_id, created_at)
	$(PSQL) -f /sql/30a_covering.sql
	$(MAKE) bench STAGE=s3a_composite

s3a-covering-dirty: ## 3a/2 covering index, visibility map deliberately stale
	$(PSQL) -f /sql/30a_covering_part2.sql
	$(MAKE) bench STAGE=s3a_covering_dirty

s3a-covering: ## 3a/3 the VACUUM that makes the index-only scan real
	$(PSQL) -f /sql/30a_covering_part3.sql
	$(MAKE) bench STAGE=s3a_covering

s3b-partial: ## 3b the partial index that works, and the one that cannot be built
	$(PSQL) -f /sql/30b_partial.sql
	$(MAKE) bench STAGE=s3b_partial

s3b-denorm: ## 3b what it costs to make Q01 indexable at all
	$(PSQL) -f /sql/30b_partial_denorm.sql
	$(MAKE) bench STAGE=s3b_denorm

s3c-brin: ## 3c BRIN vs B-tree on the append-only created_at
	$(PSQL) -f /sql/30c_brin.sql
	$(MAKE) bench STAGE=s3c_brin

s3c-brin-only: ## 3c BRIN INSTEAD of the B-tree — the real trade
	$(PSQL) -f /sql/30c_brin_only.sql
	$(MAKE) bench STAGE=s3c_brin_only

s3d-revert: ## 3d the verdict: restore the B-tree, drop the unused BRIN
	$(PSQL) -f /sql/30d_revert_brin.sql
	$(MAKE) bench STAGE=s3_final

stage3: s3a-composite s3a-covering-dirty s3a-covering s3b-partial s3b-denorm s3c-brin s3c-brin-only s3d-revert ## run all of Stage 3 in order

# --- Stage 4: the bill for Stage 3 ------------------------------------------

s4a-stats: ## 4a extended statistics on the correlated pair
	$(PSQL) -f /sql/40_extended_stats.sql
	$(MAKE) bench STAGE=s4a_extstats

s4b-writes: ## 4b write amplification — time and WAL bytes per insert
	$(PSQL) -f /sql/41_write_amplification.sql

s4c-audit: ## 4c which indexes are never used, and what they cost
	$(PSQL) -f /sql/42_index_audit.sql

s4d-cleanup: ## 4d drop the indexes Stage 3 superseded, and remeasure writes
	$(PSQL) -f /sql/43_drop_superseded.sql
	$(MAKE) bench STAGE=s4_cleaned

stage4: s4a-stats s4b-writes s4c-audit s4d-cleanup ## all of Stage 4

# --- Stage 5: partitioning, including the query it makes slower -------------

s5-partition: ## 5 convert stock_movements to monthly range partitions
	$(PSQL) -f /sql/50_partition.sql
	$(MAKE) bench STAGE=s5_partitioned

s5-ops: ## 5b operational overhead — DESTRUCTIVE, expires a month (reset after)
	$(PSQL) -f /sql/51_partition_ops.sql

s5-revert: ## 5c the verdict: revert partitioning, restore the flat table
	$(PSQL) -f /sql/52_revert_partition.sql
	$(MAKE) bench STAGE=s5_reverted

stage5: s5-partition s5-ops s5-revert ## all of Stage 5

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
