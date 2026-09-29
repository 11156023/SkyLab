"""Drafts accept incomplete input; publication still validates configuration."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.routes import course_environments as routes
from app.exceptions import BadRequestError
from app.models import CourseEnvironment, CourseEnvironmentVersion
from app.schemas.course_environment import EnvironmentDraftIn, EnvironmentNodeIn
from app.services.course_environment import environment_service


@pytest.fixture(autouse=True)
def _lxc_templates(monkeypatch):
    """自訂 LXC 來源現在會驗映像是否存在，這裡給一份固定的節點對照表。"""
    monkeypatch.setattr(
        environment_service.proxmox_service,
        "get_lxc_template_node_map",
        lambda: {"local:vztmpl/debian.tar.zst": {"pve"}},
    )


@pytest.fixture
def workspace(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    environment = CourseEnvironment(owner_id=user.id, name="previous")
    version = CourseEnvironmentVersion(environment_id=environment.id, version=1)
    session = Mock()
    monkeypatch.setattr(
        environment_service, "get_environment", lambda *args: environment
    )
    monkeypatch.setattr(environment_service, "latest_version", lambda *args: version)
    monkeypatch.setattr(
        environment_service, "serialize_version", lambda *args: {"id": environment.id}
    )
    return user, environment, version, session


def test_incomplete_draft_is_saved_without_touching_deployable_configuration(workspace):
    user, environment, version, session = workspace
    body = EnvironmentDraftIn(
        configuration={"name": "", "nodes": []}, editor={"name": "", "nodes": []}
    )
    routes.save_environment_draft(environment.id, body, session, user)
    assert (
        EnvironmentDraftIn.model_validate(version.draft_data).configuration
        == body.configuration
    )
    assert environment.name == "previous"
    session.commit.assert_called_once()


def test_incomplete_draft_cannot_publish_and_is_retained(workspace):
    user, environment, version, session = workspace
    version.draft_data = EnvironmentDraftIn(
        configuration={"name": "", "nodes": []}, editor={}
    ).model_dump(mode="json")
    original = version.draft_data
    with pytest.raises(BadRequestError):
        routes.publish_environment(environment.id, session, user)
    assert version.status == "draft"
    assert version.draft_data == original
    session.commit.assert_not_called()


def test_published_version_rejects_autosave(workspace):
    user, environment, version, session = workspace
    version.status = "published"
    with pytest.raises(BadRequestError):
        routes.save_environment_draft(
            environment.id,
            EnvironmentDraftIn(configuration={}, editor={}),
            session,
            user,
        )
    session.commit.assert_not_called()


def test_publish_materializes_latest_draft_and_clears_snapshot(workspace, monkeypatch):
    user, environment, version, session = workspace
    node = dict(
        node_key="web",
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name="web",
        role="server",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
    )
    version.draft_data = EnvironmentDraftIn(
        configuration={"name": "Latest", "nodes": [node]}, editor={}
    ).model_dump(mode="json")
    replace_nodes = Mock()
    monkeypatch.setattr(environment_service, "replace_nodes", replace_nodes)
    monkeypatch.setattr(environment_service, "is_admin", lambda _: False)
    monkeypatch.setattr(
        environment_service.quick_practice,
        "nodes_for_version",
        lambda *args, **kwargs: [EnvironmentNodeIn(**node)],
    )
    monkeypatch.setattr(environment_service, "list_edges", lambda *args: [])
    monkeypatch.setattr(
        environment_service.course_publication_service,
        "list_for_version",
        lambda *args, **kwargs: [],
    )
    routes.publish_environment(environment.id, session, user)
    assert environment.name == "Latest"
    assert version.status == "published"
    assert version.draft_data is None
    assert version.configuration_hash
    replace_nodes.assert_called_once()
    session.commit.assert_called_once()


def test_publish_records_the_peer_policy_from_the_draft(workspace, monkeypatch):
    """互通策略是規格的一部分：跟著版本走，也進 configuration_hash。"""
    user, environment, version, session = workspace
    node = dict(
        node_key="web",
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name="web",
        role="server",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
    )
    version.draft_data = EnvironmentDraftIn(
        configuration={"name": "Mesh", "nodes": [node], "peer_policy": "segment"},
        editor={},
    ).model_dump(mode="json")
    monkeypatch.setattr(environment_service, "replace_nodes", Mock())
    monkeypatch.setattr(environment_service, "is_admin", lambda _: False)
    monkeypatch.setattr(
        environment_service.quick_practice,
        "nodes_for_version",
        lambda *args, **kwargs: [EnvironmentNodeIn(**node)],
    )
    monkeypatch.setattr(environment_service, "list_edges", lambda *args: [])
    monkeypatch.setattr(
        environment_service.course_publication_service,
        "list_for_version",
        lambda *args, **kwargs: [],
    )

    routes.publish_environment(environment.id, session, user)

    assert version.peer_policy == "segment"


def test_a_draft_without_a_policy_publishes_as_explicit(workspace, monkeypatch):
    """舊草稿沒有這個欄位：預設隔離，寧可少開也不要把整段網路打通。"""
    user, environment, version, session = workspace
    node = dict(
        node_key="web",
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name="web",
        role="server",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
    )
    version.peer_policy = "segment"
    version.draft_data = EnvironmentDraftIn(
        configuration={"name": "Old", "nodes": [node]}, editor={}
    ).model_dump(mode="json")
    monkeypatch.setattr(environment_service, "replace_nodes", Mock())
    monkeypatch.setattr(environment_service, "is_admin", lambda _: False)
    monkeypatch.setattr(
        environment_service.quick_practice,
        "nodes_for_version",
        lambda *args, **kwargs: [EnvironmentNodeIn(**node)],
    )
    monkeypatch.setattr(environment_service, "list_edges", lambda *args: [])
    monkeypatch.setattr(
        environment_service.course_publication_service,
        "list_for_version",
        lambda *args, **kwargs: [],
    )

    routes.publish_environment(environment.id, session, user)

    assert version.peer_policy == "explicit"


def test_retry_creation_reuses_id_and_checks_access(workspace, monkeypatch):
    user, environment, version, session = workspace
    session.get.return_value = environment
    access = Mock(return_value=environment)
    monkeypatch.setattr(environment_service, "get_environment", access)
    body = EnvironmentDraftIn(
        configuration={}, editor={}, draft_id=environment.id
    )
    routes.create_environment_draft(body, session, user)
    access.assert_called_once_with(session, user, environment.id)
    assert all(
        not isinstance(call.args[0], CourseEnvironmentVersion)
        or call.args[0] is version
        for call in session.add.call_args_list
    )
