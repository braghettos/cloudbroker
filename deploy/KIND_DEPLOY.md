# Deploy to Kind - Instructions

## Deployed (Kind cluster: cloudburst)

- **CloudBroker**: CloudNative-PG (PostgreSQL) + API running in `cloudbroker` namespace

## Access (NodePort)

- **CloudBroker**: http://localhost:30080 (NodePort 30080)

## Quick Deploy

```bash
# 1. Create Kind cluster with NodePort mappings
make deploy-cluster

# 2. Deploy CloudBroker (CloudNative-PG + API)
make deploy

# 3. Verify
curl http://localhost:30080/health
```

## Optional: Build and load local image

If using a local image instead of the registry:

```bash
make build-kind-image
make deploy
```

## Optional: Run ingestion

```bash
make deploy-ingest
```

## Free Disk Space (if needed)

Run these to free space before retrying:

```bash
docker system prune -af
# Or more aggressive
docker builder prune -af
docker system prune -af --volumes
```
