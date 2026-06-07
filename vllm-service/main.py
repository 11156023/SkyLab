#!/usr/bin/env python3
"""
快速啟動腳本 - 自動檢查和啟動 vLLM 服務
包含預啟動檢查、健康監控和錯誤診斷
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from importlib.metadata import PackageNotFoundError, version
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from urllib.request import urlopen

# 確保專案根目錄在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.multi_model import (
    build_gateway_routes,
    load_gateway_config,
    load_model_instances,
    validate_cluster_resources,
)
from config.settings import get_settings
from core.cluster import MultiModelEngineManager
from core.engine import VLLMEngine
from utils.health_utils import check_system_health
from utils.logging_utils import get_logger


@dataclass
class GatewayRuntime:
    """Gateway 子程序執行資訊。"""

    process: subprocess.Popen[bytes]
    host: str
    port: int
    log_path: Path


@dataclass
class ClusterRuntime:
    """集群與 Gateway 的整體執行資訊。"""

    manager: MultiModelEngineManager
    gateway: GatewayRuntime | None = None


# 全局 shutdown 標記
_shutdown_requested = False


def _resolve_python_bin(project_root: Path) -> str:
    """解析啟動子程序用的 Python 執行檔。"""
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _normalize_probe_host(host: str) -> str:
    """將 0.0.0.0/:: 轉為可探測地址。"""
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _wait_gateway_ready(
    runtime: GatewayRuntime,
    timeout: int,
    logger,
) -> None:
    """等待 Gateway /health 就緒。"""
    probe_host = _normalize_probe_host(runtime.host)
    health_url = f"http://{probe_host}:{runtime.port}/health"
    start = time.time()

    while (time.time() - start) < timeout:
        process = runtime.process
        if process.poll() is not None:
            raise RuntimeError(
                f"Gateway 進程異常退出 (exit code: {process.returncode})，"
                f"請查看日誌: {runtime.log_path}"
            )

        try:
            with urlopen(health_url, timeout=2.0) as response:  # nosec B310 - 內部服務健康檢查
                if response.status == 200:
                    logger.success(f"Gateway 已就緒: {health_url}")
                    return
        except Exception:
            # Ignore health check failures during probe
            pass

        time.sleep(1)

    raise TimeoutError(
        f"等待 Gateway 就緒逾時 ({timeout}s): {health_url}，"
        f"請查看日誌: {runtime.log_path}"
    )


def _start_gateway_process(
    gateway_host: str,
    gateway_port: int,
    ready_timeout: int,
    logger,
) -> GatewayRuntime:
    """啟動 Gateway (uvicorn gateway.main:app)。"""
    project_root = Path(__file__).resolve().parent
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "cluster-gateway.log"

    python_bin = _resolve_python_bin(project_root)
    command = [
        python_bin,
        "-m",
        "uvicorn",
        "gateway.main:app",
        "--host",
        gateway_host,
        "--port",
        str(gateway_port),
    ]

    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )

    runtime = GatewayRuntime(
        process=process,
        host=gateway_host,
        port=gateway_port,
        log_path=log_path,
    )
    _wait_gateway_ready(runtime, timeout=ready_timeout, logger=logger)
    return runtime


def _stop_gateway_process(runtime: GatewayRuntime | None, logger) -> None:
    """停止 Gateway 子程序。"""
    if runtime is None:
        return

    process = runtime.process
    if process.poll() is not None:
        return

    logger.info("停止 Gateway 進程...")
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        logger.warning("Gateway 未在期限內關閉，改用 SIGKILL")
        process.kill()
        process.wait(timeout=5)


def pre_launch_check(settings=None, logger_name: str = "PreCheck") -> bool:
    """啟動前檢查（增強版）"""
    logger = get_logger(logger_name)
    settings = settings or get_settings()
    
    logger.section("啟動前檢查")

    # 依賴版本前置檢查（避免子進程才報 ImportError）
    if not check_dependency_compatibility(logger):
        return False

    if not check_runtime_cache_permissions(logger):
        return False

    if not check_native_build_toolchain(logger):
        return False
    
    # 檢查系統健康
    health = check_system_health()
    
    # 檢查 GPU
    if health.gpu_count == 0:
        logger.error("未檢測到 GPU，無法啟動 vLLM")
        logger.info("提示: 確保已安裝 CUDA 和 PyTorch GPU 版本")
        return False
    
    logger.success(f"檢測到 {health.gpu_count} 個 GPU")
    
    # 檢查 GPU 記憶體
    for i in range(health.gpu_count):
        total = health.gpu_memory_total_gb[i]
        used = health.gpu_memory_used_gb[i]
        available = total - used
        
        logger.info(f"GPU {i}: {available:.1f} GB 可用 / {total:.1f} GB 總計")
        
        if available < 10:
            logger.warning(f"GPU {i} 可用記憶體不足 10GB，可能無法載入大模型")
    
    # 檢查系統記憶體
    if health.memory_available_gb < 10:
        logger.warning(f"系統可用記憶體不足 10GB (當前: {health.memory_available_gb:.1f} GB)")
    else:
        logger.success(f"系統記憶體充足: {health.memory_available_gb:.1f} GB 可用")
    
    # 檢查模型完整性
    if not validate_model_integrity(settings, logger):
        return False
    
    # 檢查端口可用性
    if not check_port_available(settings.api_port, logger):
        return False
    
    # 檢查 CUDA 環境
    triton_ptxas = Path(settings.triton_ptxas_path)
    if not triton_ptxas.exists():
        logger.warning(f"Triton PTXAS 不存在: {triton_ptxas}")
        logger.info("對於 Blackwell 等新 GPU，建議檢查 CUDA 安裝")
    
    # 健康警告
    warnings = health.get_warnings()
    if warnings:
        logger.warning("系統健康警告:")
        for warning in warnings:
            logger.warning(f"  - {warning}")
    
    return True


def check_dependency_compatibility(logger) -> bool:
    """檢查關鍵 Python 套件相容性（以實際匯入結果為準）。"""
    try:
        hub_version = version("huggingface-hub")
    except PackageNotFoundError:
        logger.error("缺少 huggingface-hub 套件")
        logger.info("請執行: .venv/bin/pip install huggingface-hub")
        return False

    try:
        transformers_version = version("transformers")
    except PackageNotFoundError:
        logger.error("缺少 transformers 套件")
        logger.info("請執行: .venv/bin/pip install transformers")
        return False

    # 以實際匯入能力判定相容性，避免僅用版本號造成誤判。
    try:
        importlib.import_module("transformers")
        importlib.import_module("transformers.utils.hub")
    except ImportError as exc:
        logger.error("Transformers 與 huggingface-hub 目前不相容，啟動前檢查失敗")
        logger.info(f"ImportError: {exc}")
        if "is_offline_mode" in str(exc) or "huggingface_hub" in str(exc):
            logger.info(
                "請嘗試對齊版本，例如: "
                ".venv/bin/pip install \"transformers>=4.52\" \"huggingface-hub>=0.34.0,<1.0\""
            )
        return False

    logger.success(
        "依賴檢查通過: "
        f"transformers={transformers_version}, huggingface-hub={hub_version}"
    )

    try:
        major = int(hub_version.split(".")[0])
    except ValueError:
        logger.warning(f"無法解析 huggingface-hub 版本: {hub_version}")
        return True

    if major >= 1:
        logger.warning(
            "偵測到 huggingface-hub >= 1.0，"
            "目前改以實際匯入驗證為準（若能匯入則允許啟動）"
        )
    
    return True


def validate_model_integrity(settings, logger) -> bool:
    """驗證模型完整性和必要檔案。"""
    model_path = Path(settings.resolved_model_path)
    
    if not model_path.exists():
        if "/" in settings.model_name:
            # 將從 HuggingFace 下載
            logger.warning(f"模型將從 HuggingFace 下載: {settings.model_name}")
            
            if settings.hf_hub_offline == 1:
                logger.error("模型不存在且 HF_HUB_OFFLINE=1，無法下載")
                logger.info("解決方案: 設置 HF_HUB_OFFLINE=0 或手動下載模型")
                return False
            
            logger.info("首次啟動將自動下載模型，請耐心等待...")
            return True
        else:
            logger.error(f"模型路徑不存在: {settings.model_name}")
            return False
    
    # 模型已存在，檢查完整性
    logger.success(f"模型已存在: {model_path}")
    
    # 檢查 config.json
    config_file = model_path / "config.json"
    if not config_file.exists():
        logger.error("模型配置檔缺失: config.json")
        logger.info(f"請檢查模型目錄: {model_path}")
        return False
    
    logger.success("✓ config.json 存在")
    
    # 檢查權重檔案
    has_safetensors = list(model_path.glob("*.safetensors"))
    has_pytorch = list(model_path.glob("*.bin"))
    has_gguf = list(model_path.glob("*.gguf"))
    
    if has_safetensors:
        logger.success(f"✓ 找到 {len(has_safetensors)} 個 SafeTensors 權重檔")
    elif has_pytorch:
        logger.success(f"✓ 找到 {len(has_pytorch)} 個 PyTorch 權重檔")
    elif has_gguf:
        logger.success(f"✓ 找到 {len(has_gguf)} 個 GGUF 權重檔")
    else:
        logger.error("未找到模型權重檔案 (*.safetensors, *.bin, 或 *.gguf)")
        logger.info(f"請檢查模型目錄: {model_path}")
        return False
    
    # 檢查 tokenizer 檔案
    tokenizer_files = [
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json"
    ]
    
    missing_tokenizer = []
    for tf in tokenizer_files:
        if not (model_path / tf).exists():
            missing_tokenizer.append(tf)
    
    if missing_tokenizer:
        logger.warning(f"部分 tokenizer 檔案缺失: {', '.join(missing_tokenizer)}")
        logger.info("模型可能仍可正常運行，但建議檢查完整性")
    else:
        logger.success("✓ Tokenizer 檔案完整")
    
    return True


def check_port_available(port: int, logger) -> bool:
    """檢查端口是否可用。"""
    import socket
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    
    try:
        # 嘗試綁定端口
        sock.bind(("0.0.0.0", port))
        sock.close()
        logger.success(f"✓ 端口 {port} 可用")
        return True
    except OSError as e:
        sock.close()
        logger.error(f"端口 {port} 不可用: {e}")
        
        # 嘗試找出佔用進程（Linux）
        try:
            result = subprocess.run(
                ["lsof", "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout:
                logger.info(f"佔用端口的進程:\n{result.stdout}")
            else:
                logger.info(f"提示: 使用 'lsof -i :{port}' 或 'netstat -tunlp | grep {port}' 檢查佔用進程")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.info(f"提示: 使用 'netstat -tunlp | grep {port}' 檢查佔用進程")
        
        return False


def check_runtime_cache_permissions(logger) -> bool:
    """檢查 HF/Transformers runtime cache 目錄可寫性。"""
    cache_keys = [
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HOME",
        "HF_MODULES_CACHE",
    ]
    for key in cache_keys:
        raw = os.environ.get(key)
        if not raw:
            continue
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
            test_file = path / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
        except Exception as exc:
            logger.error(f"快取目錄不可寫: {key}={path}")
            logger.info(f"請修正目錄權限或改用可寫路徑，錯誤: {exc}")
            return False
    return True


def check_native_build_toolchain(logger) -> bool:
    """檢查 Triton 原生編譯依賴（gcc 與 Python.h）。"""
    gcc_path = shutil.which("gcc")
    if not gcc_path:
        logger.error("缺少 gcc，Triton 無法編譯 CUDA 驅動模組")
        logger.info("Ubuntu/Debian: sudo apt-get update && sudo apt-get install -y build-essential")
        return False

    include_dir_raw = sysconfig.get_paths().get("include", "")
    include_dir = Path(include_dir_raw) if include_dir_raw else None
    header_path = include_dir / "Python.h" if include_dir else None
    if header_path is None or not header_path.exists():
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        logger.error(
            "缺少 Python 開發標頭 Python.h，"
            "Triton 初始化會失敗（常見錯誤: fatal error: Python.h: No such file or directory）"
        )
        if header_path is not None:
            logger.info(f"預期標頭位置: {header_path}")
        logger.info(
            "Ubuntu/Debian: sudo apt-get update && "
            f"sudo apt-get install -y python{py_ver}-dev build-essential"
        )
        logger.info("若套件不存在，改用: sudo apt-get install -y python3-dev build-essential")
        return False

    return True


def quick_start_cluster(
    wait_ready: bool = True,
    timeout: int = 1800,
    base_env: str = ".env",
    models_json: str = "models.json",
    skip_check: bool = False,
    startup_delay: float = 5.0,
    start_gateway: bool = True,
    gateway_ready_timeout: int = 60,
    quantization: str = "",
    tool_call_parser: str = "",
    reasoning_parser: str = "",
    kv_cache_dtype: str = "",
) -> ClusterRuntime | None:
    """一次啟動三模型集群。
    
    Args:
        wait_ready: 是否等待所有模型就緒
        timeout: 單個模型等待就緒的超時秒數
        base_env: 基礎設定檔路徑（包含共用配置與 Gateway 配置）
        models_json: 模型配置 JSON 檔案路徑
        skip_check: 跳過預啟動檢查
        startup_delay: 串行模式下每個模型完成後的額外等待秒數
        start_gateway: 是否啟動 Gateway
        gateway_ready_timeout: Gateway 健康檢查超時秒數
        quantization: vLLM --quantization 覆寫值（空字串表示不啟用）
        tool_call_parser: vLLM --tool-call-parser 覆寫值（空字串表示不啟用）
        reasoning_parser: vLLM --reasoning-parser 覆寫值（空字串表示不啟用）
        kv_cache_dtype: vLLM --kv-cache-dtype 覆寫值（空字串表示不啟用）
    """
    logger = get_logger("ClusterLauncher")

    try:
        cli_overrides = {
            "quantization": quantization,
            "tool_call_parser": tool_call_parser,
            "reasoning_parser": reasoning_parser,
            "kv_cache_dtype": kv_cache_dtype,
        }
        instances = load_model_instances(
            base_env_file=base_env,
            models_json_file=models_json,
            cli_overrides=cli_overrides,
        )
        validate_cluster_resources(instances)
        gateway_config = load_gateway_config(base_env_file=base_env)
        routes = build_gateway_routes(instances)
    except Exception as exc:
        logger.error(f"載入集群設定失敗: {exc}")
        return None

    if not skip_check:
        logger.section("Cluster 預檢查")
        for instance in instances:
            logger.info(
                f"檢查 {instance.alias}: {instance.settings.model_name} "
                f"({instance.settings.api_host}:{instance.settings.api_port})"
            )
            ok = pre_launch_check(
                settings=instance.settings,
                logger_name=f"PreCheck:{instance.alias}",
            )
            if not ok:
                logger.error(f"模型 {instance.alias} 預檢查失敗，取消啟動")
                return None

    manager = MultiModelEngineManager(instances)
    gateway_runtime: GatewayRuntime | None = None
    try:
        logger.section("Cluster 啟動")
        manager.start_all(
            wait_ready=wait_ready,
            timeout=timeout,
            startup_delay=startup_delay,
        )
        manager.print_status()

        logger.section("Gateway 路由")
        logger.info(f"Gateway: http://{gateway_config.host}:{gateway_config.port}")
        for alias, route in routes.items():
            logger.info(f"{alias} -> {route.base_url} (model={route.model_name})")

        if start_gateway:
            logger.section("Gateway 啟動")
            gateway_runtime = _start_gateway_process(
                gateway_host=gateway_config.host,
                gateway_port=gateway_config.port,
                ready_timeout=gateway_ready_timeout,
                logger=logger,
            )
            logger.info(
                f"Gateway 日誌: {gateway_runtime.log_path}"
            )
        else:
            logger.info("已跳過 Gateway 啟動 (--no-gateway)")

        logger.info("集群啟動完成，按 Ctrl+C 停止所有模型與 Gateway")
        return ClusterRuntime(manager=manager, gateway=gateway_runtime)
    except KeyboardInterrupt:
        logger.warning("集群啟動期間收到中斷信號，正在停止所有模型...")
        _stop_gateway_process(gateway_runtime, logger)
        manager.stop_all()
        raise
    except Exception as exc:
        logger.error(f"集群啟動失敗: {exc}")
        _stop_gateway_process(gateway_runtime, logger)
        manager.stop_all()
        return None


def quick_start_single(
    wait_ready: bool = True,
    timeout: int = 600,
    skip_check: bool = False,
) -> VLLMEngine | None:
    """啟動單一模型 vLLM 主服務。"""
    logger = get_logger("SingleLauncher")
    settings = get_settings()

    if skip_check:
        logger.warning("已跳過預啟動檢查")
    elif not pre_launch_check(settings=settings):
        logger.error("預啟動檢查失敗，取消啟動")
        logger.info("請檢查模型路徑、GPU/CUDA 環境、port 佔用與 logs/ 內的服務日誌")
        return None

    logger.section("啟動單模型 vLLM 服務")
    engine = VLLMEngine(settings=settings)

    try:
        engine.start(wait_ready=wait_ready, timeout=timeout)
        if wait_ready:
            engine.print_status()

            logger.section("啟動後健康檢查")
            health = check_system_health()
            for i in range(health.gpu_count):
                used = health.gpu_memory_used_gb[i]
                total = health.gpu_memory_total_gb[i]
                usage_percent = (used / total * 100) if total > 0 else 0
                logger.info(
                    f"GPU {i} 記憶體使用: "
                    f"{used:.1f}/{total:.1f} GB ({usage_percent:.1f}%)"
                )

        logger.success("單模型 vLLM 服務啟動成功")
        logger.info(f"API 地址: {engine.base_url}")
        logger.info("按 Ctrl+C 停止服務")
        return engine
    except Exception as exc:
        logger.error(f"啟動失敗: {exc}")
        logger.info("請檢查模型路徑、GPU/CUDA 環境、port 佔用與 logs/ 內的服務日誌")
        engine.stop()
        return None


def _run_single_mode(args) -> None:
    """執行單模型模式並阻塞到服務結束。"""
    logger = get_logger("Main")
    logger.info("啟動模式: single（單一模型主服務）")

    engine = quick_start_single(
        wait_ready=not args.no_wait,
        timeout=args.timeout,
        skip_check=args.skip_check,
    )
    if engine is None:
        sys.exit(1)

    try:
        if engine._process:
            engine._process.wait()
    except KeyboardInterrupt:
        logger.info("收到中斷信號，正在停止單模型服務...")
    finally:
        engine.stop()


def _run_cluster_mode(args) -> None:
    """執行多模型 cluster/gateway 模式並阻塞到收到信號。"""
    logger = get_logger("Main")

    logger.info("啟動模式: gateway/cluster（多模型 API Gateway 服務）")
    logger.info(f"共用設定檔: {args.base_env}")
    logger.info(f"模型配置檔: {args.models_json}")
    logger.info(f"啟動 Gateway: {'否' if args.no_gateway else '是'}")
    logger.info("模型啟動策略: 串行模式")
    logger.info(
        "CLI 啟動覆寫: "
        f"quantization='{args.quantization}', "
        f"tool_call_parser='{args.tool_call_parser}', "
        f"reasoning_parser='{args.reasoning_parser}', "
        f"kv_cache_dtype='{args.kv_cache_dtype}'"
    )

    try:
        runtime = quick_start_cluster(
            wait_ready=not args.no_wait,
            timeout=args.timeout,
            base_env=args.base_env,
            models_json=args.models_json,
            skip_check=args.skip_check,
            startup_delay=args.startup_delay,
            start_gateway=not args.no_gateway,
            gateway_ready_timeout=args.gateway_ready_timeout,
            quantization=args.quantization,
            tool_call_parser=args.tool_call_parser,
            reasoning_parser=args.reasoning_parser,
            kv_cache_dtype=args.kv_cache_dtype,
        )
    except KeyboardInterrupt:
        logger.info("收到中斷信號，啟動已取消")
        return

    if runtime is None:
        sys.exit(1)

    # 設定信號處理器以支援優雅關閉
    def signal_handler(signum, frame):
        global _shutdown_requested
        sig_name = signal.Signals(signum).name
        logger.info(f"收到 {sig_name} 信號，正在優雅關閉...")
        _shutdown_requested = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        while not _shutdown_requested:
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("收到中斷信號，正在停止集群與 Gateway...")
    finally:
        logger.info("清理資源中...")
        _stop_gateway_process(runtime.gateway, logger)
        runtime.manager.stop_all()
        logger.info("所有服務已停止")


def main() -> None:
    """主函數"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="vLLM 啟動腳本：支援單模型主服務與多模型 Gateway"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("single", "gateway", "cluster"),
        default=None,
        help=(
            "啟動模式：single=單模型主服務，"
            "gateway/cluster=多模型 Gateway（預設）"
        ),
    )
    parser.add_argument(
        "--cluster",
        action="store_true",
        help="相容舊版旗標（等同 mode=cluster，可省略）",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="不等待服務就緒（後台啟動）"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="等待服務就緒的超時時間（秒）"
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="跳過預啟動檢查"
    )
    parser.add_argument(
        "--base-env",
        type=str,
        default=".env",
        help="集群共用設定檔路徑（預設 .env）"
    )
    parser.add_argument(
        "--models-json",
        type=str,
        default="models.json",
        help="模型配置 JSON 檔案路徑（預設 models.json）"
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=5.0,
        help="串行模式下每個模型完成後的額外等待秒數（預設 5.0）"
    )
    parser.add_argument(
        "--no-gateway",
        action="store_true",
        help="只啟動模型，不啟動 Gateway"
    )
    parser.add_argument(
        "--gateway-ready-timeout",
        type=int,
        default=60,
        help="Gateway 健康檢查超時秒數（預設 60）"
    )
    parser.add_argument(
        "--quantization",
        type=str,
        default="",
        help="覆寫 vLLM --quantization（預設空字串）"
    )
    parser.add_argument(
        "--tool-call-parser",
        type=str,
        default="",
        help="覆寫 vLLM --tool-call-parser（預設空字串）"
    )
    parser.add_argument(
        "--reasoning-parser",
        type=str,
        default="",
        help="覆寫 vLLM --reasoning-parser（預設空字串）"
    )
    parser.add_argument(
        "--kv-cache-dtype",
        type=str,
        default="",
        help="覆寫 vLLM --kv-cache-dtype（預設空字串）"
    )
    args = parser.parse_args()

    if args.cluster and args.mode == "single":
        parser.error("--cluster 不能與 mode=single 同時使用")

    mode = args.mode or "cluster"
    if args.cluster:
        mode = "cluster"

    if mode == "single":
        _run_single_mode(args)
        return

    _run_cluster_mode(args)


if __name__ == "__main__":
    main()
