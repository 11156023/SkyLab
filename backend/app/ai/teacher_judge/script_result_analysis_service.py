"""AI judgement for Teacher Judge script run results."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, NoReturn

from fastapi import HTTPException

from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.service import _call_vllm
from app.ai.utils import apply_thinking_control
from app.core.i18n import t

logger = logging.getLogger(__name__)

MAX_AI_ANALYSIS_CONCURRENCY = 10
_AI_ANALYSIS_SLOTS = threading.BoundedSemaphore(MAX_AI_ANALYSIS_CONCURRENCY)

_TEXT_LIMIT = 4000
_RAW_LIMIT = 4000


AI_JUDGEMENT_SYSTEM_PROMPT = """
# 角色
你是 Teacher Judge 的 AI 分析評分員。

# 任務
根據節錄後的檢查表項目與 managed script 執行結果，產生老師可讀的評分建議。

# 規則
- 只能輸出 JSON，不要 markdown。
- 你不能發明事實，只能根據 script_result.checks、errors、summary 與 metadata 判斷。
- script check status 是事實證據；你的工作是把 evidence 對齊 rubric item，產生分數與心得。
- 總分固定使用 5 分制，score 必須是 0 到 5 的整數，max_score 固定為 5。
- item_judgements 必須涵蓋每個 rubric item id；沒有 rubric_items 時才使用 script check id。
- evidence_refs 放 script_result.checks[].id。
- 所有 rubric_items 都是本次評量範圍，不可只回答前幾題；保留 rubric item id。
- evidence_refs 只能引用本次 checks 已存在的 id，不得自行發明。
- 工具缺失、timeout、skipped 或其他缺乏證據的情況使用 unknown/skipped，不得當成 pass/fail。
- 工具成功不等於 rubric 條件成立；只有直接證據支持判定時才能使用 pass/fail。
- `judgement_mode=teacher` 的項目只交由導師判斷：必須使用 unknown，score 為 0，comment 說明待導師人工審核；可引用已收集的 evidence，但不得替導師判定 pass/fail。
- 總分只代表 `judgement_mode=ai` 項目的 AI 建議，不得把 `teacher` 項目當成已通過或未通過。

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
        "id": check_id,
        "title": title[:240],
        "status": str(check.get("status") or "unknown"),
        "evidence": _truncate(check.get("evidence")),
        "raw": _truncate(check.get("raw"), _RAW_LIMIT),
    }


def _compact_rubric_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "")[:240],
        "description": _truncate(item.get("description")),
        "detectable": item.get("detectable"),
        "judgement_mode": item.get("judgement_mode") or "ai",
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
) -> list[dict[str, Any]]:
    raw_items = rubric_snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    # Rubric IDs, command keys and generated semantic check IDs are distinct
    # namespaces. Matching their strings cannot safely select the rubric subset.
    return [_compact_rubric_item(item) for item in raw_items if isinstance(item, dict)]


def _validate_ai_judgement(parsed: dict[str, Any], payload: dict[str, Any]) -> None:
    """Reject structurally valid JSON whose references cannot support a grade."""

    def invalid() -> NoReturn:
        raise HTTPException(status_code=502, detail=t("analysis.invalid_format"))

    if (
        type(parsed.get("score")) is not int
        or parsed.get("max_score") != 5
        or not isinstance(parsed.get("summary"), str)
        or not parsed["summary"].strip()
        or not isinstance(parsed.get("item_judgements"), list)
    ):
        invalid()
    script_result = payload.get("script_result")
    checks = script_result.get("checks") if isinstance(script_result, dict) else None
    rubric_items = payload.get("rubric_items")
    if not isinstance(checks, list) or not isinstance(rubric_items, list):
        invalid()
    if any(
        not isinstance(check, dict) or not isinstance(check.get("id"), str)
        for check in checks
    ):
        invalid()
    if any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        for item in rubric_items
    ):
        invalid()
    checks_by_id = {check["id"]: check for check in checks}
    rubric_ids = {item["id"] for item in rubric_items}
    judgement_modes = {
        item["id"]: str(item.get("judgement_mode") or "ai") for item in rubric_items
    }
    allowed_ids = rubric_ids or set(checks_by_id)
    items = parsed["item_judgements"]
    if allowed_ids and not items:
        invalid()
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            invalid()
        item_id = item.get("item_id") or item.get("id")
        refs = item.get("evidence_refs")
        if isinstance(refs, str):
            refs = [refs]
        status = item.get("status")
        if (
            not isinstance(item_id, str)
            or item_id not in allowed_ids
            or item_id in seen
            or not isinstance(refs, list)
            or any(not isinstance(ref, str) or ref not in checks_by_id for ref in refs)
            or not isinstance(status, str)
            or status not in {"pass", "fail", "warning", "unknown", "skipped"}
            or type(item.get("score")) is not int
            or type(item.get("max_score")) is not int
            or item["max_score"] < 1
        ):
            invalid()
        if status in {"pass", "fail"} and (
            not refs
            or all(checks_by_id[ref].get("status") in {"unknown", "skipped"} for ref in refs)
        ):
            invalid()
        if judgement_modes.get(item_id) == "teacher" and (
            status != "unknown" or item.get("score") != 0
        ):
            invalid()
        seen.add(item_id)
    if rubric_ids - seen:
        invalid()


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
                "item_id": str(raw.get("item_id") or raw.get("id") or ""),
                "title": str(raw.get("title") or "")[:240],
                "status": str(raw.get("status") or "unknown"),
                "score": max(0, min(max_score, score)),
                "max_score": max_score,
                "evidence_refs": [str(ref) for ref in evidence_refs if ref is not None],
                "comment": _truncate(raw.get("comment")),
            }
        )
    return normalized


def _normalize_ai_judgement(
    parsed: dict[str, Any],
    *,
    metrics: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        score = int(parsed.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    rubric_items = payload.get("rubric_items")
    judgement_modes = {
        str(item.get("id") or ""): str(item.get("judgement_mode") or "ai")
        for item in rubric_items or []
        if isinstance(item, dict)
    }
    item_judgements = _normalize_item_judgements(parsed.get("item_judgements"))
    for item in item_judgements:
        item["judgement_mode"] = judgement_modes.get(item["item_id"], "ai")
    teacher_review_item_ids = [
        item_id for item_id, mode in judgement_modes.items() if mode == "teacher"
    ]
    return {
        "schema_version": "teacher_judge_ai_judgement.v1",
        "status": "completed",
        "score": max(0, min(5, score)),
        "max_score": 5,
        "summary": _truncate(parsed.get("summary"), 2000),
        "item_judgements": item_judgements,
        "requires_teacher_review": bool(teacher_review_item_ids),
        "teacher_review_item_ids": teacher_review_item_ids,
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
    # Keep the process-wide limit across loops without an orphaned blocking
    # acquire in a worker thread when a queued coroutine is cancelled.
    while not _AI_ANALYSIS_SLOTS.acquire(blocking=False):
        await asyncio.sleep(0.05)


async def _call_ai_judgement(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(
            status_code=503, detail=t("analysis.model_not_configured")
        )

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
        raise HTTPException(
            status_code=502, detail=t("analysis.not_json")
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail=t("analysis.invalid_format"))
    _validate_ai_judgement(parsed, payload)
    return _normalize_ai_judgement(parsed, metrics=dict(metrics), payload=payload)


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
        "rubric_items": _rubric_excerpt(rubric_snapshot),
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

