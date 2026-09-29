"""整理：課程環境文件與班級教材共用的上傳落地規則。"""

import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.api.routes import course_environments
from app.exceptions import BadRequestError
from app.services.course_environment import environment_service, upload_store


def _upload(data: bytes, filename: str | None = "notes.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("notes.pdf", "notes.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\t\\lab 1.txt", "lab 1.txt"),
        (None, "fallback"),
        ("", "fallback"),
    ],
)
def test_sanitize_keeps_only_last_path_segment(raw, expected) -> None:
    assert (
        upload_store.sanitize_upload_filename(
            raw, default="fallback", invalid_message="bad", too_long_message="long"
        )
        == expected
    )


@pytest.mark.parametrize("raw", ["dir/..", "  .  ", "a/"])
def test_sanitize_rejects_dot_names(raw) -> None:
    with pytest.raises(BadRequestError) as exc:
        upload_store.sanitize_upload_filename(
            raw, default="x", invalid_message="bad", too_long_message="long"
        )
    assert exc.value.message == "bad"


def test_sanitize_rejects_long_names() -> None:
    with pytest.raises(BadRequestError) as exc:
        upload_store.sanitize_upload_filename(
            "a" * 256, default="x", invalid_message="bad", too_long_message="long"
        )
    assert exc.value.message == "long"


@pytest.mark.asyncio
async def test_save_upload_writes_under_root(tmp_path: Path) -> None:
    file_id, storage_key, written = await upload_store.save_upload(
        _upload(b"hello"),
        root=tmp_path / "nested",
        suffix=".bin",
        max_bytes=10,
        too_large_message="too large",
    )
    assert storage_key == f"{file_id.hex}.bin"
    assert written == 5
    assert (tmp_path / "nested" / storage_key).read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_save_upload_removes_partial_file_when_over_limit(
    tmp_path: Path,
) -> None:
    with pytest.raises(BadRequestError) as exc:
        await upload_store.save_upload(
            _upload(b"x" * 11),
            root=tmp_path,
            suffix=".task",
            max_bytes=10,
            too_large_message="too large",
        )
    assert exc.value.message == "too large"
    assert list(tmp_path.iterdir()) == []


def test_remove_blob_ignores_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "keep.bin"
    inside.write_bytes(b"1")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"2")

    upload_store.remove_blob(root, "../outside.bin")
    upload_store.remove_blob(root, None)
    upload_store.remove_blob(root, "missing.bin")
    assert outside.exists()
    assert inside.exists()

    upload_store.remove_blob(root, "keep.bin")
    assert not inside.exists()


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.deleted: list = []
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)

    def delete(self, row) -> None:
        self.deleted.append(row)

    def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_environment_file_upload_and_delete_use_shared_store(
    monkeypatch, tmp_path: Path
) -> None:
    environment = SimpleNamespace(id=uuid.uuid4(), updated_at=None)
    user = SimpleNamespace(id=uuid.uuid4())
    session = _FakeSession()
    monkeypatch.setattr(environment_service, "ENVIRONMENT_FILE_ROOT", tmp_path)
    monkeypatch.setattr(
        environment_service, "get_environment", lambda *_args: environment
    )
    monkeypatch.setattr(environment_service, "latest_version", lambda *_args: None)
    monkeypatch.setattr(
        environment_service, "serialize_version", lambda *_args: {"ok": True}
    )

    result = await course_environments.upload_environment_file(
        environment.id, session, user, _upload(b"slides", "../week1.pdf")
    )
    assert result == {"ok": True}
    stored = session.added[0]
    assert stored.filename == "week1.pdf"
    assert stored.size_bytes == 6
    assert (tmp_path / stored.storage_key).read_bytes() == b"slides"

    monkeypatch.setattr(
        environment_service,
        "get_environment_file",
        lambda *_args: (environment, stored),
    )
    course_environments.delete_environment_file(
        environment.id, stored.id, session, user
    )
    assert session.deleted == [stored]
    assert not (tmp_path / stored.storage_key).exists()
