"""
ShareGPT Benchmark 模組
使用 ShareGPT 數據集進行性能測試。

壓測目標：
- litellm（預設）：正式的 LiteLLM gateway，base URL 取 LITELLM_BASE_URL（預設
  http://127.0.0.1:4000/v1），金鑰取 LITELLM_API_KEY 或 AI_API_API_KEY。
- single：直連單模型 vLLM 主服務（.env.interface）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values

from benchmark._common import latency_stats, stream_chat
from benchmark.sharegpt_dataset import ShareGPTConversation, load_sharegpt_dataset
from config.multi_model import DEFAULT_BASE_ENV, DEFAULT_MODELS_JSON, probe_host
from config.settings import PROJECT_ROOT, Settings, resolve_env_file

if TYPE_CHECKING:
    from openai import AsyncOpenAI

DEFAULT_LITELLM_BASE_URL = "http://127.0.0.1:4000/v1"
LITELLM_KEY_ENV_VARS = ("LITELLM_API_KEY", "AI_API_API_KEY")
SINGLE_ENV_FILE = ".env.interface"
# 舊版 --target gateway 指向已移除的 FastAPI Gateway，現在視同 litellm。
TARGET_ALIASES = {"gateway": "litellm"}
DEFAULT_DATASET = "test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json"


@dataclass
class ShareGPTTestResult:
    """單一測試結果"""
    conversation_id: str
    prompt: str
    success: bool
    latency: float  # 秒
    first_token_latency: float | None  # TTFT
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_text: str
    error: str | None = None
    num_turns: int = 1
    input_length: int = 0
    output_length: int = 0


@dataclass
class ShareGPTBenchmarkReport:
    """ShareGPT Benchmark 報告"""
    # 基本資訊
    model_name: str = ""
    timestamp: str = ""
    dataset_name: str = ""
    total_tests: int = 0
    concurrency: int = 0
    sample_size: int = 0

    # 總計指標
    successful_tests: int = 0
    failed_tests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_time: float = 0.0

    # 吞吐量
    requests_per_second: float = 0.0
    tokens_per_second: float = 0.0
    output_tokens_per_second: float = 0.0
    input_tokens_per_second: float = 0.0

    # 延遲統計 (ms)
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p90_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    # TTFT 統計 (ms)
    avg_ttft_ms: float = 0.0
    min_ttft_ms: float = 0.0
    max_ttft_ms: float = 0.0
    p50_ttft_ms: float = 0.0
    p90_ttft_ms: float = 0.0
    p99_ttft_ms: float = 0.0

    # TPOT 統計 (ms/token)
    avg_tpot_ms: float = 0.0
    p50_tpot_ms: float = 0.0
    p90_tpot_ms: float = 0.0
    p99_tpot_ms: float = 0.0

    # Token 長度統計
    avg_input_length: float = 0.0
    avg_output_length: float = 0.0

    # 明細
    results: list[ShareGPTTestResult] = field(default_factory=list)

    def print_report(self) -> None:
        """印出格式化報告"""
        print(f"\n{'='*80}")
        print("  🚀 ShareGPT vLLM Benchmark 報告")
        print(f"{'='*80}")
        print(f"  時間:          {self.timestamp}")
        print(f"  模型:          {self.model_name}")
        print(f"  數據集:        {self.dataset_name}")
        print(f"{'─'*80}")
        print("  測試配置:")
        print(f"    樣本數:        {self.sample_size}")
        print(f"    總測試數:      {self.total_tests}")
        print(f"    成功測試:      {self.successful_tests}")
        print(f"    失敗測試:      {self.failed_tests}")
        print(f"    併發數:        {self.concurrency}")
        print(f"    總耗時:        {self.total_time:.2f}s")
        print(f"{'─'*80}")
        print("  ▸ Token 統計")
        print(f"    Prompt Token:      {self.total_prompt_tokens:,}")
        print(f"    Completion Token:  {self.total_completion_tokens:,}")
        print(f"    總 Token:          {self.total_tokens:,}")
        print(f"    平均輸入長度:      {self.avg_input_length:.0f} tokens")
        print(f"    平均輸出長度:      {self.avg_output_length:.0f} tokens")
        print(f"{'─'*80}")
        print("  ▸ 吞吐量")
        print(f"    請求/秒:           {self.requests_per_second:.2f} req/s")
        print(f"    總 Token/秒:       {self.tokens_per_second:.2f} tok/s")
        print(f"    輸入 Token/秒:     {self.input_tokens_per_second:.2f} tok/s")
        print(f"    輸出 Token/秒:     {self.output_tokens_per_second:.2f} tok/s")
        print(f"{'─'*80}")
        print("  ▸ 延遲 (End-to-End)")
        print(f"    平均:    {self.avg_latency_ms:.1f}ms")
        print(f"    最小:    {self.min_latency_ms:.1f}ms")
        print(f"    最大:    {self.max_latency_ms:.1f}ms")
        print(f"    P50:     {self.p50_latency_ms:.1f}ms")
        print(f"    P90:     {self.p90_latency_ms:.1f}ms")
        print(f"    P95:     {self.p95_latency_ms:.1f}ms")
        print(f"    P99:     {self.p99_latency_ms:.1f}ms")
        
        if self.avg_ttft_ms > 0:
            print(f"{'─'*80}")
            print("  ▸ TTFT (Time To First Token)")
            print(f"    平均:    {self.avg_ttft_ms:.1f}ms")
            print(f"    最小:    {self.min_ttft_ms:.1f}ms")
            print(f"    最大:    {self.max_ttft_ms:.1f}ms")
            print(f"    P50:     {self.p50_ttft_ms:.1f}ms")
            print(f"    P90:     {self.p90_ttft_ms:.1f}ms")
            print(f"    P99:     {self.p99_ttft_ms:.1f}ms")
        
        if self.avg_tpot_ms > 0:
            print(f"{'─'*80}")
            print("  ▸ TPOT (Time Per Output Token)")
            print(f"    平均:    {self.avg_tpot_ms:.3f}ms/token")
            print(f"    P50:     {self.p50_tpot_ms:.3f}ms/token")
            print(f"    P90:     {self.p90_tpot_ms:.3f}ms/token")
            print(f"    P99:     {self.p99_tpot_ms:.3f}ms/token")

        print(f"{'='*80}\n")

    def save_json(self, output_dir: str = "benchmark_results") -> str:
        """儲存 JSON 報告"""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = path / f"sharegpt_bench_{ts}.json"

        data = {
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "dataset": {
                "name": self.dataset_name,
                "sample_size": self.sample_size,
            },
            "config": {
                "total_tests": self.total_tests,
                "concurrency": self.concurrency,
            },
            "summary": {
                "successful_tests": self.successful_tests,
                "failed_tests": self.failed_tests,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "total_time_s": round(self.total_time, 3),
                "requests_per_second": round(self.requests_per_second, 3),
                "tokens_per_second": round(self.tokens_per_second, 3),
                "input_tokens_per_second": round(self.input_tokens_per_second, 3),
                "output_tokens_per_second": round(self.output_tokens_per_second, 3),
            },
            "latency_ms": {
                "avg": round(self.avg_latency_ms, 1),
                "min": round(self.min_latency_ms, 1),
                "max": round(self.max_latency_ms, 1),
                "p50": round(self.p50_latency_ms, 1),
                "p90": round(self.p90_latency_ms, 1),
                "p95": round(self.p95_latency_ms, 1),
                "p99": round(self.p99_latency_ms, 1),
            },
            "ttft_ms": {
                "avg": round(self.avg_ttft_ms, 1),
                "min": round(self.min_ttft_ms, 1),
                "max": round(self.max_ttft_ms, 1),
                "p50": round(self.p50_ttft_ms, 1),
                "p90": round(self.p90_ttft_ms, 1),
                "p99": round(self.p99_ttft_ms, 1),
            },
            "tpot_ms": {
                "avg": round(self.avg_tpot_ms, 3),
                "p50": round(self.p50_tpot_ms, 3),
                "p90": round(self.p90_tpot_ms, 3),
                "p99": round(self.p99_tpot_ms, 3),
            },
            "token_length": {
                "avg_input": round(self.avg_input_length, 1),
                "avg_output": round(self.avg_output_length, 1),
            },
            "details": [
                {
                    "conversation_id": r.conversation_id,
                    "prompt": r.prompt[:200] + "..." if len(r.prompt) > 200 else r.prompt,
                    "success": r.success,
                    "latency_ms": round(r.latency * 1000, 1),
                    "ttft_ms": round(r.first_token_latency * 1000, 1) if r.first_token_latency else None,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "response_preview": r.response_text[:200] + "..." if len(r.response_text) > 200 else r.response_text,
                    "error": r.error,
                }
                for r in self.results
            ],
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[Benchmark] 報告已儲存: {filename}")
        return str(filename)


@dataclass
class InteractiveBenchmarkConfig:
    """互動式 Benchmark 配置。"""

    dataset_path: str
    target: str
    settings: Settings
    model: str
    base_url: str
    api_key: str
    num_samples: int | None
    concurrency: int
    max_tokens: int
    temperature: float
    seed: int
    save_report: bool


@dataclass(frozen=True)
class BenchmarkTarget:
    """一個壓測入口：設定來源、OpenAI 相容 base URL 與金鑰。"""

    settings: Settings
    base_url: str
    api_key: str


def _normalize_base_url(raw_url: str) -> str:
    """標準化 OpenAI 相容 Base URL（確保結尾為 /v1）。"""
    url = raw_url.strip().rstrip("/")
    if not url:
        raise ValueError("Base URL 不能為空")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _load_bench_settings(env_file: str) -> Settings:
    """讀 benchmark 用的設定（併發、max tokens、逾時與單模型連線資訊）。

    直接建立 Settings 而不經 get_settings：後者會注入 HF／CUDA 環境變數並建立
    模型快取目錄，壓測端用不到。env 檔不存在時沿用預設值。
    """
    return Settings(_env_file=str(resolve_env_file(env_file)))


def _litellm_api_key() -> str:
    """LiteLLM 金鑰：環境變數優先，其次是 repo 根目錄 .env 的 AI_API_API_KEY。"""
    for name in LITELLM_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    root_env = PROJECT_ROOT.parent / ".env"
    if root_env.is_file():
        values = dotenv_values(root_env)
        for name in LITELLM_KEY_ENV_VARS:
            value = (values.get(name) or "").strip()
            if value:
                return value
    return ""


def _resolve_target(target: str, explicit_base_url: str | None = None) -> BenchmarkTarget:
    """依壓測目標決定設定來源、base URL 與金鑰。"""
    target = TARGET_ALIASES.get(target, target)
    if target == "litellm":
        api_key = _litellm_api_key()
        if not api_key:
            raise ValueError(
                "找不到 LiteLLM 金鑰：請設定環境變數 LITELLM_API_KEY 或 AI_API_API_KEY"
                "（或在 repo 根目錄 .env 設定 AI_API_API_KEY）"
            )
        raw_base_url = (
            explicit_base_url
            or os.environ.get("LITELLM_BASE_URL", "").strip()
            or DEFAULT_LITELLM_BASE_URL
        )
        return BenchmarkTarget(
            settings=_load_bench_settings(DEFAULT_BASE_ENV),
            base_url=_normalize_base_url(raw_base_url),
            api_key=api_key,
        )
    if target == "single":
        settings = _load_bench_settings(SINGLE_ENV_FILE)
        base_url = explicit_base_url or f"http://{probe_host(settings.api_host)}:{settings.api_port}"
        return BenchmarkTarget(
            settings=settings,
            base_url=_normalize_base_url(base_url),
            api_key=settings.api_key,
        )
    raise ValueError(f"未知的壓測目標: {target}")


def _fetch_models(base_url: str, api_key: str, timeout: float = 3.0) -> list[str]:
    """從 OpenAI 相容 /models 端點取得模型 ID 清單。"""
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - 操作者指定的 gateway URL
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data", [])
    model_ids = [str(item.get("id", "")).strip() for item in data if isinstance(item, dict)]
    return sorted([model_id for model_id in model_ids if model_id])


def _load_model_aliases_from_models_json(models_json: str | Path | None = None) -> list[str]:
    """/models 取不到時，改讀 models.json 的公開 alias（含遠端模型）。"""
    path = Path(models_json or DEFAULT_MODELS_JSON)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError(f"models.json 格式錯誤：應為陣列 ({path})")
    aliases = {
        item["alias"].strip()
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("alias"), str) and item["alias"].strip()
    }
    return sorted(aliases)


def _list_litellm_models(target: BenchmarkTarget) -> list[str]:
    """LiteLLM 可用模型：先問 /models，失敗再退回 models.json。"""
    try:
        return _fetch_models(target.base_url, target.api_key)
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"[LiteLLM] 無法從 {target.base_url}/models 取得模型清單: {exc}")
    try:
        aliases = _load_model_aliases_from_models_json()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            "無法取得可用模型，請確認 LiteLLM 已啟動、金鑰正確，或 models.json 設定正確"
        ) from exc
    print(f"[Config] 改用 models.json 的模型 alias: {len(aliases)} 個")
    return aliases


def _resolve_noninteractive_target(
    service_target: str,
    explicit_model: str | None,
    explicit_base_url: str | None,
) -> tuple[BenchmarkTarget, str | None]:
    """解析非互動模式的壓測入口與模型，避免落回預設 .env/API key。"""
    target = _resolve_target(service_target, explicit_base_url)
    if TARGET_ALIASES.get(service_target, service_target) == "litellm":
        model = explicit_model
        if not model:
            aliases = _list_litellm_models(target)
            model = aliases[0] if aliases else None
        return target, model
    return target, explicit_model or target.settings.api_model_name


def _ask_with_default(prompt: str, default: str) -> str:
    """詢問字串輸入，空白時回傳預設值。"""
    answer = input(f"{prompt} [預設: {default}]: ").strip()
    return answer or default


def _ask_int_with_default(prompt: str, default: int, allow_zero: bool = False) -> int:
    """詢問整數輸入，空白時回傳預設值。"""
    while True:
        answer = input(f"{prompt} [預設: {default}]: ").strip()
        if not answer:
            return default
        try:
            value = int(answer)
        except ValueError:
            print("[Input] 請輸入整數")
            continue
        if value < 0:
            print("[Input] 請輸入 >= 0 的整數")
            continue
        if value == 0 and not allow_zero:
            print("[Input] 需大於 0")
            continue
        return value


def _choose_service_target() -> str:
    """讓使用者選擇要壓測的服務入口。"""
    print("\n壓測目標:")
    print("  1. LiteLLM Gateway（正式多模型 API 入口）")
    print("  2. 主服務（直連單模型 vLLM 服務）")

    while True:
        answer = input("選擇服務 [預設: 1]: ").strip().lower()
        if not answer or answer in {"1", "litellm", "gateway", "api"}:
            return "litellm"
        if answer in {"2", "single", "main", "service", "主服務"}:
            return "single"
        print("[Input] 請輸入 1 或 2")


def _choose_model_alias(candidates: list[str], default_alias: str | None = None) -> str:
    """讓使用者互動選擇模型 alias。"""
    if not candidates:
        raise ValueError("沒有可選模型，請先確認 LiteLLM 或 models.json 設定")

    print("\n可用模型:")
    for idx, alias in enumerate(candidates, start=1):
        marker = " (default)" if default_alias and alias == default_alias else ""
        print(f"  {idx}. {alias}{marker}")

    default_choice = 1
    if default_alias and default_alias in candidates:
        default_choice = candidates.index(default_alias) + 1

    while True:
        answer = input(f"選擇模型編號 [預設: {default_choice}]: ").strip()
        if not answer:
            return candidates[default_choice - 1]

        if answer in candidates:
            return answer

        try:
            choice = int(answer)
            if 1 <= choice <= len(candidates):
                return candidates[choice - 1]
        except ValueError:
            # 非數字輸入，往下改以 alias 比對
            pass

        print("[Input] 請輸入模型編號或 alias")


def collect_interactive_config() -> InteractiveBenchmarkConfig:
    """收集互動式 Benchmark 參數。"""
    print(f"\n{'='*80}")
    print("  🎯 ShareGPT Benchmark 互動模式")
    print(f"{'='*80}")

    service_target = _choose_service_target()
    target = _resolve_target(service_target)
    if service_target == "litellm":
        model_aliases = _list_litellm_models(target)
        print(f"[LiteLLM] 可用模型: {len(model_aliases)} 個")
        selected_model = _choose_model_alias(
            model_aliases,
            default_alias=model_aliases[0] if model_aliases else None,
        )
    else:
        selected_model = _ask_with_default("主服務模型名稱", target.settings.api_model_name)

    # 0 代表使用全部對話
    sample_input = _ask_int_with_default("測試樣本數 (0=全部)", 100, allow_zero=True)
    num_samples = None if sample_input == 0 else sample_input

    concurrency = _ask_int_with_default("併發數", target.settings.bench_concurrency)
    max_tokens = target.settings.bench_max_tokens
    temperature = 0.7
    seed = 42

    print(f"\n{'─'*80}")
    print("  互動設定確認")
    print(f"{'─'*80}")
    print(f"  服務:            {'LiteLLM Gateway' if service_target == 'litellm' else '主服務'}")
    print(f"  API Base URL:    {target.base_url}")
    print(f"  模型:            {selected_model}")
    print(f"  資料集:          {DEFAULT_DATASET}")
    print(f"  測試樣本數:      {'全部' if num_samples is None else num_samples}")
    print(f"  併發數:          {concurrency}")
    print(f"  固定最大 Token:  {max_tokens}")
    print(f"  固定 Temperature: {temperature}")
    print(f"  固定 Seed:       {seed}")
    print("  儲存報告:        是")
    print(f"{'─'*80}\n")

    return InteractiveBenchmarkConfig(
        dataset_path=DEFAULT_DATASET,
        target=service_target,
        settings=target.settings,
        model=selected_model,
        base_url=target.base_url,
        api_key=target.api_key,
        num_samples=num_samples,
        concurrency=concurrency,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
        save_report=True,
    )


def _failed_result(
    conversation: ShareGPTConversation,
    latency: float,
    error: str,
) -> ShareGPTTestResult:
    return ShareGPTTestResult(
        conversation_id=conversation.id,
        prompt=conversation.prompt,
        success=False,
        latency=latency,
        first_token_latency=None,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        response_text="",
        error=error,
        num_turns=conversation.num_turns,
    )


async def _send_sharegpt_request(
    client: AsyncOpenAI,
    model: str,
    conversation: ShareGPTConversation,
    max_tokens: int,
    temperature: float,
    semaphore: asyncio.Semaphore,
    max_retries: int = 2,
) -> ShareGPTTestResult:
    """發送單個 ShareGPT 測試請求（逾時與暫時性錯誤會重試）"""
    async with semaphore:
        # 添加小延遲避免瞬間高峰
        await asyncio.sleep(0.05)
        # 使用對話的第一個 prompt
        messages = [{"role": "user", "content": conversation.prompt}]

        for attempt in range(max_retries + 1):
            start_time = time.perf_counter()
            is_last_attempt = attempt >= max_retries
            try:
                outcome = await stream_chat(client, model, messages, max_tokens, temperature)
            except asyncio.TimeoutError as e:
                if is_last_attempt:
                    return _failed_result(
                        conversation,
                        time.perf_counter() - start_time,
                        f"Timeout: {e} (attempt {attempt + 1}/{max_retries + 1})",
                    )
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            except Exception as e:
                error_msg = str(e)
                # 某些錯誤不應重試（例如 EngineCore 錯誤、認證失敗）
                if "EngineCore" in error_msg or "AuthenticationError" in error_msg or is_last_attempt:
                    return _failed_result(
                        conversation,
                        time.perf_counter() - start_time,
                        f"{error_msg} (attempt {attempt + 1}/{max_retries + 1})",
                    )
                # 否則重試（指數退避，最多 10 秒）
                await asyncio.sleep(min(2.0 ** attempt, 10.0))
                continue

            return ShareGPTTestResult(
                conversation_id=conversation.id,
                prompt=conversation.prompt,
                success=True,
                latency=outcome.latency,
                first_token_latency=outcome.first_token_latency,
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                total_tokens=outcome.prompt_tokens + outcome.completion_tokens,
                response_text=outcome.text,
                num_turns=conversation.num_turns,
                input_length=outcome.prompt_tokens,
                output_length=outcome.completion_tokens,
            )

        # 迴圈每一輪都會 return 或 continue，最後一輪必定 return；此行不會執行到
        return _failed_result(conversation, 0.0, "All retries exhausted")


async def run_sharegpt_benchmark(
    dataset_path: str | Path,
    settings: Settings | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    num_samples: int | None = None,
    concurrency: int | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    save_report: bool = True,
    seed: int | None = 42,
) -> ShareGPTBenchmarkReport:
    """
    執行 ShareGPT Benchmark

    Args:
        dataset_path: ShareGPT 資料集 JSON 路徑
        settings: 設定物件 (可選)
        model: 指定模型名稱/alias（可選）
        base_url: OpenAI API base URL（可選，預設使用 settings.api_host/api_port）
        api_key: API key（可選，預設使用 settings.api_key）
        num_samples: 採樣數量 (None = 使用全部)
        concurrency: 併發數 (覆蓋 .env)
        max_tokens: 每次最大生成 token 數
        temperature: 溫度參數
        save_report: 是否儲存 JSON 報告
        seed: 隨機種子 (用於採樣)

    Returns:
        ShareGPTBenchmarkReport
    """
    s = settings or Settings(_env_file=str(resolve_env_file()))
    _conc = concurrency or s.bench_concurrency
    _max_tokens = max_tokens or s.bench_max_tokens
    _model = model or s.api_model_name
    _base_url = base_url or f"http://{probe_host(s.api_host)}:{s.api_port}/v1"
    _api_key = api_key or s.api_key

    # 載入 ShareGPT 資料集
    print(f"\n{'='*80}")
    print("  🚀 ShareGPT vLLM Benchmark")
    print(f"{'='*80}")
    print(f"[Benchmark] 載入 ShareGPT 資料集: {dataset_path}")
    
    dataset = load_sharegpt_dataset(dataset_path)
    print(f"[Benchmark] 資料集: {dataset.name}")
    print(f"[Benchmark] 總對話數: {len(dataset)}")

    # 採樣
    if num_samples and num_samples < len(dataset):
        conversations = dataset.sample(num_samples, seed=seed)
        print(f"[Benchmark] 已採樣: {num_samples} 個對話")
    else:
        conversations = dataset.conversations
        print(f"[Benchmark] 使用全部對話: {len(conversations)}")

    print(f"\n  模型:           {_model}")
    print(f"  API Base URL:   {_base_url}")
    print(f"  測試數:         {len(conversations)}")
    print(f"  併發數:         {_conc}")
    print(f"  每次最大 Token: {_max_tokens}")
    print(f"  溫度:           {temperature}")
    print(f"{'='*80}\n")

    # 建立 API 客戶端（延後匯入，讓 --help 與設定解析不必先裝 openai）
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=_base_url,
        api_key=_api_key,
        timeout=s.request_timeout,
    )

    semaphore = asyncio.Semaphore(_conc)

    # 發送所有測試請求
    print(f"[Benchmark] 開始測試 (併發: {_conc})...")
    print("[Benchmark] 提示: 使用重試機制，每個請求最多嘗試 3 次")
    overall_start = time.perf_counter()

    tasks = [
        _send_sharegpt_request(client, _model, conv, _max_tokens, temperature, semaphore)
        for conv in conversations
    ]
    results: list[ShareGPTTestResult] = await asyncio.gather(*tasks)

    overall_end = time.perf_counter()
    total_time = overall_end - overall_start

    await client.close()

    # 統計錯誤類型
    error_types: dict[str, int] = {}
    for r in results:
        if not r.success and r.error:
            # 提取錯誤類型
            if "EngineCore" in r.error:
                error_type = "EngineCore Error"
            elif "timeout" in r.error.lower():
                error_type = "Timeout"
            elif "connection" in r.error.lower():
                error_type = "Connection Error"
            else:
                error_type = "Other Error"
            error_types[error_type] = error_types.get(error_type, 0) + 1

    # 計算統計
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    report = ShareGPTBenchmarkReport(
        model_name=_model,
        timestamp=datetime.now().isoformat(),
        dataset_name=dataset.name,
        total_tests=len(conversations),
        sample_size=num_samples or len(dataset),
        concurrency=_conc,
        successful_tests=len(successful),
        failed_tests=len(failed),
        total_prompt_tokens=sum(r.prompt_tokens for r in successful),
        total_completion_tokens=sum(r.completion_tokens for r in successful),
        total_tokens=sum(r.total_tokens for r in successful),
        total_time=total_time,
        results=results,
    )

    if successful:
        report.requests_per_second = len(successful) / total_time
        report.tokens_per_second = report.total_tokens / total_time
        report.input_tokens_per_second = report.total_prompt_tokens / total_time
        report.output_tokens_per_second = report.total_completion_tokens / total_time

        # 延遲統計
        latency = latency_stats([r.latency * 1000 for r in successful])
        report.avg_latency_ms = latency["avg"]
        report.min_latency_ms = latency["min"]
        report.max_latency_ms = latency["max"]
        report.p50_latency_ms = latency["p50"]
        report.p90_latency_ms = latency["p90"]
        report.p95_latency_ms = latency["p95"]
        report.p99_latency_ms = latency["p99"]

        # TTFT 統計
        ttfts_ms = [r.first_token_latency * 1000 for r in successful if r.first_token_latency]
        if ttfts_ms:
            ttft = latency_stats(ttfts_ms)
            report.avg_ttft_ms = ttft["avg"]
            report.min_ttft_ms = ttft["min"]
            report.max_ttft_ms = ttft["max"]
            report.p50_ttft_ms = ttft["p50"]
            report.p90_ttft_ms = ttft["p90"]
            report.p99_ttft_ms = ttft["p99"]

        # TPOT 統計 (Time Per Output Token) = (總延遲 - TTFT) / 輸出 tokens
        tpots_ms = [
            (r.latency - r.first_token_latency) * 1000 / r.completion_tokens
            for r in successful
            if r.first_token_latency and r.completion_tokens > 0
        ]
        if tpots_ms:
            tpot = latency_stats(tpots_ms)
            report.avg_tpot_ms = tpot["avg"]
            report.p50_tpot_ms = tpot["p50"]
            report.p90_tpot_ms = tpot["p90"]
            report.p99_tpot_ms = tpot["p99"]

        # Token 長度統計
        report.avg_input_length = sum(r.input_length for r in successful) / len(successful)
        report.avg_output_length = sum(r.output_length for r in successful) / len(successful)

    # 輸出報告
    report.print_report()

    if failed:
        print(f"\n{'─'*80}")
        print("  ❌ 失敗測試統計:")
        print(f"    總失敗數: {len(failed)}")
        if error_types:
            for error_type, count in sorted(error_types.items(), key=lambda x: x[1], reverse=True):
                print(f"    {error_type}: {count}")
        print(f"{'─'*80}")
        print("\n[Benchmark] 失敗測試明細 (前10個):")
        for i, r in enumerate(failed[:10], 1):
            error_preview = r.error[:100] + "..." if r.error and len(r.error) > 100 else r.error
            print(f"  {i}. [{r.conversation_id}] {error_preview}")
        if len(failed) > 10:
            print(f"  ... 還有 {len(failed) - 10} 個失敗測試")

    if save_report:
        report.save_json()

    return report


# ============================================================
# CLI 入口
# ============================================================

def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="vLLM ShareGPT Benchmark - 使用 ShareGPT 數據集進行性能測試"
    )
    parser.add_argument(
        "dataset",
        type=str,
        nargs="?",
        help="ShareGPT 資料集 JSON 檔案路徑",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="詢問式流程（選 LiteLLM/主服務、模型、測試量與併發）",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="指定模型名稱或 alias（走 LiteLLM 時填公開 alias）",
    )
    parser.add_argument(
        "--target",
        choices=("litellm", "single", "gateway"),
        default="litellm",
        help=(
            "非互動模式壓測目標：litellm 走 LITELLM_BASE_URL（預設 "
            f"{DEFAULT_LITELLM_BASE_URL}），金鑰取 LITELLM_API_KEY／AI_API_API_KEY；"
            "single 直連 .env.interface 的單模型 vLLM；gateway 為舊名稱，等同 litellm"
            "（預設: litellm）"
        ),
    )
    parser.add_argument(
        "--base-url",
        type=str,
        help=f"覆寫 OpenAI API Base URL（例如 {DEFAULT_LITELLM_BASE_URL}）",
    )
    parser.add_argument(
        "-n", "--num-samples",
        type=int,
        help="採樣數量 (不指定則使用全部)",
    )
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        help="併發數",
    )
    parser.add_argument(
        "-m", "--max-tokens",
        type=int,
        help="每次最大生成 token 數",
    )
    parser.add_argument(
        "-t", "--temperature",
        type=float,
        default=0.7,
        help="溫度參數 (默認: 0.7)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="隨機種子 (默認: 42)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不儲存報告",
    )
    args = parser.parse_args()

    if args.interactive or not args.dataset:
        config = collect_interactive_config()
        dataset_path = Path(config.dataset_path)
        # 互動模式也要用所選目標自己的設定與金鑰，不能落回 .env.API 的 API_KEY
        settings = config.settings
        model = config.model
        base_url = config.base_url
        api_key = config.api_key
        num_samples = config.num_samples
        concurrency = config.concurrency
        max_tokens = config.max_tokens
        temperature = config.temperature
        seed = config.seed
        save_report = config.save_report
    else:
        dataset_path = Path(args.dataset)
        target, model = _resolve_noninteractive_target(
            service_target=args.target,
            explicit_model=args.model,
            explicit_base_url=args.base_url,
        )
        settings = target.settings
        base_url = target.base_url
        api_key = target.api_key
        num_samples = args.num_samples
        concurrency = args.concurrency
        max_tokens = args.max_tokens
        temperature = args.temperature
        seed = args.seed
        save_report = not args.no_save

    # 檢查數據集是否存在，如果不存在則嘗試下載
    if not dataset_path.exists():
        print(f"[Benchmark] 數據集不存在: {dataset_path}")
        print("[Benchmark] 嘗試下載 ShareGPT_V3 數據集...")
        from benchmark.sharegpt_dataset import download_sharegpt_dataset
        dataset_path = download_sharegpt_dataset(dataset_path)

    asyncio.run(
        run_sharegpt_benchmark(
            dataset_path=dataset_path,
            settings=settings,
            model=model,
            base_url=base_url,
            api_key=api_key,
            num_samples=num_samples,
            concurrency=concurrency,
            max_tokens=max_tokens,
            temperature=temperature,
            save_report=save_report,
            seed=seed,
        )
    )


if __name__ == "__main__":
    main()
