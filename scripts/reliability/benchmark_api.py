from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

import httpx


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


async def benchmark(args: argparse.Namespace) -> dict[str, object]:
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=timeout, limits=limits
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={
                "email": args.email,
                "password": args.password,
                "organization_slug": args.organization_slug,
            },
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        semaphore = asyncio.Semaphore(args.concurrency)
        latencies: list[float] = []
        errors: list[int | str] = []

        async def request_once() -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get(args.path, headers=headers)
                    if response.status_code >= 400:
                        errors.append(response.status_code)
                except httpx.HTTPError as exc:
                    errors.append(type(exc).__name__)
                finally:
                    latencies.append((time.perf_counter() - started) * 1_000)

        started = time.perf_counter()
        await asyncio.gather(*(request_once() for _ in range(args.requests)))
        elapsed = time.perf_counter() - started

    return {
        "path": args.path,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "errors": len(errors),
        "error_samples": errors[:10],
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 2) if elapsed else 0,
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies, default=0), 2),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded, authenticated, read-only Work OS API benchmark."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/api/documents/library")
    parser.add_argument("--email", default="admin@demo.local")
    parser.add_argument("--password", default="demo-password")
    parser.add_argument("--organization-slug", default="default")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-p95-ms", type=float, default=2_000.0)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")
    if args.concurrency > args.requests:
        parser.error("--concurrency cannot exceed --requests")
    return args


def main() -> None:
    args = parse_args()
    result = asyncio.run(benchmark(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    latency = result["latency_ms"]
    if result["errors"] or latency["p95"] > args.max_p95_ms:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
