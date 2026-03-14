"""Shared HTTP fetch utility with retry, backoff, and 429 handling."""

from __future__ import annotations

import asyncio

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_TIMEOUT = 30.0


async def fetch_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    caller: str = "",
    **kwargs,
) -> httpx.Response:
    """Fetch an HTTP resource with automatic retry on transient failures.

    Retries on:
      - Connect / read / write timeouts
      - HTTP 429 (Too Many Requests) — respects Retry-After header
      - HTTP 502, 503, 504 (server errors)

    Returns the successful ``httpx.Response`` (caller can use ``.json()``, ``.text``, etc.).
    Raises the last encountered error after exhausting retries.
    """
    last_err: BaseException | None = None
    label = f"[{caller}] " if caller else ""

    for attempt in range(1, retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                if attempt < retries:
                    logger.warning(
                        "%sHTTP 429 on attempt %s/%s, retrying in %.1fs",
                        label, attempt, retries, retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()

            if resp.status_code in (502, 503, 504) and attempt < retries:
                logger.warning(
                    "%sHTTP %s on attempt %s/%s, retrying in %.1fs",
                    label, resp.status_code, attempt, retries, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            resp.raise_for_status()
            return resp

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError) as exc:
            last_err = exc
            if attempt < retries:
                logger.warning(
                    "%sAttempt %s/%s failed (%s), retrying in %.1fs",
                    label, attempt, retries, exc, backoff,
                )
                await asyncio.sleep(backoff)

    raise last_err  # type: ignore[misc]
