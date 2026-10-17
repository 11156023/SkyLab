"""platform_entry／gateway_config／system_setup／ldap_config 四個 singleton repository 的覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import AppError
from app.repositories import gateway_config as gateway_repo
from app.repositories import ldap_config as ldap_repo
from app.repositories import platform_entry as platform_repo
from app.repositories import system_setup as setup_repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ── platform_entry ─────────────────────────────────────────────────────────


def test_platform_entry_upsert_and_dns_record(session: Session) -> None:
    # 尚無設定列時不記錄 DNS
    platform_repo.set_platform_dns_record(session, zone_id="z", record_id="r")
    assert platform_repo.get_platform_entry_config(session) is None

    kwargs = {
        "enabled": True,
        "domain": "skylab.example.com",
        "upstream_host": "10.0.0.2",
        "upstream_port": 8082,
        "enable_https": True,
        "dns_proxied": False,
    }
    created = platform_repo.upsert_platform_entry_config(session, **kwargs)
    assert created.domain == "skylab.example.com"
    updated = platform_repo.upsert_platform_entry_config(
        session, **{**kwargs, "upstream_port": 9000, "dns_proxied": True}
    )
    assert updated.upstream_port == 9000 and updated.dns_proxied is True

    platform_repo.set_platform_dns_record(session, zone_id="zone1", record_id="rec1")
    stored = platform_repo.get_platform_entry_config(session)
    assert stored is not None
    assert (stored.dns_zone_id, stored.dns_record_id) == ("zone1", "rec1")


# ── gateway_config ─────────────────────────────────────────────────────────


def test_gateway_connection_settings_and_certificate_paths(session: Session) -> None:
    cert = gateway_repo.save_certificate_paths(
        session,
        ssl_certificate_path="/etc/ssl/a.crt",
        ssl_certificate_key_path="/etc/ssl/a.key",
    )
    assert cert.ssl_certificate_path == "/etc/ssl/a.crt"
    cert = gateway_repo.save_certificate_paths(
        session,
        ssl_certificate_path="/etc/ssl/b.crt",
        ssl_certificate_key_path="/etc/ssl/b.key",
    )
    assert cert.ssl_certificate_key_path == "/etc/ssl/b.key"

    conn = gateway_repo.upsert_connection_settings(session, "10.0.0.1", 2222, "admin")
    assert (conn.host, conn.ssh_port, conn.ssh_user) == ("10.0.0.1", 2222, "admin")


def test_gateway_private_key_decrypt_failure(session: Session) -> None:
    config = gateway_repo.save_keypair(session, "PEM", "pub")
    config.encrypted_private_key = "not-a-fernet-token"
    with pytest.raises(AppError) as exc:
        gateway_repo.get_decrypted_private_key(config)
    assert exc.value.status_code == 400


# ── system_setup ───────────────────────────────────────────────────────────


def test_system_setup_created_on_demand(session: Session) -> None:
    state = setup_repo.get_system_setup(session=session)
    assert state.completed is False
    admin_id = uuid.uuid4()
    assert (
        setup_repo.set_admin_user(session=session, user_id=admin_id).admin_user_id
        == admin_id
    )
    done = setup_repo.mark_completed(session=session)
    assert done.completed is True and done.completed_at is not None


# ── ldap_config ────────────────────────────────────────────────────────────


def test_ldap_update_skips_unknown_and_clears_group_dns(session: Session) -> None:
    ldap_repo.update_ldap_config(
        session=session,
        data={"teacher_group_dn": "cn=t", "admin_group_dn": "cn=a", "bind_dn": "x"},
    )
    config = ldap_repo.update_ldap_config(
        session=session,
        data={
            "no_such_field": 1,
            "teacher_group_dn": "",
            "admin_group_dn": None,
            "bind_dn": None,
            "server_uri": "ldaps://dc",
        },
    )
    assert config.teacher_group_dn is None
    assert config.admin_group_dn is None
    assert config.bind_dn == "x"
    assert config.server_uri == "ldaps://dc"
