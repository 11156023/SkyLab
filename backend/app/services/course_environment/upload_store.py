"""老師上傳的文件落地到磁碟的共用做法。

課程環境文件（``/course-environments/{id}/files``）與班級週次教材
（``/teaching-classes/{id}/weeks/{id}/files``）都是同一套規則：

- 檔名只留最後一段、不可是 ``.``／``..``、長度不超過 255
- 磁碟檔名由伺服器產生（``<uuid hex><suffix>``），資料庫只存這個 storage_key
- 以 1 MiB 為單位串流寫入，超過上限就中止並刪掉寫到一半的檔案
- 刪除時 storage_key 一律當成根目錄底下的相對路徑，擋掉路徑跳脫

根目錄與錯誤訊息都由呼叫端在呼叫當下傳入，各自沿用原本的設定與翻譯文字
（測試也才能把根目錄換成暫存資料夾）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.exceptions import BadRequestError

MAX_FILENAME_LENGTH = 255
_CHUNK_BYTES = 1024 * 1024


def sanitize_upload_filename(
    raw: str | None,
    *,
    default: str,
    invalid_message: str,
    too_long_message: str,
) -> str:
    """取出使用者檔名的最後一段；不合法時丟 400。"""
    filename = (raw or default).replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."}:
        raise BadRequestError(invalid_message)
    if len(filename) > MAX_FILENAME_LENGTH:
        raise BadRequestError(too_long_message)
    return filename


async def save_upload(
    file: UploadFile,
    *,
    root: Path,
    suffix: str,
    max_bytes: int,
    too_large_message: str,
) -> tuple[uuid.UUID, str, int]:
    """把上傳內容串流寫進 ``root``，回傳 ``(file_id, storage_key, 位元組數)``。

    超過 ``max_bytes`` 或寫入途中出錯都會刪掉半成品；上傳檔一律在結束時關閉。
    """
    file_id = uuid.uuid4()
    storage_key = f"{file_id.hex}{suffix}"
    destination = root / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(_CHUNK_BYTES):
                written += len(chunk)
                if written > max_bytes:
                    raise BadRequestError(too_large_message)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return file_id, storage_key, written


def remove_blob(root: Path, storage_key: str | None) -> None:
    """刪掉 ``root`` 底下的 storage_key；解析後跳出根目錄的路徑一律不碰。"""
    if not storage_key:
        return
    resolved_root = root.resolve()
    stored = (resolved_root / storage_key).resolve()
    if stored.is_relative_to(resolved_root):
        stored.unlink(missing_ok=True)


__all__ = [
    "MAX_FILENAME_LENGTH",
    "remove_blob",
    "sanitize_upload_filename",
    "save_upload",
]
