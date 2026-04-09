"""Shared verification infrastructure for provider connectors.

All verify modules share the same VerificationResult dataclass and
common tolerance constants. This module provides them in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationResult:
    """Result of a single price verification check."""

    passed: bool
    message: str
    details: dict[str, Any] | None = None


# Default relative tolerance for price comparisons
DEFAULT_RELTOL = 0.02  # 2%


def check_price_match(
    stored: float,
    expected: float,
    label: str,
    reltol: float = DEFAULT_RELTOL,
) -> VerificationResult:
    """Compare stored vs expected price within relative tolerance.

    Returns a VerificationResult with pass/fail and details.
    """
    if expected <= 0:
        return VerificationResult(
            passed=True,
            message=f"{label}: skipped (expected price is zero)",
        )

    diff_pct = abs(stored - expected) / expected
    if diff_pct <= reltol:
        return VerificationResult(
            passed=True,
            message=f"{label}: OK (stored={stored:.6f}, expected={expected:.6f}, diff={diff_pct:.2%})",
        )
    return VerificationResult(
        passed=False,
        message=f"{label}: MISMATCH (stored={stored:.6f}, expected={expected:.6f}, diff={diff_pct:.2%})",
        details={
            "stored": stored,
            "expected": expected,
            "diff_pct": round(diff_pct, 4),
            "tolerance": reltol,
        },
    )


def summarize_results(
    results: list[VerificationResult],
    provider_slug: str,
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    """Log summary and optionally raise on mismatches."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    logger.info(
        "Verification complete",
        provider_slug=provider_slug,
        total=len(results),
        passed=passed,
        failed=failed,
    )
    if fail_on_mismatch and failed > 0:
        messages = [r.message for r in results if not r.passed]
        raise RuntimeError(
            f"{provider_slug} verification: {failed} mismatch(es): {'; '.join(messages[:5])}"
        )
    return results
