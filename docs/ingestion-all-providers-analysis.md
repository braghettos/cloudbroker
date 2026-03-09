# CloudBroker Ingestion – All Providers Analysis

## Summary

Ingestion of all CloudBroker providers **works** when credentials are configured. Providers that lack credentials fail gracefully (error logged, ingestion continues). A few improvements would make the experience clearer and more robust.

---

## Provider Credential Requirements

| Provider      | Auth Required | Env Vars | Notes |
|---------------|---------------|----------|-------|
| **Hetzner**   | Yes           | `HETZNER_API_TOKEN` | |
| **GCP**       | Yes           | `GOOGLE_APPLICATION_CREDENTIALS` or ADC | Billing Catalog API |
| **AWS**       | Yes           | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | boto3 |
| **Azure**     | No            | — | Retail Prices API (public, unauthenticated) |
| **DigitalOcean** | Yes        | `DIGITALOCEAN_API_TOKEN` | |
| **Scaleway**  | Yes           | `SCALEWAY_ACCESS_KEY`/`SCW_ACCESS_KEY`, `SCALEWAY_SECRET_KEY`/`SCW_SECRET_KEY` | |
| **OVH**       | Yes           | `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`, `OVH_SERVICE_NAME`/`OVH_PROJECT_ID` | |
| **Aruba**     | No            | — | Config-based, no API keys |

---

## Verified Behavior

- **Hetzner**: Ingestion completes successfully with token.
- **Aruba**: Config-based ingestion works without credentials.
- **Azure**: Retail Prices API ingestion works without credentials (~2 min).

---

## Issues Identified

### 1. Misleading final message when providers fail

When a provider fails, the CLI still logs success for that provider. This suggests success even though the provider failed. A per-provider summary would be clearer.

### 2. `ingest-all` duration

`make ingest-all` can take 10+ minutes (GCP, AWS, Azure are slow). No progress or ETA is shown.

### 3. `_ensure_ingestion_providers` only ensures gcp and hetzner

Other providers (AWS, Azure, DO, Scaleway, OVH, Aruba) create their own provider via `ensure_provider` in their connector. This is correct; no change needed.

### 4. E2E credential source

The E2E script (`integration-test-e2e-all-providers.sh`) runs `make ingest-all` from `CLOUDBROKER_DIR`. Credentials come from `cloudbroker/.env` only. The cloudburst-autoscaler `.env` is not used for ingestion. Ensure `cloudbroker/.env` has all required provider credentials for E2E.

---

## Fix Plan

### Priority 1: UX improvements

1. **Add per-provider summary at end of `run_ingest`**:
   - Track success/failure per provider.
   - Log a summary: `Ingestion summary: 7 succeeded, 2 failed (ovh: ..., ...)`.

2. **Optional: skip providers without credentials**:
   - Add `--skip-missing-creds` to skip providers that would fail due to missing env vars, and log them as skipped instead of failed.

### Priority 2: Performance (optional)

3. **Reduce SQLAlchemy log verbosity** for ingestion:
   - Use `echo=False` or set log level to WARNING for the ingestion session to avoid huge logs.

4. **Add `--quick` support for `ingest-all`**:
   - When `--quick` is passed with `provider=all`, run quick mode for AWS (eu-west-1) and Azure (westeurope) to speed up validation.

---

## Implementation Checklist

- [ ] Add per-provider success/failure tracking in `run_ingest`
- [ ] Log final summary with succeeded/failed counts and provider names
- [ ] (Optional) Add `--skip-missing-creds` flag
- [ ] (Optional) Reduce SQL echo for ingestion
- [ ] (Optional) Support `--quick` for `ingest-all`
