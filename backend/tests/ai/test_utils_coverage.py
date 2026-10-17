"""Coverage tests for app.ai.utils coercion and guard helpers."""



from app.ai import utils


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
