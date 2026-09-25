"""main.py 的全域 exception handler：404 預設訊息多語化、500 統一錯誤句。"""

import asyncio
import json

from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.main import http_exception_handler, unhandled_exception_handler


def _request(accept_language: str | None = None) -> Request:
    headers = []
    if accept_language:
        headers.append((b"accept-language", accept_language.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/nope",
            "headers": headers,
            "query_string": b"",
        }
    )


def test_unknown_route_404_uses_localized_detail() -> None:
    """框架預設的 "Not Found" 換成統一訊息（ContextVar 預設語言 zh-TW）。"""
    resp = asyncio.run(http_exception_handler(_request(), HTTPException(status_code=404)))
    assert resp.status_code == 404
    assert json.loads(resp.body)["detail"] == "找不到請求的資源"


def test_route_http_exception_detail_passthrough() -> None:
    """路由自帶 detail 的 HTTPException 不得被覆寫。"""
    resp = asyncio.run(
        http_exception_handler(
            _request(), HTTPException(status_code=404, detail="VM 42 不存在")
        )
    )
    assert json.loads(resp.body)["detail"] == "VM 42 不存在"


def test_non_404_http_exception_untouched() -> None:
    resp = asyncio.run(
        http_exception_handler(
            _request(), HTTPException(status_code=405, detail="Method Not Allowed")
        )
    )
    assert resp.status_code == 405
    assert json.loads(resp.body)["detail"] == "Method Not Allowed"


def test_unhandled_exception_returns_unified_json() -> None:
    """500 回 JSON＋統一錯誤句，語言依 Accept-Language（這層拿不到 ContextVar）。"""
    resp = asyncio.run(
        unhandled_exception_handler(_request("en"), RuntimeError("boom"))
    )
    assert resp.status_code == 500
    assert json.loads(resp.body)["detail"].startswith("An error occurred")

    resp_zh = asyncio.run(unhandled_exception_handler(_request(), RuntimeError("boom")))
    assert "請聯繫管理員" in json.loads(resp_zh.body)["detail"]
