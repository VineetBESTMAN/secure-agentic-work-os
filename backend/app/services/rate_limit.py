from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


class RateLimitService:
    def __init__(self) -> None:
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._redis: Redis | None = None

    def consume(
        self, identifier: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        settings = get_settings()
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        if settings.rate_limit_backend == "redis":
            try:
                return self._consume_redis(digest, limit, window_seconds)
            except RedisError:
                if settings.app_env.lower() == "production":
                    return RateLimitDecision(
                        allowed=False,
                        limit=limit,
                        remaining=0,
                        reset_after_seconds=window_seconds,
                    )
        return self._consume_memory(digest, limit, window_seconds)

    def _consume_memory(
        self, digest: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            count, reset_at = self._memory.get(digest, (0, now + window_seconds))
            if reset_at <= now:
                count, reset_at = 0, now + window_seconds
            count += 1
            self._memory[digest] = (count, reset_at)
            if len(self._memory) > 10_000:
                self._memory = {
                    key: value
                    for key, value in self._memory.items()
                    if value[1] > now
                }
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            reset_after_seconds=max(1, int(reset_at - now)),
        )

    def _consume_redis(
        self, digest: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        if self._redis is None:
            self._redis = Redis.from_url(
                get_settings().redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        bucket = int(time.time()) // window_seconds
        key = f"workos:rate:{bucket}:{digest}"
        pipeline = self._redis.pipeline()
        pipeline.incr(key)
        pipeline.expire(key, window_seconds + 1)
        count, _ = pipeline.execute()
        reset_after = window_seconds - (int(time.time()) % window_seconds)
        return RateLimitDecision(
            allowed=int(count) <= limit,
            limit=limit,
            remaining=max(0, limit - int(count)),
            reset_after_seconds=max(1, reset_after),
        )


rate_limit_service = RateLimitService()
