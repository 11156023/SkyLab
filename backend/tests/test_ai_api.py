#!/usr/bin/env python3
"""
SkyLab AI API — 模型呼叫測試
直接填入 API Key 測試模型輸出

使用方式：
    python test_ai_api.py                # 執行基本測試（列表、非串流、串流）
    python test_ai_api.py --rate-limit   # 測試速率限制（19 個並發 + 6 個序列）
    python test_ai_api.py --full         # 執行完整測試（包含速率限制）
"""

import asyncio
import json
import sys
import time

import httpx

# ─────────────────────────────────────────────
# ★ 填入你的設定
# ─────────────────────────────────────────────
BACKEND_URL = "http://localhost:8000/api/v1"
API_KEY = "ccai_nXbbbUqCqucKWXGBHtp0zuLH0rJ0ktuU"  # ← 填入你的 ccai_xxx 金鑰
MODEL = "Qwen/Qwen3-14B-FP8"  # ← 留空會自動抓第一個可用模型
PROMPT = "你是什麼模型 50字介紹下"
# ─────────────────────────────────────────────


def header():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def list_models() -> str:
    """查詢可用模型，回傳第一個模型名稱"""
    print("\n[1] 查詢可用模型...")
    with httpx.Client(timeout=15) as client:
        r = client.get(f"{BACKEND_URL}/ai-proxy/models", headers=header())

    if r.status_code != 200:
        print(f"    ✗ 失敗 {r.status_code}: {r.text}")
        return MODEL

    models = r.json().get("data", [])
    if not models:
        print("    ✗ 沒有可用模型")
        return MODEL

    print("    可用模型：")
    for m in models:
        print(f"      - {m['id']}")

    chosen = MODEL or models[0]["id"]
    print(f"    → 使用模型: {chosen}")
    return chosen


def chat(model: str):
    """非串流呼叫"""
    print("\n[2] 非串流呼叫")
    print(f"    prompt: {PROMPT}")

    start = time.time()
    with httpx.Client(timeout=120) as client:
        r = client.post(
            f"{BACKEND_URL}/ai-proxy/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "stream": False,
                "max_tokens": 2048,
            },
            headers=header(),
        )
    elapsed = time.time() - start

    if r.status_code != 200:
        print(f"    ✗ 失敗 {r.status_code}: {r.text}")
        return

    result = r.json()
    reply = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})

    print(f"\n    ── 回應 ({elapsed:.1f}s) ──")
    print(f"    {reply}")
    print(
        f"\n    tokens → prompt: {usage.get('prompt_tokens', '?')}  "
        f"completion: {usage.get('completion_tokens', '?')}  "
        f"total: {usage.get('total_tokens', '?')}"
    )
    print("    ✓ 成功")


def chat_stream(model: str):
    """串流呼叫"""
    print("\n[3] 串流呼叫")
    print(f"    prompt: {PROMPT}")
    print("    輸出：", end="", flush=True)

    start = time.time()
    char_count = 0

    try:
        with httpx.stream(
            "POST",
            f"{BACKEND_URL}/ai-proxy/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "stream": True,
                "max_tokens": 2048,
            },
            headers=header(),
            timeout=120,
        ) as r:
            if r.status_code != 200:
                print(f"\n    ✗ 失敗 {r.status_code}")
                return

            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                chunk_str = line[6:]
                if chunk_str == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk_str)["choices"][0]["delta"].get(
                        "content", ""
                    )
                    print(delta, end="", flush=True)
                    char_count += len(delta)
                except (json.JSONDecodeError, KeyError):
                    # Ignore malformed chunks during streaming
                    pass

    except httpx.RequestError as e:
        print(f"\n    ✗ 連線錯誤: {e}")
        return

    elapsed = time.time() - start
    print(f"\n    ✓ 成功 ({elapsed:.1f}s，共 {char_count} 字)")


def check_rate_limit_status():
    """檢查速率限制狀態"""
    print("\n[4] 查詢速率限制狀態")

    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{BACKEND_URL}/ai-proxy/rate-limit/status",
            headers=header(),
        )

    if r.status_code != 200:
        print(f"    ✗ 失敗 {r.status_code}: {r.text}")
        return

    data = r.json()
    print(f"    限制：{data['limit_per_minute']} 次/分鐘")
    print(f"    已用：{data['current_usage']} 次")
    print(f"    剩餘：{data['remaining']} 次")
    print(f"    重置時間：{data['reset_at']}")

    if data.get("disabled"):
        print("    ⚠️  Redis 已禁用 - 速率限制未生效")
    elif data.get("error"):
        print(f"    ⚠️  Redis 錯誤: {data['error']}")
    else:
        print("    ✓ Redis 正常運作")


def test_rate_limit(model: str, concurrent_count: int = 19, sequential_count: int = 6):
    """
    測試速率限制功能（改進版：並發 + 序列）

    策略：
    1. 先同時發送 19 個並發請求（避免壓垮後端）
    2. 等待並發請求完成
    3. 再逐一發送 6 個序列請求

    預期行為：
    - Redis 啟用時：前 20 個成功，後 5 個被擋下 (429)
    - Redis 禁用時：所有 25 個請求都成功
    """
    total_requests = concurrent_count + sequential_count

    print("\n[5] 速率限制壓力測試（優化版）")
    print(f"    階段 1: 同時發送 {concurrent_count} 個並發請求")
    print(f"    階段 2: 逐一發送 {sequential_count} 個序列請求")
    print(f"    總計: {total_requests} 個請求")
    print("    限制: 20 次/分鐘")
    print(f"    預期: 前 20 個成功，第 21-{total_requests} 個被擋下 (HTTP 429)")
    print()

    success_count = 0
    rate_limited_count = 0
    error_count = 0
    results = []

    start_time = time.time()

    # ========================================
    # 階段 1: 並發請求
    # ========================================
    print(f"    階段 1: 發送 {concurrent_count} 個並發請求...")

    async def send_concurrent_request(session_id: int) -> tuple[int, int, str, str]:
        """
        發送單個並發請求

        Returns:
            (session_id, status_code, status_text, detail)
        """
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.post(
                    f"{BACKEND_URL}/ai-proxy/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                        "max_tokens": 10,
                    },
                    headers=header(),
                )

                if response.status_code == 200:
                    return (session_id, 200, "成功", "")
                elif response.status_code == 429:
                    detail = response.json().get("detail", {})
                    if isinstance(detail, dict):
                        msg = f"{detail.get('current', '?')}/{detail.get('limit', '?')}"
                        return (session_id, 429, "速率限制", msg)
                    return (session_id, 429, "速率限制", "")
                else:
                    error_text = response.text[:100]
                    return (session_id, response.status_code, "錯誤", error_text)

            except httpx.TimeoutException as e:
                return (session_id, 0, "超時", str(e)[:50])
            except httpx.ConnectError as e:
                return (session_id, 0, "連線失敗", str(e)[:50])
            except Exception as e:
                return (session_id, 0, "異常", str(e)[:50])

    async def run_concurrent_requests():
        tasks = [send_concurrent_request(i + 1) for i in range(concurrent_count)]
        return await asyncio.gather(*tasks)

    # 執行並發請求
    concurrent_results = asyncio.run(run_concurrent_requests())

    # 處理並發結果
    for req_id, status_code, status_text, detail in concurrent_results:
        results.append((req_id, status_code, status_text, detail))

        if status_code == 200:
            success_count += 1
            symbol = "✓"
        elif status_code == 429:
            rate_limited_count += 1
            symbol = "✗"
        else:
            error_count += 1
            symbol = "✗"

        # 顯示結果
        if detail:
            print(
                f"    [{req_id:2d}] {symbol} {status_text} ({status_code}) - {detail}"
            )
        else:
            print(f"    [{req_id:2d}] {symbol} {status_text} ({status_code})")

    elapsed_phase1 = time.time() - start_time
    print(f"\n    階段 1 完成 (耗時 {elapsed_phase1:.2f}s)")
    print(
        f"    當前統計: ✓ {success_count}  ✗ 限制 {rate_limited_count}  ✗ 錯誤 {error_count}"
    )

    # ========================================
    # 階段 2: 序列請求
    # ========================================
    print(f"\n    階段 2: 逐一發送 {sequential_count} 個序列請求...")

    phase2_start = time.time()

    with httpx.Client(timeout=60) as client:
        for i in range(sequential_count):
            req_num = concurrent_count + i + 1

            try:
                response = client.post(
                    f"{BACKEND_URL}/ai-proxy/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                        "max_tokens": 10,
                    },
                    headers=header(),
                )

                if response.status_code == 200:
                    success_count += 1
                    print(f"    [{req_num:2d}] ✓ 成功 (200)")
                    results.append((req_num, 200, "成功", ""))

                elif response.status_code == 429:
                    rate_limited_count += 1
                    detail = response.json().get("detail", {})
                    if isinstance(detail, dict):
                        msg = f"{detail.get('current', '?')}/{detail.get('limit', '?')}"
                        print(f"    [{req_num:2d}] ✗ 速率限制 (429) - 使用量: {msg}")
                        results.append((req_num, 429, "速率限制", msg))
                    else:
                        print(f"    [{req_num:2d}] ✗ 速率限制 (429)")
                        results.append((req_num, 429, "速率限制", ""))

                else:
                    error_count += 1
                    error_text = response.text[:100]
                    print(
                        f"    [{req_num:2d}] ✗ 錯誤 ({response.status_code}) - {error_text}"
                    )
                    results.append((req_num, response.status_code, "錯誤", error_text))

            except httpx.TimeoutException as e:
                error_count += 1
                print(f"    [{req_num:2d}] ✗ 超時 - {str(e)[:50]}")
                results.append((req_num, 0, "超時", str(e)[:50]))

            except httpx.ConnectError as e:
                error_count += 1
                print(f"    [{req_num:2d}] ✗ 連線失敗 - {str(e)[:50]}")
                results.append((req_num, 0, "連線失敗", str(e)[:50]))

            except Exception as e:
                error_count += 1
                print(f"    [{req_num:2d}] ✗ 異常 - {str(e)[:100]}")
                results.append((req_num, 0, "異常", str(e)[:100]))

            # 序列請求之間稍微延遲
            if i < sequential_count - 1:
                time.sleep(0.1)

    elapsed_phase2 = time.time() - phase2_start
    elapsed_total = time.time() - start_time

    print(f"\n    階段 2 完成 (耗時 {elapsed_phase2:.2f}s)")

    # ========================================
    # 總結報告
    # ========================================
    print("\n    ╔══════════════════════════════════════════╗")
    print(f"    ║  測試完成 (總耗時 {elapsed_total:.2f}s)             ║")
    print("    ╠══════════════════════════════════════════╣")
    print(f"    ║  總請求數：{total_requests:2d}                         ║")
    print(f"    ║  ✓ 成功：{success_count:2d}                           ║")
    print(f"    ║  ✗ 被限制：{rate_limited_count:2d}                         ║")
    print(f"    ║  ✗ 其他錯誤：{error_count:2d}                         ║")
    print("    ╠══════════════════════════════════════════╣")

    # 判斷測試結果
    if error_count > 0:
        print(f"    ║  ⚠️  發生 {error_count} 個錯誤，請檢查以下問題：   ║")
        print("    ║  1. 後端服務是否正常運行           ║")
        print("    ║  2. VLLM Gateway 是否可訪問        ║")
        print("    ║  3. API Key 是否有效               ║")
        print("    ║  4. 網路連線是否正常               ║")
    elif rate_limited_count > 0:
        print("    ║  ✅ Redis 速率限制正常運作！        ║")
        print(f"    ║     成功擋下 {rate_limited_count} 個超限請求           ║")
    elif success_count == total_requests:
        print("    ║  ⚠️  所有請求都成功 - Redis 可能未啟用 ║")
        print("    ║     請檢查 .env 中的 REDIS_ENABLED  ║")
    else:
        print("    ║  ⚠️  測試結果異常，請檢查錯誤訊息    ║")

    print("    ╚══════════════════════════════════════════╝")

    # 顯示錯誤詳情（如果有）
    if error_count > 0:
        print("\n    錯誤詳情:")
        for req_id, status_code, status_text, detail in results:
            if status_text not in ["成功", "速率限制"]:
                print(f"      [{req_id:2d}] {status_text} ({status_code}): {detail}")

        print("\n    💡 常見問題排查:")
        print("       - HTTP 500: 後端內部錯誤，檢查後端日誌")
        print("       - 連線失敗: VLLM Gateway 未運行或網址錯誤")
        print("       - 超時: 請求處理時間過長，增加 timeout")
        print("       - HTTP 401: API Key 無效或已過期")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    # 解析命令列參數
    test_rate_limit_only = "--rate-limit" in sys.argv
    test_full = "--full" in sys.argv

    print("=" * 55)
    print("  SkyLab AI API 模型測試")
    print(f"  backend : {BACKEND_URL}")
    print(f"  api_key : {'已設定' if API_KEY and API_KEY != 'ccai_' else '未設定'}")
    if test_rate_limit_only:
        print("  模式    : 速率限制測試")
    elif test_full:
        print("  模式    : 完整測試")
    else:
        print("  模式    : 基本測試")
    print("=" * 55)

    if not API_KEY or API_KEY == "ccai_":
        print("\n  ✗ 請先填入 API_KEY！")
        raise SystemExit(1)

    model = list_models()

    # 根據模式執行不同測試
    if test_rate_limit_only:
        # 僅測試速率限制
        check_rate_limit_status()
        test_rate_limit(model, concurrent_count=19, sequential_count=6)
    elif test_full:
        # 完整測試
        chat(model)
        chat_stream(model)
        check_rate_limit_status()

        # 詢問是否執行速率限制測試
        print("\n" + "=" * 55)
        response = input("  是否執行速率限制測試？(將發送 25 個請求) [y/N]: ")
        if response.lower() in ["y", "yes"]:
            test_rate_limit(model, concurrent_count=19, sequential_count=6)
        else:
            print("  跳過速率限制測試")
    else:
        # 基本測試（不包含速率限制）
        chat(model)
        chat_stream(model)
        check_rate_limit_status()

        print("\n" + "=" * 55)
        print("  💡 提示：")
        print("     使用 'python test_ai_api.py --rate-limit' 測試速率限制")
        print("     使用 'python test_ai_api.py --full' 執行完整測試")
        print("=" * 55)

    print("\n" + "=" * 55)
    print("  測試完成")
    print("=" * 55)
