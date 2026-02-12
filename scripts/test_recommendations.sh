#!/bin/bash
# Test recommendations API after provider ingestion
# Usage: ./scripts/test_recommendations.sh [provider_slug]

set -e

PROVIDER="${1:-all}"
API_URL="${API_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-dev-bypass}"

echo "Testing recommendations API..."
echo "Provider filter: ${PROVIDER}"
echo "API URL: ${API_URL}"
echo ""

# Test 1: Basic recommendation request
echo "=== Test 1: Basic recommendations (2 vCPU, 4GB RAM, EU, max 0.1 EUR/h) ==="
RESPONSE=$(curl -s -X POST "${API_URL}/api/recommendations" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.1,
    "limit": 20
  }')

# Extract provider counts
PROVIDERS=$(echo "${RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    providers = {}
    for r in recs:
        slug = r.get('provider_slug', 'unknown')
        providers[slug] = providers.get(slug, 0) + 1
    print('Providers found:', ' '.join(sorted(providers.keys())))
    for slug, count in sorted(providers.items()):
        print(f'  {slug}: {count} recommendations')
    print(f'Total: {len(recs)} recommendations')
except Exception as e:
    print(f'Error parsing response: {e}')
    sys.exit(1)
")

echo "${PROVIDERS}"
echo ""

# Test 2: Check specific provider if specified
if [ "${PROVIDER}" != "all" ]; then
    echo "=== Test 2: Checking ${PROVIDER} provider ==="
    PROVIDER_RECS=$(echo "${RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    provider_recs = [r for r in recs if r.get('provider_slug') == '${PROVIDER}']
    if provider_recs:
        print(f'Found {len(provider_recs)} ${PROVIDER} recommendations:')
        for r in provider_recs[:5]:
            print(f\"  - {r.get('instance_type_name')} in {r.get('region_slug')}: {r.get('explain', {}).get('price_eur_per_hour', 0):.6f} EUR/h\")
    else:
        print(f'No ${PROVIDER} recommendations found')
        sys.exit(1)
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
")
    echo "${PROVIDER_RECS}"
    echo ""
fi

# Test 3: Small instance request (more likely to include all providers)
echo "=== Test 3: Small instances (1 vCPU, 1GB RAM, EU, max 0.1 EUR/h) ==="
SMALL_RESPONSE=$(curl -s -X POST "${API_URL}/api/recommendations" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "min_vcpu": 1,
    "min_ram_gb": 1,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.1,
    "limit": 30
  }')

SMALL_PROVIDERS=$(echo "${SMALL_RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    providers = {}
    for r in recs:
        slug = r.get('provider_slug', 'unknown')
        providers[slug] = providers.get(slug, 0) + 1
    print('Providers in small instance recommendations:')
    for slug, count in sorted(providers.items()):
        print(f'  {slug}: {count} recommendations')
    print(f'Total: {len(recs)} recommendations')
except Exception as e:
    print(f'Error: {e}')
")

echo "${SMALL_PROVIDERS}"
echo ""

echo "✅ Recommendations API test completed"
