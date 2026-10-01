"""首次安裝初始化精靈 API（/setup/*）的整合測試。

精靈狀態是 singleton：fixture 先把它重設為「尚未完成」，測完恢復為已完成，
並把可能被精靈停用的 FIRST_SUPERUSER 重新啟用，避免影響其他模組的登入。
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    GatewayConfig,
    PlatformEntryConfig,
    ProxmoxConnection,
    SubnetConfig,
    SystemSetup,
    User,
    UserRole,
)
from app.repositories import system_setup as system_setup_repo
from app.repositories.user import create_user
from app.schemas import UserCreate
from app.schemas.gateway import GatewayInstallOptions, GatewayInstallStatus
from app.services.network import ip_management_service
from tests.utils.utils import random_email, random_lower_string

API = f"{settings.API_V1_STR}/setup"
ADMIN_PASSWORD = "Setup-Wizard-Pass-123"


def _status(client: TestClient) -> dict:
    r = client.get(f"{API}/status")
    assert r.status_code == 200, r.text
    return r.json()


def _reactivate_default_admin(db: Session) -> None:
    db.expire_all()
    default = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if default is not None and not default.is_active:
        default.is_active = True
        db.add(default)
        db.commit()


def _delete_wizard_admin(db: Session, email: str) -> None:
    """精靈建的是超級使用者，conftest 的清理不會刪，測試自己收拾。"""
    db.expire_all()
    state = system_setup_repo.get_system_setup(session=db)
    state.admin_user_id = None
    db.add(state)
    db.commit()
    user = db.exec(select(User).where(User.email == email)).first()
    if user is not None:
        db.delete(user)
        db.commit()


@pytest.fixture
def open_setup(db: Session) -> Generator[SystemSetup, None, None]:
    state = system_setup_repo.get_system_setup(session=db)
    state.completed = False
    state.completed_at = None
    state.admin_user_id = None
    db.add(state)
    db.commit()
    db.refresh(state)
    yield state
    db.expire_all()
    state = system_setup_repo.get_system_setup(session=db)
    state.completed = True
    state.admin_user_id = None
    db.add(state)
    db.commit()
    _reactivate_default_admin(db)


@pytest.fixture
def keep_default_admin_password(db: Session) -> Generator[None, None, None]:
    """接管測試會改掉 .env 預設管理員的密碼；測完還原雜湊，其他測試才能照常用它登入。

    .env 的密碼不一定符合複雜度規則，無法直接拿來當接管時的新密碼。
    """
    default = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    original_hash = default.hashed_password
    yield
    db.expire_all()
    default = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    default.hashed_password = original_hash
    db.add(default)
    db.commit()


def test_status_is_public_and_reports_steps(
    client: TestClient, open_setup: SystemSetup
) -> None:
    body = _status(client)
    assert body["completed"] is False
    assert set(body["steps"]) == {
        "admin",
        "proxmox",
        "subnet",
        "gateway",
        "platform_entry",
    }
    assert body["steps"]["admin"] is False


def test_admin_creates_new_superuser(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    email = random_email()
    r = client.post(
        f"{API}/admin",
        json={
            "email": email,
            "full_name": "Setup Admin",
            "password": ADMIN_PASSWORD,
            "disable_default_admin": False,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "email": email,
        "full_name": "Setup Admin",
        "created": True,
        "default_admin_disabled": False,
    }

    db.expire_all()
    user = db.exec(select(User).where(User.email == email)).one()
    assert user.is_superuser is True
    assert user.is_active is True
    assert user.role == UserRole.admin
    # 初始化精靈建的管理員第一次登入也要走首次登入引導
    assert user.onboarding_completed is False
    assert system_setup_repo.get_system_setup(session=db).admin_user_id == user.id
    assert _status(client)["steps"]["admin"] is True

    # 新管理員立刻可以用一般登入
    login = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": email, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text

    _delete_wizard_admin(db, email)


def test_admin_disables_env_default_admin(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    email = random_email()
    r = client.post(
        f"{API}/admin",
        json={"email": email, "password": ADMIN_PASSWORD, "disable_default_admin": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["default_admin_disabled"] is True

    db.expire_all()
    default = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    assert default.is_active is False

    _reactivate_default_admin(db)
    _delete_wizard_admin(db, email)


def test_admin_rejects_regular_user_email(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    user = create_user(
        session=db,
        user_create=UserCreate(email=random_email(), password=random_lower_string()),
    )
    db.commit()
    r = client.post(
        f"{API}/admin", json={"email": user.email, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 409, r.text


def test_admin_rejects_weak_password(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    """精靈建立／接管的管理員密碼也要符合複雜度；.env 的弱密碼不能原樣沿用。"""
    email = random_email()
    r = client.post(
        f"{API}/admin", json={"email": email, "password": "setup-wizard-pass-123"}
    )
    assert r.status_code == 400, r.text
    assert "特殊符號" in r.json()["detail"]
    assert db.exec(select(User).where(User.email == email)).first() is None
    assert _status(client)["steps"]["admin"] is False


def test_admin_takes_over_existing_superuser(
    client: TestClient,
    db: Session,
    open_setup: SystemSetup,
    keep_default_admin_password: None,
) -> None:
    r = client.post(
        f"{API}/admin",
        json={
            "email": settings.FIRST_SUPERUSER,
            "password": ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is False
    assert body["default_admin_disabled"] is False
    db.expire_all()
    default = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    assert default.is_active is True
    assert system_setup_repo.get_system_setup(session=db).admin_user_id == default.id


def test_proxmox_test_reports_failure_without_saving(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    before = len(db.exec(select(ProxmoxConnection)).all())
    with patch(
        "app.services.system.setup_service.fetch_cluster_nodes",
        side_effect=RuntimeError("boom"),
    ):
        r = client.post(
            f"{API}/proxmox/test",
            json={"host": "pve.invalid", "user": "root@pam", "password": "x"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False
    assert body["error"]
    assert body["nodes"] == []
    db.expire_all()
    assert len(db.exec(select(ProxmoxConnection)).all()) == before


def test_proxmox_test_lists_nodes_and_merges_shared_storage(
    client: TestClient, open_setup: SystemSetup
) -> None:
    fake_nodes = [
        {"name": "pve1", "host": "10.0.0.1", "port": 8006, "is_primary": True},
        {"name": "pve2", "host": "10.0.0.2", "port": 8006, "is_primary": False},
    ]
    api = MagicMock()
    api.nodes.return_value.storage.get.return_value = [
        {"storage": "local", "type": "dir", "content": "iso,vztmpl", "shared": 0,
         "avail": 10 * 1024**3},
        {"storage": "nfs-shared", "type": "nfs", "content": "images,rootdir",
         "shared": 1, "avail": 500 * 1024**3},
        {"storage": "disabled", "type": "dir", "content": "images", "enabled": 0},
    ]
    with (
        patch(
            "app.services.system.setup_service.fetch_cluster_nodes",
            return_value=fake_nodes,
        ),
        patch(
            "app.services.system.setup_service.open_client", return_value=api
        ),
    ):
        r = client.post(
            f"{API}/proxmox/test",
            json={"host": "pve.invalid", "user": "root@pam", "password": "x"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["is_cluster"] is True
    assert [n["name"] for n in body["nodes"]] == ["pve1", "pve2"]

    by_name = {(s["storage"], tuple(s["nodes"])): s for s in body["storages"]}
    # 節點本機的 local 每個節點一筆；共享 nfs 合併成一筆並列出兩個節點；停用的不出現
    assert ("local", ("pve1",)) in by_name
    assert ("local", ("pve2",)) in by_name
    shared = by_name[("nfs-shared", ("pve1", "pve2"))]
    assert shared["can_vm"] is True
    assert shared["can_lxc"] is True
    assert shared["can_iso"] is False
    assert by_name[("local", ("pve1",))]["can_iso"] is True
    assert all(s["storage"] != "disabled" for s in body["storages"])


def test_proxmox_creates_default_connection_and_syncs(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    name = f"setup-test-{random_lower_string()[:8]}"
    with patch(
        "app.services.proxmox.connection_sync_service.sync_connection_inventory",
        return_value=([], 0),
    ) as sync:
        r = client.post(
            f"{API}/proxmox",
            json={"name": name, "host": "pve.invalid", "user": "root@pam",
                  "password": "secret", "pool_name": "SkyLab"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == name
    assert body["sync_error"] is None
    sync.assert_called_once()

    db.expire_all()
    conn = db.exec(
        select(ProxmoxConnection).where(ProxmoxConnection.name == name)
    ).one()
    assert conn.enabled is True
    assert conn.pool_name == "SkyLab"
    assert _status(client)["steps"]["proxmox"] is True

    db.delete(conn)
    db.commit()


def test_proxmox_keeps_connection_when_sync_fails(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    name = f"setup-test-{random_lower_string()[:8]}"
    with patch(
        "app.services.proxmox.connection_sync_service.sync_connection_inventory",
        side_effect=ValueError("node name clash"),
    ):
        r = client.post(
            f"{API}/proxmox",
            json={"name": name, "host": "pve.invalid", "user": "root@pam",
                  "password": "secret"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["sync_error"] == "node name clash"
    db.expire_all()
    conn = db.exec(
        select(ProxmoxConnection).where(ProxmoxConnection.name == name)
    ).one()
    db.delete(conn)
    db.commit()


def test_subnet_configures_lab_network(
    client: TestClient, db: Session, open_setup: SystemSetup
) -> None:
    if db.get(SubnetConfig, 1) is not None:
        pytest.skip("test database already has a subnet config")
    r = client.post(
        f"{API}/subnet",
        json={
            "cidr": "10.77.0.0/24",
            "gateway": "10.77.0.1",
            "bridge_name": "vmbr1",
            "gateway_vm_ip": "10.77.0.2",
            "dns_servers": "8.8.8.8",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cidr"] == "10.77.0.0/24"
    assert body["bridge_name"] == "vmbr1"
    assert body["total_ips"] > 0
    assert _status(client)["steps"]["subnet"] is True

    # 閘道不在網段內要被同一套驗證擋下
    bad = client.post(
        f"{API}/subnet",
        json={"cidr": "10.77.0.0/24", "gateway": "10.78.0.1",
              "bridge_name": "vmbr1", "gateway_vm_ip": "10.77.0.2"},
    )
    assert bad.status_code == 400, bad.text

    db.expire_all()
    ip_management_service.delete_subnet_config(db)


@pytest.fixture
def clean_gateway(db: Session) -> Generator[None, None, None]:
    """Gateway 與平台入口都是 singleton：測試庫原本就有設定時不動它，直接略過。"""
    if db.get(GatewayConfig, 1) is not None or db.get(PlatformEntryConfig, 1) is not None:
        pytest.skip("test database already has a gateway / platform entry config")
    yield
    db.expire_all()
    for model in (PlatformEntryConfig, GatewayConfig):
        row = db.get(model, 1)
        if row is not None:
            db.delete(row)
    db.commit()


def test_gateway_step_saves_settings_and_generates_keypair_once(
    client: TestClient, db: Session, open_setup: SystemSetup, clean_gateway: None
) -> None:
    empty = client.get(f"{API}/gateway")
    assert empty.status_code == 200, empty.text
    assert empty.json()["is_configured"] is False
    assert _status(client)["steps"]["gateway"] is False

    r = client.put(f"{API}/gateway", json={"host": "10.77.0.2", "ssh_port": 2222})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host"] == "10.77.0.2"
    assert body["ssh_port"] == 2222
    assert body["ssh_user"] == "root"
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["is_configured"] is True
    # 私鑰不出後端
    assert "private" not in r.text.lower()
    assert _status(client)["steps"]["gateway"] is True

    # 再存一次（改 port）不能重產金鑰：管理員已經把公鑰貼到 Gateway 上了
    again = client.put(f"{API}/gateway", json={"host": "10.77.0.2", "ssh_port": 22})
    assert again.status_code == 200, again.text
    assert again.json()["public_key"] == body["public_key"]
    assert again.json()["ssh_port"] == 22


def test_gateway_test_reports_not_configured(
    client: TestClient, open_setup: SystemSetup, clean_gateway: None
) -> None:
    r = client.post(f"{API}/gateway/test")
    assert r.status_code == 200, r.text
    assert r.json()["success"] is False


def test_gateway_install_delegates_to_install_service(
    client: TestClient, open_setup: SystemSetup, clean_gateway: None
) -> None:
    options = GatewayInstallOptions()
    status = GatewayInstallStatus(
        state="running",
        root_access=True,
        defaults=options,
        wireguard_interface="wg0",
        wireguard_client_subnet="10.250.0.0/16",
        wireguard_vm_subnet="10.10.0.0/16",
    )
    with (
        patch(
            "app.services.network.gateway_install_service.get_install_status",
            return_value=status,
        ),
        patch(
            "app.services.network.gateway_install_service.start_install",
            return_value=status,
        ) as start,
    ):
        got = client.get(f"{API}/gateway/install")
        started = client.post(
            f"{API}/gateway/install", json=options.model_dump(mode="json")
        )
    assert got.status_code == 200, got.text
    assert started.status_code == 202, started.text
    assert started.json()["state"] == "running"
    start.assert_called_once()


def test_platform_entry_step_saves_and_reports_step(
    client: TestClient, db: Session, open_setup: SystemSetup, clean_gateway: None
) -> None:
    client.put(f"{API}/gateway", json={"host": "10.77.0.2"})

    @contextmanager
    def fake_client(_session: object) -> Iterator[object]:
        yield object()

    with (
        patch(
            "app.services.network.platform_entry_service._gateway_client", fake_client
        ),
        patch(
            "app.services.network.nginx_gateway_service._exec",
            return_value=(0, "", ""),
        ),
        patch(
            "app.services.network.reverse_proxy_service.sync_to_gateway"
        ) as sync,
    ):
        tested = client.post(
            f"{API}/platform-entry/test-upstream",
            json={"upstream_host": "10.77.0.20", "upstream_port": 8082},
        )
        saved = client.put(
            f"{API}/platform-entry",
            json={
                "enabled": True,
                "domain": "SkyLab.Example.com",
                "upstream_host": "10.77.0.20",
                "upstream_port": 8082,
                "enable_https": False,
            },
        )
    assert tested.status_code == 200, tested.text
    assert tested.json()["reachable"] is True
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["enabled"] is True
    assert body["domain"] == "skylab.example.com"
    assert body["gateway_ready"] is True
    assert body["gateway_host"] == "10.77.0.2"
    sync.assert_called_once()

    db.expire_all()
    row = db.get(PlatformEntryConfig, 1)
    assert row is not None
    assert row.upstream_host == "10.77.0.20"
    assert _status(client)["steps"]["platform_entry"] is True
    assert client.get(f"{API}/platform-entry").json()["domain"] == "skylab.example.com"


def test_platform_entry_step_rejects_bad_upstream(
    client: TestClient, open_setup: SystemSetup, clean_gateway: None
) -> None:
    r = client.put(
        f"{API}/platform-entry",
        json={
            "enabled": True,
            "domain": "skylab.example.com",
            "upstream_host": "10.0.0.1; reboot",
            "enable_https": False,
        },
    )
    assert r.status_code == 400, r.text


def test_complete_requires_admin_then_locks_wizard(
    client: TestClient,
    db: Session,
    open_setup: SystemSetup,
    keep_default_admin_password: None,
) -> None:
    r = client.post(f"{API}/complete")
    assert r.status_code == 400, r.text

    r = client.post(
        f"{API}/admin",
        json={
            "email": settings.FIRST_SUPERUSER,
            "password": ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text

    r = client.post(f"{API}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["completed"] is True
    assert r.json()["completed_at"]
    assert _status(client)["completed"] is True

    # 完成後所有寫入端點關閉
    assert client.post(
        f"{API}/admin", json={"email": random_email(), "password": ADMIN_PASSWORD}
    ).status_code == 403
    assert client.post(
        f"{API}/proxmox/test",
        json={"host": "pve.invalid", "user": "root@pam", "password": "x"},
    ).status_code == 403
    assert client.post(f"{API}/complete").status_code == 403
    # Gateway 與平台入口的精靈端點連讀取都關閉（裡面有連線設定與公鑰）
    assert client.get(f"{API}/gateway").status_code == 403
    assert client.put(f"{API}/gateway", json={"host": "10.77.0.2"}).status_code == 403
    assert client.post(f"{API}/gateway/test").status_code == 403
    assert client.get(f"{API}/gateway/install").status_code == 403
    assert client.post(
        f"{API}/gateway/install", json=GatewayInstallOptions().model_dump(mode="json")
    ).status_code == 403
    assert client.get(f"{API}/platform-entry").status_code == 403
    assert client.post(
        f"{API}/platform-entry/test-upstream", json={"upstream_host": "10.77.0.20"}
    ).status_code == 403
    assert client.put(
        f"{API}/platform-entry", json={"enabled": False}
    ).status_code == 403
