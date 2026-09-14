#!/usr/bin/env python3
"""
Standalone AI API usage test script (no third-party dependencies).

What it does:
1) Reads AI_API_TIMEOUT / AI_API_PUBLIC_BASE_URL from environment variables and/or .env in repo root.
2) Prompts for a user API key and verifies it against /usage/my first.
3) Uses that same user key through Campus /ai-proxy/models and /chat/completions.
4) Asks for model name in terminal.
5) Prints response + usage tokens.

Usage examples:
  python test_ai_api_usage.py
  python test_ai_api_usage.py --prompt "Introduce yourself in one sentence."
  python test_ai_api_usage.py --model Qwen/Qwen3-14B-FP8
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


@dataclass
class Settings:
    ai_api_timeout: int
    ai_api_public_base_url: str


def parse_dotenv(dotenv_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values

    for raw_line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        values[key] = value

    return values


def load_settings() -> Settings:
    dotenv_values = parse_dotenv(Path(__file__).resolve().parent / ".env")

    def pick(name: str, default: str) -> str:
        return os.getenv(name) or dotenv_values.get(name) or default

    timeout_raw = pick("AI_API_TIMEOUT", "120")
    public_base_url = pick("AI_API_PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")

    try:
        timeout = int(timeout_raw)
        if timeout <= 0:
            raise ValueError
    except ValueError:
        print(f"[WARN] Invalid AI_API_TIMEOUT={timeout_raw!r}, fallback to 120")
        timeout = 120

    return Settings(
        ai_api_timeout=timeout,
        ai_api_public_base_url=public_base_url,
    )


def _json_or_text(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def request_json(
    method: str,
    url: str,
    timeout: int,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = None
    req_headers = dict(headers or {})

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = request.Request(url=url, method=method.upper(), headers=req_headers, data=data)

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return int(resp.status), _json_or_text(body)
    except error.HTTPError as http_err:
        body = http_err.read() if hasattr(http_err, "read") else b""
        parsed = _json_or_text(body) if body else str(http_err)
        return int(http_err.code), parsed
    except error.URLError as net_err:
        return 0, {"error": "network_error", "detail": str(net_err)}


def auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def verify_user_api_key(settings: Settings, user_api_key: str) -> bool:
    url = f"{settings.ai_api_public_base_url}/api/v1/ai-proxy/usage/my"
    status, data = request_json(
        method="GET",
        url=url,
        timeout=settings.ai_api_timeout,
        headers=auth_headers(user_api_key),
    )

    print("\nStep 1: verify user API key")
    print(f"GET {url} -> {status}")

    if status != 200:
        print(f"[ERROR] user API key is invalid or not authorized: {json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else data}")
        if isinstance(data, dict) and data.get("error") == "network_error":
            print("[HINT] AI_API_PUBLIC_BASE_URL is unreachable.")
            print("       For local backend started with fastapi dev, try: http://localhost:8000")
        return False

    if not isinstance(data, dict):
        print(f"[ERROR] Unexpected usage response type: {type(data).__name__}")
        return False

    print("[OK] user API key is valid")
    print("User usage summary:")
    print(f"  total_requests:      {data.get('total_requests', '?')}")
    print(f"  total_input_tokens:  {data.get('total_input_tokens', '?')}")
    print(f"  total_output_tokens: {data.get('total_output_tokens', '?')}")

    by_model = data.get("by_model") if isinstance(data.get("by_model"), dict) else {}
    if by_model:
        print("By model:")
        for model_name, stats in by_model.items():
            if not isinstance(stats, dict):
                continue
            print(
                f"  - {model_name}: requests={stats.get('requests', '?')}, "
                f"input_tokens={stats.get('input_tokens', '?')}, output_tokens={stats.get('output_tokens', '?')}"
            )

    return True


def list_models(settings: Settings, user_api_key: str) -> list[str]:
    url = f"{settings.ai_api_public_base_url}/api/v1/ai-proxy/models"
    status, data = request_json(
        method="GET",
        url=url,
        timeout=settings.ai_api_timeout,
        headers=auth_headers(user_api_key),
    )

    if status != 200:
        print(f"[ERROR] GET {url} -> {status}")
        print(f"        response: {json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else data}")
        return []

    if not isinstance(data, dict):
        print(f"[ERROR] Unexpected /v1/models response type: {type(data).__name__}")
        return []

    models = []
    for item in data.get("data", []):
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            models.append(str(model_id))

    if models:
        print("\nAvailable public models:")
        for idx, mid in enumerate(models, start=1):
            print(f"  {idx:>2}. {mid}")
    else:
        print("\n[WARN] No models returned by Campus /ai-proxy/models")

    return models


def run_chat_completion(
    settings: Settings,
    user_api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
) -> int:
    url = f"{settings.ai_api_public_base_url}/api/v1/ai-proxy/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
    }

    status, data = request_json(
        method="POST",
        url=url,
        timeout=settings.ai_api_timeout,
        headers=auth_headers(user_api_key),
        payload=payload,
    )

    print(f"\nPOST {url} -> {status}")

    if status != 200:
        print(f"[ERROR] chat completion failed: {json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else data}")
        return 1

    if not isinstance(data, dict):
        print(f"[ERROR] Unexpected chat response type: {type(data).__name__}")
        return 1

    content = ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = str(message.get("content", ""))

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = usage.get("prompt_tokens", "?")
    completion_tokens = usage.get("completion_tokens", "?")
    total_tokens = usage.get("total_tokens", "?")

    print("\nModel reply:")
    print(content or "(empty)")

    print("\nUsage:")
    print(f"  prompt_tokens:     {prompt_tokens}")
    print(f"  completion_tokens: {completion_tokens}")
    print(f"  total_tokens:      {total_tokens}")

    if not usage:
        print("[WARN] usage field is missing in response")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone AI API usage test (no external dependencies)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name to use. If empty, script will prompt in terminal.",
    )
    parser.add_argument(
        "--prompt",
        default="Please introduce yourself in one short paragraph.",
        help="User prompt sent to /v1/chat/completions",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="max_tokens in chat completion request",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()

    print("AI API test settings:")
    print(f"  AI_API_PUBLIC_BASE_URL: {settings.ai_api_public_base_url}")
    print(f"  AI_API_TIMEOUT:         {settings.ai_api_timeout}")

    user_api_key = input("\nStep 1 - enter user API key (ccai_xxx): ").strip()
    if not user_api_key:
        print("[ERROR] user API key is required")
        return 1

    if not verify_user_api_key(settings, user_api_key):
        return 1

    models = list_models(settings, user_api_key)
    default_model = models[0] if models else ""

    selected_model = args.model.strip()
    if not selected_model:
        hint = f" (default: {default_model})" if default_model else ""
        selected_model = input(f"\nEnter model name{hint}: ").strip() or default_model

    if not selected_model:
        print("[ERROR] No model selected. Provide --model or ensure /v1/models returns models.")
        return 1

    print(f"\nSelected model: {selected_model}")

    rc = run_chat_completion(
        settings=settings,
        user_api_key=user_api_key,
        model=selected_model,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
    )

    return rc


if __name__ == "__main__":
    sys.exit(main())
