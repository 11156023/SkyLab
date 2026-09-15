"""Regression checks for the formal-class cutover."""

from app.main import app
from app.models import AuditAction, SQLModel
from tests.utils.routes import iter_api_routes, registered_paths


def test_retired_group_pair_routes_are_not_registered() -> None:
    paths = registered_paths(app.routes)

    assert not any(path.startswith("/api/v1/groups") for path in paths)
    assert not any(path.startswith("/api/v1/pair-sessions") for path in paths)
    assert not any(path.startswith("/api/v1/teaching/") for path in paths)


def test_ai_pve_is_registered_as_a_standalone_admin_tool() -> None:
    pve_routes = [
        (path, route)
        for path, route in iter_api_routes(app.routes)
        if path.startswith("/api/v1/ai/pve-log")
    ]
    paths = {path for path, _ in pve_routes}

    assert "/api/v1/ai/pve-log/chat" in paths
    assert "/api/v1/ai/pve-log/ssh/confirm" in paths
    assert all(
        "get_current_active_superuser"
        in {
            getattr(dependency.call, "__name__", "")
            for dependency in route.dependant.dependencies
        }
        for _, route in pve_routes
    )


def test_teacher_judge_routes_are_owned_by_formal_classes() -> None:
    paths = registered_paths(app.routes)

    assert "/api/v1/teaching-classes/{teaching_class_id}/judge/files/" in paths
    assert "/api/v1/teaching-classes/{teaching_class_id}/judge/scripts/" in paths


def test_teacher_judge_direct_rubric_mutations_are_retired() -> None:
    routes = [
        (path, set(route.methods or set()))
        for path, route in iter_api_routes(app.routes)
    ]
    file_root = "/api/v1/teaching-classes/{teaching_class_id}/judge/files/"
    file_item = "/api/v1/teaching-classes/{teaching_class_id}/judge/files/{file_id}"

    assert (file_root, {"GET"}) in routes
    assert not any(path == file_root and "POST" in methods for path, methods in routes)
    assert not any(path == f"{file_root}blank" for path, _ in routes)
    assert not any(path == file_item for path, _ in routes)
    assert not any(path in {"/api/v1/rubric/upload", "/api/v1/rubric/chat"} for path, _ in routes)
    assert any(path == "/api/v1/rubric/download-excel" for path, _ in routes)


def test_retired_group_audit_actions_stay_readable() -> None:
    """群組下線時 audit_logs 仍留有這些 action；enum 值刪掉會讓稽核頁整批讀不出來。"""
    for value in (
        "group_create",
        "group_delete",
        "group_member_add",
        "group_member_remove",
    ):
        assert AuditAction(value).value == value


def test_group_tables_and_foreign_keys_are_absent_from_current_metadata() -> None:
    assert "group" not in SQLModel.metadata.tables
    assert "group_member" not in SQLModel.metadata.tables
    assert "vm_template_group_links" not in SQLModel.metadata.tables

    group_foreign_keys = [
        foreign_key.target_fullname
        for table in SQLModel.metadata.sorted_tables
        for foreign_key in table.foreign_keys
        if foreign_key.target_fullname.startswith(("group.", "group_member."))
    ]
    assert group_foreign_keys == []
