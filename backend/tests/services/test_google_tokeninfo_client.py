"""infrastructure/google/tokeninfo：網路錯誤與非 200 必須是不同的例外。"""

from typing import Any

import httpx
import pytest

from app.infrastructure.google import tokeninfo


class _FakeResponse:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeClient:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any]) -> _FakeResponse:
        self.calls.append((url, params))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        assert isinstance(self._outcome, _FakeResponse)
        return self._outcome


def _patch_client(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> _FakeClient:
    client = _FakeClient(outcome)
    monkeypatch.setattr(
        tokeninfo.httpx, "AsyncClient", lambda *args, **kwargs: client
    )
    return client


async def test_fetch_returns_payload_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(
        monkeypatch, _FakeResponse(200, {"aud": "x", "email": "a@example.com"})
    )

    data = await tokeninfo.fetch_id_token_info("tok")

    assert data == {"aud": "x", "email": "a@example.com"}
    assert client.calls == [(tokeninfo.TOKENINFO_URL, {"id_token": "tok"})]


async def test_fetch_raises_rejected_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _FakeResponse(400, {"error": "invalid_token"}))

    with pytest.raises(tokeninfo.GoogleTokenInfoRejected) as exc_info:
        await tokeninfo.fetch_id_token_info("bad")

    assert exc_info.value.status_code == 400


async def test_fetch_raises_network_error_on_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, httpx.ConnectError("boom"))

    with pytest.raises(tokeninfo.GoogleTokenInfoNetworkError):
        await tokeninfo.fetch_id_token_info("tok")
