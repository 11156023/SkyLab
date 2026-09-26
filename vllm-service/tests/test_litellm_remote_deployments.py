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
    root = {"AI_API_API_KEY": "service-secret"}
    campus = {
        **root, "AI_API_BASE_URL": "http://host.docker.internal:4000",
        "LITELLM_RUNTIME_BASE_URL": "http://host.docker.internal:4000",
        "LITELLM_RUNTIME_API_KEY": "service-secret",
        "POSTGRES_DB": "campus", "POSTGRES_USER": "campus",
    }
    gateway = {
        "LITELLM_MASTER_KEY": "master-secret", "LITELLM_SALT_KEY": "salt-secret",
        "VLLM_UPSTREAM_API_KEY": "upstream-secret",
        "DATABASE_URL": "postgresql://litellm:db-secret@127.0.0.1:5433/litellm",
    }
    services = {name: {"environment": copy.deepcopy(campus)} for name in ("backend", "worker", "prestart")}
    services["litellm"] = {"environment": gateway}
    models = [{**local_model(), "deployment": "local", "api_key_env": "VLLM_UPSTREAM_API_KEY"}]
    return root, services, models, {"API_KEY": "upstream-secret"}


def test_preflight_accepts_isolated_credentials():
    validate_environment(*environment_fixture())


@pytest.mark.parametrize("failure", ["root-secret", "backend-secret", "loopback", "same-db", "same-key", "wrong-upstream", "remote-key-missing"])
def test_preflight_rejects_unsafe_or_inconsistent_deployment(failure):
    root, services, models, engine = environment_fixture()
    if failure == "root-secret":
        root["LITELLM_MASTER_KEY"] = "master-secret"
    elif failure == "backend-secret":
        services["worker"]["environment"]["DATABASE_URL"] = "db-secret"
    elif failure == "loopback":
        services["backend"]["environment"]["AI_API_BASE_URL"] = "http://127.0.0.1:4000"
    elif failure == "same-db":
        services["litellm"]["environment"]["DATABASE_URL"] = "postgresql://litellm:db-secret@localhost/campus"
    elif failure == "same-key":
        services["backend"]["environment"]["AI_API_API_KEY"] = "master-secret"
    elif failure == "wrong-upstream":
        engine["API_KEY"] = "wrong-secret"
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


def test_start_refuses_standalone_gateway_without_stopping_it(tmp_path, monkeypatch, capsys):
    import prepare_ai_stack
    root_env, services, _, engine = environment_fixture()
    monkeypatch.setenv("API_KEY", engine["API_KEY"])
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
    monkeypatch.setattr(prepare_ai_stack, "check_upstreams", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["prepare_ai_stack.py", "--start"])
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
