from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.multi_model import load_model_instances, validate_cluster_resources
from model_deployment import upstream_connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_litellm_config import assert_secret_free, load_models, render_config
from prepare_ai_stack import check_upstreams, validate_environment


def local_model() -> dict:
    return {"alias": "local", "served_model_name": "shared-model", "model_name": "model", "api_port": 8103, "gpu_memory_utilization": 0.4}


def remote_model() -> dict:
    return {"alias": "remote", "deployment": "remote", "served_model_name": "shared-model", "api_base": "http://192.0.2.20:8103/v1/", "api_key_env": "REMOTE_LAB_API_KEY", "gpu_memory_utilization": 0.9}


def test_remote_route_does_not_launch_engine_or_allocate_local_gpu(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps([local_model(), remote_model()]))
    env = tmp_path / ".env.API"
    env.write_text("API_KEY=test-upstream\nAPI_HOST=127.0.0.1\n")
    instances = load_model_instances(env, path)
    assert [m.alias for m in instances] == ["local"]
    monkeypatch.setenv("CLUSTER_GPU_UTIL_HARD_LIMIT", "0.95")
    validate_cluster_resources(instances)
    config = render_config(load_models(path), {}, "production")
    assert_secret_free(config)
    params = config["model_list"][1]["litellm_params"]
    assert params["api_base"] == "http://192.0.2.20:8103/v1"
    assert params["api_key"] == "os.environ/REMOTE_LAB_API_KEY"
    assert params["model"] == "hosted_vllm/shared-model"


@pytest.mark.parametrize("update", [
    {"api_base": "http://192.0.2.20:8103"},
    {"api_base": "http://user:password@192.0.2.20/v1"},
    {"api_base": "http://192.0.2.20/v1?key=secret"},
    {"api_base": "http://0.0.0.0:8103/v1"},
    {"api_base": "http://192.0.2.20:99999/v1"},
    {"api_key_env": "LITELLM_MASTER_KEY"},
    {"api_key_env": "os.environ/REMOTE_KEY"},
    {"api_key": "a-secret"},
    {"deployment": "remtoe"},
])
def test_invalid_remote_connection_rejected_without_echoing_secret(update):
    with pytest.raises(ValueError) as error:
        upstream_connection({**remote_model(), **update})
    assert "password" not in str(error.value)
    assert "a-secret" not in str(error.value)


def test_secret_check_requires_entire_env_reference():
    config = {"model_list": [{"litellm_params": {"api_key": "prefix os.environ/REMOTE_KEY secret"}}], "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"}}
    with pytest.raises(ValueError, match="明文 secret"):
        assert_secret_free(config)


def test_bootstrap_can_generate_production_config_before_key_provisioning(tmp_path):
    models_path = tmp_path / "models.json"
    output_path = tmp_path / "config.yaml"
    models_path.write_text(json.dumps([local_model(), remote_model()]))
    service_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("LITELLM_SERVICE_API_KEY", None)
    command = [sys.executable, str(service_root / "tools/generate_litellm_config.py"), "--models", str(models_path), "--output", str(output_path), "--mode", "production"]
    normal = subprocess.run(command, env=env, capture_output=True, text=True)
    assert normal.returncode != 0
    assert not output_path.exists()
    bootstrap = subprocess.run([*command, "--bootstrap"], env=env, capture_output=True, text=True)
    assert bootstrap.returncode == 0
    import yaml
    config = yaml.safe_load(output_path.read_text())
    assert config["general_settings"]["database_url"] == "os.environ/DATABASE_URL"
    assert_secret_free(config)


def environment_fixture():
    root = {"AI_API_API_KEY": "sk-service-secret"}
    campus = {
        **root, "AI_API_BASE_URL": "http://litellm:4000",
        "LITELLM_RUNTIME_BASE_URL": "http://litellm:4000",
        "LITELLM_RUNTIME_API_KEY": "sk-service-secret",
        "POSTGRES_DB": "campus", "POSTGRES_USER": "campus",
    }
    gateway = {
        "LITELLM_MASTER_KEY": "master-secret", "LITELLM_SALT_KEY": "salt-secret",
        "VLLM_UPSTREAM_API_KEY": "upstream-secret",
        "DATABASE_URL": "postgresql://litellm:db-secret@db:5432/litellm",
    }
    services = {name: {"environment": copy.deepcopy(campus)} for name in ("backend", "worker", "prestart")}
    services["litellm"] = {"environment": gateway, "ports": [{"target": 4000, "published": "4100", "host_ip": "127.0.0.1"}]}
    models = [{**local_model(), "deployment": "local", "api_key_env": "VLLM_UPSTREAM_API_KEY", "_legacy_alias_names": []}]
    return root, services, models, {"API_KEY": "upstream-secret", "API_HOST": "0.0.0.0"}


def test_preflight_accepts_isolated_credentials():
    validate_environment(*environment_fixture())


@pytest.mark.parametrize("failure", [
    "root-secret", "backend-secret", "loopback", "host-gateway", "same-db", "same-key", "wrong-upstream",
    "remote-key-missing", "pgbouncer", "db-loopback", "service-key-format", "engine-loopback",
])
def test_preflight_rejects_unsafe_or_inconsistent_deployment(failure):
    root, services, models, engine = environment_fixture()
    if failure == "root-secret":
        root["LITELLM_MASTER_KEY"] = "master-secret"
    elif failure == "backend-secret":
        services["worker"]["environment"]["DATABASE_URL"] = "db-secret"
    elif failure == "loopback":
        services["backend"]["environment"]["AI_API_BASE_URL"] = "http://127.0.0.1:4000"
    elif failure == "host-gateway":
        services["worker"]["environment"]["LITELLM_RUNTIME_BASE_URL"] = "http://host.docker.internal:4000"
    elif failure == "same-db":
        services["litellm"]["environment"]["DATABASE_URL"] = "postgresql://litellm:db-secret@db:5432/campus"
    elif failure == "same-key":
        services["backend"]["environment"]["AI_API_API_KEY"] = "master-secret"
    elif failure == "wrong-upstream":
        engine["API_KEY"] = "wrong-secret"
    elif failure == "pgbouncer":
        services["litellm"]["environment"]["DATABASE_URL"] = "postgresql://litellm:db-secret@pgbouncer:5432/litellm"
    elif failure == "db-loopback":
        services["litellm"]["environment"]["DATABASE_URL"] = "postgresql://litellm:db-secret@127.0.0.1:5433/litellm"
    elif failure == "service-key-format":
        for name in ("backend", "worker", "prestart"):
            services[name]["environment"].update(AI_API_API_KEY="service-secret", LITELLM_RUNTIME_API_KEY="service-secret")
    elif failure == "engine-loopback":
        engine["API_HOST"] = "127.0.0.1"
    else:
        models.append(remote_model())
    with pytest.raises(ValueError) as error:
        validate_environment(root, services, models, engine)
    assert all(secret not in str(error.value) for secret in ("master-secret", "db-secret", "service-secret", "upstream-secret"))


def test_upstream_check_validates_served_name_without_inference(monkeypatch):
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"data":[{"id":"shared-model"}]}'

    class Opener:
        def open(self, request, timeout):
            requested.append(request)
            return Response()

    import prepare_ai_stack
    monkeypatch.setattr(prepare_ai_stack, "build_opener", lambda *args: Opener())
    model = {**remote_model(), "api_base": "http://192.0.2.20:8103/v1"}
    check_upstreams([model], {"REMOTE_LAB_API_KEY": "remote-secret"})
    assert requested[0].full_url.endswith("/v1/models")
    assert requested[0].get_method() == "GET"
    assert requested[0].get_header("Authorization") == "Bearer remote-secret"


def _start_workspace(tmp_path, monkeypatch):
    import prepare_ai_stack
    root_env, services, _, engine = environment_fixture()
    monkeypatch.setenv("API_KEY", engine["API_KEY"])
    monkeypatch.setenv("API_HOST", engine["API_HOST"])
    service_root = tmp_path / "vllm-service"
    gateway_root = service_root / "litellm"
    gateway_root.mkdir(parents=True)
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in root_env.items()))
    (gateway_root / ".env").write_text("# isolated gateway\n")
    (service_root / ".env.API").write_text(f"API_KEY={engine['API_KEY']}\n")
    models_path = service_root / "models.json"
    models_path.write_text(json.dumps([local_model()]))
    template_path = gateway_root / "config.template.yaml"
    template_path.write_text("{}\n")
    monkeypatch.setattr(prepare_ai_stack, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(prepare_ai_stack, "PROJECT_ROOT", service_root)
    monkeypatch.setattr(prepare_ai_stack, "DEFAULT_MODELS", models_path)
    monkeypatch.setattr(prepare_ai_stack, "DEFAULT_TEMPLATE", template_path)
    monkeypatch.setattr(prepare_ai_stack, "DEFAULT_OUTPUT", gateway_root / "config.yaml")
    monkeypatch.setattr(prepare_ai_stack, "VLLM_TARGETS_FILE", tmp_path / "monitoring/prometheus/targets/vllm.json")
    monkeypatch.setattr(prepare_ai_stack, "check_upstreams", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["prepare_ai_stack.py", "--start"])
    return prepare_ai_stack, services


def test_start_refuses_standalone_gateway_without_stopping_it(tmp_path, monkeypatch, capsys):
    prepare_ai_stack, services = _start_workspace(tmp_path, monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "compose", "config"]:
            output = json.dumps({"name": "campus", "services": services})
        elif command[:2] == ["docker", "ps"]:
            output = "campus-litellm\n"
        else:
            pytest.fail("Preflight must not stop or start containers during an ownership conflict")
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(prepare_ai_stack.subprocess, "run", run)
    assert prepare_ai_stack.main() == 1
    assert len(calls) == 2
    assert "既有 gateway 未被停止" in capsys.readouterr().err


def test_start_bootstraps_database_and_service_key_before_the_application(tmp_path, monkeypatch):
    prepare_ai_stack, services = _start_workspace(tmp_path, monkeypatch)
    events = []

    def run(command, **kwargs):
        if command[:3] == ["docker", "compose", "config"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"name": "campus", "services": services}))
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[1:2] == ["scripts/init-litellm-db.sh"]:
            # The DB password travels only through the environment, never argv.
            assert kwargs["env"]["LITELLM_DB_PASSWORD"] == "db-secret"
            assert "db-secret" not in " ".join(command)
            command = ["bash", *command[1:]]
        if "exec" in command:
            command = ["db tcp ready"]
        events.append(" ".join(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(prepare_ai_stack.subprocess, "run", run)
    monkeypatch.setattr(prepare_ai_stack, "wait_for_gateway", lambda base: events.append(f"ready {base}"))
    monkeypatch.setattr(
        prepare_ai_stack, "ensure_service_key",
        lambda base, master, key, models: events.append(f"key {base} {master} {key} {models}"),
    )
    assert prepare_ai_stack.main() == 0
    targets = json.loads((tmp_path / "monitoring/prometheus/targets/vllm.json").read_text())
    assert targets[0]["targets"] == ["host.docker.internal:8103"]
    assert events == [
        "docker compose up -d --wait db",
        "db tcp ready",
        "bash scripts/init-litellm-db.sh --database litellm --role litellm",
        "docker compose up -d --force-recreate litellm",
        "ready http://127.0.0.1:4100",
        "key http://127.0.0.1:4100 master-secret sk-service-secret ['local']",
        "docker compose up -d --build",
    ]


def test_external_database_is_not_created(monkeypatch, capsys):
    import prepare_ai_stack
    monkeypatch.setattr(prepare_ai_stack.subprocess, "run", lambda *a, **k: pytest.fail("must not touch Compose db"))
    prepare_ai_stack.ensure_litellm_database("postgresql://litellm:pw@192.0.2.30:5432/litellm")
    assert "略過自動建立" in capsys.readouterr().out


def test_production_config_reaches_local_engines_through_docker_host():
    from generate_litellm_config import gateway_api_base
    local = {"deployment": "local", "api_base": "http://127.0.0.1:8103/v1"}
    remote = {"deployment": "remote", "api_base": "http://127.0.0.1:8103/v1"}
    assert gateway_api_base(local, "production") == "http://host.docker.internal:8103/v1"
    assert gateway_api_base(local, "integration") == "http://127.0.0.1:8103/v1"
    assert gateway_api_base(remote, "production") == "http://127.0.0.1:8103/v1"


def test_vllm_scrape_targets_follow_the_gateway_view_and_dedupe_upstreams():
    from generate_litellm_config import vllm_scrape_targets
    models = [
        {"alias": "local-a", "deployment": "local", "api_base": "http://127.0.0.1:8103/v1"},
        {"alias": "dgx-a", "deployment": "remote", "api_base": "http://192.0.2.20:8103/v1"},
        {"alias": "dgx-b", "deployment": "remote", "api_base": "http://192.0.2.20:8103/v1"},
        {"alias": "proxied", "deployment": "remote", "api_base": "https://llm.example.edu/lab/v1"},
    ]
    groups = {tuple(g["targets"]): g["labels"] for g in vllm_scrape_targets(models)}
    assert groups[("host.docker.internal:8103",)] == {
        "__scheme__": "http", "__metrics_path__": "/metrics", "deployment": "local", "skylab_models": "local-a",
    }
    assert groups[("192.0.2.20:8103",)]["skylab_models"] == "dgx-a,dgx-b"
    assert groups[("llm.example.edu",)]["__scheme__"] == "https"
    assert groups[("llm.example.edu",)]["__metrics_path__"] == "/lab/metrics"
    assert len(groups) == 3


def test_models_json_with_a_bom_is_accepted(tmp_path):
    path = tmp_path / "models.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([remote_model()]).encode())
    assert load_models(path)[0]["alias"] == "remote"


def test_init_env_fills_placeholders_without_touching_real_values(tmp_path):
    import prepare_ai_stack
    root_path = tmp_path / ".env"
    root_path.write_bytes(
        b"# Campus\r\nSECRET_KEY=keep-me\r\nAI_API_BASE_URL=http://host.docker.internal:4000\r\n"
        b"AI_API_API_KEY=ai-api-secret-key-change-me\r\nLITELLM_RUNTIME_BASE_URL=http://host.docker.internal:4000\r\n"
        b"# LITELLM_RUNTIME_API_KEY=\r\n"
    )
    gateway_path = tmp_path / "litellm" / ".env"  # created from the tracked template
    engine_path = tmp_path / ".env.API"
    engine_path.write_text("API_KEY=engine-secret\n")

    assert prepare_ai_stack.init_env(root_path, gateway_path, engine_path) == 0
    from dotenv import dotenv_values
    root, gateway = dotenv_values(root_path), dotenv_values(gateway_path)
    assert root["SECRET_KEY"] == "keep-me"
    assert root["AI_API_BASE_URL"] == root["LITELLM_RUNTIME_BASE_URL"] == "http://litellm:4000"
    assert root["AI_API_API_KEY"].startswith("sk-") and root["LITELLM_RUNTIME_API_KEY"] == root["AI_API_API_KEY"]
    assert b"\r\nLITELLM_RUNTIME_API_KEY=sk-" in root_path.read_bytes()  # template line reused, CRLF kept
    assert gateway["LITELLM_MASTER_KEY"].startswith("sk-")
    assert gateway["VLLM_UPSTREAM_API_KEY"] == "engine-secret"
    database = prepare_ai_stack.urlsplit(gateway["DATABASE_URL"])
    assert (database.hostname, database.port, database.username, database.path) == ("db", 5432, "litellm", "/litellm")
    assert len({gateway["LITELLM_MASTER_KEY"], gateway["LITELLM_SALT_KEY"], database.password, root["AI_API_API_KEY"]}) == 4

    before = (root_path.read_bytes(), gateway_path.read_bytes())
    assert prepare_ai_stack.init_env(root_path, gateway_path, engine_path) == 0
    assert (root_path.read_bytes(), gateway_path.read_bytes()) == before  # idempotent: salt never rotates


def test_init_env_moves_former_host_port_database_to_compose_network_and_reports_missing_upstream(tmp_path, capsys):
    import prepare_ai_stack
    root_path = tmp_path / ".env"
    root_path.write_text("AI_API_API_KEY=sk-existing\nAI_API_BASE_URL=http://litellm:4000\nLITELLM_RUNTIME_BASE_URL=http://litellm:4000\nLITELLM_RUNTIME_API_KEY=sk-existing\n")
    gateway_path = tmp_path / "gateway.env"
    gateway_path.write_text(
        "LITELLM_MASTER_KEY=sk-master\nLITELLM_SALT_KEY=salt\n"
        "DATABASE_URL=postgresql://litellm:p%40ss@127.0.0.1:5433/litellm\n"
        "VLLM_UPSTREAM_API_KEY=replace-with-the-vllm-api-key-from-.env.API\n"
    )
    assert prepare_ai_stack.init_env(root_path, gateway_path, tmp_path / "missing.env") == 1
    assert "DATABASE_URL=postgresql://litellm:p%40ss@db:5432/litellm" in gateway_path.read_text()
    assert "sk-existing" in root_path.read_text()
    out = capsys.readouterr().out
    assert "VLLM_UPSTREAM_API_KEY" in out and "p%40ss" not in out and "sk-master" not in out


def test_init_env_names_the_unwritable_file_and_writes_nothing(tmp_path, monkeypatch, capsys):
    import prepare_ai_stack
    root_path = tmp_path / ".env"
    root_path.write_text("SECRET_KEY=keep-me\n")
    gateway_path = tmp_path / "gateway.env"
    gateway_path.write_text("LITELLM_MASTER_KEY=replace-with-master\n")
    before = (root_path.read_bytes(), gateway_path.read_bytes())
    # The runner can read /opt/skylab but only the gateway file is writable.
    monkeypatch.setattr(prepare_ai_stack.os, "access", lambda path, mode: Path(path) != root_path)
    monkeypatch.setattr(sys, "argv", [
        "prepare_ai_stack.py", "--init-env", "--root-env", str(root_path),
        "--gateway-env", str(gateway_path), "--engine-env", str(tmp_path / "missing.env"),
    ])
    assert prepare_ai_stack.main() == 1
    assert (root_path.read_bytes(), gateway_path.read_bytes()) == before
    err = capsys.readouterr().err
    assert str(root_path) in err and "無法寫入" in err and "keep-me" not in err


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PermissionError(13, "Permission denied", "/opt/skylab/.env"), "沒有權限存取 /opt/skylab/.env"),
        (FileNotFoundError(2, "No such file or directory", "/opt/x"), "無法存取 /opt/x：No such file or directory"),
        (UnicodeDecodeError("utf-8", b"\xff secret", 0, 1, "invalid start byte"), "不是 UTF-8 編碼"),
    ],
)
def test_os_level_failures_explain_the_cause_without_file_contents(monkeypatch, capsys, error, expected):
    import prepare_ai_stack

    def fail(*args):
        raise error

    monkeypatch.setattr(prepare_ai_stack, "init_env", fail)
    monkeypatch.setattr(sys, "argv", ["prepare_ai_stack.py", "--init-env"])
    assert prepare_ai_stack.main() == 1
    err = capsys.readouterr().err
    assert expected in err and "secret" not in err


@pytest.mark.parametrize("update_status, expected", [(200, ["/key/update"]), (404, ["/key/update", "/key/generate"])])
def test_service_key_is_synced_or_created(monkeypatch, update_status, expected):
    import prepare_ai_stack
    calls = []

    def call(base, path, master=None, payload=None):
        calls.append((path, master, payload))
        return (update_status if path == "/key/update" else 200), {}

    monkeypatch.setattr(prepare_ai_stack, "_gateway_call", call)
    prepare_ai_stack.ensure_service_key("http://127.0.0.1:4000", "sk-master", "sk-service", ["a", "b"])
    assert [path for path, _, _ in calls] == expected
    assert all(master == "sk-master" and payload["key"] == "sk-service" and payload["models"] == ["a", "b"] for _, master, payload in calls)
    if len(calls) == 2:
        assert calls[1][2]["key_alias"] == "campus-ai-api-service"


def test_service_key_errors_do_not_echo_secrets(monkeypatch):
    import prepare_ai_stack
    monkeypatch.setattr(prepare_ai_stack, "_gateway_call", lambda *a, **k: (401, None))
    with pytest.raises(ValueError) as error:
        prepare_ai_stack.ensure_service_key("http://127.0.0.1:4000", "sk-master", "sk-service", ["a"])
    assert "sk-master" not in str(error.value) and "sk-service" not in str(error.value)


def test_gateway_wait_requires_connected_database(monkeypatch):
    import prepare_ai_stack
    responses = iter([(0, None), (200, {"db": "Not connected"}), (200, {"db": "connected"})])
    monkeypatch.setattr(prepare_ai_stack, "_gateway_call", lambda *a, **k: next(responses))
    prepare_ai_stack.wait_for_gateway("http://127.0.0.1:4000", timeout=60, interval=0)
    monkeypatch.setattr(prepare_ai_stack, "_gateway_call", lambda *a, **k: (200, {"db": "Not connected"}))
    with pytest.raises(ValueError, match="未在"):
        prepare_ai_stack.wait_for_gateway("http://127.0.0.1:4000", timeout=0, interval=0)
