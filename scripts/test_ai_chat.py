#!/usr/bin/env python3
"""互動式測試 SkyLab 的 OpenAI-compatible Chat API。

執行：
    python scripts/test_ai_chat.py

API key 只在終端機以隱藏輸入讀取，不會寫入檔案或列印到畫面。
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PUBLIC_BASE_URL = "https://skylab-tw.com/api/v1"
API_BASE_URL = f"{PUBLIC_BASE_URL.rstrip('/')}/ai-proxy"
REQUEST_TIMEOUT_SECONDS = 120
EXIT_WORDS = {"q", "quit", "exit", "離開", "結束"}


class APIRequestError(RuntimeError):
    """API 請求失敗，且訊息已整理成可安全顯示的內容。"""


def _error_message(raw_body: bytes) -> str:
    """從錯誤回應擷取簡短訊息，避免直接傾印整個回應。"""
    try:
        payload = json.loads(raw_body.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return raw_body.decode("utf-8", errors="replace").strip()[:500] or "無錯誤內容"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
        for key in ("detail", "message"):
            message = payload.get(key)
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
    return "伺服器回傳了無法辨識的錯誤格式"


def request_json(
    method: str,
    path: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """呼叫 API 並解析 JSON；不會把 API key 放入錯誤訊息。"""
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{API_BASE_URL}/{path.lstrip('/')}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw_body = response.read()
    except HTTPError as exc:
        raise APIRequestError(
            f"HTTP {exc.code}：{_error_message(exc.read())}"
        ) from exc
    except (TimeoutError, URLError) as exc:
        reason = getattr(exc, "reason", exc)
        raise APIRequestError(f"無法連線到 API：{reason}") from exc

    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise APIRequestError("API 回應不是有效的 JSON") from exc


def fetch_models(api_key: str) -> list[str]:
    """取得目前 key 可用的模型 ID。"""
    payload = request_json("GET", "models", api_key)
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise APIRequestError("/models 回應缺少 data 模型清單")

    models: list[str] = []
    for item in items:
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip() and model_id not in models:
                models.append(model_id)
    if not models:
        raise APIRequestError("目前 API key 沒有可用模型")
    return models


def choose_model(models: list[str]) -> str | None:
    """顯示編號清單並讓使用者選擇模型。"""
    print("\n可用模型：")
    for index, model in enumerate(models, start=1):
        print(f"  {index}. {model}")

    while True:
        choice = input("請輸入模型編號（輸入 q 離開）：").strip()
        if choice.lower() in EXIT_WORDS:
            return None
        try:
            index = int(choice)
        except ValueError:
            print("請輸入清單中的數字。")
            continue
        if 1 <= index <= len(models):
            return models[index - 1]
        print(f"請輸入 1 到 {len(models)} 之間的數字。")


def _assistant_text(payload: Any) -> str:
    """從 Chat Completions 回應取出 assistant content。"""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(parts).strip()
    return ""


def chat(model: str, api_key: str) -> None:
    """進入簡單多輪對話；輸入 q／離開可結束。"""
    messages: list[dict[str, str]] = []
    print(f"\n已選擇模型：{model}")
    print("輸入訊息開始對話；輸入 q、離開 或 結束可退出。\n")

    while True:
        prompt = input("你：").strip()
        if prompt.lower() in EXIT_WORDS:
            return
        if not prompt:
            continue

        messages.append({"role": "user", "content": prompt})
        try:
            response = request_json(
                "POST",
                "chat/completions",
                api_key,
                {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 512,
                    "stream": False,
                },
            )
        except APIRequestError as exc:
            messages.pop()
            print(f"\n請求失敗：{exc}\n")
            continue

        answer = _assistant_text(response)
        if not answer:
            finish_reason = "未知"
            if isinstance(response, dict):
                choices = response.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    finish_reason = str(choices[0].get("finish_reason") or "未知")
            print(f"\n模型沒有回傳文字內容（finish_reason={finish_reason}）。\n")
            continue

        messages.append({"role": "assistant", "content": answer})
        print(f"\n模型：{answer}\n")


def main() -> int:
    print(f"SkyLab AI Chat 測試工具\nAPI：{API_BASE_URL}")
    try:
        api_key = getpass.getpass("請輸入 API key（輸入時不顯示）：").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return 130
    if not api_key:
        print("API key 不可為空。", file=sys.stderr)
        return 2

    print("正在取得模型清單……")
    try:
        models = fetch_models(api_key)
    except APIRequestError as exc:
        print(f"取得模型清單失敗：{exc}", file=sys.stderr)
        return 1

    try:
        model = choose_model(models)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return 130
    if model is None:
        print("已離開。")
        return 0

    try:
        chat(model, api_key)
    except (EOFError, KeyboardInterrupt):
        print("\n已離開。")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
