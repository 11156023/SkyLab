"""AI relay 對各種「不是合法 JSON」的請求本體都要回 OpenAI 格式的 400。

json.loads 對非 UTF-8 位元組丟 UnicodeDecodeError、對超長整數丟 ValueError、
對過深巢狀丟 RecursionError，這些都不是 JSONDecodeError，以前會變成 500。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.routes import ai_proxy


def _json_request(body: bytes, *, content_type: bytes = b"application/json") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/ai-proxy/chat/completions",
            "query_string": b"",
            "headers": [(b"content-type", content_type)],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        },
        receive,
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"\xff{}", id="invalid-utf8"),
        pytest.param(b"[" * 100_000, id="deep-nesting"),
        pytest.param(b'{"model":"m","n":' + b"1" * 5000 + b"}", id="huge-integer"),
        pytest.param(b"{not json", id="plain-decode-error"),
        pytest.param(b'{"model":"m","temperature":NaN}', id="nan"),
        pytest.param(b'{"model":"m","temperature":Infinity}', id="infinity"),
        pytest.param(b'{"model":"m","temperature":-Infinity}', id="negative-infinity"),
        pytest.param(b'{"model":"m","temperature":1e400}', id="positive-overflow"),
        pytest.param(b'{"model":"m","temperature":-1e400}', id="negative-overflow"),
    ],
)
def test_malformed_bodies_return_invalid_json_400(body: bytes) -> None:
    response = asyncio.run(ai_proxy._json_payload(_json_request(body)))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "invalid_json"


def test_valid_object_body_is_returned() -> None:
    result = asyncio.run(ai_proxy._json_payload(_json_request(b'{"model":"m"}')))
    assert result == {"model": "m"}


@pytest.mark.parametrize(
    ("content_type", "accepted"),
    [
        (b"application/json; charset=utf-8", True),
        (b"application/vnd.openai+json", True),
        (b"text/application/json", False),
        (b"application/json-evil", False),
    ],
)
def test_content_type_requires_a_json_media_type(
    content_type: bytes, accepted: bool
) -> None:
    result = asyncio.run(
        ai_proxy._json_payload(
            _json_request(b'{"model":"m"}', content_type=content_type)
        )
    )

    if accepted:
        assert result == {"model": "m"}
    else:
        assert isinstance(result, JSONResponse)
        assert result.status_code == 415
        assert json.loads(result.body)["error"]["code"] == "unsupported_media_type"
