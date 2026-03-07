# CloudBroker – common tasks
# Usage: make [target]
# Prefers .venv/bin/python if present; override with: make <target> PYTHON=python3

VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/bin/python3))
PYTHON ?= $(if $(VENV_PYTHON),$(VENV_PYTHON),python3)

# Deploy: DB password for CloudBroker (default for dev only; set for production)
DB_PASSWORD ?= cloudbroker-dev
CODER_DB_PASSWORD ?= coder
# Coder template: path to cloudburst-coder-template (sibling of cloudbroker)
TEMPLATE_DIR ?= ../cloudburst-coder-template
CODER_URL ?= http://localhost:30180
CODER_ACCESS_URL ?= $(CODER_URL)
CODER_FIRST_USER_USERNAME ?= admin
CODER_FIRST_USER_EMAIL ?= admin@example.com
CODER_FIRST_USER_PASSWORD ?= admin
# OIDC (Google): set both to enable. Create OAuth client at https://console.cloud.google.com/apis/credentials
CODER_OIDC_CLIENT_ID ?=
CODER_OIDC_CLIENT_SECRET ?=
CODER_OIDC_EMAIL_DOMAIN ?=
KIND_CLUSTER ?= cloudburst
KIND_NODE_IMAGE ?= kindest/node:v1.34.3

.PHONY: help install dev run test test-exhaustive test-cov test-e2e lint format up down migrate migrate-down migrate-revision ingest-gcp ingest-gcp-machines ingest-hetzner ingest-aws ingest-aws-quick ingest-azure ingest-azure-quick ingest-azure-egress ingest-azure-spot ingest-azure-spot-quick ingest-digitalocean ingest-scaleway ingest-ovh ingest-all ingest-egress ingest-storage ingest-public-ip ingest-os-license ingest-all-costs test-recommendations docker-build clean deploy deploy-cloudbroker deploy-coder deploy-template install-prereqs deploy-cluster deploy-ingest build-kind-image

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

ingest-seeweb: ## Ingest Seeweb (requires SEEWEB_USERNAME + SEEWEB_PASSWORD)
	$(PYTHON) -m apps.ingestor ingest --provider seeweb

ingest-aruba: ## Ingest Aruba Cloud (config-based, no API keys)
	$(PYTHON) -m apps.ingestor ingest --provider aruba

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

##@ Kubernetes Deploy (Coder + CloudBroker + Cloudburst)

install-prereqs: ## Install kind, kubectl, helm, coder CLI (macOS: brew; Linux: prints instructions)
	@echo "==> Checking prerequisites..."
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		for pkg in kind kubectl helm; do \
			if ! command -v $$pkg >/dev/null 2>&1; then \
				echo "Installing $$pkg..."; brew install $$pkg || { echo "Failed to install $$pkg"; exit 1; }; \
			fi; \
		done; \
		if ! command -v coder >/dev/null 2>&1; then \
			echo "Installing coder..."; brew install coder/coder/coder || echo "Coder install failed. Template push will be skipped."; \
		fi; \
	else \
		command -v kind >/dev/null 2>&1 || { echo "Missing kind. Install: https://kind.sigs.k8s.io"; exit 1; }; \
		command -v kubectl >/dev/null 2>&1 || { echo "Missing kubectl. Install: https://kubernetes.io/docs/tasks/tools"; exit 1; }; \
		command -v helm >/dev/null 2>&1 || { echo "Missing helm. Install: https://helm.sh"; exit 1; }; \
		command -v coder >/dev/null 2>&1 || echo "Coder not found. Template push will be skipped. Install: curl -L https://coder.com/install.sh | sh"; \
	fi
	@echo "Prerequisites OK."

build-kind-image: ## Build CloudBroker image and load into Kind (for local deploy when registry image is private)
	@docker build -t ghcr.io/braghettos/cloudbroker:latest -f docker/Dockerfile .
	@kind load docker-image ghcr.io/braghettos/cloudbroker:latest --name $(KIND_CLUSTER) || \
		(docker save ghcr.io/braghettos/cloudbroker:latest | docker exec -i $(KIND_CLUSTER)-control-plane ctr --namespace k8s.io images import - && \
		echo "CloudBroker image loaded via ctr fallback.") || \
		(echo "Failed to load CloudBroker image."; exit 1)
	@docker pull ghcr.io/coder/coder:v2.31.2 2>/dev/null || true
	@kind load docker-image ghcr.io/coder/coder:v2.31.2 --name $(KIND_CLUSTER) 2>/dev/null || \
		(docker save ghcr.io/coder/coder:v2.31.2 2>/dev/null | docker exec -i $(KIND_CLUSTER)-control-plane ctr --namespace k8s.io images import - 2>/dev/null && echo "Coder image loaded.") || true
	@echo "Images loaded into Kind cluster $(KIND_CLUSTER)."

build-deployer-image: ## Build cloudburst-deployer image (vcluster+kubectl+kustomize). Requires TEMPLATE_DIR.
	@if [ -z "$(TEMPLATE_DIR)" ] || [ ! -d "$(TEMPLATE_DIR)" ]; then \
		echo "Error: TEMPLATE_DIR required (path to cloudburst-coder-template)"; exit 1; \
	fi
	@docker build -t ghcr.io/braghettos/cloudburst-deployer:latest -f "$(TEMPLATE_DIR)/docker/Dockerfile.deployer" "$(TEMPLATE_DIR)"
	@kind load docker-image ghcr.io/braghettos/cloudburst-deployer:latest --name $(KIND_CLUSTER) 2>/dev/null || \
		(docker save ghcr.io/braghettos/cloudburst-deployer:latest | docker exec -i $(KIND_CLUSTER)-control-plane ctr --namespace k8s.io images import - && \
		echo "cloudburst-deployer image loaded via ctr fallback.") || \
		(echo "Failed to load cloudburst-deployer image."; exit 1)
	@echo "cloudburst-deployer image loaded."

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
	@kubectl wait --for=condition=Ready cluster/cloudbroker-db -n cloudbroker --timeout=300s
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cloudbroker -n cloudbroker --timeout=120s
	@echo "CloudBroker deployed. NodePort 30080: http://localhost:30080 (or http://<node-ip>:30080)"

deploy-coder: ## Deploy only Coder (requires CloudBroker/deploy-cloudbroker first)
	@kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.28/releases/cnpg-1.28.1.yaml 2>/dev/null || true
	@kubectl create namespace coder 2>/dev/null || true
	@kubectl delete secret coder-db-app -n coder --ignore-not-found 2>/dev/null || true
	@kubectl create secret generic coder-db-app -n coder --type=kubernetes.io/basic-auth --from-literal=username=coder --from-literal=password="$(CODER_DB_PASSWORD)" --dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -f deploy/coder-postgres-cnpg.yaml
	@kubectl wait --for=condition=Ready cluster/coder-db -n coder --timeout=300s
	@kubectl create secret generic coder-db-url -n coder \
		--from-literal=url="postgres://coder:$(CODER_DB_PASSWORD)@coder-db-rw:5432/coder?sslmode=disable" \
		--dry-run=client -o yaml | kubectl apply -f -
	@helm repo add coder-v2 https://helm.coder.com/v2 2>/dev/null || true
	@helm repo update coder-v2
	@if [ -n "$(CODER_OIDC_CLIENT_ID)" ] && [ -n "$(CODER_OIDC_CLIENT_SECRET)" ]; then \
		echo "==> OIDC (Google) enabled. Creating coder-oidc secret..."; \
		kubectl create secret generic coder-oidc -n coder \
			--from-literal=client_id="$(CODER_OIDC_CLIENT_ID)" \
			--from-literal=client_secret="$(CODER_OIDC_CLIENT_SECRET)" \
			--dry-run=client -o yaml | kubectl apply -f -; \
		CODER_ACCESS_URL="$(CODER_ACCESS_URL)" CODER_OIDC_EMAIL_DOMAIN="$(CODER_OIDC_EMAIL_DOMAIN)" \
			envsubst '$$CODER_ACCESS_URL $$CODER_OIDC_EMAIL_DOMAIN' < deploy/coder-values-oidc.yaml > deploy/coder-values-oidc-rendered.yaml; \
		helm upgrade --install coder coder-v2/coder -n coder -f deploy/coder-values.yaml -f deploy/coder-values-oidc-rendered.yaml; \
		rm -f deploy/coder-values-oidc-rendered.yaml; \
	else \
		helm upgrade --install coder coder-v2/coder -n coder -f deploy/coder-values.yaml; \
	fi
	@echo "==> Applying RBAC for vcluster template (namespace creation)..."
	@kubectl apply -f deploy/coder-rbac-vcluster.yaml
	@echo "==> Waiting for Coder to initialize..."
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=coder -n coder --timeout=180s 2>/dev/null || sleep 30
	@echo "==> Creating first Coder admin user..."
	@kubectl delete job coder-create-admin -n coder --ignore-not-found 2>/dev/null || true
	@CODER_USERNAME="$(CODER_FIRST_USER_USERNAME)" CODER_EMAIL="$(CODER_FIRST_USER_EMAIL)" CODER_PASSWORD="$(CODER_FIRST_USER_PASSWORD)" envsubst '$$CODER_USERNAME $$CODER_EMAIL $$CODER_PASSWORD' < deploy/coder-create-admin-job.yaml | kubectl apply -f -
	@kubectl wait --for=condition=complete job/coder-create-admin -n coder --timeout=60s 2>/dev/null || true
	@echo "Coder deployed. NodePort 30180: http://localhost:30180 (or http://<node-ip>:30180)"

deploy-ingest: ## Run CloudBroker ingest-all as a Kubernetes Job (creates cloudbroker-ingest-credentials secret for provider tokens)
	@./deploy/scripts/create-ingest-secrets.sh .env cloudbroker
	@kubectl delete job cloudbroker-ingest-all -n cloudbroker --ignore-not-found=true 2>/dev/null || true
	@kubectl apply -f deploy/ingest-job.yaml
	@echo "Ingestion started. Follow: kubectl logs job/cloudbroker-ingest-all -n cloudbroker -f"
	@kubectl wait --for=condition=complete job/cloudbroker-ingest-all -n cloudbroker --timeout=180s 2>/dev/null || true

deploy-template: ## Push cloudburst-vcluster template to Coder (requires: coder login first)
	@if [ ! -d "$(TEMPLATE_DIR)" ]; then \
		echo "Error: Template dir $(TEMPLATE_DIR) not found. Set TEMPLATE_DIR=/path/to/cloudburst-coder-template"; exit 1; \
	fi
	@echo "==> Pushing template from $(TEMPLATE_DIR)..."
	@cd "$(TEMPLATE_DIR)" && coder templates push cloudburst-vcluster --directory . -y
	@echo "Template pushed. Create workspaces from Coder UI at $(CODER_URL)"

##@ Cleanup

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
