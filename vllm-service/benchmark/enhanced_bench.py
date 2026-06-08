"""
增強版 Benchmark 模組
支援問答集測試、完整效能分析、Quality Metrics
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

from config.settings import Settings, get_settings
from benchmark.dataset import TestCase, load_dataset


@dataclass
class TestResult:
    """單一測試結果"""
    test_id: str
    category: str
    prompt: str
    success: bool
    latency: float  # 秒
    first_token_latency: float | None  # TTFT
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_text: str
    error: str | None = None
    matched_keywords: list[str] | None = None
    quality_score: float | None = None


@dataclass
class CategoryStats:
    """類別統計"""
    category: str
    total_tests: int = 0
    successful_tests: int = 0
    failed_tests: int = 0
    avg_latency_ms: float = 0.0
    avg_ttft_ms: float = 0.0
    avg_tokens: float = 0.0
    avg_quality_score: float = 0.0


@dataclass
class EnhancedBenchmarkReport:
    """增強版 Benchmark 報告"""
    # 基本資訊
    model_name: str = ""
    timestamp: str = ""
    dataset_name: str = ""
    dataset_version: str = ""
    total_tests: int = 0
    concurrency: int = 0

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
    p50_ttft_ms: float = 0.0
    p90_ttft_ms: float = 0.0
    p99_ttft_ms: float = 0.0

    # 品質指標
    avg_quality_score: float = 0.0
    keyword_match_rate: float = 0.0

    # 類別統計
    category_stats: dict[str, CategoryStats] = field(default_factory=dict)

    # 明細
    results: list[TestResult] = field(default_factory=list)

    def print_report(self) -> None:
        """印出格式化報告"""
        print(f"\n{'='*80}")
        print("  🚀 增強版 vLLM Benchmark 報告")
        print(f"{'='*80}")
        print(f"  時間:          {self.timestamp}")
        print(f"  模型:          {self.model_name}")
        print(f"  測試集:        {self.dataset_name} (v{self.dataset_version})")
        print(f"{'─'*80}")
        print(f"  總測試數:      {self.total_tests}")
        print(f"  成功測試:      {self.successful_tests}")
        print(f"  失敗測試:      {self.failed_tests}")
        print(f"  併發數:        {self.concurrency}")
        print(f"  總耗時:        {self.total_time:.2f}s")
        print(f"{'─'*80}")
        print("  ▸ Token 統計")
        print(f"    Prompt Token:      {self.total_prompt_tokens:,}")
        print(f"    Completion Token:  {self.total_completion_tokens:,}")
        print(f"    總 Token:          {self.total_tokens:,}")
        print(f"{'─'*80}")
        print("  ▸ 吞吐量")
        print(f"    請求/秒:           {self.requests_per_second:.2f} req/s")
        print(f"    總 Token/秒:       {self.tokens_per_second:.2f} tok/s")
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
            print(f"    P50:     {self.p50_ttft_ms:.1f}ms")
            print(f"    P90:     {self.p90_ttft_ms:.1f}ms")
            print(f"    P99:     {self.p99_ttft_ms:.1f}ms")

        if self.avg_quality_score > 0:
            print(f"{'─'*80}")
            print("  ▸ 品質指標")
            print(f"    平均品質分數:      {self.avg_quality_score:.2f}")
            print(f"    關鍵字匹配率:      {self.keyword_match_rate:.1%}")

        if self.category_stats:
            print(f"{'─'*80}")
            print("  ▸ 類別統計")
            for cat_name, stats in sorted(self.category_stats.items()):
                print(f"    [{stats.category}]")
                print(f"      測試數: {stats.total_tests} | "
                      f"成功: {stats.successful_tests} | "
                      f"失敗: {stats.failed_tests}")
                print(f"      平均延遲: {stats.avg_latency_ms:.1f}ms | "
                      f"平均 Token: {stats.avg_tokens:.0f}")
                if stats.avg_quality_score > 0:
                    print(f"      品質分數: {stats.avg_quality_score:.2f}")

        print(f"{'='*80}\n")

    def save_json(self, output_dir: str = "benchmark_results") -> str:
        """儲存 JSON 報告"""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = path / f"enhanced_bench_{ts}.json"

        data = {
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "dataset": {
                "name": self.dataset_name,
                "version": self.dataset_version,
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
                "p50": round(self.p50_ttft_ms, 1),
                "p90": round(self.p90_ttft_ms, 1),
                "p99": round(self.p99_ttft_ms, 1),
            },
            "quality": {
                "avg_score": round(self.avg_quality_score, 3),
                "keyword_match_rate": round(self.keyword_match_rate, 3),
            },
            "category_stats": {
                cat: {
                    "total": stats.total_tests,
                    "successful": stats.successful_tests,
                    "failed": stats.failed_tests,
                    "avg_latency_ms": round(stats.avg_latency_ms, 1),
                    "avg_ttft_ms": round(stats.avg_ttft_ms, 1),
                    "avg_tokens": round(stats.avg_tokens, 1),
                    "avg_quality": round(stats.avg_quality_score, 3),
                }
                for cat, stats in self.category_stats.items()
            },
            "details": [
                {
                    "test_id": r.test_id,
                    "category": r.category,
                    "prompt": r.prompt,
                    "success": r.success,
                    "latency_ms": round(r.latency * 1000, 1),
                    "ttft_ms": round(r.first_token_latency * 1000, 1) if r.first_token_latency else None,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "response_text": r.response_text[:200] + "..." if len(r.response_text) > 200 else r.response_text,
                    "quality_score": r.quality_score,
                    "matched_keywords": r.matched_keywords,
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


def _calculate_quality_score(
    response: str,
    expected_keywords: list[str] | None
) -> tuple[float, list[str]]:
    """計算回應品質分數"""
    if not expected_keywords:
        return 0.0, []

    matched = [kw for kw in expected_keywords if kw.lower() in response.lower()]
    score = len(matched) / len(expected_keywords) if expected_keywords else 0.0
    return score, matched


async def _send_test_request(
    client: AsyncOpenAI,
    model: str,
    test_case: TestCase,
    default_max_tokens: int,
    semaphore: asyncio.Semaphore,
) -> TestResult:
    """發送單個測試請求"""
    async with semaphore:
        start_time = time.perf_counter()
        first_token_time = None
        completion_text = ""
        max_tokens = test_case.max_tokens or default_max_tokens
        temperature = test_case.temperature or 0.7

        try:
            messages = [{"role": "user", "content": test_case.prompt}]

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
                prompt_tokens = len(test_case.prompt) // 4

            # 計算品質分數
            quality_score, matched_kw = _calculate_quality_score(
                completion_text,
                test_case.expected_keywords
            )

            return TestResult(
                test_id=test_case.id,
                category=test_case.category,
                prompt=test_case.prompt,
                success=True,
                latency=end_time - start_time,
                first_token_latency=(first_token_time - start_time) if first_token_time else None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                response_text=completion_text,
                matched_keywords=matched_kw if test_case.expected_keywords else None,
                quality_score=quality_score if test_case.expected_keywords else None,
            )

        except Exception as e:
            end_time = time.perf_counter()
            return TestResult(
                test_id=test_case.id,
                category=test_case.category,
                prompt=test_case.prompt,
                success=False,
                latency=end_time - start_time,
                first_token_latency=None,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                response_text="",
                error=str(e),
            )


def _compute_category_stats(results: list[TestResult]) -> dict[str, CategoryStats]:
    """計算類別統計"""
    by_category: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)

    stats = {}
    for cat, cat_results in by_category.items():
        successful = [r for r in cat_results if r.success]
        
        stat = CategoryStats(category=cat)
        stat.total_tests = len(cat_results)
        stat.successful_tests = len(successful)
        stat.failed_tests = len(cat_results) - len(successful)

        if successful:
            stat.avg_latency_ms = sum(r.latency * 1000 for r in successful) / len(successful)
            
            ttfts = [r.first_token_latency for r in successful if r.first_token_latency]
            if ttfts:
                stat.avg_ttft_ms = sum(t * 1000 for t in ttfts) / len(ttfts)
            
            stat.avg_tokens = sum(r.total_tokens for r in successful) / len(successful)
            
            quality_scores = [r.quality_score for r in successful if r.quality_score is not None]
            if quality_scores:
                stat.avg_quality_score = sum(quality_scores) / len(quality_scores)

        stats[cat] = stat

    return stats


async def run_enhanced_benchmark(
    dataset_path: str | Path,
    settings: Settings | None = None,
    concurrency: int | None = None,
    category_filter: str | None = None,
    save_report: bool = True,
) -> EnhancedBenchmarkReport:
    """
    執行增強版 Benchmark

    Args:
        dataset_path: 測試資料集 JSON 路徑
        settings: 設定物件 (可選)
        concurrency: 併發數 (覆蓋 .env)
        category_filter: 只測試特定類別
        save_report: 是否儲存 JSON 報告

    Returns:
        EnhancedBenchmarkReport
    """
    s = settings or get_settings()
    _conc = concurrency or s.bench_concurrency
    _model = s.resolved_model_path

    # 載入測試資料集
    print(f"\n{'='*80}")
    print("  🚀 增強版 vLLM Benchmark")
    print(f"{'='*80}")
    print(f"[Benchmark] 載入測試資料集: {dataset_path}")
    
    dataset = load_dataset(dataset_path)
    print(f"[Benchmark] 資料集: {dataset.name} (v{dataset.version})")
    print(f"[Benchmark] 描述: {dataset.description}")
    print(f"[Benchmark] 測試數: {len(dataset)}")
    print(f"[Benchmark] 類別: {', '.join(dataset.get_categories())}")

    # 篩選測試案例
    if category_filter:
        test_cases = dataset.filter_by_category(category_filter)
        print(f"[Benchmark] 僅測試類別: {category_filter} ({len(test_cases)} 個測試)")
    else:
        test_cases = dataset.test_cases

    if not test_cases:
        raise ValueError("沒有測試案例可執行")

    print(f"\n  模型:       {s.model_name}")
    print(f"  測試數:     {len(test_cases)}")
    print(f"  併發數:     {_conc}")
    print(f"{'='*80}\n")

    # 建立 API 客戶端
    client = AsyncOpenAI(
        base_url=f"http://{s.api_host}:{s.api_port}/v1",
        api_key=s.api_key,
        timeout=s.request_timeout,
    )

    semaphore = asyncio.Semaphore(_conc)

    # 發送所有測試請求
    print(f"[Benchmark] 開始測試 (併發: {_conc})...")
    overall_start = time.perf_counter()

    tasks = [
        _send_test_request(client, _model, tc, s.bench_max_tokens, semaphore)
        for tc in test_cases
    ]
    results: list[TestResult] = await asyncio.gather(*tasks)

    overall_end = time.perf_counter()
    total_time = overall_end - overall_start

    await client.close()

    # 計算統計
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    report = EnhancedBenchmarkReport(
        model_name=s.model_name,
        timestamp=datetime.now().isoformat(),
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        total_tests=len(test_cases),
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
        latencies_ms = sorted(r.latency * 1000 for r in successful)
        report.requests_per_second = len(successful) / total_time
        report.tokens_per_second = report.total_tokens / total_time
        report.output_tokens_per_second = report.total_completion_tokens / total_time
        
        report.avg_latency_ms = sum(latencies_ms) / len(latencies_ms)
        report.min_latency_ms = latencies_ms[0]
        report.max_latency_ms = latencies_ms[-1]
        report.p50_latency_ms = _percentile(latencies_ms, 50)
        report.p90_latency_ms = _percentile(latencies_ms, 90)
        report.p95_latency_ms = _percentile(latencies_ms, 95)
        report.p99_latency_ms = _percentile(latencies_ms, 99)

        # TTFT
        ttfts_ms = sorted(r.first_token_latency * 1000 for r in successful if r.first_token_latency)
        if ttfts_ms:
            report.avg_ttft_ms = sum(ttfts_ms) / len(ttfts_ms)
            report.p50_ttft_ms = _percentile(ttfts_ms, 50)
            report.p90_ttft_ms = _percentile(ttfts_ms, 90)
            report.p99_ttft_ms = _percentile(ttfts_ms, 99)

        # 品質指標
        quality_scores = [r.quality_score for r in successful if r.quality_score is not None]
        if quality_scores:
            report.avg_quality_score = sum(quality_scores) / len(quality_scores)
            has_keywords = [r for r in successful if r.matched_keywords is not None]
            if has_keywords:
                report.keyword_match_rate = sum(
                    1 if r.quality_score and r.quality_score > 0 else 0 
                    for r in has_keywords
                ) / len(has_keywords)

    # 類別統計
    report.category_stats = _compute_category_stats(results)

    # 輸出報告
    report.print_report()

    if failed:
        print("\n[Benchmark] 失敗測試明細:")
        for r in failed[:10]:
            print(f"  [{r.test_id}] {r.category}: {r.error}")
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
        description="vLLM 增強版 Benchmark - 支援問答集測試"
    )
    parser.add_argument(
        "dataset",
        type=str,
        help="測試資料集 JSON 檔案路徑",
    )
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        help="併發數",
    )
    parser.add_argument(
        "--category",
        type=str,
        help="只測試特定類別",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不儲存報告",
    )
    args = parser.parse_args()

    asyncio.run(
        run_enhanced_benchmark(
            dataset_path=args.dataset,
            concurrency=args.concurrency,
            category_filter=args.category,
            save_report=not args.no_save,
        )
    )


if __name__ == "__main__":
    main()
