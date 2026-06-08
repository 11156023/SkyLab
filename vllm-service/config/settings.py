"""
設定層 - 使用 pydantic-settings 實現 .env > config 預設值 的優先級
所有參數集中管理，支持環境變數覆蓋
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.model_utils import is_vision_model

# 專案根目錄
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
ENV_FILE = DEFAULT_ENV_FILE
SERVICE_ENV_FILE_VAR = "VLLM_SERVICE_ENV_FILE"


def resolve_env_file(env_file: str | Path | None = None) -> Path:
    """解析目前服務使用的 env 檔案路徑。"""
    configured = env_file or os.getenv(SERVICE_ENV_FILE_VAR) or DEFAULT_ENV_FILE
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


class Settings(BaseSettings):
    """應用程式設定 - .env 變數自動覆蓋預設值"""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 模型設定 ----
    model_name: str = Field(
        default="nvidia/Qwen3-235B-A22B-NVFP4",
        description="HuggingFace 模型名稱或路徑",
    )
    hf_cache_dir: str = Field(
        default="/raid/hf-cache/hub",
        description="HuggingFace 快取目錄 (共用)",
    )
    trust_remote_code: bool = Field(
        default=True,
        description="是否信任遠端模型程式碼",
    )

    # ---- 伺服器設定 ----
    api_host: str = Field(default="0.0.0.0", description="API 監聽地址")
    api_port: int = Field(default=8000, description="API 監聽埠", ge=1, le=65535)
    api_key: str = Field(
        default="vllm-secret-key-change-me",
        description="API 認證金鑰",
    )

    # ---- vLLM 引擎參數 ----
    dtype: str = Field(default="auto", description="模型資料類型")
    max_model_len: int = Field(default=4096, description="最大模型上下文長度", ge=128)
    gpu_memory_utilization: float = Field(
        default=0.95,
        description="GPU 記憶體使用率",
        ge=0.1,
        le=1.0,
    )
    max_num_seqs: int = Field(default=64, description="最大併發序列數", ge=1)
    max_num_batched_tokens: int = Field(default=8192, description="單批次最大 token 數", ge=128)
    tensor_parallel_size: int = Field(default=1, description="張量並行大小", ge=1)
    enforce_eager: bool = Field(default=False, description="強制使用 eager 模式")
    enable_prefix_caching: bool = Field(default=True, description="啟用前綴快取")
    disable_log_requests: bool = Field(default=False, description="停用請求日誌")
    disable_custom_all_reduce: bool = Field(default=False, description="停用自定義 all-reduce (提高穩定性)")
    quantization: str = Field(default="", description="量化方法 (awq, gptq, fp8 等)")
    kv_cache_dtype: str = Field(default="", description="KV Cache 資料型別 (fp8, auto 等)")
    speculative_config: str = Field(
        default="",
        description="Speculative decoding 設定 (JSON 字串)，對應 --speculative-config",
    )
    vllm_nvfp4_gemm_backend: str = Field(
        default="",
        description="NVFP4 GEMM backend (如 marlin)；空值表示交由 vLLM 自動選擇",
    )
    enable_auto_tool_choice: bool = Field(default=False, description="啟用自動工具呼叫 (Function Calling / Tool Use)")
    tool_call_parser: str = Field(default="", description="工具呼叫解析器 (openai, mistral, hermes, llama3_json 等)")
    reasoning_parser: str = Field(
        default="",
        description="Reasoning 輸出解析器 (qwen3, deepseek_r1 等)",
        validation_alias=AliasChoices("REASONING_PARSER", "reasoning-parser", "reasoning_parser"),
    )
    chat_template: str = Field(
        default="",
        description="Chat template 路徑 (Jinja2 格式)；啟用 tool-call-parser 或 reasoning-parser 時通常必填",
    )
    limit_mm_per_prompt: str = Field(
        default="",
        description='每個 prompt 允許的多模態輸入上限，JSON 格式，例如 \'{"image": 5, "video": 1, "audio": 0}\'',
    )
    moe_backend: str = Field(
        default="",
        description="MoE expert 層的計算後端 (marlin 等)；空值表示 vLLM 自動選擇",
    )

    # ---- 併發與效能 ----
    uvicorn_workers: int = Field(default=1, description="Uvicorn worker 數", ge=1)
    request_timeout: int = Field(default=300, description="請求逾時秒數", ge=10)

    # ---- Benchmark 設定 ----
    bench_total_requests: int = Field(default=60, description="Benchmark 總請求數", ge=1)
    bench_concurrency: int = Field(default=64, description="Benchmark 併發數", ge=1)
    bench_max_tokens: int = Field(default=2048, description="Benchmark 每次最大 token 數", ge=1)
    bench_prompt: str = Field(
        default="請用繁體中文簡要介紹什麼是人工智慧？",
        description="Benchmark 使用的 prompt",
    )

    # ---- Webapp 推論參數 (統一管理，避免散落硬編碼) ----
    default_max_tokens: int = Field(default=2048, description="預設最大生成 token 數", ge=128)
    default_temperature: float = Field(default=0.8, description="預設溫度參數", ge=0.0, le=2.0)
    document_max_tokens: int = Field(default=16384, description="文件模式最大 token 數", ge=512)
    vision_temperature: float = Field(default=1.0, description="視覺模式溫度", ge=0.0, le=2.0)
    default_top_p: float = Field(default=0.95, description="Top-P 取樣（0.0-1.0）", ge=0.0, le=1.0)
    default_top_k: int = Field(default=20, description="Top-K 取樣（vLLM 擴展參數，-1 表示停用）", ge=-1)
    default_min_p: float = Field(default=0.0, description="Min-P 取樣（vLLM 擴展參數，0.0 表示停用）", ge=0.0, le=1.0)
    default_presence_penalty: float = Field(default=1.5, description="存在懲罰，鼓勵新主題（0.0-2.0）", ge=0.0, le=2.0)
    default_repetition_penalty: float = Field(default=1.1, description="重複懲罰（vLLM 擴展參數，1.0=無懲罰）", ge=0.0)

    # ---- 視覺模型設定 ----
    max_image_size: int = Field(default=2048, description="最大圖片尺寸 (px)", ge=256)
    enable_image_resize: bool = Field(default=True, description="自動調整圖片大小")
    allowed_local_media_path: str = Field(
        default="/",
        description="允許 vLLM 讀取本機媒體檔的目錄。預設 '/' 表示允許任意路徑（對應 --allowed-local-media-path）",
    )

    # ---- 影片模型設定 ----
    video_fps: float = Field(
        default=1.0,
        description="影片抽幀速率（幀/秒）。降低可節省 Token；0 表示取全部原始幀",
        ge=0.0,
    )
    max_video_frames_per_chunk: int = Field(
        default=64,
        description="每段最大幀數上限。超過此數自動切分多段推論（搭配 131K context）",
        ge=1,
    )
    max_video_frame_size: int = Field(
        default=768,
        description="影片幀縮放尺寸（長邊 px）。比圖片稍小以容納更多幀",
        ge=128,
    )
    video_frame_quality: int = Field(
        default=80,
        description="影片幀 JPEG 壓縮品質（1-100）",
        ge=1,
        le=100,
    )
    video_chunk_prompt: str = Field(
        default=(
            "這是影片的第 {chunk_index}/{total_chunks} 段（"
            "{start_sec:.1f}s ~ {end_sec:.1f}s，"
            "共 {num_frames} 幀）。"
            "請詳細描述這段影片的畫面內容、動作、場景變化。"
        ),
        description="分段推論時每段使用的 prompt 模板，支援格式化欄位",
    )
    video_merge_prompt: str = Field(
        default=(
            "以下是同一段影片分 {total_chunks} 段分析的結果：\n\n"
            "{summaries}\n\n"
            "請根據以上各段描述，整合成完整連貫的影片內容分析，然後回答用戶的問題：\n"
            "{user_question}"
        ),
        description="多段推論彙整時的 prompt 模板",
    )

    # ---- HuggingFace 設定 ----
    hf_hub_offline: int = Field(default=0, description="HuggingFace 離線模式")
    vllm_usage_stats_enabled: int = Field(default=0, description="vLLM 使用統計")
    tokenizers_parallelism: bool = Field(default=False, description="Tokenizer 平行化")

    # ---- Triton/CUDA 設定 ----
    triton_ptxas_path: str = Field(default="/usr/local/cuda/bin/ptxas", description="Triton 使用的 ptxas 路徑")
    cuda_home: str = Field(default="/usr/local/cuda", description="CUDA 安裝目錄")
    triton_cache_dir: str = Field(default="/tmp/triton_cache", description="Triton kernel cache 目錄")
    tiktoken_encodings_base: str = Field(
        default="",
        description="gpt-oss 專用 tiktoken encodings 目錄（對應 TIKTOKEN_ENCODINGS_BASE）",
    )

    # 快取已解析的模型路徑
    _cached_model_path: str | None = None

    @field_validator(
        "quantization",
        "kv_cache_dtype",
        "speculative_config",
        "vllm_nvfp4_gemm_backend",
        "tool_call_parser",
        "reasoning_parser",
        "chat_template",
        "limit_mm_per_prompt",
        "moe_backend",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: str) -> str:
        """空字串視為無量化"""
        return v.strip() if v else ""
    
    @field_validator("gpu_memory_utilization")
    @classmethod
    def validate_gpu_memory(cls, v: float) -> float:
        """驗證 GPU 記憶體使用率"""
        if v < 0.1 or v > 1.0:
            raise ValueError(f"gpu_memory_utilization 必須在 0.1 到 1.0 之間，當前值: {v}")
        return v
    
    @field_validator("max_model_len")
    @classmethod
    def validate_max_model_len(cls, v: int) -> int:
        """驗證最大上下文長度"""
        if v < 128:
            raise ValueError(f"max_model_len 必須至少為 128，當前值: {v}")
        if v > 128000:
            import warnings
            warnings.warn(f"max_model_len={v} 過大，可能導致 OOM")
        return v

    @property
    def resolved_model_path(self) -> str:
        """解析模型實際路徑 - 優先使用快取中的本地目錄（帶快取）"""
        # 使用快取避免重複查找
        if self._cached_model_path is not None:
            return self._cached_model_path
        
        # 如果 model_name 是本機路徑（相對或絕對）且存在，直接使用
        model_path = Path(self.model_name)
        if model_path.exists():
            self._cached_model_path = str(model_path.resolve())
            return self._cached_model_path
        
        # 否則嘗試從 HF cache 中查找
        cache_dir = Path(self.hf_cache_dir)
        if not cache_dir.exists():
            # 快取目錄不存在，返回模型名稱
            self._cached_model_path = self.model_name
            return self._cached_model_path
        
        model_dir_name = f"models--{self.model_name.replace('/', '--')}"
        model_cache_path = cache_dir / model_dir_name

        # 檢查快取目錄是否存在且包含有效模型文件
        if model_cache_path.exists():
            # 檢查是否有 snapshots 目錄（HF 快取結構）
            snapshots_dir = model_cache_path / "snapshots"
            if snapshots_dir.exists():
                # 找到最新的 snapshot
                snapshots = list(snapshots_dir.iterdir())
                if snapshots:
                    # 使用最新的 snapshot（按修改時間排序）
                    latest_snapshot = max(snapshots, key=lambda p: p.stat().st_mtime)
                    self._cached_model_path = str(latest_snapshot)
                    return self._cached_model_path
            
            # 如果沒有 snapshots 結構，直接使用快取路徑
            self._cached_model_path = str(model_cache_path)
            return self._cached_model_path

        # 快取不存在，返回模型名稱讓 vLLM 嘗試下載
        self._cached_model_path = self.model_name
        return self._cached_model_path

    def _is_vision_model(self) -> bool:
        """檢測當前模型是否為視覺模型"""
        return is_vision_model(self.model_name)

    def _requires_tiktoken_encodings(self) -> bool:
        """判斷是否需要注入 TIKTOKEN_ENCODINGS_BASE（僅 gpt-oss）。"""
        candidates = [self.model_name, self.resolved_model_path]
        for candidate in candidates:
            if "gpt-oss" in candidate.lower():
                return True
        return False

    def _resolve_tiktoken_encodings_base(self) -> str | None:
        """解析 gpt-oss 需要的 encodings 目錄；未設定時回傳 None。"""
        configured = self.tiktoken_encodings_base.strip()
        if not configured:
            return None

        expanded = os.path.expandvars(os.path.expanduser(configured))
        if "$" in expanded:
            return None

        configured_path = Path(expanded)
        if not configured_path.is_absolute():
            configured_path = (PROJECT_ROOT / configured_path).resolve()
        return str(configured_path)

    def build_vllm_serve_args(self) -> list[str]:
        """構建 vllm serve 命令列參數"""
        args = [
            "--model", self.resolved_model_path,
            "--host", self.api_host,
            "--port", str(self.api_port),
            "--dtype", self.dtype,
            "--max-model-len", str(self.max_model_len),
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
            "--max-num-seqs", str(self.max_num_seqs),
            "--max-num-batched-tokens", str(self.max_num_batched_tokens),
            "--tensor-parallel-size", str(self.tensor_parallel_size),
            "--api-key", self.api_key,
        ]

        if self.trust_remote_code:
            args.append("--trust-remote-code")
        if self.enforce_eager:
            args.append("--enforce-eager")
        if self.enable_prefix_caching:
            args.append("--enable-prefix-caching")
        if self.disable_log_requests:
            args.append("--disable-log-requests")
        if self.disable_custom_all_reduce:
            args.append("--disable-custom-all-reduce")
        if self.quantization:
            args.extend(["--quantization", self.quantization])
        if self.kv_cache_dtype:
            args.extend(["--kv-cache-dtype", self.kv_cache_dtype])
        if self.speculative_config:
            args.extend(["--speculative-config", self.speculative_config])
        if self.allowed_local_media_path:
            args.extend(["--allowed-local-media-path", self.allowed_local_media_path])
        tool_call_parser = self.tool_call_parser.strip()
        if self.enable_auto_tool_choice and not tool_call_parser:
            raise ValueError(
                "設定衝突: ENABLE_AUTO_TOOL_CHOICE=True 時必須設定 TOOL_CALL_PARSER"
            )
        if self.enable_auto_tool_choice:
            args.append("--enable-auto-tool-choice")
        if tool_call_parser:
            args.extend(["--tool-call-parser", tool_call_parser])
        if self.reasoning_parser:
            args.extend(["--reasoning-parser", self.reasoning_parser])
        if self.chat_template:
            tmpl_path = Path(self.chat_template)
            if not tmpl_path.is_absolute():
                tmpl_path = PROJECT_ROOT / tmpl_path
            args.extend(["--chat-template", str(tmpl_path)])
        if self.limit_mm_per_prompt:
            args.extend(["--limit-mm-per-prompt", self.limit_mm_per_prompt])
        if self.moe_backend:
            args.extend(["--moe-backend", self.moe_backend])

        return args

    def inject_env_vars(self) -> None:
        """注入環境變數到當前程序"""
        cache_dir = Path(self.hf_cache_dir)
        if not cache_dir.is_absolute():
            cache_dir = (PROJECT_ROOT / cache_dir).resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)

        runtime_hf_home = PROJECT_ROOT / ".runtime" / "huggingface"
        runtime_hf_home.mkdir(parents=True, exist_ok=True)
        modules_cache = runtime_hf_home / "modules" / "transformers_modules"
        modules_cache.mkdir(parents=True, exist_ok=True)

        os.environ["HF_HUB_CACHE"] = str(cache_dir)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_dir)
        os.environ["HF_HOME"] = str(runtime_hf_home)
        os.environ["HF_MODULES_CACHE"] = str(modules_cache)
        os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", "")
        os.environ.setdefault("HF_HUB_OFFLINE", str(self.hf_hub_offline))
        os.environ.setdefault("VLLM_USAGE_STATS_ENABLED", str(self.vllm_usage_stats_enabled))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", str(self.tokenizers_parallelism).lower())
        
        # Triton/CUDA 設定 - 對 Blackwell 等新 GPU 至關重要
        if self.triton_ptxas_path:
            os.environ.setdefault("TRITON_PTXAS_PATH", self.triton_ptxas_path)
        if self.cuda_home:
            os.environ.setdefault("CUDA_HOME", self.cuda_home)
        if self.triton_cache_dir:
            os.environ.setdefault("TRITON_CACHE_DIR", self.triton_cache_dir)

        # 僅 gpt-oss 需要額外的 tiktoken encodings 路徑
        if self._requires_tiktoken_encodings():
            resolved_tiktoken_path = self._resolve_tiktoken_encodings_base()
            if resolved_tiktoken_path:
                os.environ["TIKTOKEN_ENCODINGS_BASE"] = resolved_tiktoken_path
            else:
                os.environ.pop("TIKTOKEN_ENCODINGS_BASE", None)
        else:
            os.environ.pop("TIKTOKEN_ENCODINGS_BASE", None)

        # 空值時移除，避免沿用外部環境中既有值，交由 vLLM 自動選擇
        if self.vllm_nvfp4_gemm_backend:
            os.environ["VLLM_NVFP4_GEMM_BACKEND"] = self.vllm_nvfp4_gemm_backend
        else:
            os.environ.pop("VLLM_NVFP4_GEMM_BACKEND", None)


@lru_cache(maxsize=8)
def _get_settings_cached(env_file: str) -> Settings:
    """依 env 檔案快取設定物件。"""
    settings = Settings(_env_file=env_file)
    settings.inject_env_vars()
    return settings


def get_settings(env_file: str | Path | None = None) -> Settings:
    """取得設定物件。"""
    env_path = resolve_env_file(env_file)
    return _get_settings_cached(str(env_path))
