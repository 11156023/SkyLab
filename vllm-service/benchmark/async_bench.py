"""
異步 Benchmark 測試模組
測量高併發下的總請求數、總 token 數、吞吐量、延遲等指標
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

from config.settings import Settings, get_settings


@dataclass
class RequestResult:
    """單次請求結果"""
    request_id: int
    success: bool
    latency: float  # 秒
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error: str | None = None
    first_token_latency: float | None = None  # TTFT (秒)


@dataclass
class BenchmarkReport:
    """Benchmark 報告"""
    # 基本資訊
    model_name: str = ""
    timestamp: str = ""
    total_requests: int = 0
    concurrency: int = 0
    max_tokens_per_request: int = 0
    prompt: str = ""

    # 總計指標
    successful_requests: int = 0
    failed_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_time: float = 0.0

    # 吞吐量
    requests_per_second: float = 0.0
    tokens_per_second: float = 0.0
    output_tokens_per_second: float = 0.0

    # 延遲統計 (秒)
    avg_latency: float = 0.0
    min_latency: float = 0.0
    max_latency: float = 0.0
    p50_latency: float = 0.0
    p90_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0

    # TTFT 統計
    avg_ttft: float = 0.0
    p50_ttft: float = 0.0
    p90_ttft: float = 0.0
    p99_ttft: float = 0.0

    # 明細
    results: list[RequestResult] = field(default_factory=list)

    def print_report(self) -> None:
        """印出格式化報告"""
        print(f"\n{'='*70}")
        print("  vLLM 異步 Benchmark 報告")
        print(f"{'='*70}")
        print(f"  時間:           {self.timestamp}")
        print(f"  模型:           {self.model_name}")
        print(f"  Prompt:         {self.prompt[:50]}{'...' if len(self.prompt) > 50 else ''}")
        print(f"  每次最大 Token:  {self.max_tokens_per_request}")
        print(f"{'─'*70}")
        print(f"  總請求數:       {self.total_requests}")
        print(f"  成功請求:       {self.successful_requests}")
        print(f"  失敗請求:       {self.failed_requests}")
        print(f"  併發數:         {self.concurrency}")
        print(f"  總耗時:         {self.total_time:.2f}s")
        print(f"{'─'*70}")
        print("  ▸ Token 統計")
        print(f"    Prompt Token:      {self.total_prompt_tokens:,}")
        print(f"    Completion Token:  {self.total_completion_tokens:,}")
        print(f"    總 Token:          {self.total_tokens:,}")
        print(f"{'─'*70}")
        print("  ▸ 吞吐量")
        print(f"    請求/秒:           {self.requests_per_second:.2f} req/s")
        print(f"    總 Token/秒:       {self.tokens_per_second:.2f} tok/s")
        print(f"    輸出 Token/秒:     {self.output_tokens_per_second:.2f} tok/s")
        print(f"{'─'*70}")
        print("  ▸ 延遲 (End-to-End)")
        print(f"    平均:    {self.avg_latency*1000:.1f}ms")
        print(f"    最小:    {self.min_latency*1000:.1f}ms")
        print(f"    最大:    {self.max_latency*1000:.1f}ms")
        print(f"    P50:     {self.p50_latency*1000:.1f}ms")
        print(f"    P90:     {self.p90_latency*1000:.1f}ms")
        print(f"    P95:     {self.p95_latency*1000:.1f}ms")
        print(f"    P99:     {self.p99_latency*1000:.1f}ms")
        if self.avg_ttft > 0:
            print(f"{'─'*70}")
            print("  ▸ TTFT (Time To First Token)")
            print(f"    平均:    {self.avg_ttft*1000:.1f}ms")
            print(f"    P50:     {self.p50_ttft*1000:.1f}ms")
            print(f"    P90:     {self.p90_ttft*1000:.1f}ms")
            print(f"    P99:     {self.p99_ttft*1000:.1f}ms")
        print(f"{'='*70}\n")

    def save_json(self, output_dir: str = "benchmark_results") -> str:
        """儲存 JSON 報告"""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = path / f"bench_{ts}.json"

        data = {
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "config": {
                "total_requests": self.total_requests,
                "concurrency": self.concurrency,
                "max_tokens_per_request": self.max_tokens_per_request,
                "prompt": self.prompt,
            },
            "summary": {
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "total_time_s": round(self.total_time, 3),
                "requests_per_second": round(self.requests_per_second, 3),
                "tokens_per_second": round(self.tokens_per_second, 3),
                "output_tokens_per_second": round(self.output_tokens_per_second, 3),
            },
            "latency_ms": {
                "avg": round(self.avg_latency * 1000, 1),
                "min": round(self.min_latency * 1000, 1),
                "max": round(self.max_latency * 1000, 1),
                "p50": round(self.p50_latency * 1000, 1),
                "p90": round(self.p90_latency * 1000, 1),
                "p95": round(self.p95_latency * 1000, 1),
                "p99": round(self.p99_latency * 1000, 1),
            },
            "ttft_ms": {
                "avg": round(self.avg_ttft * 1000, 1),
                "p50": round(self.p50_ttft * 1000, 1),
                "p90": round(self.p90_ttft * 1000, 1),
                "p99": round(self.p99_ttft * 1000, 1),
            },
            "details": [
                {
                    "id": r.request_id,
                    "success": r.success,
                    "latency_ms": round(r.latency * 1000, 1),
                    "ttft_ms": round(r.first_token_latency * 1000, 1) if r.first_token_latency else None,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
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


async def _send_request(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
    request_id: int,
    semaphore: asyncio.Semaphore,
) -> RequestResult:
    """發送單個異步請求 (使用流式回應以取得 TTFT)"""
    async with semaphore:
        start_time = time.perf_counter()
        first_token_time = None
        completion_text = ""

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7,
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

                # 從最後的 chunk 取得 usage
                if hasattr(chunk, "usage") and chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens

            end_time = time.perf_counter()

            # 若未從 usage 取得，用粗估
            if completion_tokens == 0:
                completion_tokens = max(1, len(completion_text) // 4)
            if prompt_tokens == 0:
                prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages)

            return RequestResult(
                request_id=request_id,
                success=True,
                latency=end_time - start_time,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                first_token_latency=(first_token_time - start_time) if first_token_time else None,
            )

        except Exception as e:
            end_time = time.perf_counter()
            return RequestResult(
                request_id=request_id,
                success=False,
                latency=end_time - start_time,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                error=str(e),
            )


async def run_benchmark(
    settings: Settings | None = None,
    total_requests: int | None = None,
    concurrency: int | None = None,
    max_tokens: int | None = None,
    prompt: str | None = None,
    save_report: bool = True,
) -> BenchmarkReport:
    """
    執行異步 Benchmark

    Args:
        settings: 設定物件 (可選，預設從 .env 載入)
        total_requests: 總請求數 (覆蓋 .env)
        concurrency: 併發數 (覆蓋 .env)
        max_tokens: 最大 token 數 (覆蓋 .env)
        prompt: 測試 prompt (覆蓋 .env)
        save_report: 是否儲存 JSON 報告

    Returns:
        BenchmarkReport
    """
    s = settings or get_settings()
    _total = total_requests or s.bench_total_requests
    _conc = concurrency or s.bench_concurrency
    _max_tok = max_tokens or s.bench_max_tokens
    _prompt = prompt or s.bench_prompt
    _model = s.resolved_model_path

    print(f"\n{'='*70}")
    print("  vLLM 異步 Benchmark")
    print(f"{'='*70}")
    print(f"  模型:       {s.model_name}")
    print(f"  總請求數:   {_total}")
    print(f"  併發數:     {_conc}")
    print(f"  最大 Token: {_max_tok}")
    print(f"  Prompt:     {_prompt[:50]}{'...' if len(_prompt) > 50 else ''}")
    print(f"{'='*70}\n")

    client = AsyncOpenAI(
        base_url=f"http://{s.api_host}:{s.api_port}/v1",
        api_key=s.api_key,
        timeout=s.request_timeout,
    )

    messages = [{"role": "user", "content": _prompt}]
    semaphore = asyncio.Semaphore(_conc)

    # 發送所有請求
    print(f"[Benchmark] 開始發送 {_total} 個請求 (併發: {_conc})...")
    overall_start = time.perf_counter()

    tasks = [
        _send_request(client, _model, messages, _max_tok, i, semaphore)
        for i in range(_total)
    ]
    results: list[RequestResult] = await asyncio.gather(*tasks)

    overall_end = time.perf_counter()
    total_time = overall_end - overall_start

    await client.close()

    # 計算統計
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    report = BenchmarkReport(
        model_name=s.model_name,
        timestamp=datetime.now().isoformat(),
        total_requests=_total,
        concurrency=_conc,
        max_tokens_per_request=_max_tok,
        prompt=_prompt,
        successful_requests=len(successful),
        failed_requests=len(failed),
        total_prompt_tokens=sum(r.prompt_tokens for r in successful),
        total_completion_tokens=sum(r.completion_tokens for r in successful),
        total_tokens=sum(r.total_tokens for r in successful),
        total_time=total_time,
        results=results,
    )

    if successful:
        latencies = sorted(r.latency for r in successful)
        report.requests_per_second = len(successful) / total_time
        report.tokens_per_second = report.total_tokens / total_time
        report.output_tokens_per_second = report.total_completion_tokens / total_time
        report.avg_latency = sum(latencies) / len(latencies)
        report.min_latency = latencies[0]
        report.max_latency = latencies[-1]
        report.p50_latency = _percentile(latencies, 50)
        report.p90_latency = _percentile(latencies, 90)
        report.p95_latency = _percentile(latencies, 95)
        report.p99_latency = _percentile(latencies, 99)

        # TTFT
        ttfts = sorted(r.first_token_latency for r in successful if r.first_token_latency)
        if ttfts:
            report.avg_ttft = sum(ttfts) / len(ttfts)
            report.p50_ttft = _percentile(ttfts, 50)
            report.p90_ttft = _percentile(ttfts, 90)
            report.p99_ttft = _percentile(ttfts, 99)

    # 輸出報告
    report.print_report()

    if failed:
        print("[Benchmark] 失敗請求明細:")
        for r in failed[:5]:
            print(f"  #{r.request_id}: {r.error}")
        if len(failed) > 5:
            print(f"  ... 還有 {len(failed) - 5} 個失敗請求")

    if save_report:
        report.save_json()

    return report


# ============================================================
# CLI 入口
# ============================================================

def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="vLLM 異步 Benchmark")
    parser.add_argument("-n", "--requests", type=int, help="總請求數")
    parser.add_argument("-c", "--concurrency", type=int, help="併發數")
    parser.add_argument("-t", "--max-tokens", type=int, help="每次最大 token")
    parser.add_argument("-p", "--prompt", type=str, help="測試 prompt")
    parser.add_argument("--no-save", action="store_true", help="不儲存報告")
    args = parser.parse_args()

    asyncio.run(
        run_benchmark(
            total_requests=args.requests,
            concurrency=args.concurrency,
            max_tokens=args.max_tokens,
            prompt=args.prompt,
            save_report=not args.no_save,
        )
    )


if __name__ == "__main__":
    main()
