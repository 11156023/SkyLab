"""帳號密碼複雜度規則（純函式，不碰 DB）。"""

import pytest

from app.exceptions import BadRequestError
from app.services.user.password_policy import (
    PASSWORD_MAX_LENGTH,
    ensure_password_complexity,
    password_issues,
)


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("Skylab#2026", []),
        ("Ab1!", ["length"]),
        ("skylab#2026", ["uppercase"]),
        ("SKYLAB#2026", ["lowercase"]),
        ("Skylab#abcd", ["digit"]),
        ("Skylab12026", ["symbol"]),
        ("password", ["uppercase", "digit", "symbol"]),
        ("", ["length", "uppercase", "lowercase", "digit", "symbol"]),
        # 空白不算特殊符號；全形標點與非英文字母算
        ("Skylab 2026", ["symbol"]),
        ("Skylab，2026", []),
        ("Skylab雲2026", []),
        # 全形英數不算英文字母／數字
        ("ＳＫＹlab#2026", ["uppercase"]),
        ("Aa1!" + "x" * PASSWORD_MAX_LENGTH, ["length"]),
    ],
)
def test_password_issues(password: str, expected: list[str]) -> None:
    assert password_issues(password) == expected


def test_ensure_password_complexity_raises_400_without_echoing_password() -> None:
    with pytest.raises(BadRequestError) as excinfo:
        ensure_password_complexity("weakpassword1")
    assert excinfo.value.status_code == 400
    assert "weakpassword1" not in excinfo.value.message
    assert "8" in excinfo.value.message


def test_ensure_password_complexity_accepts_strong_password() -> None:
    ensure_password_complexity("Skylab#2026")
