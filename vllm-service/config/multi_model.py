"""多模型與 Gateway 設定載入工具。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT, SERVICE_ENV_FILE_VAR, Settings

GATEWAY_ENV_FILE_VAR = "VLLM_SERVICE_GATEWAY_ENV_FILE"
DEFAULT_BASE_ENV = ".env.API"
DEFAULT_MODELS_JSON = "models.json"


@dataclass(frozen=True)
class ModelInstanceConfig:
    """單一模型實例設定。"""

    alias: str
    model_config: dict[str, Any]
    settings: Settings

    @property
    def upstream_host(self) -> str:
        if self.settings.api_host in {"0.0.0.0", "::"}:
            return "127.0.0.1"
        return self.settings.api_host

    @property
    def upstream_base_url(self) -> str:
        return f"http://{self.upstream_host}:{self.settings.api_port}/v1"


@dataclass(frozen=True)
class GatewayConfig:
    """Gateway 運行設定。"""

    host: str
    port: int
    request_timeout: int
    max_inflight: int
    default_model: str


@dataclass(frozen=True)
class GatewayRoute:
    """Gateway 路由目標。"""

    alias: str
    model_name: str
    base_url: str
    api_key: str


def _resolve_path(file_path: str | Path) -> Path:
    """解析為絕對路徑。"""
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _default_base_env_file() -> str:
    """取得 Gateway/cluster 模式預設 env 檔。"""
    return (
        os.getenv(GATEWAY_ENV_FILE_VAR)
        or os.getenv(SERVICE_ENV_FILE_VAR)
        or DEFAULT_BASE_ENV
    )


def load_gateway_config(base_env_file: str | Path | None = None) -> GatewayConfig:
    """從 Gateway 共用 env 載入 Gateway 設定。"""
    env_path = _resolve_path(base_env_file or _default_base_env_file())
    if not env_path.exists():
        raise FileNotFoundError(f"環境設定檔不存在: {env_path}")
    
    # 載入 .env 到環境變數
    from dotenv import load_dotenv
    load_dotenv(env_path)
    
    return GatewayConfig(
        host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
        port=int(os.getenv("GATEWAY_PORT", "3000")),
        request_timeout=int(os.getenv("GATEWAY_REQUEST_TIMEOUT", "300")),
        max_inflight=int(os.getenv("GATEWAY_MAX_INFLIGHT", "48")),
        default_model=os.getenv("GATEWAY_DEFAULT_MODEL", ""),
    )


def load_model_instances(
    base_env_file: str | Path | None = None,
    models_json_file: str | Path = DEFAULT_MODELS_JSON,
    cli_overrides: dict[str, str] | None = None,
) -> list[ModelInstanceConfig]:
    """載入多模型實例設定。
    
    Args:
        base_env_file: 共用環境變數檔案路徑（預設 .env.API）
        models_json_file: 模型配置 JSON 檔案路徑（預設 models.json）
        cli_overrides: 由 main.py 傳入的命令列參數覆寫值
    
    Returns:
        模型實例配置列表
    """
    base_path = _resolve_path(base_env_file or _default_base_env_file())
    models_json_path = _resolve_path(models_json_file)
    
    if not base_path.exists():
        raise FileNotFoundError(f"集群共用設定檔不存在: {base_path}")
    if not models_json_path.exists():
        raise FileNotFoundError(f"模型配置檔不存在: {models_json_path}")
    
    # 載入 models.json
    with open(models_json_path, "r", encoding="utf-8") as f:
        models_config = json.load(f)
    
    if not isinstance(models_config, list):
        raise ValueError(f"models.json 格式錯誤：應為陣列，實際為 {type(models_config)}")
    
    # 載入 .env 到環境變數
    from dotenv import load_dotenv
    load_dotenv(base_path, override=False)
    
    instances: list[ModelInstanceConfig] = []
    seen_alias: set[str] = set()
    seen_port: set[int] = set()
    
    for idx, model_config in enumerate(models_config):
        if not isinstance(model_config, dict):
            raise ValueError(f"模型配置 #{idx} 格式錯誤：應為物件")

        effective_model_config = dict(model_config)
        if cli_overrides:
            # 僅套用非空覆寫值，避免空字串覆蓋 models.json 的既有設定。
            normalized_overrides = {
                key: value.strip()
                for key, value in cli_overrides.items()
                if isinstance(value, str) and value.strip()
            }
            effective_model_config.update(normalized_overrides)
        
        alias = effective_model_config.get("alias", "").strip()
        if not alias:
            raise ValueError(f"模型配置 #{idx} 缺少 'alias' 欄位")
        
        if alias in seen_alias:
            raise ValueError(f"MODEL_ALIAS 重複: {alias}")
        
        # 建立 Settings，使用模型配置覆蓋 .env 的值
        # 需要將 JSON 的 snake_case 轉為環境變數格式
        model_env_overrides = {}
        
        # 對應關係
        field_mapping = {
            "model_name": "MODEL_NAME",
            "api_port": "API_PORT",
            "max_model_len": "MAX_MODEL_LEN",
            "gpu_memory_utilization": "GPU_MEMORY_UTILIZATION",
            "max_num_seqs": "MAX_NUM_SEQS",
            "max_num_batched_tokens": "MAX_NUM_BATCHED_TOKENS",
            "tiktoken_encodings_base": "TIKTOKEN_ENCODINGS_BASE",
            "dtype": "DTYPE",
            "tensor_parallel_size": "TENSOR_PARALLEL_SIZE",
            "enforce_eager": "ENFORCE_EAGER",
            "enable_prefix_caching": "ENABLE_PREFIX_CACHING",
            "disable_log_requests": "DISABLE_LOG_REQUESTS",
            "disable_custom_all_reduce": "DISABLE_CUSTOM_ALL_REDUCE",
            "quantization": "QUANTIZATION",
            "kv_cache_dtype": "KV_CACHE_DTYPE",
            "speculative_config": "SPECULATIVE_CONFIG",
            "vllm_nvfp4_gemm_backend": "VLLM_NVFP4_GEMM_BACKEND",
            "allowed_local_media_path": "ALLOWED_LOCAL_MEDIA_PATH",
            "enable_auto_tool_choice": "ENABLE_AUTO_TOOL_CHOICE",
            "tool_call_parser": "TOOL_CALL_PARSER",
            "reasoning_parser": "REASONING_PARSER",
            "chat_template": "CHAT_TEMPLATE",
            "limit_mm_per_prompt": "LIMIT_MM_PER_PROMPT",
            "moe_backend": "MOE_BACKEND",
        }
        
        for json_key, env_key in field_mapping.items():
            if json_key in effective_model_config:
                model_env_overrides[env_key] = str(effective_model_config[json_key])
        
        # 臨時設定環境變數（在 Settings 初始化時會被讀取）
        original_env = {}
        for env_key, value in model_env_overrides.items():
            original_env[env_key] = os.environ.get(env_key)
            os.environ[env_key] = value
        
        try:
            settings = Settings(_env_file=str(base_path))
        finally:
            # 恢復原始環境變數
            for env_key, original_value in original_env.items():
                if original_value is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = original_value
        
        if settings.api_port in seen_port:
            raise ValueError(f"API_PORT 重複: {settings.api_port} (模型: {alias})")
        
        seen_alias.add(alias)
        seen_port.add(settings.api_port)
        
        instances.append(
            ModelInstanceConfig(
                alias=alias,
                model_config=effective_model_config,
                settings=settings,
            )
        )
    
    return instances


def build_gateway_routes(instances: list[ModelInstanceConfig]) -> dict[str, GatewayRoute]:
    """由模型實例建立 Gateway 路由表。"""
    routes: dict[str, GatewayRoute] = {}
    for instance in instances:
        routes[instance.alias] = GatewayRoute(
            alias=instance.alias,
            model_name=instance.settings.resolved_model_path,
            base_url=instance.upstream_base_url,
            api_key=instance.settings.api_key,
        )
    return routes


def validate_cluster_resources(instances: list[ModelInstanceConfig]) -> None:
    """驗證多模型資源配置，避免明顯 OOM。"""
    total_gpu_util = sum(i.settings.gpu_memory_utilization for i in instances)
    hard_limit = float(os.getenv("CLUSTER_GPU_UTIL_HARD_LIMIT", "0.95"))
    if total_gpu_util >= hard_limit:
        raise ValueError(
            "三模型 GPU_MEMORY_UTILIZATION 總和過高: "
            f"{total_gpu_util:.2f} >= {hard_limit:.2f}"
        )


def find_route_for_model(
    model: str,
    routes: dict[str, GatewayRoute],
) -> GatewayRoute | None:
    """依 alias 或實際模型名稱尋找路由。
    
    查找優先順序：
    1. 完全匹配 alias
    2. 完全匹配 model_name
    3. 部分匹配 alias（大小寫不敏感）
    4. 部分匹配 model_name（路徑末段）
    """
    # 1. 完全匹配 alias
    if model in routes:
        return routes[model]
    
    # 2. 完全匹配 model_name
    for route in routes.values():
        if model == route.model_name:
            return route
    
    # 3. 大小寫不敏感匹配 alias
    model_lower = model.lower()
    for alias, route in routes.items():
        if model_lower == alias.lower():
            return route
    
    # 4. 部分匹配 model_name 的最後一段路徑名
    for route in routes.values():
        # 從 ./AImodels/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 提取 NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
        model_basename = route.model_name.rstrip("/").split("/")[-1]
        if model_lower == model_basename.lower():
            return route
        # 也檢查用戶輸入的是否是路徑
        user_basename = model.rstrip("/").split("/")[-1]
        if user_basename.lower() == model_basename.lower():
            return route
    
    return None


def get_available_models_help(routes: dict[str, GatewayRoute]) -> str:
    """生成可用模型的幫助訊息。"""
    lines = ["可用模型:"]
    for alias, route in sorted(routes.items()):
        model_basename = route.model_name.rstrip("/").split("/")[-1]
        lines.append(f"  • {alias} ({model_basename})")
    return "\n".join(lines)
