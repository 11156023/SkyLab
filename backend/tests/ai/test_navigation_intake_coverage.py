"""Coverage tests for app.ai.navigation.intake fact collection branches."""

from app.ai.navigation.intake import SLOTS, _explicit_answer, read_intake
from app.ai.navigation.schemas import IntakeFacts, NavigationMessage


def _user(text: str) -> NavigationMessage:
    return NavigationMessage(role="user", content=text)


def _assistant(text: str) -> NavigationMessage:
    return NavigationMessage(role="assistant", content=text)


def test_explicit_answer_ignores_acknowledgements() -> None:
    assert _explicit_answer(SLOTS[0], "好") is None
    assert _explicit_answer(SLOTS[0], "  ") is None


def test_category_button_does_not_replace_detailed_purpose() -> None:
    state = read_intake(
        [_user("課程作業")],
        facts=IntakeFacts(purpose="做 python 研究"),
    )
    assert state.facts.purpose == "做 python 研究"


def test_free_text_answer_to_purpose_question_is_recorded() -> None:
    state = read_intake([_assistant(SLOTS[0].question), _user("記帳用")])
    assert state.facts.purpose == "記帳用"


def test_pending_purpose_key_records_free_text_answer() -> None:
    state = read_intake([_user("記帳用")], pending_key="purpose")
    assert state.facts.purpose == "記帳用"
