from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from config.multi_model import build_gateway_routes, load_model_instances
import main as launcher_main


def _write_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "API_HOST=127.0.0.1",
                "API_KEY=test-key",
                "HF_CACHE_DIR=/tmp/nonexistent-hf-cache",
                "TRUST_REMOTE_CODE=false",
                "ENABLE_PREFIX_CACHING=false",
                "ALLOWED_LOCAL_MEDIA_PATH=",
            ]
        ),
        encoding="utf-8",
    )


def test_build_gateway_routes_loads_admission_and_capabilities(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    models_path = tmp_path / "models.json"
    _write_env(env_path)
    models_path.write_text(
        json.dumps(
            [
                {
                    "alias": "qwen",
                    "served_model_name": "Qwen/Qwen3-14B-FP8",
                    "model_name": "./AImodels/Qwen3-14B-FP8",
                    "api_port": 8104,
                    "max_num_seqs": 24,
                    "scheduling_policy": "priority",
                    "mamba_ssm_cache_dtype": "float32",
                    "enable_chunked_prefill": True,
                    "long_prefill_token_threshold": 4096,
                    "gateway_max_inflight": 6,
                    "gateway_queue_timeout": 12.5,
                    "capabilities": {
                        "reasoning": True,
                        "priority_scheduling": True,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    instances = load_model_instances(base_env_file=env_path, models_json_file=models_path)
    routes = build_gateway_routes(instances, default_max_inflight=16, default_queue_timeout=30)

    args = instances[0].settings.build_vllm_serve_args()
    route = routes["qwen"]
    assert route.model_name == "Qwen/Qwen3-14B-FP8"
    assert route.max_inflight == 6
    assert route.queue_timeout == 12.5
    assert route.scheduling_policy == "priority"
    assert route.capabilities["reasoning"] is True
    assert route.capabilities["priority_scheduling"] is True
    assert instances[0].served_model_name == "Qwen/Qwen3-14B-FP8"
    assert args[args.index("--served-model-name") + 1] == "Qwen/Qwen3-14B-FP8"
    assert "--enable-chunked-prefill" in args
    assert "--long-prefill-token-threshold" in args
    assert args[args.index("--mamba-ssm-cache-dtype") + 1] == "float32"
    assert "--max-num-partial-prefills" not in args
    assert "--max-long-partial-prefills" not in args


def test_capabilities_must_be_an_object(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    models_path = tmp_path / "models.json"
    _write_env(env_path)
    models_path.write_text(
        json.dumps(
            [
                {
                    "alias": "bad",
                    "served_model_name": "bad-model",
                    "model_name": "bad-model",
                    "api_port": 8105,
                    "capabilities": ["not", "an", "object"],
                }
            ]
        ),
        encoding="utf-8",
    )

    instances = load_model_instances(base_env_file=env_path, models_json_file=models_path)
    with pytest.raises(ValueError, match="capabilities"):
        build_gateway_routes(instances)


def test_models_require_unique_served_model_names(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    models_path = tmp_path / "models.json"
    _write_env(env_path)
    models_path.write_text(
        json.dumps(
            [
                {"alias": "one", "served_model_name": "shared", "model_name": "one", "api_port": 8103},
                {"alias": "two", "served_model_name": "shared", "model_name": "two", "api_port": 8104},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="served_model_name 重複"):
        load_model_instances(base_env_file=env_path, models_json_file=models_path)


def test_litellm_generator_matches_models_and_uses_env_references(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    output_path = tmp_path / "config.yaml"
    models_path.write_text(
        json.dumps(
            [
                {
                    "alias": "public-model",
                    "legacy_aliases": [{"name": "old-public-model", "remove_after": "2099-01-01"}],
                    "served_model_name": "upstream-model",
                    "model_name": "./AImodels/model",
                    "api_port": 8103,
                    "litellm": {"rpm": 10},
                    "capabilities": {"chat": True},
                }
            ]
        ),
        encoding="utf-8",
    )
    generator = Path(__file__).resolve().parents[1] / "tools" / "generate_litellm_config.py"
    template = Path(__file__).resolve().parents[1] / "litellm" / "config.template.yaml"
    subprocess.run(
        [
            sys.executable,
            str(generator),
            "--models",
            str(models_path),
            "--template",
            str(template),
            "--output",
            str(output_path),
        ],
        check=True,
    )

    generated = output_path.read_text(encoding="utf-8")
    assert "model_name: public-model" in generated
    assert "model_name: old-public-model" in generated
    assert "model: hosted_vllm/upstream-model" in generated
    assert "api_base: http://127.0.0.1:8103/v1" in generated
    assert "api_key: os.environ/VLLM_UPSTREAM_API_KEY" in generated
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in generated
    assert "database_url:" not in generated


def test_litellm_generator_rejects_duplicate_public_aliases(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            [
                {"alias": "one", "served_model_name": "one", "model_name": "one", "api_port": 8103},
                {"alias": "one", "served_model_name": "two", "model_name": "two", "api_port": 8104},
            ]
        ),
        encoding="utf-8",
    )
    generator = Path(__file__).resolve().parents[1] / "tools" / "generate_litellm_config.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--models", str(models_path), "--output", str(tmp_path / "out.yaml")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "公開 alias 重複" in result.stderr


def test_cluster_without_gateway_does_not_load_legacy_gateway_config(monkeypatch) -> None:
    class FakeManager:
        def __init__(self, instances):
            self.instances = instances
            self.stopped = False

        def start_all(self, **kwargs):
            return None

        def print_status(self):
            return None

        def stop_all(self):
            self.stopped = True

    monkeypatch.setattr(launcher_main, "load_model_instances", lambda **kwargs: [object()])
    monkeypatch.setattr(launcher_main, "validate_cluster_resources", lambda instances: None)
    monkeypatch.setattr(launcher_main, "load_gateway_config", lambda **kwargs: pytest.fail("legacy config loaded"))
    monkeypatch.setattr(launcher_main, "MultiModelEngineManager", FakeManager)

    runtime = launcher_main.quick_start_cluster(
        base_env=".env.API",
        models_json="models.json",
        skip_check=True,
        start_gateway=False,
    )

    assert runtime is not None
    assert runtime.gateway is None
