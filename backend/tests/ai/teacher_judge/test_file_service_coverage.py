"""Branch coverage for app.ai.teacher_judge.file_service."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import file_service
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.models.teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from tests.ai.teacher_judge.helpers import make_session


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(file_service, "DATA_ROOT", root)
    return root


def _file(
    db: Session,
    class_id: uuid.UUID,
    *,
    filename: str | None = "rubric.pdf",
    **kwargs: Any,
) -> TeacherJudgeFile:
    item = TeacherJudgeFile(
        teaching_class_id=class_id,
        original_filename=filename,
        file_hash="a" * 64 if filename else None,
        template_key="linux",
        analysis_json={"items": []},
        **kwargs,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _http_status(exc: pytest.ExceptionInfo[HTTPException]) -> int:
    return exc.value.status_code


def test_unlink_if_exists_removes_files_and_logs_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "x.txt"
    target.write_text("x")
    file_service._unlink_if_exists(target)
    assert not target.exists()

    directory = tmp_path / "dir"
    directory.mkdir()
    with caplog.at_level("WARNING", logger=file_service.logger.name):
        file_service._unlink_if_exists(directory)
    assert "Failed to remove Teacher Judge file path" in caplog.text


def test_copy_filename_exhausted() -> None:
    names = ["rubric.pdf", *(f"rubric ({index}).pdf" for index in range(2, 1000))]
    fake = SimpleNamespace(exec=lambda statement: SimpleNamespace(all=lambda: names))
    with pytest.raises(HTTPException) as exc:
        file_service._copy_filename(
            session=fake,  # type: ignore[arg-type]
            teaching_class_id=uuid.uuid4(),
            original_filename="rubric.pdf",
        )
    assert _http_status(exc) == 409


def test_list_get_and_download_errors(data_root: Path) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    uploaded = _file(db, class_id)
    created = _file(db, class_id, filename=None, source_type="created")

    listed = file_service.list_files(session=db, teaching_class_id=class_id)
    assert {row.id for row in listed} == {str(uploaded.id), str(created.id)}

    with pytest.raises(HTTPException) as missing:
        file_service.get_file(
            session=db, teaching_class_id=uuid.uuid4(), file_id=uploaded.id
        )
    assert _http_status(missing) == 404

    with pytest.raises(HTTPException) as no_document:
        file_service.get_file_download(
            session=db, teaching_class_id=class_id, file_id=created.id
        )
    assert _http_status(no_document) == 409

    with pytest.raises(HTTPException) as no_bytes:
        file_service.get_file_download(
            session=db, teaching_class_id=class_id, file_id=uploaded.id
        )
    assert _http_status(no_bytes) == 404

    assert file_service.source_file_snapshot(
        session=db, teaching_class_id=class_id, file_id=None
    ) == (None, {})


def _analysis(*items: dict[str, Any]) -> TeacherJudgeRubricAnalysis:
    return TeacherJudgeRubricAnalysis.model_validate({"items": list(items)})


def test_update_file_analysis_rejects_replaced_and_stale_revision() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    replaced = _file(
        db, class_id, filename="old.pdf", status=TeacherJudgeFileStatus.replaced
    )
    with pytest.raises(HTTPException) as exc:
        file_service.update_file_analysis(
            session=db,
            teaching_class_id=class_id,
            file_id=replaced.id,
            analysis=_analysis(),
        )
    assert _http_status(exc) == 409

    active = _file(db, class_id)
    with pytest.raises(HTTPException) as stale:
        file_service.update_file_analysis(
            session=db,
            teaching_class_id=class_id,
            file_id=active.id,
            analysis=_analysis(),
            expected_revision=5,
        )
    assert stale.value.detail["code"] == "teacher_judge_analysis_revision_conflict"


def test_update_file_analysis_machine_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    active = _file(db, class_id)
    monkeypatch.setattr(
        file_service,
        "load_class_machine_nodes",
        lambda session, teaching_class_id: [SimpleNamespace(node_key="web")],
    )

    def update(*items: dict[str, Any]) -> HTTPException:
        with pytest.raises(HTTPException) as exc:
            file_service.update_file_analysis(
                session=db,
                teaching_class_id=class_id,
                file_id=active.id,
                analysis=_analysis(*items),
            )
        return exc.value

    unknown_node = update(
        {"id": "a", "title": "A", "detectable": "auto", "target_node_key": "ghost"}
    )
    assert unknown_node.detail["code"] == "teacher_judge_target_node_not_in_class"
    assert unknown_node.detail["target_node_keys"] == ["ghost"]

    missing_target = update({"id": "b", "title": "B", "detectable": "auto"})
    assert missing_target.detail["code"] == "teacher_judge_target_node_required"
    assert missing_target.detail["item_ids"] == ["b"]

    monkeypatch.setattr(
        file_service, "rubric_item_machine_issues", lambda item: ["bad peer"]
    )
    contract = update({"title": "Untitled", "detectable": "manual", "id": ""})
    assert contract.detail["code"] == "teacher_judge_machine_contract_invalid"
    assert contract.detail["items"] == {"Untitled": ["bad peer"]}


def test_update_file_analysis_lost_race_to_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = iter(
        [
            SimpleNamespace(
                status=TeacherJudgeFileStatus.active,
                analysis_revision=1,
                analysis_json={"items": []},
            ),
            SimpleNamespace(
                status=TeacherJudgeFileStatus.replaced,
                analysis_revision=2,
                analysis_json={"items": []},
            ),
        ]
    )
    monkeypatch.setattr(file_service, "get_file", lambda **kwargs: next(files))
    monkeypatch.setattr(
        file_service, "load_class_machine_nodes", lambda session, class_id: []
    )
    monkeypatch.setattr(file_service, "analysis_write_issues", lambda *args: [])
    rolled_back: list[bool] = []
    fake_session = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(rowcount=0),
        rollback=lambda: rolled_back.append(True),
    )
    with pytest.raises(HTTPException) as exc:
        file_service.update_file_analysis(
            session=fake_session,  # type: ignore[arg-type]
            teaching_class_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            analysis=_analysis(),
        )
    assert _http_status(exc) == 409
    assert rolled_back == [True]


def test_create_blank_file_validation() -> None:
    db = make_session()
    with pytest.raises(HTTPException) as blank:
        file_service.create_blank_file(
            session=db,
            teaching_class_id=uuid.uuid4(),
            created_by=None,
            display_name="   ",
            environment_keys=[],
        )
    assert _http_status(blank) == 422
    with pytest.raises(HTTPException) as unsupported:
        file_service.create_blank_file(
            session=db,
            teaching_class_id=uuid.uuid4(),
            created_by=None,
            display_name="Rubric",
            environment_keys=["windows"],
        )
    assert _http_status(unsupported) == 422


def test_clone_file_asset_rejects_invalid_sources(data_root: Path) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    source = _file(db, class_id)

    with pytest.raises(HTTPException) as other_class:
        file_service.clone_file_asset(
            session=db, source=source, teaching_class_id=uuid.uuid4(), created_by=None
        )
    assert _http_status(other_class) == 404

    incomplete = _file(db, class_id, filename="incomplete.pdf")
    incomplete.file_hash = None
    with pytest.raises(HTTPException) as no_hash:
        file_service.clone_file_asset(
            session=db, source=incomplete, teaching_class_id=class_id, created_by=None
        )
    assert _http_status(no_hash) == 409

    with pytest.raises(HTTPException) as missing_bytes:
        file_service.clone_file_asset(
            session=db, source=source, teaching_class_id=class_id, created_by=None
        )
    assert _http_status(missing_bytes) == 404


def test_clone_file_asset_cleans_up_when_copy_fails(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    source = _file(db, class_id)
    file_service._stored_path(source.id, "rubric.pdf").write_bytes(b"pdf")

    def broken_copy(src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(file_service.shutil, "copyfile", broken_copy)
    with pytest.raises(OSError, match="disk full"):
        file_service.clone_file_asset(
            session=db, source=source, teaching_class_id=class_id, created_by=None
        )
    leftovers = sorted(path.name for path in data_root.iterdir())
    assert leftovers == [f"{source.id}.pdf"]


def test_stage_restore_finalize_and_discard(data_root: Path) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    file = _file(db, class_id)
    stored = file_service._stored_path(file.id, "rubric.pdf")
    deleted = file_service._deleted_path(file.id, "rubric.pdf")
    stored.write_bytes(b"pdf")
    deleted.write_bytes(b"stale")
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=class_id,
        name="script",
        template_key="linux",
        rubric_snapshot_json={},
        script_content="print(1)",
        policy_check_result_json={},
        ai_review_result_json={},
        source_file_id=file.id,
    )
    db.add(artifact)
    db.commit()

    stage = file_service.stage_file_delete(session=db, file=file)

    assert stage.path == stored
    assert not stored.exists()
    assert deleted.read_bytes() == b"pdf"
    assert artifact.source_file_id is None

    file_service.restore_file_delete(stage)
    assert stored.read_bytes() == b"pdf"
    assert not deleted.exists()

    file_service.restore_file_delete(stage)  # nothing staged any more
    file_service.finalize_file_delete(stage)

    file_service.discard_file_asset(file)
    assert not stored.exists()


def test_clone_and_download_success(data_root: Path) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    source = _file(db, class_id)
    file_service._stored_path(source.id, "rubric.pdf").write_bytes(b"pdf")

    path, name = file_service.get_file_download(
        session=db, teaching_class_id=class_id, file_id=source.id
    )
    assert (path.read_bytes(), name) == (b"pdf", "rubric.pdf")
    assert file_service._file_snapshot(None) == {}

    clone = file_service.clone_file_asset(
        session=db, source=source, teaching_class_id=class_id, created_by=None
    )
    assert clone.original_filename == "rubric (2).pdf"
    assert file_service._stored_path(clone.id, "rubric (2).pdf").read_bytes() == b"pdf"
