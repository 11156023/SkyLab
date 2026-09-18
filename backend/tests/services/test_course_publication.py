"""課程環境的「外網 → 機器」宣告：每位學生各配一個網址。

網域是全域唯一的資源，模板上不可能填一個全班共用的網址，所以老師只宣告
主機名樣板，實際網域在開課／開練習時逐人組出來。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.routes.course_environments import EnvironmentPublicationIn
from app.models import CourseEnvironmentPublication
from app.schemas.firewall import PublishedServiceCreate
from app.services.teaching import course_publication_service as cps


def _user(email: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID("11111111-2222-3333-4444-555555555555"), email=email)


def _publication(**overrides) -> CourseEnvironmentPublication:
    values = {
        "version_id": uuid.uuid4(),
        "node_key": "n8n",
        "mode": "domain",
        "port": 5678,
        "protocol": "tcp",
        "hostname_prefix": "{class}-{student}-n8n",
        "zone_id": "zone-1",
        "enable_https": True,
    }
    values.update(overrides)
    return CourseEnvironmentPublication(**values)


# ── 學生識別片段 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("email", ["alice@school.edu", "student123@school.edu"])
def test_student_token_is_pseudonymous_and_dns_safe(email: str) -> None:
    token = cps.student_token(_user(email))
    assert token == "s6e76689e43"
    assert "alice" not in token and "student123" not in token


def test_student_token_handles_non_ascii_email_without_exposing_it() -> None:
    """帳號清完是空的（例如全形字元）時仍要產生合法的主機名片段。"""
    token = cps.student_token(_user("測試@school.edu"))
    assert token.startswith("s") and token[1:].isalnum()


def test_student_token_is_different_between_classes() -> None:
    user = _user("alice@school.edu")

    assert cps.student_token(user, scope="linux-a") != cps.student_token(
        user, scope="linux-b"
    )


# ── 網域組合 ────────────────────────────────────────────────────────────


def _patch_zone_and_availability(monkeypatch, *, available: bool):
    monkeypatch.setattr(
        cps.cloudflare_service,
        "get_zone",
        lambda **_kwargs: SimpleNamespace(name="lab.example.edu"),
    )
    monkeypatch.setattr(
        cps.reverse_proxy_service,
        "check_domain_availability",
        lambda *_args, **_kwargs: SimpleNamespace(available=available, reason="system"),
    )


def test_each_student_gets_their_own_domain(monkeypatch) -> None:
    _patch_zone_and_availability(monkeypatch, available=True)

    domain = cps.resolve_domain(
        Mock(), publication=_publication(), user=_user("alice@school.edu"), vmid=101,
        scope="LINUX-101 A",
    )

    assert domain == "linux-101-a-sbe1c6d9a8a-n8n.lab.example.edu"


def test_collision_falls_back_to_a_suffixed_hostname(monkeypatch) -> None:
    """兩個帳號清完可能撞在一起（alice.wang 與 alice_wang），補短碼區分。"""
    _patch_zone_and_availability(monkeypatch, available=False)

    domain = cps.resolve_domain(
        Mock(), publication=_publication(), user=_user("alice@school.edu"), vmid=101,
        scope="linux-101-a",
    )

    assert domain == "linux-101-a-sbe1c6d9a8a-1111-n8n.lab.example.edu"


# ── 模板驗證 ────────────────────────────────────────────────────────────


def _publication_in(**overrides) -> dict:
    values = {
        "node_key": "n8n",
        "mode": "domain",
        "port": 5678,
        "protocol": "tcp",
        "hostname_prefix": "{student}-n8n",
        "zone_id": "zone-1",
    }
    values.update(overrides)
    return values


def test_domain_mode_requires_the_student_placeholder() -> None:
    """少了 {student}，全班會搶同一個網址，只有第一位學生拿得到。"""
    with pytest.raises(ValueError):
        EnvironmentPublicationIn(**_publication_in(hostname_prefix="n8n"))


def test_domain_mode_requires_a_zone() -> None:
    with pytest.raises(ValueError):
        EnvironmentPublicationIn(**_publication_in(zone_id=None))


def test_domain_mode_rejects_udp() -> None:
    with pytest.raises(ValueError):
        EnvironmentPublicationIn(**_publication_in(protocol="udp"))


def test_port_forward_mode_drops_domain_fields() -> None:
    publication = EnvironmentPublicationIn(
        **_publication_in(mode="port_forward", protocol="udp")
    )

    assert publication.hostname_prefix is None
    assert publication.zone_id is None


# ── 對外 port：逐位學生配號 ───────────────────────────────────────────────


@pytest.fixture
def forward_stack(monkeypatch):
    """把配號與發布都換成可觀察的假件；allocate 依 exclude 往上挑。"""
    published: list[PublishedServiceCreate] = []
    allocations: list[frozenset[int]] = []

    def allocate(_session, _protocol, *, exclude=frozenset()):
        allocations.append(exclude)
        return next(p for p in range(30000, 30010) if p not in exclude)

    monkeypatch.setattr(cps.nat_service, "allocate_external_port", allocate)
    monkeypatch.setattr(
        cps.firewall_service, "list_vm_published_services", lambda *_a, **_k: []
    )
    return published, allocations


def test_port_forward_publication_allocates_an_external_port(forward_stack, monkeypatch):
    published, _allocations = forward_stack
    monkeypatch.setattr(
        cps.firewall_service,
        "publish_vm_service",
        lambda _vmid, create, _session: published.append(create),
    )

    external = cps.publish_forward(
        Mock(), vmid=101, publication=_publication(mode="port_forward", port=22)
    )

    assert external == 30000
    assert [(c.mode, c.port, c.external_port) for c in published] == [
        ("port_forward", 22, 30000)
    ]


def test_a_contended_port_is_skipped_and_the_next_one_used(forward_stack, monkeypatch):
    """兩個班同時開課挑到同一個 port：唯一約束擋下第二個，換號再試。"""
    from app.exceptions import BadRequestError

    published, allocations = forward_stack
    attempts = {"n": 0}

    def publish(_vmid, create, _session):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise BadRequestError("port taken")
        published.append(create)

    monkeypatch.setattr(cps.firewall_service, "publish_vm_service", publish)

    external = cps.publish_forward(
        Mock(), vmid=101, publication=_publication(mode="port_forward", port=22)
    )

    assert external == 30001
    assert allocations == [frozenset(), frozenset({30000})]


def test_publishing_gives_up_after_repeated_contention(forward_stack, monkeypatch):
    from app.exceptions import BadRequestError

    def always_taken(_vmid, _create, _session):
        raise BadRequestError("port taken")

    monkeypatch.setattr(cps.firewall_service, "publish_vm_service", always_taken)

    with pytest.raises(BadRequestError):
        cps.publish_forward(
            Mock(), vmid=101, publication=_publication(mode="port_forward", port=22)
        )


def test_apply_routes_port_forward_declarations_through_the_allocator(
    forward_stack, monkeypatch
):
    published, _allocations = forward_stack
    monkeypatch.setattr(
        cps.firewall_service,
        "publish_vm_service",
        lambda _vmid, create, _session: published.append(create),
    )
    monkeypatch.setattr(
        cps,
        "list_for_version",
        lambda _session, *, version_id: [
            _publication(version_id=version_id, mode="port_forward", node_key="ssh", port=22)
        ],
    )

    errors = cps.apply_for_machines(
        Mock(),
        version_id=uuid.uuid4(),
        vmid_by_key={"ssh": 101},
        owner=_user("alice@school.edu"),
    )

    assert errors == []
    assert [(c.mode, c.external_port) for c in published] == [("port_forward", 30000)]


# ── 學生端：對外入口 ──────────────────────────────────────────────────────


def test_forward_endpoints_carry_the_entry_host_when_configured(monkeypatch):
    from app.repositories import nat_rule as nat_repo

    monkeypatch.setattr(
        cps.ip_management_service,
        "get_subnet_config",
        lambda _session: SimpleNamespace(forward_public_host="gw.example.edu"),
    )
    monkeypatch.setattr(
        nat_repo,
        "list_rules_by_vmids",
        lambda _session, _vmids: [
            SimpleNamespace(vmid=101, external_port=30001, internal_port=22, protocol="tcp"),
            SimpleNamespace(vmid=101, external_port=30000, internal_port=3306, protocol="tcp"),
        ],
    )

    endpoints = cps.forward_endpoints_by_vmid(Mock(), [101, None])

    assert endpoints == {
        101: [
            {"host": "gw.example.edu", "external_port": 30001, "internal_port": 22, "protocol": "tcp"},
            {"host": "gw.example.edu", "external_port": 30000, "internal_port": 3306, "protocol": "tcp"},
        ]
    }


def test_forward_endpoints_have_no_host_until_the_admin_sets_one(monkeypatch):
    from app.repositories import nat_rule as nat_repo

    monkeypatch.setattr(cps.ip_management_service, "get_subnet_config", lambda _s: None)
    monkeypatch.setattr(
        nat_repo,
        "list_rules_by_vmids",
        lambda _session, _vmids: [
            SimpleNamespace(vmid=101, external_port=30001, internal_port=22, protocol="tcp")
        ],
    )

    assert cps.forward_endpoints_by_vmid(Mock(), [101])[101][0]["host"] is None
