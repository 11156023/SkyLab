#!/usr/bin/env python3
"""Capture an OpenAI-compatible baseline without recording credentials.

Run this against the old Gateway in Phase 0 and against LiteLLM in Phase 3.
The output is a JSON fixture whose request headers are deliberately omitted.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


SAFE_HEADERS = {"content-type", "x-request-id", "openai-processing-ms"}


def _headers(response: httpx.Response) -> dict[str, str]:
    return {key: value for key, value in response.headers.items() if key.lower() in SAFE_HEADERS}


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    return {
        "status_code": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "headers": _headers(response),
        "body": _json_or_text(response),
    }


def _stream_chat(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    chunks: list[str] = []
    last_usage: dict[str, Any] | None = None
    # Match the Campus relay: streaming chat requests ask vLLM/LiteLLM for a
    # final usage chunk so usage accounting can be verified without parsing
    # generated content.
    stream_options = payload.get("stream_options")
    options = dict(stream_options) if isinstance(stream_options, dict) else {}
    options.setdefault("include_usage", True)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={**payload, "stream": True, "stream_options": options},
    ) as response:
        for line in response.iter_lines():
            if not line:
                continue
            chunks.append(line)
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    usage = json.loads(line[6:]).get("usage")
                    if usage:
                        last_usage = usage
                except json.JSONDecodeError:
                    # SSE 串流中的非 JSON 片段（keep-alive 等）直接略過
                    pass
        return {
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "headers": _headers(response),
            "chunks": chunks,
            "last_usage": last_usage,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Gateway base URL, without /v1")
    key_source = parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument(
        "--api-key",
        help="test-only upstream key; prefer --api-key-env to keep it out of process arguments",
    )
    key_source.add_argument(
        "--api-key-env",
        help="environment variable containing the test-only upstream key",
    )
    parser.add_argument("--model", action="append", default=[], help="repeat for each expected public alias")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=330)
    args = parser.parse_args()

    api_key = args.api_key
    if args.api_key_env:
        api_key = os.getenv(args.api_key_env, "")
        if not api_key:
            parser.error(f"environment variable {args.api_key_env} is empty or missing")

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=args.timeout) as client:
        models = _request(client, "GET", "/v1/models")
        selected_models = args.model or [item["id"] for item in models.get("body", {}).get("data", [])]
        fixture: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url.rstrip("/"),
            "models": models,
            "cases": {},
            "error_case": _request(
                client,
                "POST",
                "/v1/chat/completions",
                json={"model": "__contract_unknown_model__", "messages": [{"role": "user", "content": "ping"}]},
            ),
        }
        for model in selected_models:
            base_payload = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16}
            fixture["cases"][model] = {
                "chat_completion": _request(client, "POST", "/v1/chat/completions", json=base_payload),
                "chat_completion_stream": _stream_chat(client, base_payload),
                "completion": _request(client, "POST", "/v1/completions", json={"model": model, "prompt": "Reply with OK.", "max_tokens": 16}),
                "responses": _request(client, "POST", "/v1/responses", json={"model": model, "input": "Reply with OK."}),
                "feature_probes": {
                    "json_schema": _request(client, "POST", "/v1/chat/completions", json={**base_payload, "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}}}}),
                    "tools": _request(client, "POST", "/v1/chat/completions", json={**base_payload, "tools": [{"type": "function", "function": {"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}}}}]}),
                    "priority_and_extra": _request(client, "POST", "/v1/chat/completions", json={**base_payload, "priority": 1, "top_k": 20}),
                },
            }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured contract fixture: {output}")


if __name__ == "__main__":
    main()
