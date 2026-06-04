from __future__ import annotations

import pytest

from app.ai.utils import safe_bool


def test_safe_bool_coercion() -> None:
    # Booleans
    assert safe_bool(True) is True
    assert safe_bool(False) is False

    # Numbers
    assert safe_bool(1) is True
    assert safe_bool(0) is False
    assert safe_bool(1.0) is True
    assert safe_bool(0.0) is False

    # None and default fallback
    assert safe_bool(None) is False
    assert safe_bool(None, default=True) is True
    assert safe_bool("invalid", default=True) is True
    assert safe_bool("invalid", default=False) is False

    # Truthy strings (case-insensitive and stripped)
    assert safe_bool("true") is True
    assert safe_bool("  TRUE  ") is True
    assert safe_bool("1") is True
    assert safe_bool("yes") is True
    assert safe_bool("Yes") is True
    assert safe_bool("y") is True
    assert safe_bool("on") is True
    assert safe_bool("checked") is True
    assert safe_bool("done") is True

    # Falsy strings (case-insensitive and stripped)
    assert safe_bool("false") is False
    assert safe_bool("  FALSE  ") is False
    assert safe_bool("0") is False
    assert safe_bool("no") is False
    assert safe_bool("n") is False
    assert safe_bool("off") is False
    assert safe_bool("unchecked") is False
    assert safe_bool("todo") is False
