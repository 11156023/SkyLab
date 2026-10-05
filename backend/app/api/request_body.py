from __future__ import annotations

from typing import Any, TypeVar

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.core.i18n import t

ModelT = TypeVar("ModelT", bound=BaseModel)

# Signup 與 AI API 申請都只有幾 KB 的 JSON。固定小上限可避免部署時誤把
# generation data-plane 的 1 MiB／一般上傳的 256 MiB 套到控制面。
CONTROL_PLANE_JSON_MAX_BYTES = 16 * 1024


def limited_json_openapi(model_type: type[BaseModel]) -> dict[str, Any]:
    """補回手動解析 body 後 FastAPI 不會自動產生的 OpenAPI requestBody。"""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": model_type.model_json_schema()}
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
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=t("error.request_body_too_large"),
                )
        except ValueError:
            # ASGI server 通常會先拒絕非法 Content-Length；若仍傳進來，改以
            # 實際收到的 bytes 判斷，不能因 header 無法解析就跳過上限。
            pass

    body = bytearray()
    async for chunk in request.stream():
        # 先判斷再 copy；ASGI adapter 若一次交付大 chunk，不能先把
        # 整塊加入 bytearray 才發現超額。
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=t("error.request_body_too_large"),
            )
        body.extend(chunk)

    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    try:
        if media_type == "application/json" or media_type.endswith("+json"):
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
]
