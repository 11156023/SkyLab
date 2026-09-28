"""AI 模組整理後的行為鎖定：角色判斷改走 core.permissions、共用關鍵字比對。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.navigation.catalog import resolve_user_role
from app.ai.utils import mentions
from app.models.user import UserRole


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ({"is_superuser": True, "role": UserRole.student}, UserRole.admin),
        ({"is_superuser": True, "role": UserRole.teacher}, UserRole.admin),
        ({"is_superuser": True, "role": "bogus"}, UserRole.admin),
        ({"is_superuser": False, "role": UserRole.admin}, UserRole.admin),
        ({"is_superuser": False, "role": UserRole.teacher}, UserRole.teacher),
        ({"is_superuser": False, "role": "teacher"}, UserRole.teacher),
        ({"is_superuser": False, "role": "bogus"}, UserRole.student),
        ({"is_superuser": False, "role": None}, UserRole.student),
        ({}, UserRole.student),
    ],
)
def test_resolve_user_role(attrs: dict[str, Any], expected: UserRole) -> None:
    assert resolve_user_role(SimpleNamespace(**attrs)) == expected  # type: ignore[arg-type]


def test_mentions_is_case_insensitive_substring_match() -> None:
    assert mentions("我要跑 PyTorch", ("pytorch",))
    assert mentions("GPU 訓練", ("gpu", "cuda"))
    assert not mentions("架個網站", ("gpu", "cuda"))
    assert not mentions("任何文字", ())
