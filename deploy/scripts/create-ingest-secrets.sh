#!/bin/bash
# Create cloudbroker-ingest-credentials secret from .env (ingestion vars only)
# Excludes DATABASE_URL, APP_ENV, etc. Run from cloudbroker repo root.
set -e

ENV_FILE="${1:-.env}"
NS="${2:-cloudbroker}"

# Ingestion-related env keys (provider credentials)
INGEST_KEYS="HETZNER_API_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION \
DIGITALOCEAN_API_TOKEN SCALEWAY_ACCESS_KEY SCALEWAY_SECRET_KEY SCW_ACCESS_KEY SCW_SECRET_KEY \
OVH_APPLICATION_KEY OVH_APPLICATION_SECRET OVH_CONSUMER_KEY OVH_PROJECT_ID OVH_SERVICE_NAME \
ARUBA_API_KEY ARUBA_API_SECRET ARUBA_PROJECT_ID \
UPCLOUD_API_TOKEN UPCLOUD_USERNAME UPCLOUD_PASSWORD \
SEEWEB_API_TOKEN SEEWEB_USERNAME SEEWEB_PASSWORD"

if [ ! -f "$ENV_FILE" ]; then
  echo "No .env file at $ENV_FILE. Skipping ingest credentials secret."
  exit 0
fi

# Filter .env to ingestion keys only, exclude empty and comments
FILTERED=$(mktemp)
trap "rm -f $FILTERED" EXIT

for key in $INGEST_KEYS; do
  val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
  if [ -n "$val" ]; then
    echo "${key}=${val}"
  fi
done > "$FILTERED"

# GCP: if GOOGLE_APPLICATION_CREDENTIALS is a file path, add file content (exclude from env - container uses mount path)
GCP_FILE=$(grep -E "^GOOGLE_APPLICATION_CREDENTIALS=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
HAS_GCP=0
[ -n "$GCP_FILE" ] && [ -f "$GCP_FILE" ] && HAS_GCP=1

if [ ! -s "$FILTERED" ] && [ "$HAS_GCP" -eq 0 ]; then
  echo "No ingestion credentials found in $ENV_FILE. Skipping secret."
  exit 0
fi

echo "Creating cloudbroker-ingest-credentials from $ENV_FILE..."
# kubectl does not allow --from-env-file with --from-file; create in two steps if both needed
if [ -s "$FILTERED" ]; then
  kubectl create secret generic cloudbroker-ingest-credentials -n "$NS" \
    --from-env-file="$FILTERED" \
    --dry-run=client -o yaml | kubectl apply -f -
fi
if [ "$HAS_GCP" -eq 1 ]; then
  if kubectl get secret cloudbroker-ingest-credentials -n "$NS" &>/dev/null; then
    kubectl patch secret cloudbroker-ingest-credentials -n "$NS" -p="{\"data\":{\"credentials.json\":\"$(base64 < "$GCP_FILE" | tr -d '\n')\"}}"
  else
    kubectl create secret generic cloudbroker-ingest-credentials -n "$NS" \
      --from-file=credentials.json="$GCP_FILE" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi
fi
echo "Secret created."
