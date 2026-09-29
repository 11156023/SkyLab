"""使用者頭像檔案的存放、查找與清除。

頭像存在 repo 根的 ``data/avatars``（與 teacher-judge 慣例一致），檔名固定為
``{user_id}{ext}``；每位使用者同時只保留一個檔。
"""

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

AVATAR_DIR = Path(__file__).resolve().parents[4] / "data" / "avatars"
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def store_avatar(user_id: uuid.UUID, ext: str, data: bytes) -> None:
    """寫入新頭像並移除該使用者其他副檔名的舊檔。"""
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for old in AVATAR_DIR.glob(f"{user_id}.*"):
        old.unlink(missing_ok=True)
    (AVATAR_DIR / f"{user_id}{ext}").write_bytes(data)


def delete_avatar_files(user_id: uuid.UUID) -> None:
    """帳號刪除後移除頭像檔：頭像端點不驗證身分，留著就會被任何知道 UUID 的人下載。"""
    for path in AVATAR_DIR.glob(f"{user_id}.*"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove avatar file %s", path, exc_info=True)


def find_avatar(user_id: uuid.UUID) -> Path | None:
    """回傳使用者目前的頭像檔路徑，沒有則 None。"""
    matches = sorted(AVATAR_DIR.glob(f"{user_id}.*"))
    return matches[0] if matches else None
