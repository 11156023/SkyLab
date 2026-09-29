"""基本資訊發布後仍可調整；機器設定不行。"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.routes import course_environments as routes
from app.models import CourseEnvironment, CourseEnvironmentVersion
from app.schemas.course_environment import EnvironmentBasicsIn
from app.services.course_environment import environment_service


@pytest.fixture
def workspace(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), role="teacher", is_superuser=False)
    environment = CourseEnvironment(
        owner_id=user.id, name="lab", usage_scope="course"
    )
    version = CourseEnvironmentVersion(
        environment_id=environment.id, version=1, status="published"
    )
    session = Mock()
    monkeypatch.setattr(
        environment_service, "get_environment", lambda *args: environment
    )
    monkeypatch.setattr(environment_service, "latest_version", lambda *args: version)
    monkeypatch.setattr(
        environment_service, "serialize_version", lambda *args: {"id": environment.id}
    )
    return user, environment, version, session


def test_usage_scope_is_editable_after_publication(workspace):
    user, environment, version, session = workspace
    routes.update_environment_basics(
        environment.id,
        EnvironmentBasicsIn(name="lab", usage_scope="both"),
        session,
        user,
    )
    assert environment.usage_scope == "both"
    # 版本沒被動到：不可變保護的是機器設定，不是這組環境提供給誰。
    assert version.status == "published"
    assert version.version == 1
    session.commit.assert_called_once()


def test_environment_can_be_taken_out_of_the_student_list(workspace):
    user, environment, version, session = workspace
    environment.usage_scope = "both"
    routes.update_environment_basics(
        environment.id,
        EnvironmentBasicsIn(name="lab", usage_scope="course"),
        session,
        user,
    )
    assert environment.usage_scope == "course"
    session.commit.assert_called_once()


def test_draft_snapshot_follows_so_publishing_does_not_revert_the_change(workspace):
    user, environment, version, session = workspace
    version.status = "draft"
    version.draft_data = {
        "configuration": {"usage_scope": "course"},
        "editor": {"usageScope": "course"},
    }
    original = version.draft_data
    routes.update_environment_basics(
        environment.id,
        EnvironmentBasicsIn(name="lab", usage_scope="quick_practice"),
        session,
        user,
    )
    draft = version.draft_data
    # 必須是新物件：就地修改同一個 dict，ORM 不會把 JSON 欄位寫回資料庫
    assert draft is not original
    assert draft["configuration"]["usage_scope"] == "quick_practice"
    assert draft["editor"]["usageScope"] == "quick_practice"


def test_retire_is_gone_in_favour_of_the_usage_scope_switch():
    assert not hasattr(routes, "retire_environment")


def test_name_and_description_are_editable_after_publication(workspace):
    user, environment, version, session = workspace
    routes.update_environment_basics(
        environment.id,
        EnvironmentBasicsIn(
            name="  n8n 練習  ", description="給課後練習用", usage_scope="both"
        ),
        session,
        user,
    )
    assert environment.name == "n8n 練習"
    assert environment.description == "給課後練習用"
    assert version.status == "published"
