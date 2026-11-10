"""Shared HTTP client with timeouts, transient-error classification and bounded retries."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..logs import get_logger, redact

log = get_logger("http")

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False, status: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.status = status


class NotConfigured(ProviderError):
    """Raised when an integration lacks credentials. Never treated as success."""


def request(
    method: str,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 1.5,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
    **kwargs: Any,
) -> httpx.Response:
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = client.request(method, url, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt > retries:
                    raise ProviderError(f"{method} {url} network error: {exc}", transient=True) from exc
                time.sleep(backoff ** attempt)
                continue
            if resp.status_code in TRANSIENT_STATUS and attempt <= retries:
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff ** attempt
                log.warning("transient %s from %s, retrying in %.1fs", resp.status_code, url, delay)
                time.sleep(min(delay, 30))
                continue
            if resp.status_code >= 400:
                body = redact(resp.text[:500])
                raise ProviderError(
                    f"{method} {url.split('?')[0]} -> {resp.status_code}: {body}",
                    transient=resp.status_code in TRANSIENT_STATUS,
                    status=resp.status_code,
                )
            return resp
    finally:
        if own:
            client.close()
