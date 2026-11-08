"""Minimal Cloudflare API client for admin-only domain management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.exceptions import (
    BadRequestError,
    GatewayTimeoutError,
    NotFoundError,
    UpstreamServiceError,
)

_CLOUDFLARE_API_BASE_URL = "https://api.cloudflare.com/client/v4"
_QueryValue = str | int | float | bool | None


def _extract_error_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    messages: list[str] = []
    errors = payload.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict):
                message = error.get("message")
                code = error.get("code")
                if isinstance(message, str):
                    if code is None:
                        messages.append(message)
                    else:
                        messages.append(f"{code}: {message}")
            elif isinstance(error, str):
                messages.append(error)

    if messages:
        return "; ".join(messages)

    api_messages = payload.get("messages")
    if isinstance(api_messages, list):
        message_parts = [message for message in api_messages if isinstance(message, str)]
        if message_parts:
            return "; ".join(message_parts)

    result = payload.get("result")
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, str) and message:
            return message

    return None


class CloudflareAPIClient:
    def __init__(
        self,
        *,
        api_token: str,
        timeout: float = 15.0,
        base_url: str = _CLOUDFLARE_API_BASE_URL,
    ) -> None:
        self._api_token = api_token
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object | None] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        clean_params: dict[str, _QueryValue] | None = None
        if params is not None:
            clean_params = {}
            for key, value in params.items():
                if value is None:
                    continue
                if value == "":
                    continue
                if isinstance(value, (str, int, float, bool)):
                    clean_params[key] = value

        try:
            with httpx.Client(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_token}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            ) as client:
                response = client.request(
                    method,
                    path,
                    params=clean_params,
                    json=dict(json_body) if json_body is not None else None,
                )
        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError("Cloudflare API 逾時，請稍後再試") from exc
        except httpx.RequestError as exc:
            raise UpstreamServiceError(f"Cloudflare API 連線失敗：{exc}") from exc

        payload: object
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.is_error:
            message = _extract_error_message(payload) or response.text or "Cloudflare API 請求失敗"
            if response.status_code == 404:
                raise NotFoundError(message)
            raise BadRequestError(message) if response.status_code < 500 else UpstreamServiceError(message)

        if not isinstance(payload, dict):
            raise UpstreamServiceError("Cloudflare API 回傳格式不正確")

        if payload.get("success") is False:
            message = _extract_error_message(payload) or "Cloudflare API 回傳錯誤"
            raise BadRequestError(message)

        return payload

    def verify_token(self) -> dict[str, Any]:
        """驗證使用者層級（My Profile → API Tokens）的 token。"""
        return self._verify("/user/tokens/verify")

    def verify_account_token(self, account_id: str) -> dict[str, Any]:
        """驗證帳戶層級（Account → API Tokens）的 token：這種 token 打 /user/tokens/verify
        會回 1000 Invalid API Token，要用帳戶端點。"""
        return self._verify(f"/accounts/{account_id.strip()}/tokens/verify")

    def _verify(self, path: str) -> dict[str, Any]:
        payload = self._request("GET", path)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamServiceError("Cloudflare 驗證結果格式不正確")
        return result

    def list_zones(
        self,
        *,
        page: int,
        per_page: int,
        search: str | None,
        status: str | None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/zones",
            params={
                "page": page,
                "per_page": per_page,
                "name": search,
                "status": status,
            },
        )

    def get_zone(self, zone_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/zones/{zone_id}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamServiceError("Cloudflare zone 格式不正確")
        return result

    def create_zone(
        self,
        *,
        name: str,
        account_id: str,
        jump_start: bool,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/zones",
            json_body={
                "name": name,
                "account": {"id": account_id},
                "jump_start": jump_start,
                "type": "full",
            },
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamServiceError("Cloudflare zone 建立結果格式不正確")
        return result

    def list_dns_records(
        self,
        *,
        zone_id: str,
        page: int,
        per_page: int,
        search: str | None,
        record_type: str | None,
        proxied: bool | None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={
                "page": page,
                "per_page": per_page,
                "name": search,
                "type": record_type,
                "proxied": proxied,
            },
        )

    def create_dns_record(
        self,
        *,
        zone_id: str,
        record: Mapping[str, object],
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/zones/{zone_id}/dns_records",
            json_body=record,
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamServiceError("Cloudflare DNS 建立結果格式不正確")
        return result

    def update_dns_record(
        self,
        *,
        zone_id: str,
        record_id: str,
        record: Mapping[str, object],
    ) -> dict[str, Any]:
        payload = self._request(
            "PATCH",
            f"/zones/{zone_id}/dns_records/{record_id}",
            json_body=record,
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamServiceError("Cloudflare DNS 更新結果格式不正確")
        return result

    def delete_dns_record(self, *, zone_id: str, record_id: str) -> None:
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")


__all__ = ["CloudflareAPIClient"]
