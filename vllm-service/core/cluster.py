"""多模型集群引擎管理。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from config.multi_model import ModelInstanceConfig
from core.engine import VLLMEngine
from utils.logging_utils import get_logger


@dataclass
class ManagedEngine:
    """集群中的單一已管理引擎。"""

    alias: str
    engine: VLLMEngine
    config: ModelInstanceConfig


class MultiModelEngineManager:
    """管理多個 vLLM 實例的生命週期（串行啟動模式）。"""

    def __init__(self, instances: list[ModelInstanceConfig]) -> None:
        self.instances = instances
        self._engines: dict[str, ManagedEngine] = {}
        self.logger = get_logger("Cluster")

    def start_all(
        self,
        wait_ready: bool = True,
        timeout: int = 1800,
        startup_delay: float = 5.0,
    ) -> None:
        """啟動所有模型（串行模式）。

        Args:
            wait_ready: 是否等待模型就緒
            timeout: 單個模型等待就緒的超時秒數
            startup_delay: 每個模型完成後的額外等待秒數
        """
        if not self.instances:
            raise ValueError("未提供任何模型實例設定")

        total = len(self.instances)
        self.logger.section("集群部署進度")
        self.logger.info(f"目標模型數量: {total}")
        self.logger.info("啟動模式: SEQUENTIAL (串行)")

        self._start_sequential(wait_ready, timeout, startup_delay)

    def _start_sequential(
        self, wait_ready: bool, timeout: int, startup_delay: float
    ) -> None:
        """串行啟動：每個模型僅嘗試一次，失敗即中止。"""
        total = len(self.instances)
        cluster_start = time.time()

        for idx, instance in enumerate(self.instances, start=1):
            alias = instance.alias
            settings = instance.settings
            self.logger.section(f"模型 {idx}/{total}: {alias}")
            self.logger.info(
                f"啟動 {alias}: {settings.model_name} ({settings.api_host}:{settings.api_port})"
            )

            engine = VLLMEngine(settings=settings, alias=alias)
            try:
                # 串行模式：啟動並等待此模型完全就緒
                engine.start(wait_ready=wait_ready, timeout=timeout)
                self._engines[alias] = ManagedEngine(
                    alias=alias,
                    engine=engine,
                    config=instance,
                )
                self.logger.success(f"✓ 模型 {alias} 已就緒")
            except Exception as exc:
                self.logger.error(f"模型 {alias} 啟動失敗: {exc}")
                self.logger.error("啟動失敗，開始回滾停止已啟動實例")
                # 確保停止失敗的引擎
                try:
                    engine.stop()
                except Exception:
                    # 回滾中的停止失敗不影響後續清理
                    pass
                self.stop_all()
                raise

            # 非最後一個模型，等待一小段時間讓系統穩定
            if idx < total and startup_delay > 0:
                self.logger.info(f"等待 {startup_delay:.1f}s 後啟動下一個模型...")
                time.sleep(startup_delay)

        elapsed = time.time() - cluster_start
        self.logger.success(f"全部 {total} 個模型已就緒，總耗時 {elapsed:.1f}s")

    def _start_parallel(self, wait_ready: bool, timeout: int) -> None:
        """並行啟動：先啟動所有模型進程，再等待全部就緒（原有模式）。"""
        total = len(self.instances)

        for idx, instance in enumerate(self.instances, start=1):
            alias = instance.alias
            settings = instance.settings
            self.logger.info(
                f"[{idx}/{total}] 啟動進程 {alias}: {settings.model_name} ({settings.api_host}:{settings.api_port})"
            )
            engine = VLLMEngine(settings=settings, alias=alias)
            try:
                engine.start(wait_ready=False, timeout=timeout)
                self._engines[alias] = ManagedEngine(
                    alias=alias,
                    engine=engine,
                    config=instance,
                )
                self.logger.info(f"進程已啟動: {alias}")
            except Exception:
                self.logger.error(f"模型 {alias} 啟動失敗，開始回滾停止已啟動實例")
                self.stop_all()
                raise

        self.logger.info("全部模型進程已啟動")

        if wait_ready:
            self._wait_all_ready(timeout=timeout)

    def _wait_all_ready(self, timeout: int) -> None:
        """等待所有模型實例完成載入，並回報進度。"""
        pending = set(self._engines.keys())
        total = len(pending)
        start_ts = time.time()
        last_progress_log = 0.0

        while pending and (time.time() - start_ts) < timeout:
            for alias in list(pending):
                managed = self._engines[alias]
                process = managed.engine._process
                if process is not None and process.poll() is not None:
                    self.stop_all()
                    raise RuntimeError(f"模型 {alias} 進程異常退出 (exit code: {process.returncode})")
                if managed.engine.probe_ready(timeout=3.0):
                    pending.remove(alias)
                    ready_count = total - len(pending)
                    self.logger.success(f"READY {ready_count}/{total}: {alias}")

            now = time.time()
            if pending and (now - last_progress_log) >= 5:
                ready_count = total - len(pending)
                progress = int((ready_count / total) * 100)
                waiting_aliases = ", ".join(sorted(pending))
                self.logger.info(
                    f"載入進度 {ready_count}/{total} ({progress}%)，等待中: {waiting_aliases}"
                )
                last_progress_log = now

            if pending:
                time.sleep(2)

        if pending:
            waiting_aliases = ", ".join(sorted(pending))
            self.stop_all()
            raise TimeoutError(
                f"等待模型就緒逾時 ({timeout}s)，仍未就緒: {waiting_aliases}"
            )

        elapsed = time.time() - start_ts
        self.logger.success(f"全部模型已就緒，總耗時 {elapsed:.1f}s")

    def stop_all(self) -> None:
        """反向停止所有已啟動引擎。"""
        for alias in reversed(list(self._engines.keys())):
            managed = self._engines[alias]
            self.logger.info(f"停止模型 {alias}")
            try:
                managed.engine.stop()
            except Exception as exc:
                self.logger.warning(f"模型 {alias} 停止時發生錯誤: {exc}")
        self._engines.clear()

    def get_status(self) -> list[dict[str, str | int | bool]]:
        """取得集群狀態摘要。"""
        rows: list[dict[str, str | int | bool]] = []
        for instance in self.instances:
            alias = instance.alias
            managed = self._engines.get(alias)
            running = managed.engine.is_running() if managed else False
            rows.append(
                {
                    "alias": alias,
                    "model_name": instance.settings.model_name,
                    "host": instance.settings.api_host,
                    "port": instance.settings.api_port,
                    "running": running,
                }
            )
        return rows

    def print_status(self) -> None:
        """輸出集群狀態。"""
        self.logger.section("多模型集群狀態")
        for row in self.get_status():
            status = "RUNNING" if row["running"] else "STOPPED"
            self.logger.info(
                f"{status} {row['alias']}: {row['model_name']} "
                f"@ {row['host']}:{row['port']}"
            )
