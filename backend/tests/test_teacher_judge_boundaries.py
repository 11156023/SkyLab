from __future__ import annotations

from app.ai.teacher_judge.schemas import RubricItem, TeacherJudgeRubricItem
from app.ai.teacher_judge.service import normalize_items_for_export
from app.schemas.rubric import RubricItem as LegacyRubricItem
from app.services import rubric_service


def test_legacy_rubric_schema_imports_teacher_judge_schema() -> None:
    assert LegacyRubricItem is RubricItem
    assert RubricItem is TeacherJudgeRubricItem


def test_legacy_rubric_service_exports_teacher_judge_workflow() -> None:
    assert rubric_service.normalize_items_for_export is normalize_items_for_export
    assert callable(rubric_service.analyze_rubric)
    assert callable(rubric_service.chat_with_rubric)
    assert callable(rubric_service.export_to_excel)


def test_teacher_judge_normalizes_ai_returned_items() -> None:
    items = normalize_items_for_export(
        [
            {
                "name": "Port 80",
                "desc": "檢查 Web 服務",
                "is_checked": "yes",
                "detectable": "AUTO",
                "detection": "TCP Port 80 探測",
                "suggestion": "請學生確認防火牆設定",
            },
            {"title": "程式碼品質", "detectable": "unknown"},
        ]
    )

    assert items == [
        RubricItem(
            id="item-1",
            title="Port 80",
            description="檢查 Web 服務",
            checked=True,
            detectable="auto",
            detection_method="TCP Port 80 探測",
            fallback="請學生確認防火牆設定",
        ),
        RubricItem(
            id="item-2",
            title="程式碼品質",
            description="",
            checked=False,
            detectable="manual",
            detection_method=None,
            fallback=None,
        ),
    ]
