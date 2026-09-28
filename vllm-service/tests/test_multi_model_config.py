from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import main as launcher_main
from config.multi_model import load_model_instances

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_litellm_config import load_models


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


def test_models_json_fields_become_vllm_serve_args(tmp_path: Path) -> None:
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

    settings = instances[0].settings
    args = settings.build_vllm_serve_args()
    assert settings.scheduling_policy == "priority"
    assert args[args.index("--scheduling-policy") + 1] == "priority"
    # 呼叫端（benchmark）要送 served name，而不是主機上的模型路徑
    assert settings.api_model_name == "Qwen/Qwen3-14B-FP8"
    assert instances[0].served_model_name == "Qwen/Qwen3-14B-FP8"
    assert args[args.index("--served-model-name") + 1] == "Qwen/Qwen3-14B-FP8"
    assert "--enable-chunked-prefill" in args
    assert "--long-prefill-token-threshold" in args
    assert args[args.index("--mamba-ssm-cache-dtype") + 1] == "float32"
    assert "--max-num-partial-prefills" not in args
    assert "--max-long-partial-prefills" not in args


def test_litellm_generator_rejects_non_object_capabilities(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
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

    with pytest.raises(ValueError, match="capabilities"):
        load_models(models_path)


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


def test_cluster_mode_returns_the_engine_manager(monkeypatch) -> None:
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
    monkeypatch.setattr(launcher_main, "MultiModelEngineManager", FakeManager)

    manager = launcher_main.quick_start_cluster(
        base_env=".env.API",
        models_json="models.json",
        skip_check=True,
    )

    assert isinstance(manager, FakeManager)
    assert manager.stopped is False


def test_launcher_rejects_removed_gateway_mode(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "gateway"])

    with pytest.raises(SystemExit) as excinfo:
        launcher_main.main()

    assert excinfo.value.code == 2
    assert "LiteLLM" in capsys.readouterr().err


def test_launcher_still_accepts_legacy_gateway_flags(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(launcher_main, "_install_shutdown_handlers", lambda: None)
    monkeypatch.setattr(launcher_main, "_run_cluster_mode", calls.append)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "cluster", "--no-gateway", "--gateway-ready-timeout", "30", "--base-env", ".env.API"],
    )

    launcher_main.main()

    assert len(calls) == 1
    assert calls[0].base_env == ".env.API"
