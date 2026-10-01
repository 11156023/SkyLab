#!/usr/bin/env python3
"""Validate secret boundaries, generate the LiteLLM config and bootstrap the gateway."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

import yaml
from dotenv import dotenv_values
from generate_litellm_config import (
    DEFAULT_MODELS,
    DEFAULT_OUTPUT,
    DEFAULT_TEMPLATE,
    PROJECT_ROOT,
    load_models,
    load_template,
    render_config,
    validate_generated_config,
    vllm_scrape_targets,
)

REPO_ROOT = PROJECT_ROOT.parent
# Prometheus (monitoring profile) file_sd for vLLM /metrics; read-only mount of
# monitoring/prometheus, matched by a glob so a missing file means no targets.
VLLM_TARGETS_FILE = REPO_ROOT / "monitoring/prometheus/targets/vllm.json"
PRIVILEGED_KEYS = {"LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "DATABASE_URL", "VLLM_UPSTREAM_API_KEY"}
PLACEHOLDER_PREFIXES = ("replace-with-", "ai-api-secret-")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}
# LiteLLM and backend/worker share the root Compose `skylab` network.
GATEWAY_INTERNAL_URL = "http://litellm:4000"
# LiteLLM connects to PostgreSQL directly; PgBouncer is for the Campus app only.
COMPOSE_DB_HOST = "db"
COMPOSE_DB_PORT = 5432
SERVICE_KEY_ALIAS = "campus-ai-api-service"
GATEWAY_READY_TIMEOUT = 300
DATABASE_READY_TIMEOUT = 90


def is_placeholder(value: object) -> bool:
    return not isinstance(value, str) or not value.strip() or any(
        marker in value for marker in PLACEHOLDER_PREFIXES
    )


def require_secret(values: dict, name: str) -> str:
    value = values.get(name)
    if is_placeholder(value):
        raise ValueError(f"請設定有效的 {name}（不會顯示內容；可先執行 prepare-ai-stack.sh --init-env）")
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
            raise ValueError(
                "Campus service key 必須與 LiteLLM master / vLLM upstream key 分開；"
                "舊部署的 AI_API_API_KEY 若沿用 vLLM API_KEY，執行 prepare-ai-stack.sh --init-env 會換成新的受限 key"
            )
        if not service_key.startswith("sk-"):
            raise ValueError("AI_API_API_KEY 必須是 LiteLLM virtual key 格式（sk- 開頭）；可用 --init-env 產生")
        if env.get("LITELLM_SERVICE_API_KEY") and env["LITELLM_SERVICE_API_KEY"] != service_key:
            raise ValueError("LITELLM_SERVICE_API_KEY 必須與 AI_API_API_KEY 使用同一把受限 key")
        runtime_key = env.get("LITELLM_RUNTIME_API_KEY")
        if runtime_key and runtime_key != service_key:
            raise ValueError("LITELLM_RUNTIME_API_KEY 必須與 AI_API_API_KEY 使用同一把受限 key")
        for field in ("AI_API_BASE_URL", "LITELLM_RUNTIME_BASE_URL"):
            url = urlsplit(env.get(field, ""))
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError(f"{field} 必須設定有效的 gateway URL")
            if url.hostname in LOOPBACK_HOSTS | {"host.docker.internal"}:
                raise ValueError(f"{field} 請改用 {GATEWAY_INTERNAL_URL}（LiteLLM 與 backend 同在 Compose 內網）")
            if url.path not in {"", "/"} or url.query or url.fragment or url.username or url.password:
                raise ValueError(f"{field} 填 gateway 根位址，不含 /v1 或帳密")
    if "LITELLM_SERVICE_API_KEY" in gateway or "AI_API_API_KEY" in gateway or "LITELLM_RUNTIME_API_KEY" in gateway:
        raise ValueError("Campus 的受限 service key 不應注入 LiteLLM 容器")
    database = urlsplit(gateway["DATABASE_URL"])
    campus = services["backend"]["environment"]
    if database.scheme not in {"postgresql", "postgres"} or not database.hostname or not database.path.strip("/"):
        raise ValueError("DATABASE_URL 必須指向專用 PostgreSQL 資料庫")
    if database.hostname == "pgbouncer":
        raise ValueError(f"LiteLLM 須直連 {COMPOSE_DB_HOST}:{COMPOSE_DB_PORT}，不可經 PgBouncer")
    if database.hostname in LOOPBACK_HOSTS:
        raise ValueError(f"LiteLLM 在 Compose 內網，DATABASE_URL 的 loopback 指向容器自己；同機資料庫請用 {COMPOSE_DB_HOST}:{COMPOSE_DB_PORT}")
    if unquote(database.path.strip("/")) == campus.get("POSTGRES_DB") or unquote(database.username or "") == campus.get("POSTGRES_USER"):
        raise ValueError("LiteLLM 必須使用與 Campus 不同的資料庫名稱及帳號")
    if any(model["deployment"] == "local" for model in models) and engine_env.get("API_HOST", "127.0.0.1") in LOOPBACK_HOSTS - {"0.0.0.0", "::"}:
        raise ValueError(".env.API 的 API_HOST 只綁 loopback，Compose 內的 LiteLLM 連不到本機 vLLM；請改為 0.0.0.0 並以防火牆限制引擎埠")
    for model in models:
        if "apikeys" in model:
            upstream_key = model["apikeys"]
            if is_placeholder(upstream_key):
                raise ValueError(f"模型 {model['alias']} 的 apikeys 尚未設定有效金鑰")
            if upstream_key in {master, campus["AI_API_API_KEY"]}:
                raise ValueError(f"模型 {model['alias']} 不可使用 LiteLLM master / Campus service key 作為上游金鑰")
            continue
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
        upstream_key = model.get("apikeys")
        if upstream_key is None:
            upstream_key = gateway_env[model["api_key_env"]]
        request = Request(
            model["api_base"] + "/models",
            headers={"Authorization": f"Bearer {upstream_key}"},
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


def write_vllm_targets(models: list[dict]) -> None:
    """Refresh the Prometheus vLLM targets; Prometheus picks up the change by itself."""
    VLLM_TARGETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    VLLM_TARGETS_FILE.write_text(
        json.dumps(vllm_scrape_targets(models), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已更新 Prometheus vLLM 抓取目標：monitoring/prometheus/targets/vllm.json（{len(models)} 個模型）")


# ---------------------------------------------------------------------------
# --init-env: fill missing secrets in place, never replacing a real value.
# ---------------------------------------------------------------------------

def _write_env_values(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = dict(updates)
    # Replace active assignments first, then a commented template line
    # such as "# LITELLM_RUNTIME_API_KEY=", and append whatever is left.
    for pattern in (r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", r"\s*#\s*([A-Za-z_][A-Za-z0-9_]*)=\s*(?:#.*)?$"):
        for index, line in enumerate(lines):
            match = re.match(pattern, line)
            if match and match.group(1) in pending:
                lines[index] = f"{match.group(1)}={pending.pop(match.group(1))}"
    lines.extend(f"{name}={value}" for name, value in pending.items())
    path.write_text(newline.join(lines) + newline, encoding="utf-8")


def _compose_database_url(url: str) -> str:
    """Point a loopback/host-port URL at the Compose db service, keeping credentials."""
    parts = urlsplit(url)
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@{COMPOSE_DB_HOST}:{COMPOSE_DB_PORT}" if userinfo else f"{COMPOSE_DB_HOST}:{COMPOSE_DB_PORT}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def plan_env_updates(root_env: dict, gateway_env: dict, engine_env: dict) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return (root updates, gateway updates, names still requiring an operator)."""
    gateway: dict[str, str] = {}
    root: dict[str, str] = {}
    manual: list[str] = []

    if is_placeholder(gateway_env.get("LITELLM_MASTER_KEY")):
        gateway["LITELLM_MASTER_KEY"] = "sk-" + secrets.token_urlsafe(32)
    if is_placeholder(gateway_env.get("LITELLM_SALT_KEY")):
        gateway["LITELLM_SALT_KEY"] = secrets.token_urlsafe(48)
    database_url = gateway_env.get("DATABASE_URL")
    if is_placeholder(database_url):
        gateway["DATABASE_URL"] = (
            f"postgresql://litellm:{secrets.token_hex(24)}@{COMPOSE_DB_HOST}:{COMPOSE_DB_PORT}/litellm"
        )
    elif urlsplit(database_url).hostname in LOOPBACK_HOSTS:
        # Former host-network default (127.0.0.1:5433): same server, internal address.
        gateway["DATABASE_URL"] = _compose_database_url(database_url)
    if is_placeholder(gateway_env.get("VLLM_UPSTREAM_API_KEY")):
        if not is_placeholder(engine_env.get("API_KEY")):
            gateway["VLLM_UPSTREAM_API_KEY"] = engine_env["API_KEY"]
        else:
            manual.append("VLLM_UPSTREAM_API_KEY（DGX／推論主機 vLLM 的 API_KEY）")

    service_key = root_env.get("AI_API_API_KEY")
    # Before LiteLLM the backend called vLLM directly with its API_KEY. Such a
    # leftover is not a restricted virtual key and collides with the upstream
    # key, so it is replaced like a placeholder; --start registers the new one.
    reserved = {value for value in (*{**gateway_env, **gateway}.values(), engine_env.get("API_KEY")) if value}
    if is_placeholder(service_key) or not service_key.startswith("sk-") or service_key in reserved:
        service_key = "sk-" + secrets.token_urlsafe(32)
        root["AI_API_API_KEY"] = service_key
    # Both must be the same restricted key as AI_API_API_KEY (see validate_environment).
    if root_env.get("LITELLM_RUNTIME_API_KEY") != service_key:
        root["LITELLM_RUNTIME_API_KEY"] = service_key
    if "LITELLM_SERVICE_API_KEY" in root_env and root_env["LITELLM_SERVICE_API_KEY"] != service_key:
        root["LITELLM_SERVICE_API_KEY"] = service_key
    for field in ("AI_API_BASE_URL", "LITELLM_RUNTIME_BASE_URL"):
        hostname = urlsplit(root_env.get(field) or "").hostname
        if not hostname or hostname in LOOPBACK_HOSTS | {"host.docker.internal"}:
            root[field] = GATEWAY_INTERNAL_URL
    return root, gateway, manual


def _require_writable(path: Path) -> None:
    """Fail before touching anything, naming the path and the account that lacks access."""
    target = path
    while not target.exists() and target.parent != target:
        target = target.parent
    if os.access(target, os.W_OK | (os.X_OK if target.is_dir() else 0)):
        return
    user = getpass.getuser()
    raise ValueError(
        f"目前執行身分 {user} 無法寫入 {target}（--init-env 需要把補齊的金鑰寫回 {path}）；"
        f"請在該主機以 sudo chown {user} {target} 或調整權限後重試"
    )


def init_env(root_path: Path, gateway_path: Path, engine_path: Path) -> int:
    if not root_path.is_file():
        raise ValueError(f"缺少主設定 {root_path}，請先由 .env.example 建立並填入 Campus 必要參數")
    if not gateway_path.is_file():
        _require_writable(gateway_path)
        gateway_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / "litellm/.env.example", gateway_path)
        os.chmod(gateway_path, 0o600)
        print(f"已由範本建立 {gateway_path}")
    engine_env = dotenv_values(engine_path) if engine_path.is_file() else {}
    root_env = dotenv_values(root_path)
    root, gateway, manual = plan_env_updates(root_env, dotenv_values(gateway_path), engine_env)
    if "AI_API_API_KEY" in root and not is_placeholder(root_env.get("AI_API_API_KEY")):
        print("原 AI_API_API_KEY 不是 sk- virtual key 或與 LiteLLM／vLLM 金鑰相同（舊部署殘留），將換成新的受限 service key")
    # Check both files first so a permission problem never leaves only one updated.
    for path, updates in ((root_path, root), (gateway_path, gateway)):
        if updates:
            _require_writable(path)
    for path, updates in ((root_path, root), (gateway_path, gateway)):
        if updates:
            _write_env_values(path, updates)
            print(f"已更新 {path}：{', '.join(updates)}（值不顯示）")
    if not root and not gateway:
        print("金鑰與位址均已設定，未修改任何檔案")
    for name in manual:
        print(f"仍需手動填入 {gateway_path} 的 {name}")
    return 1 if manual else 0


# ---------------------------------------------------------------------------
# --start: database, gateway readiness and the Campus service Virtual Key.
# ---------------------------------------------------------------------------

def ensure_litellm_database(database_url: str) -> None:
    """Create or align the dedicated role/database on the Compose PostgreSQL."""
    database = urlsplit(database_url)
    if database.hostname != COMPOSE_DB_HOST:
        print("DATABASE_URL 指向外部資料庫，略過自動建立（由該資料庫管理者維護）")
        return
    started = subprocess.run(["docker", "compose", "up", "-d", "--wait", COMPOSE_DB_HOST], cwd=REPO_ROOT)
    if started.returncode:
        raise ValueError("主 Compose 資料庫未能啟動")
    # A fresh volume first runs a socket-only init server that already passes
    # the socket-based healthcheck and then shuts down; wait for TCP instead.
    deadline = time.monotonic() + DATABASE_READY_TIMEOUT
    while subprocess.run(
        ["docker", "compose", "exec", "-T", COMPOSE_DB_HOST, "sh", "-c",
         'pg_isready -q -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER"'],
        cwd=REPO_ROOT, capture_output=True,
    ).returncode:
        if time.monotonic() >= deadline:
            raise ValueError(f"主 Compose 資料庫未在 {DATABASE_READY_TIMEOUT} 秒內接受 TCP 連線")
        time.sleep(2)
    result = subprocess.run(
        [
            shutil.which("bash") or "bash", "scripts/init-litellm-db.sh",
            "--database", unquote(database.path.strip("/")),
            "--role", unquote(database.username or ""),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "LITELLM_DB_PASSWORD": unquote(database.password or "")},
    )
    if result.returncode:
        raise ValueError("LiteLLM 專用資料庫建立／驗證失敗")


def gateway_host_url(litellm_service: dict) -> str:
    for port in litellm_service.get("ports", []):
        if int(port.get("target", 0)) == 4000 and port.get("published"):
            return f"http://127.0.0.1:{port['published']}"
    return "http://127.0.0.1:4000"


def _gateway_call(base: str, path: str, master: str | None = None, payload: dict | None = None) -> tuple[int, dict | None]:
    headers = {"Content-Type": "application/json"}
    if master:
        headers["Authorization"] = f"Bearer {master}"
    request = Request(
        base + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, None
    except (URLError, OSError, ValueError):
        return 0, None


def wait_for_gateway(base: str, timeout: float = GATEWAY_READY_TIMEOUT, interval: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while True:
        status, body = _gateway_call(base, "/health/readiness")
        if status == 200 and isinstance(body, dict) and body.get("db") == "connected":
            print("LiteLLM 已就緒（資料庫已連線）")
            return
        if time.monotonic() >= deadline:
            raise ValueError(
                f"LiteLLM 未在 {int(timeout)} 秒內就緒（資料庫未連線或 migration 未完成）；"
                "請查看 docker compose logs litellm"
            )
        time.sleep(interval)


def ensure_service_key(base: str, master: str, service_key: str, models: list[str]) -> None:
    """Register the root .env service key in LiteLLM, or sync its model allowlist."""
    status, _ = _gateway_call(base, "/key/update", master, {"key": service_key, "models": models})
    if status == 200:
        print(f"Campus service key 已存在，模型白名單已同步（{len(models)} 個）")
        return
    if status not in {400, 404}:
        raise ValueError(f"LiteLLM /key/update 回傳 HTTP {status}，請核對 master key 與 gateway 狀態")
    status, _ = _gateway_call(
        base, "/key/generate", master,
        {"key": service_key, "models": models, "key_alias": SERVICE_KEY_ALIAS},
    )
    if status != 200:
        raise ValueError(
            f"LiteLLM /key/generate 回傳 HTTP {status}；若已有別把 key 使用別名 {SERVICE_KEY_ALIAS}，"
            "請把那把 key 填入 AI_API_API_KEY 或先在 LiteLLM 撤銷"
        )
    print(f"已核發 Campus service key（別名 {SERVICE_KEY_ALIAS}，模型 {len(models)} 個）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="驗證既有 config.yaml，不改寫檔案")
    parser.add_argument("--check-upstreams", action="store_true", help="逐一查詢上游 /v1/models（不產生推論）")
    parser.add_argument("--start", action="store_true", help="驗證設定後建立 LiteLLM 資料庫、核發 service key 並啟動主 Compose；不會停止獨立 gateway")
    parser.add_argument("--init-env", action="store_true", help="補齊兩份 .env 中缺少或仍為範例值的金鑰與位址後結束（不覆寫既有真實值）")
    parser.add_argument("--root-env", type=Path, default=REPO_ROOT / ".env", help="--init-env 的主 .env 路徑")
    parser.add_argument("--gateway-env", type=Path, default=PROJECT_ROOT / "litellm/.env", help="--init-env 的 LiteLLM .env 路徑")
    parser.add_argument("--engine-env", type=Path, default=PROJECT_ROOT / ".env.API", help="--init-env 讀取本機 vLLM API_KEY 的路徑")
    args = parser.parse_args()
    if args.check_only and args.start:
        parser.error("--check-only 與 --start 不可同時使用")
    if args.init_env and (args.check_only or args.start or args.check_upstreams):
        parser.error("--init-env 需單獨執行")

    try:
        if args.init_env:
            return init_env(args.root_env, args.gateway_env, args.engine_env)
        for path in (REPO_ROOT / ".env", PROJECT_ROOT / "litellm/.env"):
            if not path.is_file():
                raise ValueError(f"缺少 {path.relative_to(REPO_ROOT)}，請先執行 prepare-ai-stack.sh --init-env")
        models = load_models(DEFAULT_MODELS)
        config = render_config(models, load_template(DEFAULT_TEMPLATE), "production")
        validate_generated_config(config)
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
        if args.check_upstreams:
            check_upstreams(models, services["litellm"]["environment"])
        if DEFAULT_OUTPUT.exists() and not DEFAULT_OUTPUT.is_file():
            raise ValueError("litellm/config.yaml 必須為檔案，請先移除誤建的空目錄")
        if args.check_only:
            if not DEFAULT_OUTPUT.is_file() or yaml.safe_load(DEFAULT_OUTPUT.read_text()) != config:
                raise ValueError("config.yaml 缺少或與模型／template 不一致；請執行 prepare-ai-stack.sh 重新產生")
        else:
            DEFAULT_OUTPUT.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            print("已產生 production 設定：vllm-service/litellm/config.yaml")
            write_vllm_targets(models)
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
            gateway_env = services["litellm"]["environment"]
            ensure_litellm_database(gateway_env["DATABASE_URL"])
            # Bind-mounted config contents do not trigger Compose recreation.
            # Load the freshly generated routes before starting the application.
            gateway_start = subprocess.run(
                ["docker", "compose", "up", "-d", "--force-recreate", "litellm"], cwd=REPO_ROOT,
            )
            if gateway_start.returncode:
                return gateway_start.returncode
            base = gateway_host_url(services["litellm"])
            wait_for_gateway(base)
            ensure_service_key(
                base, gateway_env["LITELLM_MASTER_KEY"],
                services["backend"]["environment"]["AI_API_API_KEY"],
                [name for model in models for name in (model["alias"], *model["_legacy_alias_names"])],
            )
            return subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=REPO_ROOT).returncode
        print("正式啟動：bash scripts/prepare-ai-stack.sh --start")
        return 0
    except (OSError, ValueError, KeyError, yaml.YAMLError, subprocess.TimeoutExpired):
        # Errors from parsers may embed source text; only our ValueErrors are safe.
        # Paths and OS error text are safe to show; file contents never are.
        exc = sys.exc_info()[1]
        if type(exc) is ValueError:
            message = str(exc)
        elif isinstance(exc, PermissionError):
            message = f"執行身分 {getpass.getuser()} 沒有權限存取 {exc.filename or '設定檔'}，請核對擁有者與權限"
        elif isinstance(exc, OSError) and exc.filename:
            message = f"無法存取 {exc.filename}：{exc.strerror or type(exc).__name__}"
        elif isinstance(exc, UnicodeDecodeError):
            message = "設定檔不是 UTF-8 編碼，請轉存為 UTF-8 後重試"
        else:
            message = f"設定讀取失敗（{type(exc).__name__}），請核對檔案格式、Python 相依套件與 Docker 可用性"
        print(f"預檢查失敗：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
