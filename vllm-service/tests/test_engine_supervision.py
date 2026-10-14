"""launcher 監看 vLLM 子程序：崩潰只重啟該模型、次數有限且退避，超過上限就放棄。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

import core.cluster as cluster_module
import core.engine as engine_module
import main as launcher_main
from config.settings import SERVICE_ENV_FILE_VAR, Settings
from core.engine import VLLMEngine
from core.supervisor import MAX_BACKOFF_SECONDS, RestartPolicy, RestartTracker

# ---------------------------------------------------------------------------
# 重啟策略
# ---------------------------------------------------------------------------


def test_backoff_doubles_until_the_attempt_limit() -> None:
    tracker = RestartTracker(RestartPolicy(max_attempts=3, backoff_seconds=10, reset_after_seconds=600))

    assert [tracker.next_delay(1.0) for _ in range(4)] == [10, 20, 40, None]


def test_backoff_is_capped() -> None:
    tracker = RestartTracker(RestartPolicy(max_attempts=10, backoff_seconds=100, reset_after_seconds=600))

    delays = [tracker.next_delay(0.0) for _ in range(4)]

    assert delays == [100, 200, MAX_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS]


def test_long_uptime_resets_the_attempt_count() -> None:
    tracker = RestartTracker(RestartPolicy(max_attempts=2, backoff_seconds=5, reset_after_seconds=600))
    assert tracker.next_delay(1.0) == 5
    assert tracker.next_delay(1.0) == 10

    # 穩定跑了一段時間才又崩潰：視為偶發，重新從第一次算起
    assert tracker.next_delay(601.0) == 5
    assert tracker.attempts == 1


def test_zero_attempts_disables_restart() -> None:
    assert RestartTracker(RestartPolicy(max_attempts=0)).next_delay(0.0) is None


def test_policy_reads_settings(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "ENGINE_RESTART_MAX_ATTEMPTS",
        "ENGINE_RESTART_BACKOFF_SECONDS",
        "ENGINE_RESTART_RESET_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env.API"
    env.write_text(
        "ENGINE_RESTART_MAX_ATTEMPTS=5\nENGINE_RESTART_BACKOFF_SECONDS=2.5\n"
        "ENGINE_RESTART_RESET_SECONDS=60\n",
        encoding="utf-8",
    )

    assert RestartPolicy.from_settings(Settings(_env_file=str(env))) == RestartPolicy(
        max_attempts=5, backoff_seconds=2.5, reset_after_seconds=60
    )
    assert RestartPolicy.from_settings(Settings(_env_file=str(tmp_path / "missing"))) == RestartPolicy()


def test_negative_attempts_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_RESTART_MAX_ATTEMPTS", "-1")

    with pytest.raises(ValueError):
        Settings(_env_file=str(tmp_path / "missing"))


# ---------------------------------------------------------------------------
# cluster：只重啟崩潰的模型
# ---------------------------------------------------------------------------


class _ProcessEngine:
    """記錄 start/stop 的假引擎；``code`` 為 None 代表 vLLM 仍在執行。"""

    instances: ClassVar[list[_ProcessEngine]] = []
    fail_restart: ClassVar[bool] = False

    def __init__(self, settings=None, alias=None) -> None:
        self.settings = settings
        self.alias = alias
        self.code: int | None = None
        self.starts: list[bool] = []
        self.stops = 0
        _ProcessEngine.instances.append(self)

    def start(self, wait_ready: bool = True, timeout: int = 1800) -> None:
        if self.starts and _ProcessEngine.fail_restart:
            self.starts.append(wait_ready)
            raise OSError("port in use")
        self.starts.append(wait_ready)
        self.code = None

    def stop(self) -> None:
        self.stops += 1

    def returncode(self) -> int | None:
        return self.code

    def is_running(self) -> bool:
        return self.code is None


@pytest.fixture
def process_engine(monkeypatch):
    _ProcessEngine.instances = []
    _ProcessEngine.fail_restart = False
    monkeypatch.setattr(cluster_module, "VLLMEngine", _ProcessEngine)
    monkeypatch.setattr(launcher_main, "VLLMEngine", _ProcessEngine)
    return _ProcessEngine


def _instances(*aliases: str, **restart) -> list[SimpleNamespace]:
    settings = {
        "engine_restart_max_attempts": 2,
        "engine_restart_backoff_seconds": 10.0,
        "engine_restart_reset_seconds": 600.0,
        **restart,
    }
    return [
        SimpleNamespace(
            alias=alias,
            settings=SimpleNamespace(
                model_name=alias, api_host="127.0.0.1", api_port=8100 + idx, **settings
            ),
        )
        for idx, alias in enumerate(aliases, start=1)
    ]


def _started_manager(*aliases: str, **restart) -> cluster_module.MultiModelEngineManager:
    manager = cluster_module.MultiModelEngineManager(_instances(*aliases, **restart))
    manager.start_all(wait_ready=True, startup_delay=0)
    for managed in manager._engines.values():
        managed.started_at = 0.0
    return manager


def test_crashed_model_restarts_alone_after_backoff(process_engine) -> None:
    manager = _started_manager("first", "second")
    first, second = process_engine.instances
    first.code = -9

    manager.check_engines(now=100.0)

    # 崩潰的模型先清理殘留程序，排定 10 秒後重啟；另一個模型完全不受影響
    assert first.stops == 1 and first.starts == [True]
    assert second.stops == 0 and second.starts == [True]

    manager.check_engines(now=105.0)
    assert first.starts == [True]

    manager.check_engines(now=110.0)
    assert first.starts == [True, False]
    assert second.starts == [True]
    assert not manager.all_given_up

    # 重啟後正常運作：不會再被重啟
    manager.check_engines(now=200.0)
    assert first.starts == [True, False]


def test_model_is_given_up_after_the_retry_limit(process_engine) -> None:
    manager = _started_manager("first", "second")
    first, second = process_engine.instances
    now = 100.0
    for expected_delay in (10.0, 20.0):
        first.code = 1
        manager.check_engines(now=now)
        now += expected_delay
        manager.check_engines(now=now)
        now += 1.0

    first.code = 1
    manager.check_engines(now=now)
    manager.check_engines(now=now + 1000)

    assert first.starts == [True, False, False]
    assert manager._engines["first"].given_up is True
    assert manager._engines["second"].given_up is False
    assert second.stops == 0
    assert not manager.all_given_up

    # second 也連續崩潰到上限：cluster 已沒有模型可服務
    now += 2000
    for _ in range(3):
        second.code = 1
        manager.check_engines(now=now)
        now += 100
        manager.check_engines(now=now)
        now += 1
    assert second.starts == [True, False, False]
    assert manager.all_given_up


def test_failed_restart_counts_as_an_attempt(process_engine) -> None:
    manager = _started_manager("only", engine_restart_max_attempts=2)
    (engine,) = process_engine.instances
    process_engine.fail_restart = True
    engine.code = 1

    manager.check_engines(now=100.0)
    manager.check_engines(now=110.0)  # 第 1 次重啟失敗 → 20 秒後再試
    manager.check_engines(now=130.0)  # 第 2 次重啟失敗 → 放棄

    assert engine.starts == [True, False, False]
    assert manager.all_given_up


def test_cluster_launcher_exits_non_zero_when_every_model_gave_up(
    monkeypatch, process_engine
) -> None:
    monkeypatch.setattr(launcher_main._shutdown, "requested", False)
    monkeypatch.setenv(SERVICE_ENV_FILE_VAR, "unused")
    instances = _instances("only", engine_restart_max_attempts=1, engine_restart_backoff_seconds=0)
    monkeypatch.setattr(launcher_main, "resolve_env_file", lambda value: Path(str(value)))
    monkeypatch.setattr(launcher_main, "load_model_instances", lambda **_: instances)
    monkeypatch.setattr(launcher_main, "validate_cluster_resources", lambda _: None)
    # time 是同一個模組物件，這裡等於讓 launcher 主迴圈不真的等待
    monkeypatch.setattr(launcher_main.time, "sleep", lambda _seconds: None)

    def crash_on_every_start(self, wait_ready=True, timeout=1800):
        self.starts.append(wait_ready)
        self.code = 1

    monkeypatch.setattr(_ProcessEngine, "start", crash_on_every_start)
    args = SimpleNamespace(
        no_wait=False, timeout=1, base_env="unused", models_json="unused", skip_check=True,
        startup_delay=0.0, quantization="", tool_call_parser="", reasoning_parser="",
        kv_cache_dtype="",
    )

    with pytest.raises(SystemExit) as exc_info:
        launcher_main._run_cluster_mode(args)

    assert exc_info.value.code == 1
    (engine,) = process_engine.instances
    assert engine.starts == [True, False]
    assert engine.stops >= 2


# ---------------------------------------------------------------------------
# 單模型：崩潰後重啟，超過上限 launcher 以非 0 結束
# ---------------------------------------------------------------------------


class _SingleEngine:
    """``_process.wait()`` 依序取出 ``outcomes``：整數＝退出碼，callable＝在 wait 中執行。"""

    instances: ClassVar[list[_SingleEngine]] = []
    outcomes: ClassVar[list] = []

    def __init__(self, settings=None, alias=None) -> None:
        self.settings = settings
        self.base_url = "http://127.0.0.1:8000"
        self.starts: list[bool] = []
        self.stops = 0
        self._process = None
        _SingleEngine.instances.append(self)

    def _wait(self):
        outcome = _SingleEngine.outcomes.pop(0)
        return outcome() if callable(outcome) else outcome

    def start(self, wait_ready: bool = True, timeout: int = 1800) -> None:
        self.starts.append(wait_ready)
        self._process = SimpleNamespace(wait=self._wait)

    def stop(self) -> None:
        self.stops += 1
        self._process = None

    def print_status(self) -> None:
        pass


@pytest.fixture
def single_engine(monkeypatch):
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(launcher_main._shutdown, "requested", False)
    monkeypatch.setenv(SERVICE_ENV_FILE_VAR, "unused")
    _SingleEngine.instances = []
    _SingleEngine.outcomes = []
    monkeypatch.setattr(launcher_main, "VLLMEngine", _SingleEngine)
    monkeypatch.setattr(launcher_main, "resolve_env_file", lambda value: Path(str(value)))
    monkeypatch.setattr(
        launcher_main,
        "get_settings",
        lambda env_file=None: SimpleNamespace(
            engine_restart_max_attempts=2,
            engine_restart_backoff_seconds=10.0,
            engine_restart_reset_seconds=600.0,
        ),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(launcher_main.time, "sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["main.py", "single", "--skip-check", "--no-wait"])
    yield _SingleEngine, sleeps
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_single_model_gives_up_and_exits_non_zero(single_engine) -> None:
    engine_cls, sleeps = single_engine
    engine_cls.outcomes = [1, 1, 1]

    with pytest.raises(SystemExit) as exc_info:
        launcher_main.main()

    assert exc_info.value.code == 1
    (engine,) = engine_cls.instances
    # 首次啟動 + 2 次重啟（重啟不等待就緒），之後放棄
    assert engine.starts == [False, False, False]
    assert sleeps == [10.0, 20.0]
    assert engine.stops >= 3


def test_single_model_restart_then_sigterm_stops_cleanly(single_engine) -> None:
    engine_cls, sleeps = single_engine

    def sigterm():
        launcher_main._request_shutdown(signal.SIGTERM, None)

    engine_cls.outcomes = [-11, sigterm]

    launcher_main.main()  # 正常結束，不 sys.exit

    (engine,) = engine_cls.instances
    assert engine.starts == [False, False]
    assert sleeps == [10.0]
    assert engine.stops >= 2


# ---------------------------------------------------------------------------
# VLLMEngine：重啟接續寫日誌、清掉崩潰後殘留的子程序
# ---------------------------------------------------------------------------


def _engine_settings() -> SimpleNamespace:
    return SimpleNamespace(
        inject_env_vars=lambda: None,
        build_vllm_serve_args=lambda: ["--port", "1"],
        resolved_model_path="model",
        api_host="127.0.0.1",
        api_port=1,
    )


def test_restart_appends_to_the_model_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "LOGS_DIR", tmp_path)
    launched: list[list[str]] = []

    def fake_popen(cmd, stdout=None, **_kwargs):
        launched.append(cmd)
        stdout.write(f"run {len(launched)}\n")
        return SimpleNamespace(poll=lambda: 1, returncode=1, pid=999_999_999)

    monkeypatch.setattr(engine_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(VLLMEngine, "_kill_leftover_group", staticmethod(lambda _pgid: None))
    engine = VLLMEngine(settings=_engine_settings(), alias="model/a")

    engine.start(wait_ready=False)
    engine.stop()
    engine.start(wait_ready=False)
    engine.stop()

    log = (tmp_path / "model_a.log").read_text(encoding="utf-8")
    assert log.startswith("run 1\n")
    assert "重新啟動 vLLM" in log
    assert log.rstrip().endswith("run 2")
    assert engine.returncode() is None


def test_returncode_reports_exit_of_the_vllm_process() -> None:
    engine = VLLMEngine(settings=_engine_settings())
    assert engine.returncode() is None

    engine._process = SimpleNamespace(poll=lambda: None)
    assert engine.returncode() is None

    engine._process = SimpleNamespace(poll=lambda: -9)
    assert engine.returncode() == -9


@pytest.mark.skipif(sys.platform != "linux", reason="需要 /proc 與 POSIX 程序群組")
def test_stop_after_crash_kills_leftover_workers(tmp_path: Path) -> None:
    pid_file = tmp_path / "worker.pid"
    # 模擬 vLLM 主程序崩潰、但 worker 子程序還留在同一個程序群組
    leader = subprocess.Popen(
        ["sh", "-c", f'sleep 60 & echo $! > "{pid_file}"; exit 3'],
        start_new_session=True,
    )
    assert leader.wait(timeout=10) == 3
    worker = int(pid_file.read_text().strip())
    assert Path(f"/proc/{worker}").exists()

    engine = VLLMEngine(settings=_engine_settings())
    engine._process = leader
    assert engine.returncode() == 3

    engine.stop()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stat = Path(f"/proc/{worker}/stat")
        if not stat.exists() or stat.read_text().split(") ", 1)[1].startswith("Z"):
            break
        time.sleep(0.05)
    else:
        os.kill(worker, signal.SIGKILL)
        raise AssertionError("崩潰後殘留的 worker 沒有被清掉")
    assert engine.returncode() is None
