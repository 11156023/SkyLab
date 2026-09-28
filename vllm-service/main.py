#!/usr/bin/env python3
"""
快速啟動腳本 - 自動檢查和啟動 vLLM 服務
包含預啟動檢查、健康監控和錯誤診斷
"""

from __future__ import annotations

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

# 確保專案根目錄在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.multi_model import (
    DEFAULT_BASE_ENV,
    DEFAULT_MODELS_JSON,
    load_model_instances,
    validate_cluster_resources,
)
from config.settings import SERVICE_ENV_FILE_VAR, get_settings, resolve_env_file
from core.cluster import MultiModelEngineManager
from core.engine import VLLMEngine
from utils.health_utils import check_system_health
from utils.logging_utils import get_logger


# 全局 shutdown 標記
_shutdown_requested = False


def _request_shutdown(signum, frame) -> None:
    """把 SIGTERM／SIGINT 轉成 KeyboardInterrupt，讓既有 except/finally 停掉 vLLM。

    vLLM 以 start_new_session 啟動，launcher 若被 SIGTERM 以預設動作結束，
    子程序不會跟著停（nohup 背景執行時 SIGINT 也被忽略）。只在第一次收到時
    丟出例外；清理期間再收到信號就忽略，避免打斷 engine.stop()／stop_all()。
    """
    global _shutdown_requested
    if _shutdown_requested:
        return
    _shutdown_requested = True
    raise KeyboardInterrupt(signal.Signals(signum).name)


def _install_shutdown_handlers() -> None:
    """在任何模型啟動前安裝停止信號處理。

    SIGHUP 刻意不處理：start_*.sh 以 nohup 忽略 SIGHUP，讓服務撐過 SSH 登出。
    """
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)


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
    if not check_port_available(settings.api_host, settings.api_port, logger):
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


def check_port_available(host: str, port: int, logger) -> bool:
    """檢查端口是否可用。"""
    import socket
    
    # IPv6 位址（含 ::）要用 AF_INET6，否則 bind 會丟 gaierror 而誤判為佔用。
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    try:
        # 嘗試綁定端口
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.bind((host.strip("[]"), port))
        logger.success(f"✓ 端口 {host}:{port} 可用")
        return True
    except OSError as e:
        logger.error(f"端口 {host}:{port} 不可用: {e}")
        
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
    base_env: str = DEFAULT_BASE_ENV,
    models_json: str = DEFAULT_MODELS_JSON,
    skip_check: bool = False,
    startup_delay: float = 5.0,
    quantization: str = "",
    tool_call_parser: str = "",
    reasoning_parser: str = "",
    kv_cache_dtype: str = "",
) -> MultiModelEngineManager | None:
    """一次啟動所有本機模型；對外路由（含遠端模型）交由 LiteLLM。
    
    Args:
        wait_ready: 是否等待所有模型就緒
        timeout: 單個模型等待就緒的超時秒數
        base_env: 基礎設定檔路徑（各模型共用的配置）
        models_json: 模型配置 JSON 檔案路徑
        skip_check: 跳過預啟動檢查
        startup_delay: 串行模式下每個模型完成後的額外等待秒數
        quantization: vLLM --quantization 覆寫值（空字串表示不啟用）
        tool_call_parser: vLLM --tool-call-parser 覆寫值（空字串表示不啟用）
        reasoning_parser: vLLM --reasoning-parser 覆寫值（空字串表示不啟用）
        kv_cache_dtype: vLLM --kv-cache-dtype 覆寫值（空字串表示不啟用）
    """
    logger = get_logger("ClusterLauncher")
    base_env_path = resolve_env_file(base_env)
    os.environ[SERVICE_ENV_FILE_VAR] = str(base_env_path)

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
        if not instances:
            logger.error("沒有本機模型可啟動；全遠端部署請直接啟動 LiteLLM Compose")
            return None
        validate_cluster_resources(instances)
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
    try:
        logger.section("Cluster 啟動")
        manager.start_all(
            wait_ready=wait_ready,
            timeout=timeout,
            startup_delay=startup_delay,
        )
        manager.print_status()
        logger.info("集群啟動完成，對外 API 由 LiteLLM 提供；按 Ctrl+C 停止所有模型")
        return manager
    except KeyboardInterrupt:
        logger.warning("集群啟動期間收到中斷信號，正在停止所有模型...")
        manager.stop_all()
        raise
    except Exception as exc:
        logger.error(f"集群啟動失敗: {exc}")
        manager.stop_all()
        return None


def quick_start_single(
    wait_ready: bool = True,
    timeout: int = 600,
    skip_check: bool = False,
    env_file: str = ".env.interface",
) -> VLLMEngine | None:
    """啟動單一模型 vLLM 主服務。"""
    logger = get_logger("SingleLauncher")
    env_path = resolve_env_file(env_file)
    os.environ[SERVICE_ENV_FILE_VAR] = str(env_path)
    settings = get_settings(env_file=env_path)

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
    except BaseException:
        # 等待就緒期間被中斷（SIGTERM／Ctrl+C）：呼叫端拿不到 engine，這裡就要停掉子程序。
        logger.warning("單模型啟動期間收到中斷信號，正在停止 vLLM...")
        engine.stop()
        raise


def _run_single_mode(args) -> None:
    """執行單模型模式並阻塞到服務結束。"""
    logger = get_logger("Main")
    logger.info("啟動模式: single（單一模型主服務）")
    logger.info(f"單模型設定檔: {args.env_file}")

    try:
        engine = quick_start_single(
            wait_ready=not args.no_wait,
            timeout=args.timeout,
            skip_check=args.skip_check,
            env_file=args.env_file,
        )
    except KeyboardInterrupt:
        logger.info("收到中斷信號，啟動已取消")
        return
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
    """執行多模型 cluster 模式並阻塞到服務結束。"""
    logger = get_logger("Main")

    logger.info("啟動模式: 多模型 vLLM cluster")
    logger.info(f"共用設定檔: {args.base_env}")
    logger.info(f"模型配置檔: {args.models_json}")
    logger.info("模型啟動策略: 串行模式")
    logger.info(
        "CLI 啟動覆寫: "
        f"quantization='{args.quantization}', "
        f"tool_call_parser='{args.tool_call_parser}', "
        f"reasoning_parser='{args.reasoning_parser}', "
        f"kv_cache_dtype='{args.kv_cache_dtype}'"
    )

    try:
        manager = quick_start_cluster(
            wait_ready=not args.no_wait,
            timeout=args.timeout,
            base_env=args.base_env,
            models_json=args.models_json,
            skip_check=args.skip_check,
            startup_delay=args.startup_delay,
            quantization=args.quantization,
            tool_call_parser=args.tool_call_parser,
            reasoning_parser=args.reasoning_parser,
            kv_cache_dtype=args.kv_cache_dtype,
        )
    except KeyboardInterrupt:
        logger.info("收到中斷信號，啟動已取消")
        return

    if manager is None:
        sys.exit(1)

    # SIGTERM／SIGINT 由 main() 開頭安裝的 _request_shutdown 轉成 KeyboardInterrupt。
    try:
        while not _shutdown_requested:
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("收到中斷信號，正在停止集群...")
    finally:
        logger.info("清理資源中...")
        manager.stop_all()
        logger.info("所有服務已停止")


def main() -> None:
    """主函數"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description=(
            "vLLM 啟動腳本：支援單模型與多模型 cluster；"
            "多模型的對外 API 由 LiteLLM 提供（見 litellm/）"
        )
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("single", "cluster", "gateway"),
        default=None,
        help=(
            "啟動模式：single=單模型主服務，cluster=多模型 vLLM（預設）；"
            "gateway 是已移除的舊 FastAPI Gateway 模式，指定時會直接報錯"
        ),
    )
    parser.add_argument(
        "--cluster",
        action="store_true",
        help="舊版相容旗標，不影響行為（多模型本來就是預設模式，可省略）",
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
        "--env-file",
        type=str,
        default=".env.interface",
        help="單模型主服務設定檔路徑（預設 .env.interface）",
    )
    parser.add_argument(
        "--base-env",
        type=str,
        default=DEFAULT_BASE_ENV,
        help=f"cluster 共用設定檔路徑（預設 {DEFAULT_BASE_ENV}）"
    )
    parser.add_argument(
        "--models-json",
        type=str,
        default=DEFAULT_MODELS_JSON,
        help=f"模型配置 JSON 檔案路徑（預設 {DEFAULT_MODELS_JSON}）"
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=5.0,
        help="串行模式下每個模型完成後的額外等待秒數（預設 5.0）"
    )
    # 舊 Gateway 已移除；這兩個旗標只為既有指令（如舊版 start_multi_model_cluster.sh
    # 或部署主機上的自訂腳本帶的 --no-gateway）相容而接受，不影響行為，也不列在說明中。
    parser.add_argument("--no-gateway", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gateway-ready-timeout", type=int, default=None, help=argparse.SUPPRESS)
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
    if args.mode == "gateway":
        parser.error(
            "舊 FastAPI Gateway 已移除：多模型請用 cluster 模式（start_multi_model_cluster.sh），"
            "對外 API 改由 LiteLLM 提供（見 litellm/README.md）"
        )

    # --cluster 只是舊版旗標，不影響結果。
    mode = args.mode or "cluster"

    _install_shutdown_handlers()

    if mode == "single":
        _run_single_mode(args)
        return

    _run_cluster_mode(args)


if __name__ == "__main__":
    main()
