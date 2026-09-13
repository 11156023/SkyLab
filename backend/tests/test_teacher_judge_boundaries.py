from __future__ import annotations

from app.ai.teacher_judge.schemas import TeacherJudgeRubricItem
from app.ai.teacher_judge.service import normalize_items_for_export


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
        TeacherJudgeRubricItem(
            id="item-1",
            title="Port 80",
            description="檢查 Web 服務",
            checked=True,
            detectable="auto",
            detection_method="TCP Port 80 探測",
            fallback="請學生確認防火牆設定",
        ),
        TeacherJudgeRubricItem(
            id="item-2",
            title="程式碼品質",
            description="",
            checked=False,
            detectable="manual",
            detection_method=None,
            fallback=None,
        ),
    ]
