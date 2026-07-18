"""Shared HTTP helpers for public Podscripts page access."""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://podscripts.co"
DEFAULT_MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {408, 429}


def build_podscripts_client() -> httpx.Client:
    """Build the configured client used for public Podscripts requests."""
    return httpx.Client(
        timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    )


def fetch_podscripts_html(
    *,
    client: httpx.Client,
    url: str,
    retry_delay_seconds: float,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Fetch public Podscripts HTML with bounded retry on transient failure.

    Args:
        client: Configured HTTP client.
        url: Public Podscripts page URL.
        retry_delay_seconds: Base wait between transient failures.
        max_attempts: Maximum request attempts.

    Returns:
        Successfully retrieved response body.

    Raises:
        httpx.HTTPError: When a permanent error occurs or retries are exhausted.
        ValueError: When ``max_attempts`` is invalid.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as error:
            if (
                error.response.status_code not in RETRYABLE_STATUS_CODES
                and error.response.status_code < 500
            ):
                raise
            if attempt == max_attempts:
                raise
        except httpx.TransportError:
            if attempt == max_attempts:
                raise

        wait_seconds = retry_delay_seconds * attempt
        logger.warning(
            "Transient Podscripts request failure; retrying %s in %.1fs",
            url,
            wait_seconds,
        )
        time.sleep(wait_seconds)

    raise RuntimeError("Podscripts retry loop completed without returning or raising")
