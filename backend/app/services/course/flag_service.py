"""Flag 驗證純函式：正規化、SHA-256 比對、進度百分比。

刻意保持無 DB / 無 I/O，方便單元測試與在 service 間複用。
比對策略：strip 前後空白後精確比對（大小寫敏感），與設計文件一致。
"""

import hashlib
import hmac


def normalize_answer(raw: str) -> str:
    """正規化答案：僅去除前後空白（含換行/tab），保留大小寫與內部空白。"""
    return raw.strip()


def hash_flag(flag: str) -> str:
    """明文 flag → 正規化 → SHA-256 hexdigest（入庫格式，64 字元）。"""
    return hashlib.sha256(normalize_answer(flag).encode("utf-8")).hexdigest()


def verify_flag(answer: str | None, flag_hash: str | None) -> bool:
    """比對學生答案與儲存的 flag hash；缺答案或缺 hash 一律 False。"""
    if not answer or not flag_hash:
        return False
    candidate = hashlib.sha256(
        normalize_answer(answer).encode("utf-8")
    ).hexdigest()
    # 常數時間比較，避免以回應時間差猜測 hash
    return hmac.compare_digest(candidate, flag_hash)


def progress_percent(completed: int, total: int) -> float:
    """完成百分比（一位小數）；total 為 0 回 0.0，completed 以 total 封頂。"""
    if total <= 0:
        return 0.0
    return round(min(completed, total) / total * 100, 1)


__all__ = ["normalize_answer", "hash_flag", "verify_flag", "progress_percent"]
