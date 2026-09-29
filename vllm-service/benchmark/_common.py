"""Benchmark 共用：串流請求計時、百分位數與延遲統計。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class StreamOutcome:
    """一次串流 chat completion 的量測結果（秒）。"""

    latency: float
    first_token_latency: float | None
    prompt_tokens: int
    completion_tokens: int
    text: str


def percentile(sorted_data: list[float], p: float) -> float:
    """以線性內插計算已排序資料的百分位數。"""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])


def latency_stats(values: list[float]) -> dict[str, float]:
    """回傳 avg/min/max/p50/p90/p95/p99；空清單全部為 0。"""
    if not values:
        return {key: 0.0 for key in ("avg", "min", "max", "p50", "p90", "p95", "p99")}
    ordered = sorted(values)
    return {
        "avg": sum(ordered) / len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "p95": percentile(ordered, 95),
        "p99": percentile(ordered, 99),
    }


async def stream_chat(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
) -> StreamOutcome:
    """送出一個串流請求並量測 TTFT／端到端延遲；錯誤直接往上拋。

    usage 取自最後一個 chunk（stream_options.include_usage）；上游沒回 usage 時
    以「字元數 // 4」粗估 token 數。
    """
    start = time.perf_counter()
    first_token_at: float | None = None
    parts: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                parts.append(delta)
        usage = getattr(chunk, "usage", None)
        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

    end = time.perf_counter()
    text = "".join(parts)
    if completion_tokens == 0:
        completion_tokens = max(1, len(text) // 4)
    if prompt_tokens == 0:
        prompt_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages)

    return StreamOutcome(
        latency=end - start,
        first_token_latency=(first_token_at - start) if first_token_at is not None else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        text=text,
    )
