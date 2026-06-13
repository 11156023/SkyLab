"""
API 呼叫範例入口
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.client import ModelClient


def demo_sync():
    """同步呼叫範例"""
    client = ModelClient()

    print("=" * 60)
    print("  同步 Chat Completion 範例")
    print("=" * 60)

    # 範例 1: 簡單對話
    print("\n[範例1] 簡單對話")
    answer = client.chat_simple(
        "請用一句話解釋什麼是 Transformer 模型？",
        max_tokens=2048,
    )
    print(f"回應: {answer}\n")

    # 範例 2: 多輪對話
    print("[範例2] 多輪對話")
    messages = [
        {"role": "system", "content": "你是一個友善的 AI 助手，使用繁體中文回答。"},
        {"role": "user", "content": "什麼是 vLLM？"},
    ]
    response = client.chat(messages, max_tokens=2048)
    print(f"回應: {response.choices[0].message.content}\n")

    # 範例 3: 流式輸出
    print("[範例3] 流式輸出")
    print("回應: ", end="")
    stream = client.chat_stream("請簡述深度學習的三大要素", max_tokens=2048)
    if stream:
        for chunk in stream:
            print(chunk, end="", flush=True)
    print("\n")


async def demo_async():
    """異步呼叫範例"""
    client = ModelClient()

    print("=" * 60)
    print("  異步 Chat Completion 範例")
    print("=" * 60)

    # 範例 4: 異步對話
    print("\n[範例4] 異步簡單對話")
    answer = await client.achat_simple(
        "什麼是 GPU 推論優化？",
        max_tokens=2048,
    )
    print(f"回應: {answer}\n")

    # 範例 5: 併發多個請求
    print("[範例5] 併發 3 個請求")
    prompts = [
        "用一句話說明什麼是注意力機制？",
        "用一句話說明什麼是量化推論？",
        "用一句話說明什麼是 KV Cache？",
    ]
    tasks = [client.achat_simple(p, max_tokens=2048) for p in prompts]
    results = await asyncio.gather(*tasks)
    for p, r in zip(prompts, results):
        print(f"  Q: {p}")
        print(f"  A: {r}\n")

    # 範例 6: 異步流式
    print("[範例6] 異步流式輸出")
    print("回應: ", end="")
    stream = await client.achat_stream("什麼是連續批次處理？", max_tokens=2048)
    if stream:
        async for chunk in stream:
            print(chunk, end="", flush=True)
    print("\n")

    await client.aclose()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sync"

    if mode == "async":
        asyncio.run(demo_async())
    elif mode == "all":
        demo_sync()
        print("\n" + "─" * 60 + "\n")
        asyncio.run(demo_async())
    else:
        demo_sync()
