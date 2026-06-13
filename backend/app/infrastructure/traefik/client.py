from __future__ import annotations

import json
import shlex
from typing import Any

from app.core.config import settings
from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure.ssh import create_key_client, exec_command


def _normalize_path(path: str) -> str:
    if path.startswith("/"):
        return path
    return f"/{path}"


def _build_curl_command(url: str) -> str:
    return (
        "curl -sS -L "
        f"--max-time {settings.TRAEFIK_API_TIMEOUT} "
        "-H 'Accept: application/json' "
        "-w '\n%{http_code}' "
        f"{shlex.quote(url)}"
    )


def _normalize_collection(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        normalized: list[dict[str, Any]] = []
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("name", str(key))
            normalized.append(item)
        return normalized

    return []


class TraefikGatewayClient:
    def __init__(self, session: object):
        from app.repositories import gateway_config as gw_repo  # noqa: PLC0415
        from app.repositories.gateway_config import (  # noqa: PLC0415
            get_decrypted_private_key,
        )

        config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
        if config is None or not config.host or not config.encrypted_private_key:
            raise BadRequestError("Gateway VM 尚未設定，無法讀取 Traefik runtime")

        private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
        self._base_url = settings.TRAEFIK_API_BASE_URL.rstrip("/")
        self._client = create_key_client(
            config.host,
            config.ssh_port,
            config.ssh_user,
            private_key_pem,
            timeout=10,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TraefikGatewayClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def fetch_json(self, path: str) -> Any:
        url = f"{self._base_url}{_normalize_path(path)}"
        command = _build_curl_command(url)
        code, out, err = exec_command(self._client, command)
        if code != 0:
            detail = (err or out).strip()
            raise ProxmoxError(f"Traefik API 請求失敗：{detail or 'curl 執行失敗'}")

        body, _, status_text = out.rpartition("\n")
        if not body and status_text and not status_text.strip().isdigit():
            body = out
            status_text = ""

        try:
            status_code = int(status_text.strip()) if status_text.strip() else 200
        except ValueError as exc:
            raise ProxmoxError("Traefik API 狀態碼解析失敗") from exc

        if status_code >= 400:
            detail = body.strip() or err.strip()
            raise ProxmoxError(
                f"Traefik API 回應錯誤 ({status_code})：{detail or '無詳細訊息'}"
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProxmoxError("Traefik API 回傳不是合法 JSON") from exc

    def fetch_collection(self, path: str) -> list[dict[str, Any]]:
        return _normalize_collection(self.fetch_json(path))
