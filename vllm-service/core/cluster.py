"""多模型集群引擎管理。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config.multi_model import ModelInstanceConfig
from core.engine import VLLMEngine
from core.supervisor import RestartPolicy, RestartTracker
from utils.logging_utils import get_logger


@dataclass
class ManagedEngine:
    """集群中的單一已管理引擎。"""

    alias: str
    engine: VLLMEngine
    tracker: RestartTracker = field(default_factory=lambda: RestartTracker(RestartPolicy()))
    started_at: float = field(default_factory=time.monotonic)
    # 崩潰後排定的重啟時間（time.monotonic）；None 表示沒有待重啟
    restart_at: float | None = None
    given_up: bool = False


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
                    tracker=RestartTracker(RestartPolicy.from_settings(settings)),
                )
                self.logger.success(f"✓ 模型 {alias} 已就緒")
            except BaseException as exc:
                # 也要涵蓋 KeyboardInterrupt（SIGTERM 會被轉成它）：載入中的引擎
                # 還沒登記到 _engines，stop_all() 停不到它。
                if isinstance(exc, KeyboardInterrupt):
                    self.logger.warning(f"模型 {alias} 啟動期間收到中斷信號")
                else:
                    self.logger.error(f"模型 {alias} 啟動失敗: {exc}")
                self.logger.error("啟動失敗，開始回滾停止已啟動實例")
                # 確保停止失敗的引擎；即使 engine.stop() 期間又被 SIGTERM
                # 轉成的 KeyboardInterrupt 打斷，也一定要停掉已啟動的其他模型。
                try:
                    engine.stop()
                except Exception:
                    # 回滾中的停止失敗不影響後續清理
                    pass
                finally:
                    self.stop_all()
                raise

            # 非最後一個模型，等待一小段時間讓系統穩定
            if idx < total and startup_delay > 0:
                self.logger.info(f"等待 {startup_delay:.1f}s 後啟動下一個模型...")
                time.sleep(startup_delay)

        elapsed = time.time() - cluster_start
        self.logger.success(f"全部 {total} 個模型已就緒，總耗時 {elapsed:.1f}s")

    @property
    def all_given_up(self) -> bool:
        """所有已啟動的模型都已放棄重啟（cluster 已沒有任何模型可服務）。"""
        return bool(self._engines) and all(m.given_up for m in self._engines.values())

    def check_engines(self, now: float | None = None) -> None:
        """監看已啟動的模型：崩潰的只重啟那一個，其餘模型照常服務。

        由 launcher 的主迴圈定期呼叫；重啟不等待就緒（wait_ready=False），
        避免一個模型載入中時看不到其他模型崩潰。
        """
        now = time.monotonic() if now is None else now
        for managed in self._engines.values():
            if managed.given_up:
                continue
            if managed.restart_at is not None:
                if now >= managed.restart_at:
                    self._restart(managed, now)
                continue
            code = managed.engine.returncode()
            if code is None:
                continue
            uptime = now - managed.started_at
            self.logger.error(
                f"模型 {managed.alias} 的 vLLM 程序意外結束（退出碼 {code}，"
                f"已運行 {uptime:.0f}s），詳見 logs/ 內該模型的日誌"
            )
            self._stop_crashed(managed)
            self._schedule_restart(managed, now, uptime)

    def _stop_crashed(self, managed: ManagedEngine) -> None:
        # 清掉殘留的 worker 子程序並關閉日誌檔；失敗時照樣嘗試重啟（錯誤已記錄）
        try:
            managed.engine.stop()
        except Exception as exc:
            self.logger.error(f"模型 {managed.alias} 清理殘留程序失敗: {exc}")

    def _schedule_restart(self, managed: ManagedEngine, now: float, uptime: float) -> None:
        delay = managed.tracker.next_delay(uptime)
        limit = managed.tracker.policy.max_attempts
        if delay is None:
            managed.given_up = True
            self.logger.error(
                f"模型 {managed.alias} 已自動重啟 {limit} 次仍失敗，放棄此模型（其他模型繼續服務）；"
                "排除問題後請重新啟動 cluster"
            )
            return
        managed.restart_at = now + delay
        self.logger.warning(
            f"模型 {managed.alias} 將在 {delay:.0f}s 後重啟（第 {managed.tracker.attempts}/{limit} 次）"
        )

    def _restart(self, managed: ManagedEngine, now: float) -> None:
        managed.restart_at = None
        managed.started_at = now
        self.logger.info(f"重新啟動模型 {managed.alias}")
        try:
            managed.engine.start(wait_ready=False)
        except Exception as exc:
            self.logger.error(f"模型 {managed.alias} 重新啟動失敗: {exc}")
            self._stop_crashed(managed)
            self._schedule_restart(managed, now, 0.0)

    def stop_all(self) -> None:
        """反向停止所有已啟動引擎。

        單一引擎的 stop() 被中斷（例如 SIGTERM 轉成的 KeyboardInterrupt）時，
        仍繼續停止其餘引擎，全部處理完再把第一個中斷重新丟出。
        """
        interrupted: BaseException | None = None
        for alias in reversed(list(self._engines.keys())):
            managed = self._engines[alias]
            self.logger.info(f"停止模型 {alias}")
            try:
                managed.engine.stop()
            except Exception as exc:
                self.logger.warning(f"模型 {alias} 停止時發生錯誤: {exc}")
            except (KeyboardInterrupt, SystemExit) as exc:
                self.logger.warning(f"模型 {alias} 停止時被中斷，繼續停止其餘模型")
                if interrupted is None:
                    interrupted = exc
        self._engines.clear()
        if interrupted is not None:
            raise interrupted

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
