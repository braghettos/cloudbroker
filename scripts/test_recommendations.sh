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

# Test 4: TCO placement - derive data source from live response, then call with TCO context
echo "=== Test 4: TCO placement (data source from live data) ==="
BASE_RESPONSE=$(curl -s -X POST "${API_URL}/api/recommendations" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "ANY",
    "max_price_eur_per_hour": 1.0,
    "limit": 20
  }')

TCO_TEST4=$(echo "${BASE_RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    if not recs:
        print('FAIL: No recommendations to derive data source from')
        sys.exit(1)
    first = recs[0]
    ds_provider = first.get('provider_slug', '')
    ds_region = first.get('region_slug', '')
    if not ds_provider or not ds_region:
        print('FAIL: First recommendation missing provider_slug or region_slug')
        sys.exit(1)
    print(f'DATA_SOURCE_PROVIDER={ds_provider}')
    print(f'DATA_SOURCE_REGION={ds_region}')
except Exception as e:
    print(f'FAIL: {e}')
    sys.exit(1)
")

eval "${TCO_TEST4}"
echo "Data source: ${DATA_SOURCE_PROVIDER} / ${DATA_SOURCE_REGION}"

TCO_RESPONSE=$(curl -s -X POST "${API_URL}/api/recommendations" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d "{
    \"min_vcpu\": 2,
    \"min_ram_gb\": 4,
    \"arch\": \"x86_64\",
    \"region_constraint\": \"ANY\",
    \"max_price_eur_per_hour\": 1.0,
    \"limit\": 20,
    \"data_source_provider\": \"${DATA_SOURCE_PROVIDER}\",
    \"data_source_region\": \"${DATA_SOURCE_REGION}\",
    \"estimated_egress_gb_per_hour\": 1.0
  }")

TCO_ASSERT4=$(echo "${TCO_RESPONSE}" | python3 -c "
import sys, json, os
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    ds_provider = os.environ.get('DATA_SOURCE_PROVIDER', '')
    ds_region = os.environ.get('DATA_SOURCE_REGION', '')
    if not recs:
        print('FAIL: No TCO recommendations returned')
        sys.exit(1)
    if recs[0].get('explain', {}).get('tco_eur_per_hour') is None:
        print('SKIP: egress_rates not loaded (run: make ingest-azure-egress or ingest --provider azure --egress)')
        sys.exit(0)
    errors = []
    for r in recs:
        explain = r.get('explain', {})
        tco = explain.get('tco_eur_per_hour')
        egress = explain.get('egress_cost_eur_per_hour')
        if tco is None and egress is None:
            errors.append(f'Recommendation {r.get(\"provider_slug\")}/{r.get(\"region_slug\")} missing tco_eur_per_hour and egress_cost_eur_per_hour')
        prov, reg = r.get('provider_slug', ''), r.get('region_slug', '')
        if prov == ds_provider and reg == ds_region and egress is not None and egress != 0:
            errors.append(f'Same provider+region {prov}/{reg} should have egress_cost=0, got {egress}')
    if errors:
        for e in errors:
            print(f'FAIL: {e}')
        sys.exit(1)
    print('PASS: All TCO recommendations have tco/egress fields; same provider+region has zero egress')
except Exception as ex:
    print(f'FAIL: {ex}')
    sys.exit(1)
")

echo "${TCO_ASSERT4}"
echo ""

# Test 5: TCO ranking - top recommendation should match data source when such candidate exists
echo "=== Test 5: TCO ranking behavior ==="
TCO_ASSERT5=$(echo "${TCO_RESPONSE}" | python3 -c "
import sys, json, os
try:
    data = json.load(sys.stdin)
    recs = data.get('recommendations', [])
    ds_provider = os.environ.get('DATA_SOURCE_PROVIDER', '')
    ds_region = os.environ.get('DATA_SOURCE_REGION', '')
    if not recs:
        print('FAIL: No TCO recommendations')
        sys.exit(1)
    if recs[0].get('explain', {}).get('tco_eur_per_hour') is None:
        print('SKIP: egress_rates not loaded (run ingest with --egress)')
        sys.exit(0)
    has_match = any(r.get('provider_slug') == ds_provider and r.get('region_slug') == ds_region for r in recs)
    if has_match:
        top = recs[0]
        if top.get('provider_slug') != ds_provider or top.get('region_slug') != ds_region:
            print(f'FAIL: Top recommendation is {top.get(\"provider_slug\")}/{top.get(\"region_slug\")} but data source {ds_provider}/{ds_region} exists in results - zero egress should favor it')
            sys.exit(1)
        print('PASS: Top recommendation matches data source (zero egress cost favored)')
    else:
        print('SKIP: No recommendation matches data source provider+region (single-provider catalog)')
except Exception as ex:
    print(f'FAIL: {ex}')
    sys.exit(1)
")

echo "${TCO_ASSERT5}"
echo ""

echo "✅ Recommendations API test completed"
