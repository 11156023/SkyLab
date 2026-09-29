"""使用統計的 tz 查詢參數無效時要退回 UTC，不能讓端點變成 500。

pip 的 tzdata 套件裡 "Asia" 這類名稱是目錄，ZoneInfo 在 Windows 丟
PermissionError、在 Linux 丟 IsADirectoryError。CI 的 Linux 沒裝 pip tzdata，
直接呼叫 ZoneInfo("Asia") 只會走到 ZoneInfoNotFoundError，所以用 monkeypatch
模擬這兩種 OSError。
"""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.llm_gateway import ai_gateway_service


@pytest.mark.parametrize("error", [PermissionError, IsADirectoryError])
def test_directory_zone_name_falls_back_to_utc(
    monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    def fake_zoneinfo(key: str) -> ZoneInfo:
        raise error(13, "Permission denied", key)

    monkeypatch.setattr(ai_gateway_service, "ZoneInfo", fake_zoneinfo)

    assert ai_gateway_service._usage_timezone("Asia") is timezone.utc


def test_invalid_and_missing_zone_names_fall_back_to_utc() -> None:
    assert ai_gateway_service._usage_timezone(None) is timezone.utc
    assert ai_gateway_service._usage_timezone("") is timezone.utc
    assert ai_gateway_service._usage_timezone("Invalid/Zone") is timezone.utc
    assert ai_gateway_service._usage_timezone("../etc/passwd") is timezone.utc


def test_valid_zone_name_is_used() -> None:
    assert ai_gateway_service._usage_timezone("UTC") == ZoneInfo("UTC")
