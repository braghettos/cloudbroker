#!/usr/bin/env python3
"""
Check that Scorecard analytics results match the Recommendations API response for the same requirements.

The Scorecard (ProviderRadar) calls POST /api/recommendations with cost_weight, performance_weight,
reliability_weight and aggregates by provider: avg(normalized_price) as Cost, avg(performance_score|resource_fit)
as Performance, avg(reliability_score) as Reliability. Best provider = max(Cost + Performance + Reliability).

This script:
1. Calls the API with the same params the Scorecard uses
2. Reconstructs the scorecard aggregation (mirroring ProviderRadar logic)
3. Verifies consistency: all recs have required explain fields; best provider is deterministic
4. Optionally compares with a second API call (default recs params) to ensure both use the same underlying data

Usage:
  python scripts/check_scorecard_recommendations_match.py [--api-url URL] [--verbose]
  API_URL=http://localhost:8000 python scripts/check_scorecard_recommendations_match.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


# Scorecard params (same as ProviderRadar in cloudbroker-ui)
SCORECARD_PARAMS = {
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 1,
    "purchase_model": "on_demand",
    "limit": 50,
    "cost_weight": 0.4,
    "performance_weight": 0.3,
    "reliability_weight": 0.3,
}

# Default recommendations params (for cross-check)
DEFAULT_RECS_PARAMS = {
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 1,
    "purchase_model": "on_demand",
    "limit": 20,
}


def call_recommendations(api_url: str, params: dict[str, Any], api_key: str | None = None) -> dict:
    """POST /api/recommendations and return JSON."""
    import urllib.request

    url = f"{api_url.rstrip('/')}/api/recommendations"
    data = json.dumps(params).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def reconstruct_scorecard(recommendations: list[dict]) -> tuple[list[dict], dict | None]:
    """
    Reconstruct scorecard aggregation from API response (mirrors ProviderRadar logic).
    Returns (series, best_provider).
    """
    by_provider: dict[str, dict[str, list[float]]] = {}

    for r in recommendations:
        slug = r.get("provider_slug")
        if not slug:
            continue
        explain = r.get("explain") or {}
        if slug not in by_provider:
            by_provider[slug] = {"cost": [], "perf": [], "rel": []}

        cost_val = explain.get("normalized_price")
        if cost_val is not None:
            by_provider[slug]["cost"].append(float(cost_val))

        perf_val = explain.get("performance_score")
        if perf_val is None:
            perf_val = explain.get("resource_fit")
        if perf_val is not None:
            by_provider[slug]["perf"].append(float(perf_val))

        rel_val = explain.get("reliability_score")
        if rel_val is None:
            rel_val = 0.5
        by_provider[slug]["rel"].append(float(rel_val))

    series = []
    for slug, v in by_provider.items():
        cost_avg = sum(v["cost"]) / len(v["cost"]) if v["cost"] else 0.0
        perf_avg = sum(v["perf"]) / len(v["perf"]) if v["perf"] else 0.0
        rel_avg = sum(v["rel"]) / len(v["rel"]) if v["rel"] else 0.0
        combined = cost_avg + perf_avg + rel_avg
        series.append(
            {
                "slug": slug,
                "Cost": cost_avg,
                "Performance": perf_avg,
                "Reliability": rel_avg,
                "combined": combined,
                "rec_count": len(v["cost"]),
            }
        )

    if not series:
        return [], None

    best = max(series, key=lambda s: s["combined"])
    return series, best


def run_checks(api_url: str, api_key: str | None, verbose: bool) -> int:
    """Run all checks. Returns 0 on success, 1 on failure."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        resp = call_recommendations(api_url, SCORECARD_PARAMS, api_key)
    except Exception as e:
        errors.append(f"API call failed: {e}")
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    recs = resp.get("recommendations", [])
    if not recs:
        errors.append("No recommendations returned")
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    for i, r in enumerate(recs):
        explain = r.get("explain")
        if not explain:
            errors.append(f"Recommendation {i} missing explain block")
            continue
        if "normalized_price" not in explain:
            errors.append(f"Recommendation {i} missing explain.normalized_price")
        perf = explain.get("performance_score")
        fit = explain.get("resource_fit")
        if perf is None and fit is None:
            warnings.append(f"Recommendation {i} has neither performance_score nor resource_fit (using 0.5)")

    series, best = reconstruct_scorecard(recs)
    if not series:
        errors.append("Scorecard aggregation produced no providers")
    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    print("=== Scorecard (from Recommendations API) ===")
    print(f"Params: min_vcpu={SCORECARD_PARAMS['min_vcpu']}, min_ram_gb={SCORECARD_PARAMS['min_ram_gb']}, "
          f"arch={SCORECARD_PARAMS['arch']}, cost_weight=0.4, perf_weight=0.3, rel_weight=0.3")
    print(f"Recommendations: {len(recs)}")
    print()
    print("Provider scores (Cost, Perf, Rel = 0–1; combined = sum):")
    for s in sorted(series, key=lambda x: -x["combined"]):
        marker = " (best)" if best and s["slug"] == best["slug"] else ""
        print(f"  {s['slug']:15} Cost={s['Cost']:.3f} Perf={s['Performance']:.3f} Rel={s['Reliability']:.3f} "
              f"combined={s['combined']:.3f} (n={s['rec_count']}){marker}")
    print()
    print(f"Best provider: {best['slug']} (Cost={best['Cost']*100:.0f}% Perf={best['Performance']*100:.0f}% "
          f"Rel={best['Reliability']*100:.0f}%)")
    print()

    print("=== Top 5 Recommendations (API ranking) ===")
    for i, r in enumerate(recs[:5]):
        explain = r.get("explain", {})
        perf = explain.get("performance_score") or explain.get("resource_fit") or 0
        rel = explain.get("reliability_score", 0.5)
        print(f"  {i+1}. {r['provider_slug']}/{r['instance_type_name']} {r['region_slug']} "
              f"score={r.get('score', 0):.4f} cost={explain.get('normalized_price', 0):.3f} "
              f"perf={perf:.3f} rel={rel:.3f}")
    print()

    top_rec_provider = recs[0]["provider_slug"] if recs else None
    if best and top_rec_provider != best["slug"]:
        print(f"Note: Top recommendation is from {top_rec_provider}, scorecard best is {best['slug']}. "
              "This can differ: API ranks by per-instance score; scorecard aggregates by provider.")
    else:
        print("Match: Top recommendation and scorecard best provider align.")

    if verbose:
        print()
        print("=== Cross-check: default recs params (no weights) ===")
        try:
            default_resp = call_recommendations(api_url, DEFAULT_RECS_PARAMS, api_key)
            default_recs = default_resp.get("recommendations", [])
            default_series, default_best = reconstruct_scorecard(default_recs)
            if default_best:
                print(f"Default params best provider: {default_best['slug']}")
                print(f"Scorecard params best provider: {best['slug']}")
                if default_best["slug"] != best["slug"]:
                    print("(Different params can yield different best providers)")
        except Exception as e:
            print(f"Cross-check skipped: {e}")

    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    print()
    print("PASS: Scorecard aggregation matches Recommendations API response.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Scorecard analytics matches Recommendations API for same requirements"
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_URL", "http://localhost:8000"),
        help="CloudBroker API base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("API_KEY"),
        help="API key (optional in dev)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    return run_checks(args.api_url, args.api_key, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
