"""Shared HTTP plumbing for every ingester.

The important bit here is the rate limiter. GDELT throttles at roughly one
request every five seconds and does NOT return a clean 429 - it hands back a
plain-text complaint with a 200 status, which json.loads() then chokes on.
So we pace ourselves rather than react to errors.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

# Some publishers (PR Newswire notably) return 403 to anything that looks like a bot.
USER_AGENT = (
    "private-company-dashboard/0.1 (portfolio project; contact via GitHub) "
    "python-requests"
)

DEFAULT_TIMEOUT = 30


@dataclass
class RateLimiter:
    """Blocking minimum-interval limiter.

    One instance per upstream service. Not thread-safe, which is fine - the
    pipeline is deliberately sequential.
    """

    min_interval_seconds: float
    _last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            log.debug("rate limit: sleeping %.2fs", remaining)
            time.sleep(remaining)
        self._last_call = time.monotonic()


class FetchError(RuntimeError):
    """Upstream gave us something we can't use."""


def fetch_text(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """GET with pacing and retries. Returns the raw body."""
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        if limiter:
            limiter.wait()
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            # exponential backoff: 2s, 4s, 8s
            backoff = 2**attempt
            log.warning("fetch failed (attempt %d/%d): %s - retrying in %ds",
                        attempt, retries, exc, backoff)
            if attempt < retries:
                time.sleep(backoff)

    raise FetchError(f"GET {url} failed after {retries} attempts: {last_error}")


def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    limiter: RateLimiter | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Like fetch_text but parses JSON, with a clearer error when it isn't JSON.

    GDELT's throttle message comes back as HTTP 200 with a plain-text body, so
    a bare json.loads() would raise something unhelpful about line 1 column 1.
    """
    body = fetch_text(url, params, limiter=limiter, retries=retries)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = body[:200].replace("\n", " ")
        raise FetchError(
            f"Expected JSON from {url} but got: {snippet!r}. "
            "For GDELT this usually means you're being rate limited."
        ) from exc


def payload_hash(payload: Any) -> str:
    """Stable hash of a response, used as the idempotency key in raw_api_response.

    sort_keys matters: dict ordering must not change the hash, or re-running the
    pipeline would look like new data every time.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()
