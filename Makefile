# CloudBroker – common tasks
# Usage: make [target]
# Prefers .venv/bin/python if present; override with: make <target> PYTHON=python3

VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/bin/python3))
PYTHON ?= $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)

.PHONY: help install dev run test test-cov lint format up down migrate migrate-down migrate-revision ingest-gcp ingest-gcp-machines ingest-hetzner ingest-aws ingest-aws-quick ingest-azure ingest-azure-quick ingest-azure-spot ingest-azure-spot-quick ingest-digitalocean ingest-scaleway ingest-ovh ingest-all test-recommendations docker-build clean

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

test: ## Run pytest
	$(PYTHON) -m pytest

test-cov: ## Run pytest with coverage
	$(PYTHON) -m pytest --cov=app --cov=connectors --cov=apps --cov-report=term-missing --cov-report=html

lint: ## Run ruff linter
	$(PYTHON) -m ruff check app tests connectors apps

format: ## Run ruff formatter
	$(PYTHON) -m ruff format app tests connectors apps

test-recommendations: ## Test recommendations API endpoint
	@./scripts/test_recommendations.sh

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

##@ Cleanup

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
