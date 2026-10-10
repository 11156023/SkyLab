#!/usr/bin/env python3
"""以一次隱藏輸入的登入 token 檢查官網公開模型、AI 導覽與 PVE 助手。

從 repository 根目錄執行：uv run --no-project --with httpx python scripts/test_ai_services.py
固定情境各重複三次；公開模型測一般／串流，內建流程只做唯讀查詢。
只覆寫 test-results/ai-services/latest.json；不讀取或儲存憑證檔案。
"""

from __future__ import annotations

import getpass
import json
import re
import sys
import time
import uuid
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

API_BASE_URL = "https://skylab-tw.com/api/v1/ai-proxy"
SITE_API_URL = "https://skylab-tw.com/api/v1"
REPORT_FILE = (
    Path(__file__).resolve().parents[1] / "test-results/ai-services/latest.json"
)
MAX_TOKENS = 512
REPETITIONS = 3
PUBLIC_SCENARIOS = (
    {
        "id": "explain_limits",
        "title": "解釋 RPM 與併發限制",
        "prompt": "請用繁體中文，以 180～240 字向學生說明：API 每 60 秒最多 20 次請求，與同時最多 10 個請求有何差別？舉出兩個具體例子，並說明遇到 HTTP 429 與 Retry-After 時應如何處理。不要虛構官網設定。",
        "validator": "manual",
    },
    {
        "id": "extract_vm",
        "title": "擷取機器需求為 JSON",
        "prompt": "將需求轉成 JSON，只輸出 JSON，不加 Markdown 或說明。需求：主機名稱 campus-demo、2 個 vCPU、記憶體 4096 MB、磁碟 30 GB、不需要 GPU。欄位必須只有 hostname、vcpu、memory_mb、disk_gb、requires_gpu；數值用整數，GPU 用布林值。",
        "validator": "json_exact",
        "expected": {
            "hostname": "campus-demo",
            "vcpu": 2,
            "memory_mb": 4096,
            "disk_gb": 30,
            "requires_gpu": False,
        },
    },
)
SYSTEM_SCENARIOS = (
    {
        "id": "nav_api",
        "title": "導覽：我的 API 金鑰",
        "service": "navigation",
        "prompt": "我想查看自己申請的 API 金鑰和使用紀錄，應該去哪個頁面？",
        "expected_path": "/ai-api",
    },
    {
        "id": "nav_resources",
        "title": "導覽：我的機器",
        "service": "navigation",
        "prompt": "我想查看自己名下的 VM 和 LXC 機器，應該去哪個頁面？",
        "expected_path": "/my-resources",
    },
    {
        "id": "nav_request",
        "title": "導覽：機器申請流程",
        "service": "navigation",
        "prompt": "我想申請一台機器供課業使用，請帶我完成從填申請單、等審核到開始使用的完整流程。",
        "expected_flow": "request_machine",
        "expected_steps": [
            "/my-requests",
            "/my-requests",
            "/my-requests",
            "/my-resources",
        ],
    },
    {
        "id": "pve_nodes",
        "title": "PVE：節點 CPU 與記憶體",
        "service": "pve_assistant",
        "prompt": "請使用 get_nodes 唯讀查詢目前 PVE 節點的 CPU 與記憶體使用率，並用繁體中文簡短整理。不查詢 VM、不執行 SSH、不修改任何設定。",
        "expected_tool": "get_nodes",
    },
    {
        "id": "pve_storage",
        "title": "PVE：儲存容量",
        "service": "pve_assistant",
        "prompt": "請使用 get_storage 唯讀查詢目前儲存空間的總容量、剩餘容量與使用率，並用繁體中文整理。不查詢 VM、不執行 SSH、不修改任何設定。",
        "expected_tool": "get_storage",
    },
    {
        "id": "pve_cluster",
        "title": "PVE：叢集狀態",
        "service": "pve_assistant",
        "prompt": "請使用 get_cluster 唯讀查詢目前叢集名稱、節點數與 quorum 狀態，並用繁體中文整理。不要把沒有資料說成健康。不查詢 VM、不執行 SSH、不修改任何設定。",
        "expected_tool": "get_cluster",
    },
)


class ProbeError(RuntimeError):
    """只包含可安全顯示的錯誤代碼，不包含原始回應或憑證。"""


def redact(value: Any, api_key: str) -> Any:
    """回應文字也可能回顯金鑰；所有輸出在離開記憶體前遮蔽。"""
    if isinstance(value, str):
        value = value.replace(api_key, "<redacted>") if api_key else value
        return re.sub(r"ccai_[A-Za-z0-9_-]+", "<redacted>", value)
    if isinstance(value, list):
        return [redact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {
            redact(key, api_key): redact(item, api_key) for key, item in value.items()
        }
    return value


def redact_secrets(value: Any, secrets: tuple[str, ...]) -> Any:
    for secret in secrets:
        value = redact(value, secret)
    return value


def get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(f"{API_BASE_URL}/{path}")
    if response.status_code != 200:
        raise ProbeError(f"{path}: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProbeError(f"{path}: response_not_json") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"{path}: response_not_object")
    return payload


def model_ids(payload: dict[str, Any]) -> list[str]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise ProbeError("models: data_missing")
    models: list[str] = []
    for item in items:
        model = item.get("id") if isinstance(item, dict) else None
        if isinstance(model, str) and model.strip() and model.strip() not in models:
            models.append(model.strip())
    if not models:
        raise ProbeError("models: no_available_models")
    return models


def rate_status(client: httpx.Client) -> dict[str, Any]:
    payload = get_json(client, "rate-limit/status")
    if payload.get("error"):
        raise ProbeError("rate_limit_status_unavailable")
    for field in ("limit_per_minute", "current_usage", "remaining"):
        value = payload.get(field)
        if type(value) is not int or value < 0:
            raise ProbeError("rate_limit_status_invalid")
    if payload["limit_per_minute"] < 1 or type(payload.get("disabled")) is not bool:
        raise ProbeError("rate_limit_status_invalid")
    return payload


def wait_for_budget(client: httpx.Client) -> None:
    """查詢不消耗生成額度；額度用盡時最多等待 70 秒，不重送生成。"""
    deadline = time.monotonic() + 70
    while True:
        status = rate_status(client)
        if status["disabled"] or status["remaining"] > 0:
            return
        if time.monotonic() >= deadline:
            raise ProbeError("rate_limit_budget_unavailable")
        print("生成額度已用盡，5 秒後再次查詢……")
        time.sleep(5)


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            item["text"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def read_event(payload: Any, result: dict[str, Any], *, stream: bool) -> None:
    if not isinstance(payload, dict):
        raise ProbeError("response_not_object")
    if "error" in payload:
        error = payload["error"]
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
                result["error_code"] = code
        raise ProbeError("response_error_event")
    if isinstance(payload.get("usage"), dict):
        usage = payload["usage"]
        result["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
    if isinstance(payload.get("model"), str):
        result["response_model"] = payload["model"]
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProbeError("choice_invalid")
    message = choice.get("delta" if stream else "message")
    if isinstance(message, dict):
        result["answer"] += content_text(message.get("content"))
    if isinstance(choice.get("finish_reason"), str):
        result["finish_reason"] = choice["finish_reason"]


def evaluate_answer(answer: str, scenario: dict[str, Any]) -> dict[str, Any]:
    if scenario["validator"] == "manual":
        return {"status": "needs_review", "reason": "explanation_requires_human_review"}
    try:
        actual = json.loads(answer)
    except ValueError:
        return {"status": "failed", "reason": "answer_not_json"}
    # JSON 的布林值不能冒充整數；同時確認欄位與值。
    expected = scenario["expected"]
    valid = (
        isinstance(actual, dict)
        and actual.keys() == expected.keys()
        and all(
            type(actual[key]) is type(value) and actual[key] == value
            for key, value in expected.items()
        )
    )
    return {
        "status": "passed" if valid else "failed",
        "reason": None if valid else "extracted_values_mismatch",
    }


def token_rate(tokens: Any, duration_ms: Any) -> float | None:
    if (
        type(tokens) is int
        and tokens >= 0
        and type(duration_ms) in (int, float)
        and duration_ms > 0
    ):
        return round(tokens * 1000 / duration_ms, 2)
    return None


def probe_model(
    client: httpx.Client,
    model: str,
    *,
    stream: bool,
    scenario: dict[str, Any] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    scenario = scenario or PUBLIC_SCENARIOS[0]
    started = time.perf_counter()
    result: dict[str, Any] = {
        "model": model,
        "scenario_id": scenario["id"],
        "title": scenario["title"],
        "attempt": attempt,
        "prompt": scenario["prompt"],
        "stream": stream,
        "ok": False,
        "status_code": None,
        "error": None,
        "error_code": None,
        "retry_after": None,
        "request_id": None,
        "response_model": None,
        "answer": "",
        "finish_reason": None,
        "usage": None,
        "duration_ms": None,
        "first_content_ms": None,
        "last_content_ms": None,
        "stream_content_duration_ms": None,
        "e2e_output_tokens_per_second": None,
        "stream_done": None,
        "semantic_check": {"status": "not_evaluated", "reason": "request_not_complete"},
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": scenario["prompt"]}],
        "max_tokens": MAX_TOKENS,
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    try:
        with client.stream(
            "POST",
            f"{API_BASE_URL}/chat/completions",
            json=payload,
            headers={"Accept": "text/event-stream" if stream else "application/json"},
        ) as response:
            result["status_code"] = response.status_code
            result["request_id"] = response.headers.get("x-request-id")
            result["retry_after"] = response.headers.get("retry-after")
            if response.status_code != 200:
                raise ProbeError(f"http_{response.status_code}")
            if not stream:
                read_event(json.loads(response.read()), result, stream=False)
            else:
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ProbeError("stream_content_type_invalid")
                result["stream_done"] = False
                # 以空行界定 SSE event，也支援多個 data: 行。
                data: list[str] = []
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
                    if line or not data:
                        continue
                    event = "\n".join(data)
                    data.clear()
                    if event.strip() == "[DONE]":
                        result["stream_done"] = True
                        break
                    previous_length = len(result["answer"])
                    read_event(json.loads(event), result, stream=True)
                    if len(result["answer"]) > previous_length:
                        received_ms = (time.perf_counter() - started) * 1000
                        if result["first_content_ms"] is None:
                            result["first_content_ms"] = received_ms
                        result["last_content_ms"] = received_ms
                if not result["stream_done"]:
                    raise ProbeError("stream_done_missing")
            if not result["answer"].strip():
                raise ProbeError("answer_empty")
            usage = result["usage"]
            if not isinstance(usage, dict) or any(
                type(value) is not int or value < 0 for value in usage.values()
            ):
                raise ProbeError("usage_missing_or_invalid")
            result["ok"] = True
            result["semantic_check"] = evaluate_answer(result["answer"], scenario)
            if result["finish_reason"] == "length":
                result["semantic_check"] = {
                    "status": "failed",
                    "reason": "output_truncated",
                }
    except ProbeError as exc:
        result["error"] = str(exc)
    except (ValueError, UnicodeError):
        result["error"] = "response_not_json"
    except httpx.TimeoutException:
        result["error"] = "request_timeout"
    except httpx.HTTPError as exc:
        result["error"] = type(exc).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000
    result["duration_ms"] = round(elapsed_ms, 2)
    if result["ok"]:
        result["e2e_output_tokens_per_second"] = token_rate(
            result["usage"]["completion_tokens"], elapsed_ms
        )
    if result["first_content_ms"] is not None:
        result["stream_content_duration_ms"] = round(
            result["last_content_ms"] - result["first_content_ms"], 2
        )
        result["first_content_ms"] = round(result["first_content_ms"], 2)
        result["last_content_ms"] = round(result["last_content_ms"], 2)
    return result


def optional_snapshot(client: httpx.Client, path: str) -> dict[str, Any]:
    try:
        return (
            rate_status(client)
            if path == "rate-limit/status"
            else get_json(client, path)
        )
    except (ProbeError, httpx.HTTPError) as exc:
        return {
            "error": str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        }


def summarize_public(results: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for item in results:
        groups.setdefault(
            (item["model"], item["scenario_id"], item["stream"]), []
        ).append(item)
    rows: list[dict[str, Any]] = []
    for (model, scenario_id, stream), items in groups.items():
        successful = [item for item in items if item["ok"]]
        tokens = sum(item["usage"]["completion_tokens"] for item in successful)
        elapsed = sum(item["duration_ms"] for item in successful)
        rows.append(
            {
                "model": model,
                "scenario_id": scenario_id,
                "stream": stream,
                "tested": len(items),
                "passed": len(successful),
                "failed": len(items) - len(successful),
                "semantic_failed": sum(
                    item["semantic_check"]["status"] == "failed" for item in items
                ),
                "needs_review": sum(
                    item["semantic_check"]["status"] == "needs_review" for item in items
                ),
                "failure_rate": round((len(items) - len(successful)) / len(items), 4),
                "e2e_output_tokens_per_second": token_rate(tokens, elapsed)
                if successful
                else None,
                "mean_duration_ms": round(
                    sum(item["duration_ms"] for item in items) / len(items), 2
                ),
            }
        )
    return {
        "planned": planned,
        "tested": len(results),
        "passed": sum(item["ok"] for item in results),
        "failed": sum(not item["ok"] for item in results),
        "untested": planned - len(results),
        "semantic_failed": sum(
            item["semantic_check"]["status"] == "failed" for item in results
        ),
        "needs_review": sum(
            item["semantic_check"]["status"] == "needs_review" for item in results
        ),
        "failure_counts": dict(
            Counter(item["error"] for item in results if item["error"])
        ),
        "by_scenario": rows,
    }


def run_checks(
    client: httpx.Client,
    api_key: str,
    *,
    additional_secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_base_url": API_BASE_URL,
        "max_tokens": MAX_TOKENS,
        "repetitions": REPETITIONS,
        "models": [],
        "rate_limit_before": None,
        "rate_limit_after": None,
        "usage_before": None,
        "usage_after": None,
        "results": [],
        "error": None,
    }
    try:
        report["rate_limit_before"] = rate_status(client)
        status = report["rate_limit_before"]
        print(
            f"憑證額度：{status['limit_per_minute']} 次／分鐘；剩餘 {status['remaining']} 次；disabled={status['disabled']}"
        )
        report["models"] = model_ids(get_json(client, "models"))
        report["usage_before"] = optional_snapshot(client, "usage/my")
        cases = [
            (model, scenario, attempt, stream)
            for attempt in range(1, REPETITIONS + 1)
            for scenario in PUBLIC_SCENARIOS
            for model in report["models"]
            for stream in (False, True)
        ]
        print(
            f"可見模型 {len(report['models'])} 個；{len(PUBLIC_SCENARIOS)} 題 × 一般／串流 × {REPETITIONS} 次，預計生成 {len(cases)} 次。"
        )
        for model, scenario, attempt, stream in cases:
            wait_for_budget(client)
            result = probe_model(
                client, model, stream=stream, scenario=scenario, attempt=attempt
            )
            report["results"].append(result)
            safe = redact_secrets(result, (api_key, *additional_secrets))
            print(
                f"{'PASS' if safe['ok'] else 'FAIL'} {safe['model']}／{safe['title']}／第 {attempt} 次／{'串流' if stream else '一般'}：{safe['duration_ms']} ms；{safe['e2e_output_tokens_per_second']} tokens/s；語意 {safe['semantic_check']['status']}；{safe['error'] or '回覆與用量已取得'}"
            )
            if safe["answer"]:
                print(f"  回覆預覽：{safe['answer'][:200]}")
            if result["status_code"] in (401, 403):
                raise ProbeError("authentication_or_permission_failed")
        report["rate_limit_after"] = optional_snapshot(client, "rate-limit/status")
        report["usage_after"] = optional_snapshot(client, "usage/my")
    except (ProbeError, httpx.HTTPError) as exc:
        report["error"] = (
            str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        )
    except (EOFError, KeyboardInterrupt):
        report["error"] = "cancelled"
    report["summary"] = summarize_public(
        report["results"],
        len(report["models"]) * len(PUBLIC_SCENARIOS) * REPETITIONS * 2,
    )
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return redact_secrets(report, (api_key, *additional_secrets))


def site_get(client: httpx.Client, path: str) -> Any:
    response = client.get(f"{SITE_API_URL}/{path}")
    if response.status_code != 200:
        raise ProbeError(f"{path.split('?')[0]}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProbeError("site_response_not_json") from exc


def owned_api_key(client: httpx.Client) -> tuple[str, str]:
    """只讀取本人最新可用金鑰；不申請、核准、輪替或撤銷。"""
    skip = 0
    while True:
        payload = site_get(client, f"ai-api/credentials/my?skip={skip}&limit=100")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProbeError("credentials_list_invalid")
        count = payload.get("count")
        if type(count) is not int or count < 0:
            raise ProbeError("credentials_count_invalid")
        rows = payload["data"]
        for item in rows:
            if not isinstance(item, dict) or item.get("revoked_at"):
                continue
            expiry = item.get("expires_at")
            if expiry is not None:
                try:
                    expires = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    expires = (
                        expires.replace(tzinfo=timezone.utc)
                        if expires.tzinfo is None
                        else expires
                    )
                    if expires <= datetime.now(timezone.utc):
                        continue
                except (AttributeError, TypeError, ValueError):
                    continue
            try:
                credential_id = str(uuid.UUID(str(item.get("id"))))
            except ValueError:
                continue
            detail = site_get(client, f"ai-api/credentials/{credential_id}")
            key = detail.get("api_key") if isinstance(detail, dict) else None
            if isinstance(key, str) and re.fullmatch(r"ccai_[A-Za-z0-9_-]+", key):
                return credential_id, key
        skip += len(rows)
        if not rows or skip >= count:
            raise ProbeError("no_active_owned_ai_credential")


def pve_evidence(tools: list[Any], tool_name: str) -> dict[str, Any]:
    fields = {
        "get_nodes": (
            "node",
            "status",
            "cpu_usage",
            "cpu_cores",
            "mem_used_bytes",
            "mem_total_bytes",
            "mem_used_pct",
        ),
        "get_storage": (
            "node",
            "storage",
            "storage_type",
            "avail_bytes",
            "used_bytes",
            "total_bytes",
            "used_pct",
            "active",
            "enabled",
        ),
        "get_cluster": (
            "cluster_name",
            "is_cluster",
            "node_count",
            "quorate",
            "cluster_version",
        ),
    }[tool_name]
    rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "tool": tool_name,
        "data": rows,
        "truncated": False,
        "error": None,
    }
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") != tool_name:
            continue
        data = tool.get("result")
        if not isinstance(data, dict):
            continue
        if data.get("error"):
            evidence["error"] = "pve_collection_error"
        if data.get("truncated"):
            evidence["truncated"] = True
            items = data.get("partial_result", [])
        else:
            items = (
                [data]
                if tool_name == "get_cluster"
                else data.get("items", data.get("data", []))
            )
        if isinstance(items, list):
            rows.extend(
                {field: item.get(field) for field in fields}
                for item in items
                if isinstance(item, dict)
            )
    if not rows:
        evidence["error"] = evidence["error"] or "pve_data_missing"
    for row in rows:
        valid = False
        if tool_name == "get_nodes":
            valid = (
                isinstance(row["node"], str)
                and bool(row["node"].strip())
                and all(
                    type(row[field]) in (int, float) and row[field] >= 0
                    for field in (
                        "cpu_usage",
                        "cpu_cores",
                        "mem_used_bytes",
                        "mem_total_bytes",
                        "mem_used_pct",
                    )
                )
                and row["mem_total_bytes"] > 0
                and row["cpu_cores"] > 0
                and row["cpu_usage"] <= 1
                and row["mem_used_pct"] <= 100
            )
        elif tool_name == "get_storage":
            valid = (
                isinstance(row["storage"], str)
                and bool(row["storage"])
                and all(
                    type(row[field]) in (int, float) and row[field] >= 0
                    for field in (
                        "avail_bytes",
                        "used_bytes",
                        "total_bytes",
                        "used_pct",
                    )
                )
                and row["used_pct"] <= 100
            )
        else:
            valid = (
                type(row["is_cluster"]) is bool
                and type(row["quorate"]) is bool
                and type(row["node_count"]) is int
                and row["node_count"] > 0
            )
        if not valid:
            evidence["error"] = evidence["error"] or "pve_metrics_invalid"
    return evidence


def probe_system(
    client: httpx.Client,
    service: str,
    *,
    scenario: dict[str, Any] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """保存唯讀流程輸出；不保存 messages、確認 token 或工具原始參數。"""
    scenario = scenario or next(
        item for item in SYSTEM_SCENARIOS if item["service"] == service
    )
    if service == "navigation":
        path = "ai/navigation/resolve"
        payload = {"query": scenario["prompt"]}
    else:
        path = "ai/pve-log/chat"
        payload = {"message": scenario["prompt"]}
    result: dict[str, Any] = {
        "service": service,
        "attempted": True,
        "scenario_id": scenario["id"],
        "title": scenario["title"],
        "attempt": attempt,
        "endpoint": path,
        "prompt": payload,
        "status": "failed",
        "status_code": None,
        "error": None,
        "duration_ms": None,
        "request_id": None,
        "retry_after": None,
        "model_execution": "not_exposed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "semantic_check": {"status": "not_evaluated", "reason": "request_not_complete"},
        "response": None,
    }
    started = time.perf_counter()
    try:
        response = client.post(f"{SITE_API_URL}/{path}", json=payload)
        result["status_code"] = response.status_code
        result["request_id"] = response.headers.get(
            "x-ai-request-id"
        ) or response.headers.get("x-request-id")
        result["retry_after"] = response.headers.get("retry-after")
        if response.status_code != 200:
            result["status"] = (
                "permission_denied" if response.status_code == 403 else "failed"
            )
            raise ProbeError(f"http_{response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise ProbeError("system_response_not_object")
        if service == "navigation":
            if body.get("action") not in {
                "navigate",
                "suggest",
                "clarify",
                "guide",
                "answer",
            }:
                raise ProbeError("navigation_action_invalid")
            visible = (
                body.get("answer")
                or body.get("clarification_question")
                or body.get("primary")
                or body.get("suggestions")
                or body.get("steps")
            )
            if not visible:
                raise ProbeError("navigation_result_empty")
            result["response"] = {
                field: body.get(field)
                for field in (
                    "intent",
                    "confidence",
                    "action",
                    "primary",
                    "suggestions",
                    "clarification_question",
                    "flow_id",
                    "steps",
                    "answer",
                )
            }
            primary = body.get("primary")
            if "expected_path" in scenario:
                matches = (
                    isinstance(primary, dict)
                    and primary.get("path") == scenario["expected_path"]
                )
            else:
                steps = body.get("steps")
                matches = (
                    body.get("flow_id") == scenario["expected_flow"]
                    and isinstance(steps, list)
                    and [item.get("path") for item in steps if isinstance(item, dict)]
                    == scenario["expected_steps"]
                )
            result["semantic_check"] = {
                "status": "passed" if matches else "failed",
                "reason": None if matches else "navigation_flow_mismatch",
            }
        else:
            if body.get("error"):
                raise ProbeError("pve_assistant_response_error")
            reply = body.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise ProbeError("pve_assistant_reply_empty")
            tools = body.get("tools_called")
            if not isinstance(tools, list):
                raise ProbeError("pve_tools_invalid")
            evidence = pve_evidence(tools, scenario["expected_tool"])
            result["response"] = {
                "reply": reply,
                "needs_confirmation": body.get("needs_confirmation"),
                "tools_called": [
                    item.get("name") for item in tools if isinstance(item, dict)
                ],
                "evidence": evidence,
            }
            if body.get("needs_confirmation"):
                result["status"] = "needs_confirmation"
                raise ProbeError("pve_assistant_requested_confirmation")
            if evidence["error"]:
                raise ProbeError(evidence["error"])
            result["semantic_check"] = {
                "status": "needs_review",
                "reason": "compare_reply_with_tool_evidence",
            }
        result["status"] = "passed"
    except (ProbeError, httpx.HTTPError, ValueError) as exc:
        result["error"] = (
            str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        )
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    return result


def observe_model_calls(
    client: httpx.Client, user_id: str, result: dict[str, Any]
) -> dict[str, Any]:
    """讀取本人單次問題期間的 platform 紀錄；時間關聯不能證明精確歸屬。"""
    observed: dict[str, Any] = {
        "association": "time_window",
        "status": "unavailable",
        "error": None,
        "calls": [],
    }
    query = urlencode(
        {
            "user_id": user_id,
            "start_date": result["started_at"],
            "end_date": result["completed_at"],
            "limit": 200,
        }
    )
    try:
        payload = site_get(client, f"ai-api/monitoring/template-calls?{query}")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProbeError("monitoring_response_invalid")
        allowed = (
            {"ai_nav"}
            if result["service"] == "navigation"
            else {"pve_chat", "pve_chat_adherence"}
        )
        for item in payload["data"]:
            if (
                not isinstance(item, dict)
                or item.get("user_id") != user_id
                or item.get("call_type") not in allowed
            ):
                continue
            call = {
                field: item.get(field)
                for field in (
                    "id",
                    "request_id",
                    "call_type",
                    "model_name",
                    "response_model",
                    "status",
                    "input_tokens",
                    "output_tokens",
                    "request_duration_ms",
                    "usage_reported",
                    "started_at",
                    "completed_at",
                )
            }
            call["e2e_output_tokens_per_second"] = (
                token_rate(call["output_tokens"], call["request_duration_ms"])
                if call["usage_reported"] is True
                else None
            )
            observed["calls"].append(call)
        count = payload.get("count")
        if type(count) is not int or count < 0:
            raise ProbeError("monitoring_count_invalid")
        observed["status"] = (
            "partial"
            if count > len(payload["data"])
            else "available"
            if observed["calls"]
            else "no_records"
        )
    except (ProbeError, httpx.HTTPError) as exc:
        observed["error"] = (
            str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        )
    return observed


def summarize_system(results: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for scenario in SYSTEM_SCENARIOS:
        items = [item for item in results if item["scenario_id"] == scenario["id"]]
        if not items:
            continue
        # 以 record ID 去重，避免寬時間區間／並行其他請求造成重複加總。
        calls = {
            call["id"]: call
            for item in items
            for call in item.get("observed_model_calls", {}).get("calls", [])
            if call.get("id")
        }
        measured = [
            call
            for call in calls.values()
            if call["e2e_output_tokens_per_second"] is not None
        ]
        durations = [
            item["duration_ms"] for item in items if item["duration_ms"] is not None
        ]
        rows.append(
            {
                "service": scenario["service"],
                "scenario_id": scenario["id"],
                "tested": sum(item["attempted"] for item in items),
                "passed": sum(item["status"] == "passed" for item in items),
                "failed": sum(item["status"] == "failed" for item in items),
                "blocked": sum(
                    item["status"] in ("permission_denied", "needs_confirmation")
                    for item in items
                ),
                "semantic_failed": sum(
                    item["semantic_check"]["status"] == "failed" for item in items
                ),
                "needs_review": sum(
                    item["semantic_check"]["status"] == "needs_review" for item in items
                ),
                "observed_model_calls": len(calls),
                "observed_model_failures": sum(
                    call["status"] == "error" for call in calls.values()
                ),
                "observed_model_e2e_output_tokens_per_second": token_rate(
                    sum(call["output_tokens"] for call in measured),
                    sum(call["request_duration_ms"] for call in measured),
                )
                if measured
                else None,
                "mean_duration_ms": round(sum(durations) / len(durations), 2)
                if durations
                else None,
            }
        )
    return {
        "planned": planned,
        "tested": sum(item["attempted"] for item in results),
        "untested": planned - sum(item["attempted"] for item in results),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "blocked": sum(
            item["status"] in ("permission_denied", "needs_confirmation")
            for item in results
        ),
        "semantic_failed": sum(
            item["semantic_check"]["status"] == "failed" for item in results
        ),
        "needs_review": sum(
            item["semantic_check"]["status"] == "needs_review" for item in results
        ),
        "failure_counts": dict(
            Counter(item["error"] for item in results if item["error"])
        ),
        "by_scenario": rows,
    }


def system_usage(client: httpx.Client) -> Any:
    try:
        return site_get(client, "ai/template-recommendation/usage/my")
    except (ProbeError, httpx.HTTPError) as exc:
        return {
            "error": str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        }


def run_site_checks(
    login_client: httpx.Client,
    model_client: httpx.Client,
    token: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "site_api_url": SITE_API_URL,
        "repetitions": REPETITIONS,
        "identity": None,
        "credential_id": None,
        "public_models": None,
        "system_ai": [],
        "error": None,
        "system_usage_before": None,
        "system_usage_after": None,
        "excluded": ["teacher_judge", "contextual_help", "template_recommendation"],
    }
    secrets = (token,)
    try:
        identity = site_get(login_client, "users/me")
        if not isinstance(identity, dict) or identity.get("role") not in {
            "student",
            "teacher",
            "admin",
        }:
            raise ProbeError("identity_response_invalid")
        report["identity"] = {"id": identity.get("id"), "role": identity["role"]}
        print(f"已驗證登入身分：{identity['role']}")
        report["system_usage_before"] = system_usage(login_client)
        # 公開 API 未取得金鑰仍可測內建服務；分別回報阻塞。
        try:
            credential_id, key = owned_api_key(login_client)
            secrets = (token, key)
            report["credential_id"] = credential_id
            model_client.headers["Authorization"] = f"Bearer {key}"
            report["public_models"] = run_checks(
                model_client, key, additional_secrets=(token,)
            )
            if report["public_models"]["error"] == "cancelled":
                raise KeyboardInterrupt
        except (ProbeError, httpx.HTTPError) as exc:
            report["public_models"] = {
                "error": str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
            }
        for scenario, attempt in (
            (scenario, attempt)
            for attempt in range(1, REPETITIONS + 1)
            for scenario in SYSTEM_SCENARIOS
        ):
            service = scenario["service"]
            if service == "pve_assistant" and identity["role"] != "admin":
                result = {
                    "service": service,
                    "attempted": False,
                    "scenario_id": scenario["id"],
                    "title": scenario["title"],
                    "attempt": attempt,
                    "status": "permission_denied",
                    "error": "admin_required",
                    "status_code": None,
                    "duration_ms": None,
                    "semantic_check": {
                        "status": "not_evaluated",
                        "reason": "admin_required",
                    },
                }
            else:
                result = probe_system(
                    login_client, service, scenario=scenario, attempt=attempt
                )
            # 先保存請求結果；取消後的監控查詢不會丟失已完成的流程。
            report["system_ai"].append(result)
            if identity["role"] == "admin":
                result["observed_model_calls"] = observe_model_calls(
                    login_client, identity["id"], result
                )
            safe = redact_secrets(result, secrets)
            print(
                f"{safe['title']}／第 {attempt} 次：{safe['status']}；{safe['duration_ms']} ms；語意 {safe['semantic_check']['status']}；{safe.get('error') or '流程輸出已保存'}"
            )
            if safe.get("response"):
                preview = safe["response"].get("reply") or json.dumps(
                    safe["response"], ensure_ascii=False
                )
                print(f"  輸出預覽：{preview[:300]}")
            for call in safe.get("observed_model_calls", {}).get("calls", []):
                print(
                    f"  模型觀測 {call['call_type']}：{call['output_tokens']} output tokens；{call['e2e_output_tokens_per_second']} tokens/s；{call['status']}（時間區間關聯）"
                )
            observed = safe.get("observed_model_calls", {})
            if not observed.get("calls"):
                print(
                    f"  模型逐筆用量：{observed.get('error') or observed.get('status') or '此身分沒有管理端觀測權限'}"
                )
            if result["status_code"] == 401:
                raise ProbeError("login_token_expired_or_revoked")
        report["system_usage_after"] = system_usage(login_client)
    except (ProbeError, httpx.HTTPError) as exc:
        report["error"] = (
            str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        )
    except (EOFError, KeyboardInterrupt):
        report["error"] = "cancelled"
    public = report["public_models"] or {}
    public_summary = public.get("summary", {})
    report["system_summary"] = summarize_system(
        report["system_ai"], len(SYSTEM_SCENARIOS) * REPETITIONS
    )
    report["ok"] = bool(
        not report["error"]
        and not public.get("error")
        and public_summary.get("tested", 0) > 0
        and public_summary.get("failed") == 0
        and public_summary.get("semantic_failed", 0) == 0
        and public_summary.get("untested") == 0
        and report["system_summary"]["untested"] == 0
        and report["system_summary"]["semantic_failed"] == 0
        and all(item["status"] == "passed" for item in report["system_ai"])
    )
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return redact_secrets(report, secrets)


def main() -> int:
    print(
        f"SkyLab AI 情境測試\nAPI：{SITE_API_URL}\n每題重複 {REPETITIONS} 次；公開模型各 {len(PUBLIC_SCENARIOS) * REPETITIONS * 2} 次，另測 {len(SYSTEM_SCENARIOS) * REPETITIONS} 次內建流程，會產生真實用量。"
    )
    try:
        # getpass 在非互動終端可能退回明文輸入；禁止此 fallback。
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass(
                "請輸入官網登入 access token（隱藏輸入，不含 Bearer）："
            ).strip()
    except (EOFError, KeyboardInterrupt):
        return 130
    except getpass.GetPassWarning:
        print("無法隱藏輸入，請在互動式 PowerShell 終端執行。", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        print("請輸入官網登入 JWT access token，不含 Bearer 前綴。", file=sys.stderr)
        return 2
    with (
        httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(120, connect=10),
            follow_redirects=False,
        ) as login_client,
        httpx.Client(
            timeout=httpx.Timeout(120, connect=10),
            follow_redirects=False,
        ) as model_client,
    ):
        report = run_site_checks(login_client, model_client, token)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    public_summary = (report["public_models"] or {}).get("summary", {})
    print(
        f"公開模型：PASS {public_summary.get('passed', 0)}／FAIL {public_summary.get('failed', 0)}／語意不符 {public_summary.get('semantic_failed', 0)}／待人工檢查 {public_summary.get('needs_review', 0)}"
    )
    for row in public_summary.get("by_scenario", []):
        print(
            f"  {row['model']}／{row['scenario_id']}／{'串流' if row['stream'] else '一般'}：失敗 {row['failed']}/{row['tested']}；語意不符 {row['semantic_failed']}；{row['e2e_output_tokens_per_second']} tokens/s"
        )
    for row in report["system_summary"]["by_scenario"]:
        print(
            f"  {row['scenario_id']}：流程失敗 {row['failed']}/{row['tested']}；阻塞 {row['blocked']}；語意不符 {row['semantic_failed']}；觀測模型呼叫失敗 {row['observed_model_failures']}/{row['observed_model_calls']}；觀測 {row['observed_model_e2e_output_tokens_per_second']} tokens/s"
        )
    print(
        f"{'PASS' if report['ok'] else 'FAIL'}；錯誤：{report['error'] or ('無' if report['ok'] else '詳見各服務結果')}"
    )
    print(
        "說明文字與 PVE 回覆仍需人工核對；token/s 是整體請求速度，模型觀測紀錄使用時間區間關聯。"
    )
    print(f"報告：{REPORT_FILE}")
    if (
        report["error"] == "cancelled"
        or (report["public_models"] or {}).get("error") == "cancelled"
    ):
        return 130
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
