"""回歸測試：教學／課程／AI 評分路由用到的每個 t() key 都要在三個語系都有翻譯。

``app.core.i18n.translate`` 在某個語系找不到 key 時會退回 zh-TW，zh-TW 也沒有
才回傳 key 本身，所以漏加的訊息不會報錯，只會讓使用者看到別的語言或
``teacherJudgeSessions.xxx`` 這種原始字串；因此這裡直接檢查各語系自己的目錄。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.i18n import SUPPORTED_LANGUAGES, _catalog

BACKEND_ROOT = Path(__file__).resolve().parents[3]

B1_SOURCES = [
    "app/api/routes/teacher_judge_sessions.py",
    "app/api/routes/teacher_judge_scripts.py",
    "app/api/routes/teacher_judge_files.py",
    "app/api/routes/rubric.py",
    "app/api/routes/teaching_classes.py",
    "app/api/routes/course_environments.py",
    "app/schemas/course_environment.py",
    "app/schemas/teaching_class.py",
    "app/api/routes/courses.py",
    "app/api/routes/course_admin.py",
    "app/api/routes/classroom.py",
    "app/api/routes/quick_practice.py",
    "app/ai/teacher_judge/session_chat_service.py",
    "app/services/teaching/class_capacity_service.py",
    "app/services/teaching/class_lifecycle_service.py",
]

# t("key") 與跨行的 t(\n    "key", ...)
_KEY_PATTERN = re.compile(r'\bt\(\s*"([A-Za-z0-9_.\-]+)"')


def _source_files() -> list[Path]:
    files = [BACKEND_ROOT / rel for rel in B1_SOURCES]
    files.extend(sorted((BACKEND_ROOT / "app/services/course_environment").glob("*.py")))
    return [path for path in files if path.is_file()]


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for path in _source_files():
        keys.update(_KEY_PATTERN.findall(path.read_text(encoding="utf-8")))
    return keys


def test_scanner_finds_keys() -> None:
    keys = _used_keys()
    assert "teacherJudgeSessions.attachmentNotFound" in keys
    assert "teachingClasses.csvTooLarge" in keys
    assert "classroom.monitor_forbidden" in keys
    assert "class_capacity.ip_insufficient" in keys


@pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGUAGES))
def test_every_used_key_is_translated(lang: str) -> None:
    catalog = _catalog(lang)
    missing = sorted(key for key in _used_keys() if not catalog.get(key))
    assert missing == []
