"""Coverage tests for app.ai.utils coercion and guard helpers."""

from types import SimpleNamespace

import pytest

from app.ai import utils
from app.exceptions import BadRequestError


def test_conversation_limits() -> None:
    message = SimpleNamespace(content="abc")
    utils.ensure_conversation_within_limits([message], max_messages=1, max_chars=3)
    with pytest.raises(BadRequestError):
        utils.ensure_conversation_within_limits([message, message], max_messages=1)
    with pytest.raises(BadRequestError):
        utils.ensure_conversation_within_limits([message], max_chars=2)


def test_form_context_limit() -> None:
    utils.ensure_form_context_within_limits("{}", max_chars=2)
    with pytest.raises(BadRequestError):
        utils.ensure_form_context_within_limits("{}", max_chars=1)


def test_strip_think_tags_without_marker() -> None:
    assert utils.strip_think_tags("  plain answer  ") == "plain answer"
    assert utils.strip_think_tags("<think>x</think> done") == "done"


def test_safe_int_branches() -> None:
    assert utils.safe_int(None, 7) == 7
    assert utils.safe_int("2 vCPU", extract_digits=True) == 2
    assert utils.safe_int("abc", 5, extract_digits=True) == 5
    assert utils.safe_int("12") == 12
    assert utils.safe_int(object(), 3) == 3
    assert utils.safe_int(-4, minimum=0) == 0
    assert utils.safe_int(4, minimum=0) == 4


def test_safe_float_branches() -> None:
    assert utils.safe_float(None, 1.5) == 1.5
    assert utils.safe_float("2.5") == 2.5
    assert utils.safe_float("bad", 9.0) == 9.0
