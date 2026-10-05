#!/usr/bin/env python3
"""Probe authenticated AI API request creation with bounded concurrency.

Successful requests create real pending applications. This runner does not
approve applications, issue keys, or retry failed requests. Enter a Campus
access token interactively; it is never saved in the latest JSON report.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx

REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "test-results"
    / "ai-api-requests"
    / "latest.json"
)


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/v1"
    ):
        raise ValueError("base URL must be an http(s) URL ending in /api/v1")
    return value.rstrip("/")


def run_probe(
    *, base_url: str, token: str, requests: int, concurrency: int
) -> dict:
    with httpx.Client(
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "CampusCloud-RequestProbe/1.0",
        },
        timeout=20,
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=concurrency, max_keepalive_connections=concurrency
        ),
    ) as client:
        me = client.get(f"{base_url}/users/me")
        if me.status_code != 200:
            raise RuntimeError(f"account preflight returned HTTP {me.status_code}")
        account = me.json()
        print(f"Account: {account['email']} | Target: {base_url}")
        print(f"Requests: {requests} | Concurrency: {concurrency}")

        def send(index: int) -> dict:
            started = time.perf_counter()
            result = {"index": index, "http_status": None, "request_id": None}
            try:
                response = client.post(
                    f"{base_url}/ai-api/requests",
                    json={
                        "purpose": f"Authorized request-control probe {index}",
                        "api_key_name": f"reqprobe_{index:04d}",
                        "duration": "1d",
                    },
                )
                result["http_status"] = response.status_code
                result["retry_after"] = response.headers.get("Retry-After")
                if response.is_success:
                    body = response.json()
                    result["request_id"] = body.get("id")
                    result["request_status"] = body.get("status")
            except (httpx.HTTPError, ValueError) as exc:
                result["error_type"] = type(exc).__name__
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return result

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(send, range(1, requests + 1)))
        elapsed = time.perf_counter() - started

    durations = sorted(item["duration_ms"] for item in results)
    created = sum(
        item.get("request_status") == "pending" and item["request_id"] is not None
        for item in results
    )
    return {
        "base_url": base_url,
        "user_id": account["id"],
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 3),
        "completed_per_second": round(requests / elapsed, 2),
        "created_pending": created,
        "http_status_counts": dict(
            Counter(str(item["http_status"] or "transport_error") for item in results)
        ),
        "median_ms": round(statistics.median(durations), 2),
        "p95_ms": durations[math.ceil(len(durations) * 0.95) - 1],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Campus root ending in /api/v1")
    parser.add_argument("--requests", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.requests <= 1000 or not 1 <= args.concurrency <= 50:
        parser.error("requests must be 1-1000; concurrency must be 1-50")
    try:
        base_url = validate_base_url(args.base_url)
        token = getpass.getpass("Campus access token (hidden): ").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or token.startswith("ccai_") or len(token.split(".")) != 3:
            raise ValueError("enter a Campus login access token, not a ccai API key")
        report = run_probe(
            base_url=base_url,
            token=token,
            requests=args.requests,
            concurrency=min(args.concurrency, args.requests),
        )
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        # Transport exceptions can contain request details; print only their type.
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__)
        return 1
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    print(f"Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
