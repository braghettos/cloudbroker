<p align="center">
  <img src="assets/logo.png" alt="CloudBroker" width="200">
</p>

<h1 align="center">CloudBroker</h1>

Production-ready Python backend for a **compute pricing marketplace**: FastAPI, PostgreSQL, SQLAlchemy 2.x, Alembic, Pydantic, Docker.

## Tech stack

- **FastAPI** – API framework
- **PostgreSQL** – Database
- **SQLAlchemy 2.x** – ORM (async via `asyncpg`)
- **Alembic** – Migrations
- **Pydantic** – Settings and validation
- **Docker** & **docker-compose** – Run app and DB locally

## Quick start (Docker)

```bash
# Clone and enter project
cd cloudbroker

# Copy env (optional; defaults work with compose)
cp .env.example .env

# Start API + PostgreSQL
make up

# Run migrations (from host; requires venv and .env pointing at localhost)
make migrate

# Seed database (3 providers, 5 regions, 20 instance types, pricing)
make seed

# API is at http://localhost:8000
```

If you don't have a local Python env, run migrate and seed inside the API container:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python scripts/seed.py
```

Stop the stack:

```bash
make down
```

## Quick start (local)

1. **Virtualenv and install**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   make install
   ```

2. **Env and database**

   ```bash
   cp .env.example .env
   # Start PostgreSQL (e.g. local or Docker: docker compose up -d db)
   make migrate
   make seed
   ```

3. **Run API**

   ```bash
   make dev
   ```

4. Open **http://localhost:8000** and **http://localhost:8000/docs**.

## Makefile commands

Run `make help` for the full list. Key targets:

| Command | Description |
|---------|-------------|
| `make up` | Start stack (docker compose up -d) |
| `make down` | Stop stack |
| `make migrate` | Run Alembic migrations (upgrade head) |
| `make seed` | Seed DB with providers, regions, instance types |
| `make install` | Install all dependencies (dev + ingest) |
| `make dev` | Run API with hot-reload |
| `make run` | Run API (production mode) |
| `make test` | Run pytest |
| `make lint` | Run ruff linter |
| `make ingest-all` | Ingest pricing from all providers |
| `make ingest-gcp` | Ingest GCP Billing Catalog |
| `make ingest-aws` | Ingest AWS EC2 pricing |
| `make ingest-scaleway` | Ingest Scaleway pricing |
| `make clean` | Remove caches and build artifacts |

## cURL examples

Base URL: `http://localhost:8000` (use `http://localhost:8000/api` for list endpoints).

**Health**

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

**List providers (paginated)**

```bash
curl -s "http://localhost:8000/api/providers?limit=5"
```

**List regions (filter by provider)**

```bash
curl -s "http://localhost:8000/api/regions?provider=aws"
curl -s "http://localhost:8000/api/regions?provider=gcp&limit=10"
```

**List instance types (filter by min specs and arch)**

```bash
curl -s "http://localhost:8000/api/instance-types?min_vcpu=2&min_ram_gb=4&arch=x86_64&limit=10"
```

**List prices (filter by provider and region)**

```bash
curl -s "http://localhost:8000/api/prices?provider=aws&region=eu-west-1&limit=5"
```

**Recommendations (POST)**

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.2,
    "limit": 5
  }'
```

## Project structure

```
app/
  main.py              # FastAPI app
  api/                 # Routes (providers, regions, instance-types, prices, recommendations, metrics)
  core/                # Config, database, logging, security, middleware
  models/              # SQLAlchemy models
  schemas/             # Pydantic schemas
  services/            # Business logic (recommendations, ingestion, analytics)
apps/
  ingestor/            # CLI for provider data ingestion
connectors/            # Provider-specific pricing connectors (GCP, AWS, Azure, Hetzner, Scaleway, etc.)
migrations/            # Alembic migrations
scripts/
  seed.py              # DB seed script
  test_recommendations.sh
tests/                 # Pytest test suite
docs/                  # Additional documentation
docker/
docker-compose.yml
Makefile
```

## Environment variables

See `.env.example`. Main ones:

- `DATABASE_URL` – Async URL for the app (`postgresql+asyncpg://...`)
- `DATABASE_URL_SYNC` – Sync URL for Alembic (`postgresql://...`)
- `APP_ENV`, `DEBUG`, `HOST`, `PORT`

## Documentation

- [Architecture](docs/architecture.md) -- data model, recommendation engine, ingestion pipeline, and integration with Cloudburst
- [End-to-End Manual Testing Guide](docs/E2E_MANUAL_TEST.md) -- full step-by-step guide to test every feature
- [Ingestion Guide](docs/ingestion.md) -- details on provider data ingestion
- [Usage Guide](USAGE_GUIDE.md) -- API examples and workflows
- [AWS Pricing API Research](docs/aws_pricing_api_research.md) -- API overview, authentication, rate limits
- [Azure Pricing API Research](docs/azure_pricing_api_research.md) -- Retail Prices API details
- [AWS Reserved Instance Research](docs/aws_reserved_instance_research.md) -- RI pricing via GetProducts
- **Swagger UI**: http://localhost:8000/docs (when running)

## License

See [LICENSE](LICENSE).
