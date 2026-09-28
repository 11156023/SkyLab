"""ShareGPT benchmark：預設打 LiteLLM、單模型目標沿用自己的設定與金鑰。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import _common, sharegpt_bench, sharegpt_dataset
from config.settings import Settings


@pytest.fixture
def no_litellm_env(monkeypatch, tmp_path: Path):
    for name in ("LITELLM_API_KEY", "AI_API_API_KEY", "LITELLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    # 讓「repo 根目錄 .env」指向空的暫存目錄，測試不受本機 .env 影響
    fake_root = tmp_path / "service"
    fake_root.mkdir()
    monkeypatch.setattr(sharegpt_bench, "PROJECT_ROOT", fake_root)
    return tmp_path


def test_litellm_target_is_the_default_and_uses_litellm_key(monkeypatch, no_litellm_env) -> None:
    monkeypatch.setenv("LITELLM_API_KEY", "sk-litellm")
    monkeypatch.setattr(sharegpt_bench, "_fetch_models", lambda base_url, api_key: ["gemma-4-31b", "qwen3-14b"])

    target, model = sharegpt_bench._resolve_noninteractive_target("litellm", None, None)

    assert target.base_url == "http://127.0.0.1:4000/v1"
    assert target.api_key == "sk-litellm"
    assert model == "gemma-4-31b"


def test_legacy_gateway_target_means_litellm(monkeypatch, no_litellm_env) -> None:
    monkeypatch.setenv("AI_API_API_KEY", "sk-service")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://gateway.example:4000")

    target, model = sharegpt_bench._resolve_noninteractive_target("gateway", "qwen3-14b", None)

    assert target.base_url == "http://gateway.example:4000/v1"
    assert target.api_key == "sk-service"
    assert model == "qwen3-14b"


def test_litellm_key_falls_back_to_repo_root_env(no_litellm_env) -> None:
    (no_litellm_env / ".env").write_text("AI_API_API_KEY=sk-from-root-env\n", encoding="utf-8")

    target = sharegpt_bench._resolve_target("litellm")

    assert target.api_key == "sk-from-root-env"


def test_litellm_target_without_key_fails_clearly(no_litellm_env) -> None:
    with pytest.raises(ValueError, match="LITELLM_API_KEY"):
        sharegpt_bench._resolve_target("litellm")


def test_litellm_models_fall_back_to_models_json_aliases(monkeypatch, no_litellm_env) -> None:
    monkeypatch.setenv("LITELLM_API_KEY", "sk-litellm")

    def unreachable(base_url, api_key):
        raise sharegpt_bench.URLError("connection refused")

    # fixture 把 PROJECT_ROOT 指到 no_litellm_env/service，預設 models.json 就在那裡
    (no_litellm_env / "service" / "models.json").write_text(
        '[{"alias": "remote-model", "deployment": "remote"}, {"alias": "local-model"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(sharegpt_bench, "_fetch_models", unreachable)

    target = sharegpt_bench._resolve_target("litellm")

    assert sharegpt_bench._list_litellm_models(target) == ["local-model", "remote-model"]


def test_models_json_aliases_include_remote_models(tmp_path: Path) -> None:
    models_json = tmp_path / "models.json"
    models_json.write_text(
        '[{"alias": "zeta", "deployment": "remote"}, {"alias": " alpha "}, {"served_model_name": "x"}]',
        encoding="utf-8",
    )

    assert sharegpt_bench._load_model_aliases_from_models_json(models_json) == ["alpha", "zeta"]


def test_single_target_sends_served_model_name(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        api_host="0.0.0.0",
        api_port=8123,
        api_key="single-key",
        model_name="./AImodels/Qwen3-14B-FP8",
        served_model_name="qwen3-14b",
    )
    monkeypatch.setattr(sharegpt_bench, "_load_bench_settings", lambda env_file: settings)

    target, model = sharegpt_bench._resolve_noninteractive_target("single", None, None)

    assert target.base_url == "http://127.0.0.1:8123/v1"
    assert target.api_key == "single-key"
    assert model == "qwen3-14b"


def test_interactive_single_target_uses_its_own_key(monkeypatch) -> None:
    """選主服務時要用 .env.interface 的設定與金鑰，不能落回 .env.API。"""
    single = Settings(_env_file=None, api_key="single-key", served_model_name="qwen3-14b")
    api = Settings(_env_file=None, api_key="api-key")
    monkeypatch.setattr(
        sharegpt_bench,
        "_load_bench_settings",
        lambda env_file: single if env_file == sharegpt_bench.SINGLE_ENV_FILE else api,
    )
    answers = iter(["2", "", "10", "4"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sharegpt_bench, "run_sharegpt_benchmark", fake_run)
    monkeypatch.setattr(sys, "argv", ["sharegpt_bench.py", "--interactive"])
    monkeypatch.setattr(sharegpt_bench.Path, "exists", lambda self: True)

    sharegpt_bench.main()

    assert captured["settings"] is single
    assert captured["api_key"] == "single-key"
    assert captured["model"] == "qwen3-14b"
    assert captured["num_samples"] == 10
    assert captured["concurrency"] == 4


def test_latency_stats_and_percentile() -> None:
    stats = _common.latency_stats([4.0, 1.0, 3.0, 2.0])

    assert stats["avg"] == 2.5
    assert stats["min"] == 1.0
    assert stats["max"] == 4.0
    assert stats["p50"] == 2.5
    assert _common.latency_stats([])["p99"] == 0.0


def test_stream_chat_collects_text_usage_and_ttft() -> None:
    def chunk(content=None, usage=None):
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content))] if content is not None else []
        return SimpleNamespace(choices=choices, usage=usage)

    async def stream():
        for item in (
            chunk("Hel"),
            chunk("lo"),
            chunk(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2)),
        ):
            yield item

    recorded: dict = {}

    async def create(**kwargs):
        recorded.update(kwargs)
        return stream()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    outcome = asyncio.run(
        _common.stream_chat(client, "qwen3-14b", [{"role": "user", "content": "hi"}], 16, 0.0)
    )

    assert outcome.text == "Hello"
    assert (outcome.prompt_tokens, outcome.completion_tokens) == (7, 2)
    assert outcome.first_token_latency is not None
    assert recorded["temperature"] == 0.0
    assert recorded["stream_options"] == {"include_usage": True}


def test_download_creates_parent_directory_and_leaves_no_partial_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "test_datasets" / "sharegpt.json"

    def fake_retrieve(url, filename):
        Path(filename).write_text("[]", encoding="utf-8")

    monkeypatch.setattr("urllib.request.urlretrieve", fake_retrieve)

    assert sharegpt_dataset.download_sharegpt_dataset(target) == target
    assert target.read_text(encoding="utf-8") == "[]"
    assert list(target.parent.iterdir()) == [target]


def test_interrupted_download_does_not_leave_a_dataset_behind(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "test_datasets" / "sharegpt.json"

    def broken_retrieve(url, filename):
        Path(filename).write_text("[{", encoding="utf-8")
        raise OSError("connection reset")

    monkeypatch.setattr("urllib.request.urlretrieve", broken_retrieve)

    with pytest.raises(OSError):
        sharegpt_dataset.download_sharegpt_dataset(target)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []
