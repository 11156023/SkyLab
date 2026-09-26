#!/usr/bin/env python3
"""Validate secret boundaries and generate the integrated LiteLLM config."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import yaml
from dotenv import dotenv_values

from generate_litellm_config import (
    DEFAULT_MODELS, DEFAULT_OUTPUT, DEFAULT_TEMPLATE, PROJECT_ROOT,
    assert_secret_free, load_models, load_template, render_config,
)

REPO_ROOT = PROJECT_ROOT.parent
PRIVILEGED_KEYS = {"LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "DATABASE_URL", "VLLM_UPSTREAM_API_KEY"}


def require_secret(values: dict, name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip() or value.startswith(("replace-with-", "ai-api-secret-")):
        raise ValueError(f"請設定有效的 {name}（不會顯示內容）")
    return value


def validate_environment(root_env: dict, services: dict, models: list[dict], engine_env: dict) -> None:
    leaked = PRIVILEGED_KEYS.intersection(root_env)
    if leaked:
        raise ValueError(f"請將 {', '.join(sorted(leaked))} 留在 litellm/.env，不可放入主 .env")
    gateway = services["litellm"].get("environment", {})
    for name in PRIVILEGED_KEYS:
        require_secret(gateway, name)
    master = gateway["LITELLM_MASTER_KEY"]
    for service_name in ("backend", "worker", "prestart"):
        env = services[service_name].get("environment", {})
        if PRIVILEGED_KEYS.intersection(env):
            raise ValueError(f"{service_name} 不可注入 LiteLLM 管理或上游金鑰")
        service_key = require_secret(env, "AI_API_API_KEY")
        if service_key in {master, gateway["VLLM_UPSTREAM_API_KEY"]}:
            raise ValueError("Campus service key 必須與 LiteLLM master / vLLM upstream key 分開")
        if env.get("LITELLM_SERVICE_API_KEY") and env["LITELLM_SERVICE_API_KEY"] != service_key:
            raise ValueError("LITELLM_SERVICE_API_KEY 必須與 AI_API_API_KEY 使用同一把受限 key")
        runtime_key = env.get("LITELLM_RUNTIME_API_KEY")
        if runtime_key and runtime_key != service_key:
            raise ValueError("LITELLM_RUNTIME_API_KEY 必須與 AI_API_API_KEY 使用同一把受限 key")
        for field in ("AI_API_BASE_URL", "LITELLM_RUNTIME_BASE_URL"):
            url = urlsplit(env.get(field, ""))
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError(f"{field} 必須設定有效的 gateway URL")
            if url.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}:
                raise ValueError(f"Compose 容器的 {field} 請改用 http://host.docker.internal:4000")
            if url.path not in {"", "/"} or url.query or url.fragment or url.username or url.password:
                raise ValueError(f"{field} 填 gateway 根位址，不含 /v1 或帳密")
    if "LITELLM_SERVICE_API_KEY" in gateway or "AI_API_API_KEY" in gateway or "LITELLM_RUNTIME_API_KEY" in gateway:
        raise ValueError("Campus 的受限 service key 不應注入 LiteLLM 容器")
    database = urlsplit(gateway["DATABASE_URL"])
    campus = services["backend"]["environment"]
    if database.scheme not in {"postgresql", "postgres"} or not database.hostname or not database.path.strip("/"):
        raise ValueError("DATABASE_URL 必須指向專用 PostgreSQL 資料庫")
    if unquote(database.path.strip("/")) == campus.get("POSTGRES_DB") or unquote(database.username or "") == campus.get("POSTGRES_USER"):
        raise ValueError("LiteLLM 必須使用與 Campus 不同的資料庫名稱及帳號")
    for model in models:
        key_name = model["api_key_env"]
        upstream_key = require_secret(gateway, key_name)
        if key_name in root_env:
            raise ValueError(f"上游 {key_name} 請只放在 litellm/.env")
        if upstream_key in {master, campus["AI_API_API_KEY"]}:
            raise ValueError(f"模型 {model['alias']} 不可使用 LiteLLM master / Campus service key 作為上游金鑰")
        if model["deployment"] == "local" and upstream_key != require_secret(engine_env, "API_KEY"):
            raise ValueError(f"本機模型 {model['alias']} 上游金鑰與 .env.API 的 API_KEY 不一致")


def check_upstreams(models: list[dict], gateway_env: dict) -> None:
    # A direct host connection must not be redirected through HTTP_PROXY.
    opener = build_opener(ProxyHandler({}))
    for model in models:
        request = Request(
            model["api_base"] + "/models",
            headers={"Authorization": f"Bearer {gateway_env[model['api_key_env']]}"},
        )
        try:
            with opener.open(request, timeout=10) as response:
                payload = json.load(response)
            ids = {item.get("id") for item in payload.get("data", [])}
        except HTTPError as exc:
            raise ValueError(f"上游 {model['alias']} 回傳 HTTP {exc.code}，請核對服務與 key") from None
        except (URLError, OSError, ValueError, TypeError, AttributeError):
            raise ValueError(f"上游 {model['alias']} 無法取得 /v1/models，請核對連線與服務") from None
        if model["served_model_name"] not in ids:
            raise ValueError(f"上游 {model['alias']} 未提供指定的 served_model_name")
        print(f"上游就緒：{model['alias']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="驗證既有 config.yaml，不改寫檔案")
    parser.add_argument("--check-upstreams", action="store_true", help="逐一查詢上游 /v1/models（不產生推論）")
    parser.add_argument("--start", action="store_true", help="預檢查後執行主 Compose up -d --build；不會停止獨立 gateway")
    args = parser.parse_args()
    if args.check_only and args.start:
        parser.error("--check-only 與 --start 不可同時使用")

    try:
        for path in (REPO_ROOT / ".env", PROJECT_ROOT / "litellm/.env"):
            if not path.is_file():
                raise ValueError(f"缺少 {path.relative_to(REPO_ROOT)}，請先複製對應 .env.example 並填入設定")
        models = load_models(DEFAULT_MODELS)
        config = render_config(models, load_template(DEFAULT_TEMPLATE), "production")
        assert_secret_free(config)
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            # Compose expands secrets; never relay its complete output or stderr.
            raise ValueError("主 Compose 無法解析；請核對兩份 .env、必要變數與 Compose >= 2.20（可用 docker compose config --quiet）")
        compose = json.loads(result.stdout)
        services = compose["services"]
        engine_env = {**dotenv_values(PROJECT_ROOT / ".env.API"), **os.environ}
        validate_environment(dotenv_values(REPO_ROOT / ".env"), services, models, engine_env)
        if args.check_upstreams or args.start:
            check_upstreams(models, services["litellm"]["environment"])
        if DEFAULT_OUTPUT.exists() and not DEFAULT_OUTPUT.is_file():
            raise ValueError("litellm/config.yaml 必須為檔案，請先移除誤建的空目錄")
        if args.check_only:
            if not DEFAULT_OUTPUT.is_file() or yaml.safe_load(DEFAULT_OUTPUT.read_text()) != config:
                raise ValueError("config.yaml 缺少或與模型／template 不一致；請執行 prepare-ai-stack.sh 重新產生")
        else:
            DEFAULT_OUTPUT.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            print("已產生 production 設定：vllm-service/litellm/config.yaml（只含金鑰 reference）")
        print(f"主 Compose 與金鑰邊界檢查通過；本機 {sum(m['deployment'] == 'local' for m in models)} 個、遠端 {sum(m['deployment'] == 'remote' for m in models)} 個模型")

        ownership = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.service=litellm", "--format", '{{.Label "com.docker.compose.project"}}'],
            capture_output=True, text=True, timeout=15,
        )
        if ownership.returncode:
            raise ValueError("無法連線 Docker daemon；請確認 Docker 正在執行")
        other_projects = set(ownership.stdout.split()) - {compose["name"]}
        if other_projects:
            print("另有獨立 LiteLLM 執行中；接管前請先依 docs/ai-api-user-manual.md 停止該 gateway。")
            if args.start:
                raise ValueError("尚未接管 host port 4000，取消主 Compose 啟動；既有 gateway 未被停止")
        if args.start:
            # Bind-mounted config contents do not trigger Compose recreation.
            # Load the freshly generated routes before starting the application.
            gateway_start = subprocess.run(
                ["docker", "compose", "up", "-d", "--force-recreate", "litellm"], cwd=REPO_ROOT,
            )
            if gateway_start.returncode:
                return gateway_start.returncode
            return subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=REPO_ROOT).returncode
        print("正式啟動：bash scripts/prepare-ai-stack.sh --start")
        return 0
    except (OSError, ValueError, KeyError, yaml.YAMLError, subprocess.TimeoutExpired):
        # Errors from parsers may embed source text; only our ValueErrors are safe.
        exc = sys.exc_info()[1]
        message = str(exc) if type(exc) is ValueError else "設定讀取失敗，請核對檔案格式、Python 相依套件與 Docker 可用性"
        print(f"預檢查失敗：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
