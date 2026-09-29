"""LiteLLM runtime 健康探測（同步 httpx，給平台健康監控用）。

只負責對 LiteLLM 發 HTTP 請求並整理回應；狀態判定規則在
``services/monitoring/health_policy``，快取與逾時包裝在
``services/monitoring/system_health_service``。與 ``api/routes/ai_monitoring``
的 async 探測是不同用途，刻意不合併。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 單一 HTTP 請求的逾時；整體探測的上限由呼叫端另外包
REQUEST_TIMEOUT_SECONDS = 5.0


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}: {exc}"[:200]


def probe(
    base_url: str,
    api_key: str,
    *,
    transport: Any = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """問 LiteLLM：活著嗎、DB 連上沒、有哪些模型、背景健康檢查的結果。

    ``/health`` 讀的是 LiteLLM 背景健康檢查的快取（config 開了
    background_health_checks），不會為了這次探測去打推論服務。它只回
    ``hosted_vllm/<served>`` 與 ``model_id``，所以用 ``/model/info`` 把 id 對回
    公開 alias。全程用受限的 runtime key，不需要 master key。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    result: dict[str, Any] = {"reachable": False, "db": None, "models": None, "deployments": None}
    with httpx.Client(base_url=base_url, timeout=timeout, transport=transport) as client:
        try:
            client.get("/health/liveliness").raise_for_status()
        except httpx.HTTPError as exc:
            result["error"] = _short_error(exc)
            return result
        result["reachable"] = True
        try:
            readiness = client.get("/health/readiness").json()
            result["db"] = readiness.get("db") if isinstance(readiness, dict) else None
        except (httpx.HTTPError, ValueError):
            result["db"] = None

        alias_by_id: dict[str, str] = {}
        try:
            info = client.get("/model/info", headers=headers)
            info.raise_for_status()
            for entry in info.json().get("data", []):
                alias = entry.get("model_name")
                model_id = (entry.get("model_info") or {}).get("id")
                if isinstance(alias, str) and alias:
                    if isinstance(model_id, str) and model_id:
                        alias_by_id[model_id] = alias
                    result["models"] = [*(result["models"] or []), alias]
        except (httpx.HTTPError, ValueError, AttributeError):
            logger.debug("LiteLLM /model/info probe failed", exc_info=True)

        try:
            health = client.get("/health", headers=headers)
            health.raise_for_status()
            payload = health.json()
        except (httpx.HTTPError, ValueError):
            logger.debug("LiteLLM /health probe failed", exc_info=True)
        else:
            deployments: dict[str, dict[str, int]] = {}
            for group, key in (("healthy_endpoints", "healthy"), ("unhealthy_endpoints", "unhealthy")):
                entries = payload.get(group) if isinstance(payload, dict) else None
                for entry in entries if isinstance(entries, list) else []:
                    alias = alias_by_id.get(str((entry or {}).get("model_id")))
                    if alias is None:
                        continue  # 對不回公開 alias 的部署不顯示（避免露出上游名稱）
                    counts = deployments.setdefault(alias, {"healthy": 0, "unhealthy": 0})
                    counts[key] += 1
            result["deployments"] = deployments
    return result
