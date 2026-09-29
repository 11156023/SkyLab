"""稽核修正的回歸測試：launcher 信號清理、IPv6 探測、媒體路徑、legacy alias。"""

from __future__ import annotations

import json
import signal
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

import core.cluster as cluster_module
import main as launcher_main
from config.multi_model import load_model_instances, probe_host
from config.settings import SERVICE_ENV_FILE_VAR, Settings
from core.engine import VLLMEngine
from utils.logging_utils import get_logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_litellm_config import load_models

# ---------------------------------------------------------------------------
# 共用
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_signal_handlers(monkeypatch):
    """main.main() 會安裝 SIGTERM／SIGINT handler，測試結束後還原。"""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(launcher_main, "_shutdown_requested", False)
    monkeypatch.setenv(SERVICE_ENV_FILE_VAR, "unused")
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def _deliver_sigterm() -> None:
    signal.raise_signal(signal.SIGTERM)
    # Python 在下一個 bytecode 邊界執行 handler；給它一點時間。
    for _ in range(200):
        time.sleep(0.01)
    raise AssertionError("SIGTERM 沒有中斷 launcher")


class _FakeEngine:
    instances: ClassVar[list[_FakeEngine]] = []
    start_hook: ClassVar = None
    stop_hook: ClassVar = None

    def __init__(self, settings=None, alias=None) -> None:
        self.settings = settings
        self.alias = alias
        self.base_url = "http://127.0.0.1:8000"
        self.stopped = False
        self._process = None
        _FakeEngine.instances.append(self)

    def start(self, wait_ready: bool = True, timeout: int = 1800) -> None:
        if _FakeEngine.start_hook is not None:
            _FakeEngine.start_hook(self)

    def stop(self) -> None:
        self.stopped = True
        if _FakeEngine.stop_hook is not None:
            _FakeEngine.stop_hook(self)

    def print_status(self) -> None:
        pass


@pytest.fixture
def fake_engine(monkeypatch):
    _FakeEngine.instances = []
    _FakeEngine.start_hook = None
    _FakeEngine.stop_hook = None
    monkeypatch.setattr(launcher_main, "VLLMEngine", _FakeEngine)
    monkeypatch.setattr(cluster_module, "VLLMEngine", _FakeEngine)
    return _FakeEngine


def _patch_single_settings(monkeypatch) -> None:
    monkeypatch.setattr(launcher_main, "resolve_env_file", lambda value: Path(str(value)))
    monkeypatch.setattr(launcher_main, "get_settings", lambda env_file=None: SimpleNamespace())


# ---------------------------------------------------------------------------
# SIGTERM 必須讓 launcher 停掉 vLLM 子程序
# ---------------------------------------------------------------------------


def test_sigterm_while_single_model_is_running_stops_engine(
    monkeypatch, restore_signal_handlers, fake_engine
) -> None:
    _patch_single_settings(monkeypatch)

    def start_hook(engine: _FakeEngine) -> None:
        engine._process = SimpleNamespace(wait=_deliver_sigterm)

    fake_engine.start_hook = start_hook
    monkeypatch.setattr(sys, "argv", ["main.py", "single", "--skip-check", "--no-wait"])

    launcher_main.main()

    assert len(fake_engine.instances) == 1
    assert fake_engine.instances[0].stopped is True


def test_sigterm_during_single_model_readiness_wait_stops_engine(
    monkeypatch, restore_signal_handlers, fake_engine
) -> None:
    _patch_single_settings(monkeypatch)
    fake_engine.start_hook = lambda engine: _deliver_sigterm()
    monkeypatch.setattr(sys, "argv", ["main.py", "single", "--skip-check"])

    launcher_main.main()

    assert len(fake_engine.instances) == 1
    assert fake_engine.instances[0].stopped is True


def test_sigterm_while_cluster_model_is_loading_stops_every_engine(
    monkeypatch, restore_signal_handlers, fake_engine
) -> None:
    instances = [
        SimpleNamespace(
            alias=alias,
            settings=SimpleNamespace(model_name=alias, api_host="127.0.0.1", api_port=port),
        )
        for alias, port in (("first", 8101), ("second", 8102))
    ]
    monkeypatch.setattr(launcher_main, "resolve_env_file", lambda value: Path(str(value)))
    monkeypatch.setattr(launcher_main, "load_model_instances", lambda **_: instances)
    monkeypatch.setattr(launcher_main, "validate_cluster_resources", lambda _: None)

    def start_hook(engine: _FakeEngine) -> None:
        if engine.alias == "second":
            _deliver_sigterm()

    fake_engine.start_hook = start_hook
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "cluster", "--skip-check", "--no-gateway", "--startup-delay", "0"],
    )

    launcher_main.main()

    assert [engine.alias for engine in fake_engine.instances] == ["first", "second"]
    assert all(engine.stopped for engine in fake_engine.instances)


def _cluster_instances(*aliases: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            alias=alias,
            settings=SimpleNamespace(model_name=alias, api_host="127.0.0.1", api_port=8100 + idx),
        )
        for idx, alias in enumerate(aliases, start=1)
    ]


def test_sigterm_during_rollback_stop_still_stops_started_engines(
    restore_signal_handlers, fake_engine
) -> None:
    """模型載入失敗→回滾停它時收到 SIGTERM，先前已啟動的模型仍必須被停掉。"""
    signal.signal(signal.SIGTERM, launcher_main._request_shutdown)

    def start_hook(engine: _FakeEngine) -> None:
        if engine.alias == "second":
            raise TimeoutError("模型 second 未在期限內就緒")

    def stop_hook(engine: _FakeEngine) -> None:
        if engine.alias == "second":
            _deliver_sigterm()

    fake_engine.start_hook = start_hook
    fake_engine.stop_hook = stop_hook
    manager = cluster_module.MultiModelEngineManager(_cluster_instances("first", "second"))

    with pytest.raises(KeyboardInterrupt):
        manager.start_all(wait_ready=True, timeout=1, startup_delay=0)

    first, second = fake_engine.instances
    assert second.stopped is True
    assert first.stopped is True


def test_stop_all_continues_after_interrupted_engine_stop(fake_engine) -> None:
    """stop_all 中某個引擎的 stop() 被中斷，其餘引擎仍要停，最後再丟出中斷。"""
    manager = cluster_module.MultiModelEngineManager(
        _cluster_instances("first", "second", "third")
    )
    manager.start_all(wait_ready=False, startup_delay=0)

    def stop_hook(engine: _FakeEngine) -> None:
        if engine.alias == "second":
            raise KeyboardInterrupt("SIGTERM")

    fake_engine.stop_hook = stop_hook

    with pytest.raises(KeyboardInterrupt):
        manager.stop_all()

    assert [engine.alias for engine in fake_engine.instances] == ["first", "second", "third"]
    assert all(engine.stopped for engine in fake_engine.instances)
    assert not any(row["running"] for row in manager.get_status())


def test_second_signal_during_cleanup_is_ignored(monkeypatch) -> None:
    monkeypatch.setattr(launcher_main, "_shutdown_requested", False)

    with pytest.raises(KeyboardInterrupt):
        launcher_main._request_shutdown(signal.SIGTERM, None)
    # 清理中再收到信號不可再丟例外，否則會打斷 engine.stop()。
    launcher_main._request_shutdown(signal.SIGTERM, None)


# ---------------------------------------------------------------------------
# API_HOST 為 :: 或 IPv6 位址時的探測 URL 與 port 檢查
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("0.0.0.0", "127.0.0.1"),
        ("::", "127.0.0.1"),
        ("fd00::1", "[fd00::1]"),
        ("[fd00::1]", "[fd00::1]"),
        ("127.0.0.1", "127.0.0.1"),
        ("gpu-node", "gpu-node"),
    ],
)
def test_probe_host_maps_wildcards_and_brackets_ipv6(host: str, expected: str) -> None:
    assert probe_host(host) == expected


def test_engine_health_url_is_valid_for_ipv6_hosts() -> None:
    wildcard = VLLMEngine(settings=SimpleNamespace(api_host="::", api_port=8000))
    literal = VLLMEngine(settings=SimpleNamespace(api_host="fd00::1", api_port=8000))

    assert wildcard.health_url == "http://127.0.0.1:8000/health"
    assert literal.health_url == "http://[fd00::1]:8000/health"


def test_check_port_available_accepts_ipv4_host() -> None:
    assert launcher_main.check_port_available("127.0.0.1", 0, get_logger("TestPort")) is True


@pytest.mark.skipif(not socket.has_ipv6, reason="此環境不支援 IPv6")
@pytest.mark.parametrize("host", ["::1", "[::1]"])
def test_check_port_available_accepts_ipv6_host(host: str) -> None:
    # 含 ":" 的主機（包括 "::" 萬用位址）都走 AF_INET6；測試只綁 loopback，不開放到所有介面
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        pytest.skip("此環境無法綁定 IPv6 位址")

    assert launcher_main.check_port_available(host, 0, get_logger("TestPort")) is True


# ---------------------------------------------------------------------------
# 預設不開放 file:// 本機媒體，且拒絕根目錄
# ---------------------------------------------------------------------------


def _write_models(path: Path, extra: dict | None = None) -> None:
    model = {"alias": "local", "served_model_name": "model", "model_name": "model", "api_port": 8103}
    model.update(extra or {})
    path.write_text(json.dumps([model]), encoding="utf-8")


def test_allowed_local_media_path_is_off_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_LOCAL_MEDIA_PATH", raising=False)
    env_path = tmp_path / ".env.API"
    env_path.write_text("API_KEY=test\nAPI_HOST=127.0.0.1\n", encoding="utf-8")
    models_path = tmp_path / "models.json"
    _write_models(models_path)

    assert Settings.model_fields["allowed_local_media_path"].default == ""
    instances = load_model_instances(env_path, models_path)
    assert "--allowed-local-media-path" not in instances[0].settings.build_vllm_serve_args()


def test_allowed_local_media_path_rejects_root_in_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_LOCAL_MEDIA_PATH", raising=False)
    env_path = tmp_path / ".env.API"
    env_path.write_text(
        "API_KEY=test\nAPI_HOST=127.0.0.1\nALLOWED_LOCAL_MEDIA_PATH=/\n", encoding="utf-8"
    )
    models_path = tmp_path / "models.json"
    _write_models(models_path)

    with pytest.raises(ValueError, match="ALLOWED_LOCAL_MEDIA_PATH"):
        load_model_instances(env_path, models_path)


def test_allowed_local_media_path_rejects_root_in_models_json(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ALLOWED_LOCAL_MEDIA_PATH", raising=False)
    env_path = tmp_path / ".env.API"
    env_path.write_text("API_KEY=test\nAPI_HOST=127.0.0.1\n", encoding="utf-8")
    models_path = tmp_path / "models.json"
    _write_models(models_path, {"allowed_local_media_path": "/"})

    with pytest.raises(ValueError, match="ALLOWED_LOCAL_MEDIA_PATH"):
        load_model_instances(env_path, models_path)


def test_allowed_local_media_path_keeps_dedicated_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ALLOWED_LOCAL_MEDIA_PATH", raising=False)
    env_path = tmp_path / ".env.API"
    env_path.write_text("API_KEY=test\nAPI_HOST=127.0.0.1\n", encoding="utf-8")
    models_path = tmp_path / "models.json"
    _write_models(models_path, {"allowed_local_media_path": "/srv/vllm-media"})

    args = load_model_instances(env_path, models_path)[0].settings.build_vllm_serve_args()
    assert args[args.index("--allowed-local-media-path") + 1] == "/srv/vllm-media"


# ---------------------------------------------------------------------------
# legacy alias 的 30 天規則不可每次都拿今天比對
# ---------------------------------------------------------------------------


def _write_legacy_alias(path: Path, remove_after: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "alias": "public",
                    "served_model_name": "model",
                    "model_name": "model",
                    "api_port": 8103,
                    "legacy_aliases": [{"name": "old-public", "remove_after": remove_after}],
                }
            ]
        ),
        encoding="utf-8",
    )


def test_legacy_alias_close_to_removal_still_loads_with_warning(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "models.json"
    _write_legacy_alias(path, (date.today() + timedelta(days=10)).isoformat())

    models = load_models(path)

    assert models[0]["_legacy_alias_names"] == ["old-public"]
    assert "old-public" in capsys.readouterr().err


def test_legacy_alias_removal_day_itself_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_legacy_alias(path, date.today().isoformat())

    assert load_models(path)[0]["_legacy_alias_names"] == ["old-public"]


def test_expired_legacy_alias_is_rejected_with_clear_message(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    expired = (date.today() - timedelta(days=1)).isoformat()
    _write_legacy_alias(path, expired)

    with pytest.raises(ValueError, match=f"已於 {expired} 到期"):
        load_models(path)


def test_legacy_alias_with_malformed_date_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_legacy_alias(path, "2026/12/01")

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_models(path)
