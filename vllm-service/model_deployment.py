"""Dependency-free validation for local launch targets and remote vLLM routes."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ENV_REFERENCE = re.compile(r"os\.environ/[A-Za-z_][A-Za-z0-9_]*")
ADMIN_ENV_NAMES = {
    "LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "DATABASE_URL",
    "LITELLM_SERVICE_API_KEY", "AI_API_API_KEY", "LITELLM_RUNTIME_API_KEY",
}


def deployment_kind(model: dict[str, Any]) -> str:
    kind = model.get("deployment", "local")
    if kind not in ("local", "remote"):
        raise ValueError("deployment 必須為 local 或 remote")
    return kind


def upstream_connection(model: dict[str, Any]) -> tuple[str, str]:
    """Return the base URL and key variable name, never a plaintext secret."""
    kind = deployment_kind(model)
    key_name = model.get("api_key_env", "VLLM_UPSTREAM_API_KEY")
    if (
        not isinstance(key_name, str)
        or not ENV_NAME.fullmatch(key_name)
        or key_name in ADMIN_ENV_NAMES
    ):
        raise ValueError("api_key_env 必須為上游金鑰的環境變數名稱，不可使用管理金鑰")
    if "api_key" in model:
        raise ValueError("models.json 不得填入明文 api_key，請使用 api_key_env")
    if kind == "local":
        port = model.get("api_port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("api_port 必須介於 1 和 65535")
        if "api_base" in model:
            raise ValueError("local 不可覆寫 api_base；跨主機路由請使用 deployment=remote")
        return f"http://127.0.0.1:{port}/v1", key_name

    base = model.get("api_base")
    if not isinstance(base, str) or any(c.isspace() for c in base):
        raise ValueError("remote 必須設定完整的 http(s) api_base，以 /v1 結尾")
    try:
        url = urlsplit(base)
        valid = (
            url.scheme in {"http", "https"} and url.hostname
            and url.hostname not in {"0.0.0.0", "::"}
            and not url.username and not url.password and not url.query and not url.fragment
            and url.path.rstrip("/").endswith("/v1")
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("remote api_base 必須是含 /v1 的 http(s) URL，不含帳密或查詢參數")
    return base.rstrip("/"), key_name
