"""
ShareGPT Benchmark 模組
使用 ShareGPT 數據集進行性能測試，保持與 enhanced_bench 相同的架構
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from openai import AsyncOpenAI

from config.multi_model import load_gateway_config, load_model_instances
from config.settings import Settings, get_settings
from benchmark.sharegpt_dataset import ShareGPTConversation, load_sharegpt_dataset


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


def _percentile(sorted_data: list[float], p: float) -> float:
    """計算百分位數"""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])


@dataclass
class InteractiveBenchmarkConfig:
    """互動式 Benchmark 配置。"""

    dataset_path: str
    model: str
    base_url: str
    num_samples: int | None
    concurrency: int
    max_tokens: int
    temperature: float
    seed: int
    save_report: bool


def _normalize_gateway_base_url(raw_url: str) -> str:
    """標準化 Gateway OpenAI Base URL（確保結尾為 /v1）。"""
    url = raw_url.strip().rstrip("/")
    if not url:
        raise ValueError("Gateway URL 不能為空")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = f"http://{url}"
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _get_default_gateway_base_url(settings: Settings) -> str:
    """取得預設 Gateway base URL。"""
    try:
        gateway_cfg = load_gateway_config()
        host = gateway_cfg.host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return _normalize_gateway_base_url(f"http://{host}:{gateway_cfg.port}")
    except Exception:
        host = settings.api_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return _normalize_gateway_base_url(f"http://{host}:{settings.api_port}")


def _get_default_service_base_url(settings: Settings) -> str:
    """取得單模型主服務 OpenAI base URL。"""
    host = settings.api_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return _normalize_gateway_base_url(f"http://{host}:{settings.api_port}")


def _fetch_gateway_models(base_url: str, timeout: float = 3.0) -> list[str]:
    """從 Gateway 的 /models 端點取得模型 alias 清單。"""
    models_url = f"{base_url.rstrip('/')}/models"
    with urlopen(models_url, timeout=timeout) as response:  # nosec B310 - 內部 Gateway URL
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data", [])
    aliases = [str(item.get("id", "")).strip() for item in data if isinstance(item, dict)]
    return sorted([alias for alias in aliases if alias])


def _load_model_aliases_from_local_config() -> list[str]:
    """當 Gateway 不可用時，從本地多模型設定推導 alias。"""
    instances = load_model_instances()
    aliases = [instance.alias for instance in instances if instance.alias]
    return sorted(aliases)


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


def _ask_float_with_default(prompt: str, default: float) -> float:
    """詢問浮點輸入，空白時回傳預設值。"""
    while True:
        answer = input(f"{prompt} [預設: {default}]: ").strip()
        if not answer:
            return default
        try:
            return float(answer)
        except ValueError:
            print("[Input] 請輸入數字")


def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    """詢問是/否輸入。"""
    default_label = "Y/n" if default_yes else "y/N"
    while True:
        answer = input(f"{prompt} ({default_label}): ").strip().lower()
        if not answer:
            return default_yes
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("[Input] 請輸入 y 或 n")


def _choose_service_target() -> str:
    """讓使用者選擇要壓測的服務入口。"""
    print("\n壓測目標:")
    print("  1. API Gateway（多模型 API 服務）")
    print("  2. 主服務（單模型 vLLM 服務）")

    while True:
        answer = input("選擇服務 [預設: 1]: ").strip().lower()
        if not answer:
            return "gateway"
        if answer in {"1", "gateway", "api", "api gateway"}:
            return "gateway"
        if answer in {"2", "single", "main", "service", "主服務"}:
            return "single"
        print("[Input] 請輸入 1 或 2")


def _choose_model_alias(candidates: list[str], default_alias: str | None = None) -> str:
    """讓使用者互動選擇模型 alias。"""
    if not candidates:
        raise ValueError("沒有可選模型，請先確認 Gateway 或 models.json 設定")

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
            pass

        print("[Input] 請輸入模型編號或 alias")


def collect_interactive_config() -> InteractiveBenchmarkConfig:
    """收集互動式 Benchmark 參數。"""
    print(f"\n{'='*80}")
    print("  🎯 ShareGPT Benchmark 互動模式")
    print(f"{'='*80}")

    service_target = _choose_service_target()
    if service_target == "gateway":
        target_settings = get_settings(".env.API")
        base_url = _get_default_gateway_base_url(target_settings)
        model_aliases: list[str] = []
        try:
            model_aliases = _fetch_gateway_models(base_url)
            print(f"[Gateway] 已讀取模型清單: {len(model_aliases)} 個")
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            print(f"[Gateway] 無法從 {base_url}/models 取得模型清單: {exc}")
            try:
                model_aliases = _load_model_aliases_from_local_config()
                print(f"[Config] 回退使用本地設定模型: {len(model_aliases)} 個")
            except Exception as fallback_exc:
                raise RuntimeError(
                    "無法取得可用模型，請確認 Gateway 已啟動或 models.json 設定正確"
                ) from fallback_exc

        selected_model = _choose_model_alias(
            model_aliases,
            default_alias=model_aliases[0] if model_aliases else None,
        )
    else:
        target_settings = get_settings(".env.interface")
        base_url = _get_default_service_base_url(target_settings)
        selected_model = _ask_with_default("主服務模型名稱", target_settings.model_name)

    default_dataset = "test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json"
    dataset_path = default_dataset

    # 0 代表使用全部對話
    sample_input = _ask_int_with_default("測試樣本數 (0=全部)", 100, allow_zero=True)
    num_samples = None if sample_input == 0 else sample_input

    concurrency = _ask_int_with_default("併發數", target_settings.bench_concurrency)
    max_tokens = target_settings.bench_max_tokens
    temperature = 0.7
    seed = 42
    save_report = True

    print(f"\n{'─'*80}")
    print("  互動設定確認")
    print(f"{'─'*80}")
    print(f"  服務:            {'API Gateway' if service_target == 'gateway' else '主服務'}")
    print(f"  API Base URL:    {base_url}")
    print(f"  模型:            {selected_model}")
    print(f"  資料集:          {dataset_path}")
    print(f"  測試樣本數:      {'全部' if num_samples is None else num_samples}")
    print(f"  併發數:          {concurrency}")
    print(f"  固定最大 Token:  {max_tokens}")
    print(f"  固定 Temperature: {temperature}")
    print(f"  固定 Seed:       {seed}")
    print("  儲存報告:        是")
    print(f"{'─'*80}\n")

    return InteractiveBenchmarkConfig(
        dataset_path=dataset_path,
        model=selected_model,
        base_url=base_url,
        num_samples=num_samples,
        concurrency=concurrency,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
        save_report=save_report,
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
    """發送單個 ShareGPT 測試請求（改進的重試逻輯）"""
    async with semaphore:
        # 添加小延遲避免瞬間高峰
        await asyncio.sleep(0.05)
        
        for attempt in range(max_retries + 1):
            start_time = time.perf_counter()
            first_token_time = None
            completion_text = ""
            is_last_attempt = (attempt >= max_retries)

            try:
                # 使用對話的第一個 prompt
                messages = [{"role": "user", "content": conversation.prompt}]

                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                prompt_tokens = 0
                completion_tokens = 0

                async for chunk in stream:
                    if first_token_time is None and chunk.choices:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            first_token_time = time.perf_counter()
                            completion_text += delta
                    elif chunk.choices:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            completion_text += delta

                    if hasattr(chunk, "usage") and chunk.usage:
                        prompt_tokens = chunk.usage.prompt_tokens
                        completion_tokens = chunk.usage.completion_tokens

                end_time = time.perf_counter()

                # 估算 tokens 如果 API 沒提供
                if completion_tokens == 0:
                    completion_tokens = max(1, len(completion_text) // 4)
                if prompt_tokens == 0:
                    prompt_tokens = len(conversation.prompt) // 4

                return ShareGPTTestResult(
                    conversation_id=conversation.id,
                    prompt=conversation.prompt,
                    success=True,
                    latency=end_time - start_time,
                    first_token_latency=(first_token_time - start_time) if first_token_time else None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    response_text=completion_text,
                    num_turns=conversation.num_turns,
                    input_length=prompt_tokens,
                    output_length=completion_tokens,
                )

            except asyncio.TimeoutError as e:
                error_msg = f"Timeout: {str(e)}"
                if is_last_attempt:
                    end_time = time.perf_counter()
                    return ShareGPTTestResult(
                        conversation_id=conversation.id,
                        prompt=conversation.prompt,
                        success=False,
                        latency=end_time - start_time,
                        first_token_latency=None,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        response_text="",
                        error=f"{error_msg} (attempt {attempt + 1}/{max_retries + 1})",
                        num_turns=conversation.num_turns,
                    )
                # 重試
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
                
            except Exception as e:
                error_msg = str(e)
                # 某些錯誤不應重試（例如 EngineCore 錯誤）
                if "EngineCore" in error_msg or "AuthenticationError" in error_msg or is_last_attempt:
                    end_time = time.perf_counter()
                    return ShareGPTTestResult(
                        conversation_id=conversation.id,
                        prompt=conversation.prompt,
                        success=False,
                        latency=end_time - start_time,
                        first_token_latency=None,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        response_text="",
                        error=f"{error_msg} (attempt {attempt + 1}/{max_retries + 1})",
                        num_turns=conversation.num_turns,
                    )
                # 否則重試（指數退避）
                backoff_time = min(2.0 ** attempt, 10.0)  # 最多 10 秒
                await asyncio.sleep(backoff_time)
                continue
        
        # 此行正常不應該執行到
        end_time = time.perf_counter()
        return ShareGPTTestResult(
            conversation_id=conversation.id,
            prompt=conversation.prompt,
            success=False,
            latency=end_time - start_time,
            first_token_latency=None,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            response_text="",
            error="All retries exhausted",
            num_turns=conversation.num_turns,
        )


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
    s = settings or get_settings()
    _conc = concurrency or s.bench_concurrency
    _max_tokens = max_tokens or s.bench_max_tokens
    _model = model or s.model_name
    _base_url = base_url or f"http://{s.api_host}:{s.api_port}/v1"
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

    # 建立 API 客戶端
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
        # 延遲統計
        latencies_ms = sorted(r.latency * 1000 for r in successful)
        report.requests_per_second = len(successful) / total_time
        report.tokens_per_second = report.total_tokens / total_time
        report.input_tokens_per_second = report.total_prompt_tokens / total_time
        report.output_tokens_per_second = report.total_completion_tokens / total_time
        
        report.avg_latency_ms = sum(latencies_ms) / len(latencies_ms)
        report.min_latency_ms = latencies_ms[0]
        report.max_latency_ms = latencies_ms[-1]
        report.p50_latency_ms = _percentile(latencies_ms, 50)
        report.p90_latency_ms = _percentile(latencies_ms, 90)
        report.p95_latency_ms = _percentile(latencies_ms, 95)
        report.p99_latency_ms = _percentile(latencies_ms, 99)

        # TTFT 統計
        ttfts_ms = sorted(r.first_token_latency * 1000 for r in successful if r.first_token_latency)
        if ttfts_ms:
            report.avg_ttft_ms = sum(ttfts_ms) / len(ttfts_ms)
            report.min_ttft_ms = ttfts_ms[0]
            report.max_ttft_ms = ttfts_ms[-1]
            report.p50_ttft_ms = _percentile(ttfts_ms, 50)
            report.p90_ttft_ms = _percentile(ttfts_ms, 90)
            report.p99_ttft_ms = _percentile(ttfts_ms, 99)

        # TPOT 統計 (Time Per Output Token)
        tpots_ms = []
        for r in successful:
            if r.first_token_latency and r.completion_tokens > 0:
                # TPOT = (總延遲 - TTFT) / 輸出 tokens
                decode_time = r.latency - r.first_token_latency
                tpot = (decode_time * 1000) / r.completion_tokens
                tpots_ms.append(tpot)
        
        if tpots_ms:
            tpots_ms_sorted = sorted(tpots_ms)
            report.avg_tpot_ms = sum(tpots_ms) / len(tpots_ms)
            report.p50_tpot_ms = _percentile(tpots_ms_sorted, 50)
            report.p90_tpot_ms = _percentile(tpots_ms_sorted, 90)
            report.p99_tpot_ms = _percentile(tpots_ms_sorted, 99)

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
        help="詢問式流程（選 API Gateway/主服務、模型、測試量與併發）",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="指定模型名稱或 alias（走 Gateway 時填 alias）",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        help="OpenAI API Base URL（例如 http://127.0.0.1:3000/v1）",
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

    settings = get_settings()

    if args.interactive or not args.dataset:
        config = collect_interactive_config()
        dataset_path = Path(config.dataset_path)
        model = config.model
        base_url = config.base_url
        num_samples = config.num_samples
        concurrency = config.concurrency
        max_tokens = config.max_tokens
        temperature = config.temperature
        seed = config.seed
        save_report = config.save_report
    else:
        dataset_path = Path(args.dataset)
        model = args.model
        base_url = _normalize_gateway_base_url(args.base_url) if args.base_url else None
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
