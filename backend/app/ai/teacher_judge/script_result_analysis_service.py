"""AI judgement for Teacher Judge script run results."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.service import _call_vllm
from app.ai.utils import apply_thinking_control

logger = logging.getLogger(__name__)

MAX_AI_ANALYSIS_CONCURRENCY = 10
_AI_ANALYSIS_SLOTS = threading.BoundedSemaphore(MAX_AI_ANALYSIS_CONCURRENCY)

_TEXT_LIMIT = 4000
_RAW_LIMIT = 4000
_MAX_RUBRIC_ITEMS_FALLBACK = 20


AI_JUDGEMENT_SYSTEM_PROMPT = """
# 角色
你是 Teacher Judge 的 AI 分析評分員。

# 任務
根據節錄後的評分表項目與 managed script 執行結果，產生老師可讀的評分建議。

# 規則
- 只能輸出 JSON，不要 markdown。
- 你不能發明事實，只能根據 script_result.checks、errors、summary 與 metadata 判斷。
- script check status 是事實證據；你的工作是把 evidence 對齊 rubric item，產生分數與心得。
- 總分固定使用 5 分制，score 必須是 0 到 5 的整數，max_score 固定為 5。
- item_judgements 必須盡量引用 rubric item id；若只有 script check id，也可使用該 check id。
- evidence_refs 放 script_result.checks[].id。

# 輸出格式
{
  "score": 0,
  "max_score": 5,
  "summary": "繁體中文整體心得",
  "item_judgements": [
    {
      "item_id": "rubric 或 check id",
      "title": "項目名稱",
      "status": "pass | fail | warning | unknown | skipped",
      "score": 0,
      "max_score": 1,
      "evidence_refs": ["check.id"],
      "comment": "繁體中文分析心得"
    }
  ]
}
""".strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: Any, limit: int = _TEXT_LIMIT) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _compact_check(check: Any) -> dict[str, Any] | None:
    if not isinstance(check, dict):
        return None
    check_id = str(check.get("id") or "").strip()
    title = str(check.get("title") or "").strip()
    if not check_id or not title:
        return None
    return {
        "id": check_id[:120],
        "title": title[:240],
        "status": str(check.get("status") or "unknown"),
        "evidence": _truncate(check.get("evidence")),
        "raw": _truncate(check.get("raw"), _RAW_LIMIT),
    }


def _rubric_item_keys(item: dict[str, Any]) -> set[str]:
    keys = {str(item.get("id") or "").strip()}
    for step in item.get("check_steps") or []:
        if isinstance(step, dict):
            keys.add(str(step.get("command_key") or "").strip())
    return {key for key in keys if key}


def _compact_rubric_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or "")[:120],
        "title": str(item.get("title") or "")[:240],
        "description": _truncate(item.get("description")),
        "detectable": item.get("detectable"),
        "detection_method": _truncate(item.get("detection_method")),
        "fallback": _truncate(item.get("fallback")),
        "check_steps": [
            {
                "template_key": step.get("template_key"),
                "command_key": step.get("command_key"),
                "command_label": step.get("command_label"),
            }
            for step in item.get("check_steps") or []
            if isinstance(step, dict)
        ],
    }


def _rubric_excerpt(
    rubric_snapshot: dict[str, Any],
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_items = rubric_snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    check_ids = {str(check.get("id") or "") for check in checks}
    matched: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        compact = _compact_rubric_item(raw_item)
        fallback.append(compact)
        if _rubric_item_keys(raw_item) & check_ids:
            matched.append(compact)

    return matched or fallback[:_MAX_RUBRIC_ITEMS_FALLBACK]


def _normalize_item_judgements(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            score = int(raw.get("score") or 0)
            max_score = int(raw.get("max_score") or 1)
        except (TypeError, ValueError):
            score = 0
            max_score = 1
        max_score = max(1, max_score)
        evidence_refs = raw.get("evidence_refs")
        if isinstance(evidence_refs, str):
            evidence_refs = [evidence_refs]
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        normalized.append(
            {
                "item_id": str(raw.get("item_id") or raw.get("id") or "")[:120],
                "title": str(raw.get("title") or "")[:240],
                "status": str(raw.get("status") or "unknown"),
                "score": max(0, min(max_score, score)),
                "max_score": max_score,
                "evidence_refs": [
                    str(ref)[:120]
                    for ref in evidence_refs
                    if ref is not None
                ],
                "comment": _truncate(raw.get("comment")),
            }
        )
    return normalized


def _normalize_ai_judgement(
    parsed: dict[str, Any],
    *,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    try:
        score = int(parsed.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return {
        "schema_version": "teacher_judge_ai_judgement.v1",
        "status": "completed",
        "score": max(0, min(5, score)),
        "max_score": 5,
        "summary": _truncate(parsed.get("summary"), 2000),
        "item_judgements": _normalize_item_judgements(
            parsed.get("item_judgements")
        ),
        "metrics": metrics,
        "model": settings.VLLM_MODEL_NAME,
        "analyzed_at": _now_iso(),
    }


def _skipped_judgement(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "teacher_judge_ai_judgement.v1",
        "status": "skipped",
        "score": None,
        "max_score": 5,
        "summary": reason,
        "item_judgements": [],
        "analyzed_at": _now_iso(),
    }


def _failed_judgement(message: str) -> dict[str, Any]:
    return {
        "schema_version": "teacher_judge_ai_judgement.v1",
        "status": "failed",
        "score": None,
        "max_score": 5,
        "summary": "AI 分析失敗。",
        "error": _truncate(message, 1000),
        "item_judgements": [],
        "analyzed_at": _now_iso(),
    }


def pending_judgement() -> dict[str, Any]:
    return {
        "schema_version": "teacher_judge_ai_judgement.v1",
        "status": "pending",
        "score": None,
        "max_score": 5,
        "summary": "AI 分析排隊中。",
        "item_judgements": [],
        "analyzed_at": None,
    }


async def _acquire_ai_slot() -> None:
    await asyncio.to_thread(_AI_ANALYSIS_SLOTS.acquire)


async def _call_ai_judgement(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

    request_payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": AI_JUDGEMENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "max_tokens": min(settings.VLLM_CHAT_MAX_TOKENS, 2048),
            "temperature": 0.0,
            "top_p": settings.VLLM_TOP_P,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )

    await _acquire_ai_slot()
    try:
        content, metrics = await _call_vllm(
            request_payload,
            timeout=float(settings.VLLM_TIMEOUT),
        )
    finally:
        _AI_ANALYSIS_SLOTS.release()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI 分析回傳不是 JSON。") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="AI 分析回傳格式不正確。")
    return _normalize_ai_judgement(parsed, metrics=dict(metrics))


async def _analyze_one_target(
    *,
    rubric_snapshot: dict[str, Any],
    script_metadata: dict[str, Any],
    target_result: dict[str, Any],
) -> dict[str, Any]:
    result = dict(target_result)
    validation = result.get("validation")
    parsed_result = result.get("parsed_result")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        error = (
            str(validation.get("error"))
            if isinstance(validation, dict) and validation.get("error")
            else "JSON 驗證未通過，略過 AI 分析。"
        )
        result["ai_judgement"] = _skipped_judgement(error)
        return result
    if not isinstance(parsed_result, dict):
        result["ai_judgement"] = _skipped_judgement("缺少可分析的 parsed result。")
        return result

    checks = [
        compact
        for raw_check in parsed_result.get("checks") or []
        if (compact := _compact_check(raw_check)) is not None
    ]
    payload = {
        "rubric_items": _rubric_excerpt(rubric_snapshot, checks),
        "script_metadata": script_metadata,
        "target": {
            "vmid": result.get("vmid"),
            "name": result.get("name"),
            "proxmox_node": result.get("proxmox_node"),
            "resource_type": result.get("resource_type"),
            "user": result.get("user"),
            "execution_status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "exit_code": result.get("exit_code"),
        },
        "script_result": {
            "schema_version": parsed_result.get("schema_version"),
            "metadata": parsed_result.get("metadata"),
            "summary": _truncate(parsed_result.get("summary"), 2000),
            "checks": checks,
            "errors": [
                _truncate(error, 1000)
                for error in parsed_result.get("errors") or []
                if error is not None
            ],
        },
    }

    try:
        result["ai_judgement"] = await _call_ai_judgement(payload)
    except Exception as exc:
        logger.warning(
            "Teacher Judge AI judgement failed vmid=%s",
            result.get("vmid"),
            exc_info=True,
        )
        result["ai_judgement"] = _failed_judgement(str(exc))
    return result


async def analyze_target_results(
    *,
    rubric_snapshot: dict[str, Any],
    script_metadata: dict[str, Any],
    target_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tasks = [
        _analyze_one_target(
            rubric_snapshot=rubric_snapshot,
            script_metadata=script_metadata,
            target_result=target_result,
        )
        for target_result in target_results
    ]
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks))

