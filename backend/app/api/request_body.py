from __future__ import annotations

from typing import Any, TypeVar

import anyio
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.core.i18n import t

ModelT = TypeVar("ModelT", bound=BaseModel)

# Signup 與 AI API 申請都只有幾 KB 的 JSON。固定小上限可避免部署時誤把
# generation data-plane 的 1 MiB／一般上傳的 256 MiB 套到控制面。
CONTROL_PLANE_JSON_MAX_BYTES = 16 * 1024
REQUEST_BODY_TIMEOUT_SECONDS = 30.0


def limited_json_openapi(model_type: type[BaseModel]) -> dict[str, Any]:
    """補回手動解析 body 後 FastAPI 不會自動產生的 OpenAPI requestBody。"""
    schema = model_type.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value: Any) -> Any:
        # 此處的控制面 schema 僅有非遞迴 model／enum。嵌入 operation 前展開
        # 本地 $defs，否則 #/$defs/... 會指向不存在的 OpenAPI document root。
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/$defs/"):
                value = {
                    **definitions[reference.removeprefix("#/$defs/")],
                    **{key: item for key, item in value.items() if key != "$ref"},
                }
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": inline(schema)}
            },
        }
    }


def _validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """補回 body location，並避免 raw bytes 讓 422 handler 又觸發 500。"""

    def json_safe(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    return [
        {
            **json_safe(error),
            "loc": ("body", *tuple(error.get("loc", ()))),
        }
        for error in exc.errors()
    ]


async def read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    """逐塊限制 body 配置量；30 秒總期限同時涵蓋慢速持續上傳。"""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(413, detail=t("error.request_body_too_large"))
        except ValueError:
            pass

    body = bytearray()
    try:
        with anyio.fail_after(REQUEST_BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > max_bytes:
                    raise HTTPException(413, detail=t("error.request_body_too_large"))
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, detail="Request body upload timed out.") from None
    return bytes(body)


async def parse_limited_json(
    request: Request,
    model_type: type[ModelT],
    *,
    max_bytes: int = CONTROL_PLANE_JSON_MAX_BYTES,
) -> ModelT:
    """在配置完整 request body 前，以串流方式限制小型控制面 JSON。

    呼叫端不宣告 Pydantic body parameter，讓 FastAPI 先完成 auth／route
    dependencies；只有通過後才會走到這裡讀 body。沒有 Content-Length 或使用
    chunked transfer 時仍會逐 chunk 計數，不能只靠可偽造／可省略的 header。
    """
    body = await read_limited_body(request, max_bytes=max_bytes)

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if media_type == "application/json" or (
            media_type.startswith("application/") and media_type.endswith("+json")
        ):
            return model_type.model_validate_json(bytes(body))
        # 保留 FastAPI 對非 JSON Content-Type 的語意：原始 bytes 不會被當成
        # JSON 偷偷接受，而是交給 model validation 產生 422。
        return model_type.model_validate(bytes(body))
    except ValidationError as exc:
        raise RequestValidationError(
            _validation_errors(exc), body=bytes(body)
        ) from exc


__all__ = [
    "CONTROL_PLANE_JSON_MAX_BYTES",
    "limited_json_openapi",
    "parse_limited_json",
    "read_limited_body",
]
