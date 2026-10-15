"""app.services.user 套件的延遲載入與 avatar_service 清檔失敗分支補測。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services import user as user_pkg
from app.services.user import avatar_service


def test_package_lazy_loads_known_modules_and_rejects_others() -> None:
    assert user_pkg.avatar_service is avatar_service
    with pytest.raises(AttributeError, match="no_such_service"):
        user_pkg.no_such_service  # noqa: B018


def test_delete_avatar_files_logs_unlink_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user_id = uuid.uuid4()
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", tmp_path)
    (tmp_path / f"{user_id}.png").write_bytes(b"png")

    def locked(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("file in use")

    monkeypatch.setattr(Path, "unlink", locked)

    avatar_service.delete_avatar_files(user_id)

    assert "Failed to remove avatar file" in caplog.text
