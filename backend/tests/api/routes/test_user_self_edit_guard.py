"""管理員不可透過 PATCH /users/{id} 停用自己或變更自己的角色，也不可拿掉最後一位管理員。"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.exceptions import BadRequestError
from app.models import User, UserRole
from app.services.user import user_service
from tests.utils.user import create_random_user


def _me_id(client: TestClient, headers: dict[str, str]) -> str:
    r = client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    assert r.status_code == 200
    return str(r.json()["id"])


def test_superuser_cannot_deactivate_self(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    me = _me_id(client, superuser_token_headers)
    r = client.patch(
        f"{settings.API_V1_STR}/users/{me}",
        headers=superuser_token_headers,
        json={"is_active": False},
    )
    assert r.status_code == 403
    # 缺 locale key 時 t() 會直接回傳 key 本身
    assert r.json()["detail"] != "user.selfEditLocked"


def test_superuser_cannot_change_own_role(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    me = _me_id(client, superuser_token_headers)
    r = client.patch(
        f"{settings.API_V1_STR}/users/{me}",
        headers=superuser_token_headers,
        json={"role": "teacher"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] != "user.selfEditLocked"


def test_superuser_can_still_edit_own_full_name(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    me = _me_id(client, superuser_token_headers)
    current = client.get(
        f"{settings.API_V1_STR}/users/me", headers=superuser_token_headers
    ).json()
    r = client.patch(
        f"{settings.API_V1_STR}/users/{me}",
        headers=superuser_token_headers,
        # 帶上與目前相同的 role／is_active 不算變更，不應觸發保護
        json={
            "full_name": current.get("full_name") or "Admin",
            "role": current["role"],
            "is_active": True,
        },
    )
    assert r.status_code == 200


def test_superuser_can_deactivate_another_user(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    other = create_random_user(db)
    r = client.patch(
        f"{settings.API_V1_STR}/users/{other.id}",
        headers=superuser_token_headers,
        json={"is_active": False},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def _admin(active: bool = True) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="x",
        role=UserRole.admin,
        is_active=active,
    )


def _session_with_other_admins(count: int) -> MagicMock:
    session = MagicMock()
    session.exec.return_value.one.return_value = count
    return session


@pytest.mark.parametrize(
    ("deactivating", "role_changed"), [(True, False), (False, True)]
)
def test_last_active_admin_cannot_lose_admin_rights(
    deactivating: bool, role_changed: bool
) -> None:
    with pytest.raises(BadRequestError) as exc_info:
        user_service._ensure_not_removing_last_admin(
            session=_session_with_other_admins(0),
            db_user=_admin(),
            deactivating=deactivating,
            role_changed=role_changed,
        )
    assert exc_info.value.message != "user.lastAdminLocked"


def test_admin_can_be_demoted_when_another_admin_remains() -> None:
    user_service._ensure_not_removing_last_admin(
        session=_session_with_other_admins(1),
        db_user=_admin(),
        deactivating=True,
        role_changed=True,
    )


def test_last_admin_guard_skips_unrelated_changes() -> None:
    session = _session_with_other_admins(0)
    user_service._ensure_not_removing_last_admin(
        session=session, db_user=_admin(), deactivating=False, role_changed=False
    )
    user_service._ensure_not_removing_last_admin(
        session=session,
        db_user=_admin(active=False),
        deactivating=True,
        role_changed=True,
    )
    session.exec.assert_not_called()
