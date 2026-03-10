# CloudBroker – common tasks
# Usage: make [target]
# Prefers .venv/bin/python if present; override with: make <target> PYTHON=python3

VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/bin/python3))
PYTHON ?= $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)

# Deploy: DB password for CloudBroker (default for dev only; set for production)
DB_PASSWORD ?= cloudbroker-dev
KIND_CLUSTER ?= cloudburst
KIND_NODE_IMAGE ?= kindest/node:v1.34.3

.PHONY: help install dev run test test-exhaustive test-cov test-e2e lint format up down migrate migrate-down migrate-revision ingest-gcp ingest-gcp-verify ingest-gcp-machines ingest-hetzner ingest-aws ingest-aws-quick ingest-azure ingest-azure-quick ingest-azure-egress ingest-azure-spot ingest-azure-spot-quick ingest-digitalocean ingest-scaleway ingest-ovh ingest-seeweb ingest-all ingest-egress ingest-storage ingest-public-ip ingest-os-license ingest-all-costs test-recommendations check-scorecard-recommendations verify-gcp verify-hetzner verify-all verify-azure verify-otc verify-ovh verify-config docker-build clean deploy deploy-cloudbroker install-prereqs deploy-cluster deploy-ingest build-kind-image seed-dr-hetzner-first

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

check-scorecard-recommendations: ## Verify Scorecard analytics matches Recommendations API for same requirements
	$(PYTHON) scripts/check_scorecard_recommendations_match.py

verify-gcp: ## Verify GCP ingested prices match Billing Catalog API (run after ingest)
	$(PYTHON) scripts/verify_gcp_ingestion.py

verify-hetzner: ## Verify Hetzner ingested prices match Cloud API (run after ingest, requires HETZNER_API_TOKEN)
	$(PYTHON) scripts/verify_hetzner_ingestion.py

verify-all: ## Verify ingested prices for all providers (GCP, Hetzner, Azure, OTC, OVH, DO, AWS, Scaleway, UpCloud, Seeweb, config-based)
	$(PYTHON) scripts/verify_all_ingestion.py

verify-azure: ## Verify Azure ingested prices (public API, no auth)
	$(PYTHON) scripts/verify_all_ingestion.py --provider azure

verify-otc: ## Verify Open Telekom Cloud ingested prices (public API, no auth)
	$(PYTHON) scripts/verify_all_ingestion.py --provider otc

verify-ovh: ## Verify OVH ingested prices (public catalog, no auth)
	$(PYTHON) scripts/verify_all_ingestion.py --provider ovh

verify-config: ## Verify config-based providers (Aruba, Exoscale, IONOS, gridscale, STACKIT, Elastx, Cyso)
	$(PYTHON) scripts/verify_all_ingestion.py --provider config

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

ingest-gcp-verify: ## Ingest GCP and verify prices match API (fails on mismatch with --verify-fail)
	$(PYTHON) -m apps.ingestor ingest --provider gcp --only-eu true --verify

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

ingest-aruba: ## Ingest Aruba Cloud (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider aruba

ingest-upcloud: ## Ingest UpCloud (requires UPCLOUD_USERNAME + UPCLOUD_PASSWORD)
	$(PYTHON) -m apps.ingestor ingest --provider upcloud

ingest-open-telekom-cloud: ## Ingest Open Telekom Cloud (public pricing API, no auth)
	$(PYTHON) -m apps.ingestor ingest --provider open_telekom_cloud

ingest-exoscale: ## Ingest Exoscale (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider exoscale

ingest-ionos: ## Ingest IONOS Cloud (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider ionos

ingest-gridscale: ## Ingest gridscale (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider gridscale

ingest-stackit: ## Ingest STACKIT (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider stackit

ingest-elastx: ## Ingest Elastx (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider elastx

ingest-cyso-cloud: ## Ingest Cyso Cloud (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider cyso_cloud

ingest-seeweb: ## Ingest Seeweb (requires SEEWEB_API_TOKEN or SEEWEB_USERNAME + SEEWEB_PASSWORD)
	$(PYTHON) -m apps.ingestor ingest --provider seeweb

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

##@ Kubernetes Deploy

install-prereqs: ## Install kind, kubectl, helm (macOS: brew; Linux: prints instructions)
	@echo "==> Checking prerequisites..."
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		for pkg in kind kubectl helm; do \
			if ! command -v $$pkg >/dev/null 2>&1; then \
				echo "Installing $$pkg..."; brew install $$pkg || { echo "Failed to install $$pkg"; exit 1; }; \
			fi; \
		done; \
	else \
		command -v kind >/dev/null 2>&1 || { echo "Missing kind. Install: https://kind.sigs.k8s.io"; exit 1; }; \
		command -v kubectl >/dev/null 2>&1 || { echo "Missing kubectl. Install: https://kubernetes.io/docs/tasks/tools"; exit 1; }; \
		command -v helm >/dev/null 2>&1 || { echo "Missing helm. Install: https://helm.sh"; exit 1; }; \
	fi
	@echo "Prerequisites OK."

build-kind-image: ## Build CloudBroker image and load into Kind (for local deploy when registry image is private)
	@docker build -t ghcr.io/braghettos/cloudbroker:latest -f docker/Dockerfile .
	@kind load docker-image ghcr.io/braghettos/cloudbroker:latest --name $(KIND_CLUSTER) || \
		(docker save ghcr.io/braghettos/cloudbroker:latest | docker exec -i $(KIND_CLUSTER)-control-plane ctr --namespace k8s.io images import - && \
		echo "CloudBroker image loaded via ctr fallback.") || \
		(echo "Failed to load CloudBroker image."; exit 1)
	@echo "Images loaded into Kind cluster $(KIND_CLUSTER)."

deploy-cluster: ## Create Kind cluster with NodePort mappings (deletes and recreates if exists)
	@if ! command -v kind >/dev/null 2>&1; then echo "Error: kind not found. Run: make install-prereqs"; exit 1; fi
	@if kind get clusters 2>/dev/null | grep -q "^$(KIND_CLUSTER)$$"; then \
		echo "==> Kind cluster $(KIND_CLUSTER) exists. Deleting and recreating..."; \
		kind delete cluster --name $(KIND_CLUSTER); \
	fi
	@echo "==> Creating Kind cluster $(KIND_CLUSTER)..."
	@kind create cluster --name $(KIND_CLUSTER) --image $(KIND_NODE_IMAGE) --config deploy/kind-config.yaml
	@kubectl config use-context kind-$(KIND_CLUSTER) 2>/dev/null || true
	@echo "==> Cluster ready."

deploy: deploy-cloudbroker ## Deploy CloudBroker only (CloudNative-PG + API)

deploy-cloudbroker: ## Deploy CloudBroker (CloudNative-PG + API)
	@kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.28/releases/cnpg-1.28.1.yaml 2>/dev/null || true
	@kubectl create namespace cloudbroker 2>/dev/null || true
	@kubectl delete cluster cloudbroker-db -n cloudbroker --ignore-not-found 2>/dev/null || true
	@kubectl delete secret cloudbroker-db-app -n cloudbroker --ignore-not-found 2>/dev/null || true
	@echo "==> Waiting for CNPG operator..."
	@kubectl rollout status deployment/cnpg-controller-manager -n cnpg-system --timeout=120s 2>/dev/null || sleep 10
	@kubectl create secret generic cloudbroker-db-app -n cloudbroker --type=kubernetes.io/basic-auth --from-literal=username=cloudbroker --from-literal=password="$(DB_PASSWORD)" --dry-run=client -o yaml | kubectl apply -f -
	@kubectl create secret generic cloudbroker-api-secrets -n cloudbroker \
		--from-literal=database_url="postgresql+asyncpg://cloudbroker:$(DB_PASSWORD)@cloudbroker-db-rw:5432/cloudbroker" \
		--from-literal=database_url_sync="postgresql://cloudbroker:$(DB_PASSWORD)@cloudbroker-db-rw:5432/cloudbroker" \
		--dry-run=client -o yaml | kubectl apply -f -
	@for i in 1 2 3 4 5 6; do kubectl apply -k deploy/ && break; echo "Webhook may be starting, retry in 15s..."; sleep 15; done
	@kubectl wait --for=condition=Ready cluster/cloudbroker-db -n cloudbroker --timeout=600s
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cloudbroker -n cloudbroker --timeout=120s
	@echo "CloudBroker deployed. NodePort 30080: http://localhost:30080 (or http://<node-ip>:30080)"

deploy-ingest: ## Run CloudBroker ingest-all as a Kubernetes Job (creates cloudbroker-ingest-credentials secret for provider tokens)
	@./deploy/scripts/create-ingest-secrets.sh .env cloudbroker
	@kubectl delete job cloudbroker-ingest-all -n cloudbroker --ignore-not-found=true 2>/dev/null || true
	@kubectl apply -f deploy/ingest-job.yaml
	@echo "Ingestion started. Follow: kubectl logs job/cloudbroker-ingest-all -n cloudbroker -f"
	@kubectl wait --for=condition=complete job/cloudbroker-ingest-all -n cloudbroker --timeout=180s 2>/dev/null || true

##@ Cleanup

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
