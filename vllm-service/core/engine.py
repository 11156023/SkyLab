"""
核心引擎層 - 封裝 vLLM serve 啟動與管理
使用 vLLM 官方 OpenAI Compatible Server 作為高併發推論後端
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO

import httpx

from config.settings import Settings, get_settings


class VLLMEngine:
    """vLLM 伺服器引擎管理器"""

    def __init__(self, settings: Settings | None = None, alias: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.alias = alias  # 用於識別日誌檔名
        self._process: subprocess.Popen | None = None
        self._http_client: httpx.Client | None = None
        self._log_handle: IO[str] | None = None  # 日誌檔案句柄

    def _get_http_client(self) -> httpx.Client:
        """取得或建立 HTTP 客戶端（連線池）"""
        if self._http_client is None:
            self._http_client = httpx.Client(
                timeout=5.0,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._http_client

    @property
    def base_url(self) -> str:
        return f"http://{self.settings.api_host}:{self.settings.api_port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/v1/models"

    def start(self, wait_ready: bool = True, timeout: int = 1800) -> None:
        """啟動 vLLM serve 程序"""
        if self._process and self._process.poll() is None:
            print("[Engine] vLLM 伺服器已在運行中")
            return

        self.settings.inject_env_vars()
        args = self.settings.build_vllm_serve_args()
        cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server"] + args

        print(f"[Engine] 啟動指令: {' '.join(cmd)}")
        print(f"[Engine] 模型路徑: {self.settings.resolved_model_path}")
        print(f"[Engine] 監聽地址: {self.base_url}")

        # 明確傳遞環境變數到子進程
        env = os.environ.copy()
        
        # 建立日誌目錄和日誌檔案
        if self.alias:
            logs_dir = Path(__file__).resolve().parent.parent / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            # 將 alias 中的斜線替換為底線，避免被解釋為目錄分隔符
            safe_alias = self.alias.replace("/", "_").replace("\\", "_")
            log_file_path = logs_dir / f"{safe_alias}.log"
            print(f"[Engine] 日誌檔案: {log_file_path}")
            self._log_handle = open(log_file_path, "w", encoding="utf-8", buffering=1)
            stdout_target = self._log_handle
            stderr_target = self._log_handle
        else:
            # 若無 alias，使用預設行為（輸出到 sys.stdout/stderr）
            stdout_target = sys.stdout
            stderr_target = sys.stderr
        
        self._process = subprocess.Popen(
            cmd,
            stdout=stdout_target,
            stderr=stderr_target,
            env=env,
            # 讓 vLLM 子進程成為獨立 session，停止時可一次終止整個進程群組。
            start_new_session=(os.name != "nt"),
        )

        if wait_ready:
            self._wait_for_ready(timeout)

    def _wait_for_ready(self, timeout: int) -> None:
        """等待伺服器就緒（改進的健康檢查）"""
        print(f"[Engine] 等待伺服器就緒 (逾時: {timeout}s)...")
        start = time.time()
        consecutive_failures = 0
        last_error = None
        
        while time.time() - start < timeout:
            elapsed = time.time() - start
            
            # 檢查程序是否還活著
            if self._process and self._process.poll() is not None:
                returncode = self._process.returncode
                error_msg = (
                    f"[Engine] vLLM 程序異常退出\n"
                    f"  退出碼: {returncode}\n"
                    f"  已耗時: {elapsed:.1f}s\n"
                    f"  提示: 檢查 GPU 記憶體、CUDA 版本、模型路徑"
                )
                if last_error:
                    error_msg += f"\n  最後錯誤: {last_error}"
                raise RuntimeError(error_msg)
            
            try:
                # 先檢查 health 端點
                resp = httpx.get(self.health_url, timeout=5)
                if resp.status_code == 200:
                    # 再檢查 models 端點確認模型已載入
                    try:
                        models_resp = httpx.get(
                            self.models_url,
                            headers={"Authorization": f"Bearer {self.settings.api_key}"},
                            timeout=5
                        )
                        if models_resp.status_code == 200:
                            elapsed = time.time() - start
                            print(f"[Engine] 伺服器就緒！耗時 {elapsed:.1f}s")
                            print(f"[Engine] 已載入模型: {models_resp.json().get('data', [{}])[0].get('id', 'unknown') if models_resp.json().get('data') else 'unknown'}")
                            return
                    except Exception as e:
                        last_error = f"models 端點檢查失敗: {e}"
                        # health 正常但 models 失敗，繼續等待
                        pass
                
                consecutive_failures = 0
                
            except httpx.ConnectError as e:
                last_error = f"連接錯誤: {e}"
                consecutive_failures += 1
            except httpx.ReadTimeout as e:
                last_error = f"讀取逾時: {e}"
                consecutive_failures += 1
            except Exception as e:
                last_error = f"未預期錯誤: {e}"
                consecutive_failures += 1
            
            # 如果連續失敗太多次，提示可能的問題
            if consecutive_failures > 0 and consecutive_failures % 10 == 0:
                print(f"[Engine] 等待中... ({elapsed:.1f}s / {timeout}s) - 連續失敗 {consecutive_failures} 次")
                if last_error:
                    print(f"[Engine] 最後錯誤: {last_error}")
            
            # 動態調整等待時間（早期更頻繁檢查）
            if elapsed < 30:
                sleep_time = 2
            elif elapsed < 120:
                sleep_time = 5
            else:
                sleep_time = 10
            
            time.sleep(sleep_time)

        # 超時
        error_msg = f"[Engine] 伺服器在 {timeout}s 內未就緒"
        if last_error:
            error_msg += f"\n  最後錯誤: {last_error}"
        error_msg += "\n  提示: 增加 timeout 參數或檢查伺服器日誌"
        self.stop()
        raise TimeoutError(error_msg)

    def probe_ready(self, timeout: float = 5.0) -> bool:
        """非阻塞檢查服務是否已就緒（health + models）。"""
        if self._process is None or self._process.poll() is not None:
            return False
        try:
            client = self._get_http_client()
            health_resp = client.get(self.health_url, timeout=timeout)
            if health_resp.status_code != 200:
                return False
            models_resp = client.get(
                self.models_url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=timeout,
            )
            return models_resp.status_code == 200
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPError):
            return False

    def stop(self) -> None:
        """停止 vLLM 伺服器（改進的進程管理）"""
        # 關閉 HTTP 客戶端連線池
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass
            self._http_client = None
        
        if self._process and self._process.poll() is None:
            print("[Engine] 正在停止 vLLM 伺服器...")

            pid = self._process.pid
            # 先嘗試温和結束 (SIGTERM)
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                self._process.send_signal(signal.SIGTERM)
            
            try:
                # 等待最多 30 秒
                self._process.wait(timeout=30)
                print("[Engine] 伺服器已正常停止")
            except subprocess.TimeoutExpired:
                # 如果超時，強制終止 (SIGKILL)
                print("[Engine] 温和停止超時，強制終止...")
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        # Process already terminated
                        pass
                else:
                    self._process.kill()
                try:
                    self._process.wait(timeout=5)
                    print("[Engine] 伺服器已強制停止")
                except subprocess.TimeoutExpired:
                    print("[Engine] 警告: 無法停止進程，可能成為僵屍進程")

        else:
            print("[Engine] 伺服器未運行或已停止")
        
        self._process = None
        
        # 關閉日誌檔案句柄
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def is_running(self) -> bool:
        """檢查伺服器是否運行中"""
        if self._process is None or self._process.poll() is not None:
            return False
        try:
            client = self._get_http_client()
            resp = client.get(self.health_url, timeout=5)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ReadTimeout):
            return False

    def get_models(self) -> dict:
        """取得已載入的模型列表"""
        client = self._get_http_client()
        resp = client.get(
            self.models_url,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def print_status(self) -> None:
        """輸出伺服器狀態"""
        running = self.is_running()
        print(f"\n{'='*60}")
        print("  vLLM 伺服器狀態")
        print(f"{'='*60}")
        print(f"  狀態:     {'RUNNING' if running else 'STOPPED'}")
        print(f"  地址:     {self.base_url}")
        print(f"  模型:     {self.settings.model_name}")
        print(f"  路徑:     {self.settings.resolved_model_path}")
        print(f"  快取目錄: {self.settings.hf_cache_dir}")
        print(f"  最大長度: {self.settings.max_model_len}")
        print(f"  併發數:   {self.settings.max_num_seqs}")
        print(f"  GPU利用:  {self.settings.gpu_memory_utilization}")
        print(f"{'='*60}\n")


def run_server() -> None:
    """直接運行 vLLM 伺服器 (阻塞式)"""
    engine = VLLMEngine()
    engine.print_status()

    try:
        engine.start(wait_ready=True)
        # 保持主程序，直到收到中斷信號
        if engine._process:
            engine._process.wait()
    except KeyboardInterrupt:
        print("\n[Engine] 收到中斷信號")
    finally:
        engine.stop()


if __name__ == "__main__":
    run_server()
