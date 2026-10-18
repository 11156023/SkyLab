"""Coverage for app.infrastructure.cloudflare.client and turnstile (fake httpx transport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.exceptions import (
    BadRequestError,
    GatewayTimeoutError,
    NotFoundError,
    UpstreamServiceError,
)
from app.infrastructure.cloudflare import client as cf_client
from app.infrastructure.cloudflare import turnstile
from app.infrastructure.cloudflare.client import (
    CloudflareAPIClient,
    _extract_error_message,
)

_REAL_CLIENT = httpx.Client
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    def _factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return _REAL_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(cf_client.httpx, "Client", _factory)
    return seen


def _json(status: int, body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(status, json=body)


# ---------------------------------------------------------------- error parsing


def test_extract_error_message_errors_variants() -> None:
    payload = {
        "errors": [
            {"message": "no code"},
            {"message": "bad", "code": 1003},
            {"message": 5},
            "plain error",
            7,
        ]
    }
    assert _extract_error_message(payload) == "no code; 1003: bad; plain error"


def test_extract_error_message_messages_list() -> None:
    assert _extract_error_message({"errors": [], "messages": ["a", 1, "b"]}) == "a; b"


def test_extract_error_message_messages_without_strings_falls_to_result() -> None:
    payload = {"messages": [1], "result": {"message": "from result"}}
    assert _extract_error_message(payload) == "from result"


def test_extract_error_message_result_empty_message() -> None:
    assert _extract_error_message({"result": {"message": ""}}) is None
    assert _extract_error_message({"result": "x"}) is None


# ---------------------------------------------------------------- _request


def test_request_cleans_params_and_sends_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": []}))
    client = CloudflareAPIClient(api_token="tok", base_url="https://cf.test/v4/")

    out = client._request(
        "GET",
        "/zones",
        params={"a": None, "b": "", "c": "x", "d": 2, "e": [1, 2], "f": True},
    )

    assert out == {"success": True, "result": []}
    req = seen[0]
    assert req.headers["Authorization"] == "Bearer tok"
    assert dict(req.url.params) == {"c": "x", "d": "2", "f": "true"}
    assert str(req.url).startswith("https://cf.test/v4/zones")


def test_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _install(monkeypatch, _boom)
    with pytest.raises(GatewayTimeoutError):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


def test_request_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, _boom)
    with pytest.raises(UpstreamServiceError, match="refused"):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


def test_request_404_with_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(404, {"errors": [{"code": 7003, "message": "nope"}]}))
    with pytest.raises(NotFoundError, match="7003: nope"):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


def test_request_400_uses_text_when_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda _r: httpx.Response(400, text="plain bad"))
    with pytest.raises(BadRequestError, match="plain bad"):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


def test_request_non_dict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, [1, 2]))
    with pytest.raises(UpstreamServiceError, match="格式不正確"):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


def test_request_success_false_with_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": False, "errors": ["denied"]}))
    with pytest.raises(BadRequestError, match="denied"):
        CloudflareAPIClient(api_token="t")._request("GET", "/x")


# ---------------------------------------------------------------- endpoints


def test_verify_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": {"id": "1"}}))
    assert CloudflareAPIClient(api_token="t").verify_token() == {"id": "1"}
    assert seen[0].url.path.endswith("/user/tokens/verify")


def test_verify_token_bad_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True, "result": None}))
    with pytest.raises(UpstreamServiceError):
        CloudflareAPIClient(api_token="t").verify_token()


def test_list_zones(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": []}))
    out = CloudflareAPIClient(api_token="t").list_zones(
        page=1, per_page=20, search="ex", status=None
    )
    assert out["result"] == []
    assert dict(seen[0].url.params) == {"page": "1", "per_page": "20", "name": "ex"}


def test_get_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True, "result": {"id": "z"}}))
    assert CloudflareAPIClient(api_token="t").get_zone("z") == {"id": "z"}


def test_get_zone_bad_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True, "result": []}))
    with pytest.raises(UpstreamServiceError):
        CloudflareAPIClient(api_token="t").get_zone("z")


def test_create_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": {"id": "n"}}))
    out = CloudflareAPIClient(api_token="t").create_zone(
        name="ex.com", account_id="acc", jump_start=True
    )
    assert out == {"id": "n"}
    body = json.loads(seen[0].content)
    assert body == {
        "name": "ex.com",
        "account": {"id": "acc"},
        "jump_start": True,
        "type": "full",
    }


def test_create_zone_bad_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True}))
    with pytest.raises(UpstreamServiceError):
        CloudflareAPIClient(api_token="t").create_zone(
            name="ex.com", account_id="acc", jump_start=False
        )


def test_list_dns_records(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": []}))
    CloudflareAPIClient(api_token="t").list_dns_records(
        zone_id="z", page=2, per_page=5, search=None, record_type="A", proxied=False
    )
    assert seen[0].url.path.endswith("/zones/z/dns_records")
    assert dict(seen[0].url.params) == {
        "page": "2",
        "per_page": "5",
        "type": "A",
        "proxied": "false",
    }


def test_create_dns_record(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": {"id": "r"}}))
    out = CloudflareAPIClient(api_token="t").create_dns_record(
        zone_id="z", record={"type": "A"}
    )
    assert out == {"id": "r"}
    assert seen[0].method == "POST"


def test_create_dns_record_bad_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True, "result": "x"}))
    with pytest.raises(UpstreamServiceError):
        CloudflareAPIClient(api_token="t").create_dns_record(zone_id="z", record={})


def test_update_dns_record(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": {"id": "r"}}))
    out = CloudflareAPIClient(api_token="t").update_dns_record(
        zone_id="z", record_id="r", record={"comment": ""}
    )
    assert out == {"id": "r"}
    assert seen[0].method == "PATCH"
    assert seen[0].url.path.endswith("/zones/z/dns_records/r")


def test_update_dns_record_bad_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _json(200, {"success": True, "result": None}))
    with pytest.raises(UpstreamServiceError):
        CloudflareAPIClient(api_token="t").update_dns_record(
            zone_id="z", record_id="r", record={}
        )


def test_delete_dns_record(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _json(200, {"success": True, "result": {"id": "r"}}))
    assert (
        CloudflareAPIClient(api_token="t").delete_dns_record(zone_id="z", record_id="r")
        is None
    )
    assert seen[0].method == "DELETE"
    assert seen[0].content == b""


# ---------------------------------------------------------------- turnstile


def _install_async(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(turnstile.httpx, "AsyncClient", _factory)
    return seen


async def test_siteverify_success(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_async(
        monkeypatch, lambda _r: httpx.Response(200, json={"success": True})
    )
    out = await turnstile.siteverify(secret="s", token="tk")
    assert out == {"success": True}
    assert str(seen[0].url) == turnstile.SITEVERIFY_URL
    assert seen[0].content == b"secret=s&response=tk"


async def test_siteverify_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _install_async(monkeypatch, _boom)
    with pytest.raises(turnstile.TurnstileNetworkError, match="down"):
        await turnstile.siteverify(secret="s", token="tk")


async def test_siteverify_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_async(monkeypatch, lambda _r: httpx.Response(503, text="x"))
    with pytest.raises(turnstile.TurnstileNetworkError, match="HTTP 503"):
        await turnstile.siteverify(secret="s", token="tk")
