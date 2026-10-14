"""Grafana（監控 profile）只能經 SkyLab 管理員免密碼登入，且不再預設 admin／admin。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _grafana() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service: dict[str, Any] = compose["services"]["grafana"]
    return service


def test_grafana_own_login_is_disabled_but_auth_proxy_stays() -> None:
    env = _grafana()["environment"]

    assert env["GF_AUTH_DISABLE_LOGIN_FORM"] == "true"
    assert env["GF_AUTH_BASIC_ENABLED"] == "false"
    # disable_login 會連 auth.proxy 用的內建使用者來源一起關掉，SSO 就失效
    assert "GF_AUTH_DISABLE_LOGIN" not in env
    assert env["GF_AUTH_PROXY_ENABLED"].startswith("${GRAFANA_AUTH_PROXY_ENABLED:-true}")
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"


def test_grafana_admin_password_has_no_weak_default() -> None:
    env = _grafana()["environment"]

    assert env["GF_SECURITY_ADMIN_PASSWORD"] == "${GRAFANA_ADMIN_PASSWORD:-}"
    # ${VAR:?} 會讓沒開監控 profile 的站台整份 compose 解析失敗
    assert ":?" not in yaml.safe_dump(_grafana())


def _entrypoint_script() -> str:
    entrypoint = _grafana()["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"]
    # compose 把 $$ 還原成 $；/run.sh 換成印出最後拿到的密碼
    return entrypoint[2].replace("$$", "$").replace(
        'exec /run.sh "$@"', 'printf "%s|%s" "$GF_SECURITY_ADMIN_PASSWORD" "$*"'
    )


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="POSIX sh required")
@pytest.mark.parametrize("configured", ["", "admin", "changethis"])
def test_entrypoint_replaces_weak_admin_password_with_random(configured: str) -> None:
    script = _entrypoint_script()
    env = {**os.environ, "GF_SECURITY_ADMIN_PASSWORD": configured}

    first = subprocess.run(
        ["sh", "-c", script, "grafana", "arg"], env=env, capture_output=True, text=True, check=True
    ).stdout
    second = subprocess.run(
        ["sh", "-c", script, "grafana"], env=env, capture_output=True, text=True, check=True
    ).stdout

    password, args = first.split("|")
    assert args == "arg"
    assert len(password) >= 32 and password not in {"admin", "changethis"}
    assert second.split("|")[0] != password


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="POSIX sh required")
def test_entrypoint_keeps_an_operator_password() -> None:
    env = {**os.environ, "GF_SECURITY_ADMIN_PASSWORD": "Operator-Chosen-1"}

    result = subprocess.run(
        ["sh", "-c", _entrypoint_script(), "grafana"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "Operator-Chosen-1|"
