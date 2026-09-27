# Tagline
#
# Stage 1 is the tagged site; Stage 2 turns GA4 export data into stitched, enriched tables
# in BigQuery. The project id comes from tagline/.env (copy .env.example; gitignored).
#
#   make setup && make test && make reference && make build && make numbers

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
