"""
單次測試 vllm-service / OpenAI-compatible Chat Completions API。

使用方式：
1. 修改下方 CONFIG 區塊的 API_KEY、BASE_URL、MODEL。
2. 從專案根目錄執行：
   python test_vllm_service_api.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


# =========================
# CONFIG: 請自行填入
# =========================
API_KEY = "ccai_Ql5cpWA46d2-iAhQeuv5tJ_nlcq1Ab12"
BASE_URL = "http://127.0.0.1:3000/v1"
MODEL = "gpt-oss-20B"

PROMPT = "請用繁體中文簡短回答：vLLM 是什麼？"
SYSTEM_PROMPT = "你是一個簡潔、可靠的 AI 助手，請使用繁體中文回答。"

MAX_TOKENS = 256
TEMPERATURE = 0.2
TIMEOUT_SECONDS = 60


def build_request_body() -> dict:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": PROMPT},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False,
    }


def call_chat_completions() -> dict:
    url = f"{BASE_URL.rstrip('/')}/chat/completions"
    body = json.dumps(build_request_body()).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        response_body = response.read().decode("utf-8")
        return json.loads(response_body)


def main() -> int:
    print(f"[vLLM API Test] POST {BASE_URL.rstrip('/')}/chat/completions")
    print(f"[Model] {MODEL}")
    print(f"[Prompt] {PROMPT}")

    try:
        data = call_chat_completions()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"\n[HTTP Error] {exc.code} {exc.reason}", file=sys.stderr)
        print(error_body, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"\n[Connection Error] {exc.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print(f"\n[Timeout] 超過 {TIMEOUT_SECONDS} 秒沒有回應", file=sys.stderr)
        return 1

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")

    print("\n[Response]")
    print(content)

    usage = data.get("usage")
    if usage:
        print("\n[Usage]")
        print(json.dumps(usage, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
