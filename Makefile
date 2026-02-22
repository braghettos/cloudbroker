# CloudBroker – common tasks
# Usage: make [target]
# Prefers .venv/bin/python if present; override with: make <target> PYTHON=python3

VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/bin/python3))
PYTHON ?= $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)

.PHONY: help install dev run test test-exhaustive test-cov test-e2e lint format up down migrate migrate-down migrate-revision ingest-gcp ingest-gcp-machines ingest-hetzner ingest-aws ingest-aws-quick ingest-azure ingest-azure-quick ingest-azure-egress ingest-azure-spot ingest-azure-spot-quick ingest-digitalocean ingest-scaleway ingest-ovh ingest-all ingest-egress ingest-storage ingest-public-ip ingest-os-license ingest-all-costs test-recommendations docker-build clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

##@ Setup

install: ## Install all dependencies (dev + ingest extras)
	$(PYTHON) -m pip install -e ".[dev,ingest]"

##@ Development

dev: ## Run uvicorn with hot-reload
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run: ## Run uvicorn (production mode)
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

test: ## Run pytest (excludes exhaustive matrix)
	$(PYTHON) -m pytest -m "not exhaustive"

test-exhaustive: ## Run full recommendation param matrix (~440 tests)
	$(PYTHON) -m pytest -m exhaustive tests/test_recommendations_param_matrix.py -v

test-cov: ## Run pytest with coverage (excludes exhaustive matrix)
	$(PYTHON) -m pytest -m "not exhaustive" --cov=app --cov=connectors --cov=apps --cov-report=term-missing --cov-report=html

lint: ## Run ruff linter
	$(PYTHON) -m ruff check app tests connectors apps

format: ## Run ruff formatter
	$(PYTHON) -m ruff format app tests connectors apps

test-recommendations: ## Test recommendations API endpoint
	@./scripts/test_recommendations.sh

test-e2e: ## Run e2e tests (requires: make up, make migrate, ingest, ingest --egress)
	$(PYTHON) -m pytest tests/test_e2e_recommendations_tco.py -v -m e2e

##@ Docker

up: ## Start stack (docker compose up -d)
	docker compose up -d

down: ## Stop stack (docker compose down)
	docker compose down

docker-build: ## Build API Docker image
	docker compose build api

##@ Database

migrate: ## Run Alembic migrations (upgrade head)
	$(PYTHON) -m alembic upgrade head

migrate-down: ## Rollback one Alembic migration
	$(PYTHON) -m alembic downgrade -1

migrate-revision: ## Create new migration (use: make migrate-revision msg='description')
	$(PYTHON) -m alembic revision --autogenerate -m "$(msg)"

##@ Ingestion

ingest-gcp: ## Ingest GCP Billing Catalog (requires ADC)
	$(PYTHON) -m apps.ingestor ingest --provider gcp

ingest-gcp-machines: ## Compute GCP machine type prices from unit prices
	$(PYTHON) -m apps.ingestor.machines

ingest-hetzner: ## Ingest Hetzner Cloud (requires HETZNER_API_TOKEN)
	$(PYTHON) -m apps.ingestor ingest --provider hetzner

ingest-aws: ## Ingest AWS EC2 (requires AWS credentials)
	$(PYTHON) -m apps.ingestor ingest --provider aws

ingest-aws-quick: ## Ingest AWS single region (eu-west-1)
	$(PYTHON) -m apps.ingestor ingest --provider aws --quick

ingest-azure: ## Ingest Azure (Retail Prices API, no auth required)
	$(PYTHON) -m apps.ingestor ingest --provider azure

ingest-azure-quick: ## Ingest Azure single region (westeurope)
	$(PYTHON) -m apps.ingestor ingest --provider azure --quick

ingest-azure-egress: ## Ingest Azure plus egress rates
	$(PYTHON) -m apps.ingestor ingest --provider azure --egress

ingest-azure-spot: ## Ingest Azure Spot VM prices (all EU regions)
	$(PYTHON) -m apps.ingestor ingest --provider azure --purchase-model spot

ingest-azure-spot-quick: ## Ingest Azure Spot VM prices (westeurope only)
	$(PYTHON) -m apps.ingestor ingest --provider azure --purchase-model spot --quick

ingest-digitalocean: ## Ingest DigitalOcean (requires DIGITALOCEAN_API_TOKEN)
	$(PYTHON) -m apps.ingestor ingest --provider digitalocean

ingest-scaleway: ## Ingest Scaleway (requires SCW_ACCESS_KEY + SCW_SECRET_KEY)
	$(PYTHON) -m apps.ingestor ingest --provider scaleway

ingest-ovh: ## Ingest OVH (requires OVH keys)
	$(PYTHON) -m apps.ingestor ingest --provider ovh

ingest-all: ## Ingest all providers
	$(PYTHON) -m apps.ingestor ingest --provider all

ingest-egress: ## Ingest egress rates for all 7 providers (API + config)
	$(PYTHON) -m apps.ingestor ingest --provider all --egress

ingest-storage: ## Ingest storage rates for all providers (costs only, no instances)
	$(PYTHON) -m apps.ingestor ingest --provider all --storage --costs-only

ingest-public-ip: ## Ingest public IP rates for all providers (costs only)
	$(PYTHON) -m apps.ingestor ingest --provider all --public-ip --costs-only

ingest-os-license: ## Ingest OS license rates for all providers (costs only)
	$(PYTHON) -m apps.ingestor ingest --provider all --os-license --costs-only

ingest-all-costs: ## Ingest egress, storage, public IP, OS license rates (costs only)
	$(PYTHON) -m apps.ingestor ingest --provider all --all-costs --costs-only

seed-dr-hetzner-first: ## Seed DB so Hetzner ranks first (for DR failover test)
	$(PYTHON) scripts/seed_dr_hetzner_first.py

##@ Cleanup

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
