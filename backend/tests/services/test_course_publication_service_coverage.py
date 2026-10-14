"""Coverage tests for app.services.teaching.course_publication_service."""

import uuid
from types import SimpleNamespace

from app.services.teaching import course_publication_service as svc


def _publication(node_key, port, *, mode="domain", protocol="tcp"):
    return SimpleNamespace(
        node_key=node_key,
        port=port,
        protocol=protocol,
        mode=mode,
        enable_https=True,
    )


def test_apply_for_machines_covers_each_outcome(monkeypatch):
    publications = [
        _publication("absent", 80),  # node not built -> skipped
        _publication("unreadable", 80),  # listing published services fails
        _publication("web", 80),  # already published -> skipped
        _publication("web", 8080),  # domain publish succeeds
        _publication("api", 9000, mode="port_forward"),  # forward succeeds
        _publication("broken", 443),  # domain publish fails
    ]
    monkeypatch.setattr(svc, "list_for_version", lambda _s, *, version_id: publications)

    def list_published(vmid, _session):
        if vmid == 2:
            raise RuntimeError("pve down")
        if vmid == 3:
            return [SimpleNamespace(port=80, protocol="tcp")]
        return []

    monkeypatch.setattr(
        svc.firewall_service, "list_vm_published_services", list_published
    )
    monkeypatch.setattr(
        svc,
        "resolve_domain",
        lambda _s, *, publication, user, vmid, scope: f"vm{vmid}-{scope}.example.com",
    )
    published: list = []

    def publish(vmid, create, _session):
        if vmid == 5:
            raise RuntimeError("nginx failed")
        published.append((vmid, create.domain, create.port))

    monkeypatch.setattr(svc.firewall_service, "publish_vm_service", publish)
    forwards: list = []
    monkeypatch.setattr(
        svc,
        "publish_forward",
        lambda _s, *, vmid, publication: forwards.append((vmid, publication.port)),
    )

    errors = svc.apply_for_machines(
        object(),  # type: ignore[arg-type]
        version_id=uuid.uuid4(),
        vmid_by_key={"unreadable": 2, "web": 3, "api": 4, "broken": 5},
        owner=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        scope="cls",
    )
    assert errors == [
        "2: published services unreadable",
        "5: publishing port 443 failed",
    ]
    assert published == [(3, "vm3-cls.example.com", 8080)]
    assert forwards == [(4, 9000)]


def test_forward_endpoints_without_vmids():
    assert svc.forward_endpoints_by_vmid(object(), [None]) == {}  # type: ignore[arg-type,list-item]


def test_apply_for_machines_without_publications(monkeypatch):
    monkeypatch.setattr(svc, "list_for_version", lambda _s, *, version_id: [])
    assert (
        svc.apply_for_machines(
            object(),  # type: ignore[arg-type]
            version_id=uuid.uuid4(),
            vmid_by_key={"web": 1},
            owner=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        )
        == []
    )


def test_list_for_version_queries_session():
    class _Session:
        def exec(self, _stmt):
            return SimpleNamespace(all=lambda: ["row"])

    assert svc.list_for_version(_Session(), version_id=uuid.uuid4()) == ["row"]  # type: ignore[arg-type]


def test_public_urls_by_vmid_takes_first_url(monkeypatch):
    from app.services.resource import resource_service

    monkeypatch.setattr(
        resource_service,
        "public_urls_by_vmid",
        lambda _s, vmids: {1: ["https://a", "https://b"], 2: []},
    )
    assert svc.public_urls_by_vmid(object(), [1, 2]) == {1: "https://a"}  # type: ignore[arg-type]
