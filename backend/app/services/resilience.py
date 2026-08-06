from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.core.config import get_settings


T = TypeVar("T")
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def is_retryable_provider_read_error(error: Exception) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_HTTP_STATUSES
    return False


async def retry_idempotent_provider_read(operation: Callable[[], Awaitable[T]]) -> T:
    """Retry a provider read; callers must never use this for external writes."""
    settings = get_settings()
    retries = settings.connector_read_max_retries
    for attempt in range(retries + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt >= retries or not is_retryable_provider_read_error(exc):
                raise
            exponential = settings.connector_retry_base_delay_seconds * (2**attempt)
            bounded = min(exponential, settings.connector_retry_max_delay_seconds)
            jitter = random.uniform(0.8, 1.2) if bounded else 0.0
            await asyncio.sleep(bounded * jitter)
    raise RuntimeError("Provider retry loop exited unexpectedly.")
