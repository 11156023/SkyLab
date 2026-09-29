"""頭像檔案服務（services/user/avatar_service）的單元測試：不需要 DB。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services.user import avatar_service


@pytest.fixture
def avatar_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "avatars"
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", target)
    return target


def test_avatar_dir_points_to_repo_data_avatars() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    assert avatar_service.AVATAR_DIR == repo_root / "data" / "avatars"


def test_store_creates_dir_and_replaces_other_extensions(avatar_dir: Path) -> None:
    user_id = uuid.uuid4()
    avatar_service.store_avatar(user_id, ".png", b"png")
    assert (avatar_dir / f"{user_id}.png").read_bytes() == b"png"

    avatar_service.store_avatar(user_id, ".jpg", b"jpg")
    assert sorted(p.name for p in avatar_dir.iterdir()) == [f"{user_id}.jpg"]
    assert avatar_service.find_avatar(user_id) == avatar_dir / f"{user_id}.jpg"


def test_find_avatar_missing_returns_none(avatar_dir: Path) -> None:
    assert avatar_service.find_avatar(uuid.uuid4()) is None
    avatar_dir.mkdir()
    assert avatar_service.find_avatar(uuid.uuid4()) is None


def test_delete_only_removes_that_users_files(avatar_dir: Path) -> None:
    keep, drop = uuid.uuid4(), uuid.uuid4()
    avatar_service.store_avatar(keep, ".png", b"a")
    avatar_service.store_avatar(drop, ".gif", b"b")

    avatar_service.delete_avatar_files(drop)

    assert avatar_service.find_avatar(drop) is None
    assert avatar_service.find_avatar(keep) == avatar_dir / f"{keep}.png"
    # 沒有檔案（或目錄不存在）時也不應拋錯
    avatar_service.delete_avatar_files(drop)
