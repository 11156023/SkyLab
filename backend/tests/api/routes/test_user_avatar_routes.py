"""頭像端點（/users/me/avatar、/users/{id}/avatar）經 avatar_service 存取檔案。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.services.user import avatar_service
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string, random_password

API = settings.API_V1_STR
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def avatar_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", tmp_path)
    return tmp_path


def _new_user(
    client: TestClient, superuser_headers: dict[str, str]
) -> tuple[str, dict[str, str]]:
    email = random_email()
    password = random_password()
    r = client.post(
        f"{API}/users/",
        headers=superuser_headers,
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    headers = user_authentication_headers(client=client, email=email, password=password)
    return r.json()["id"], headers


def test_upload_serve_replace_and_delete(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    avatar_dir: Path,
) -> None:
    user_id, headers = _new_user(client, superuser_token_headers)

    r = client.post(
        f"{API}/users/me/avatar",
        headers=headers,
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["avatar_url"].startswith(f"{API}/users/{user_id}/avatar?v=")

    got = client.get(f"{API}/users/{user_id}/avatar")
    assert got.status_code == 200
    assert got.content == _PNG

    # 換成另一種格式：舊副檔名的檔案要被移除
    r = client.post(
        f"{API}/users/me/avatar",
        headers=headers,
        files={"file": ("a.gif", b"GIF89a", "image/gif")},
    )
    assert r.status_code == 200, r.text
    assert [p.name for p in avatar_dir.glob(f"{user_id}.*")] == [f"{user_id}.gif"]

    r = client.delete(f"{API}/users/{user_id}", headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    assert list(avatar_dir.glob(f"{user_id}.*")) == []
    assert client.get(f"{API}/users/{user_id}/avatar").status_code == 404


def test_upload_rejects_unsupported_type_and_oversize(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    avatar_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = client.post(
        f"{API}/users/me/avatar",
        headers=normal_user_token_headers,
        files={"file": ("a.svg", b"<svg/>", "image/svg+xml")},
    )
    assert r.status_code == 400

    monkeypatch.setattr(avatar_service, "AVATAR_MAX_BYTES", 8)
    r = client.post(
        f"{API}/users/me/avatar",
        headers=normal_user_token_headers,
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert r.status_code == 400
    assert list(avatar_dir.iterdir()) == []


def test_unknown_user_avatar_is_404(client: TestClient, avatar_dir: Path) -> None:
    orphan_id = uuid.uuid4()
    (avatar_dir / f"{orphan_id}.png").write_bytes(_PNG)
    assert client.get(f"{API}/users/{orphan_id}/avatar").status_code == 404
