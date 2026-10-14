"""Coverage for app.ai.teacher_judge.script_quality_validator AST helper branches."""

from __future__ import annotations

import ast
from textwrap import dedent

from app.ai.teacher_judge import script_quality_validator as sqv


def _parse(source: str) -> ast.Module:
    return ast.parse(dedent(source).strip())


def _func(source: str) -> ast.FunctionDef:
    node = _parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _call(source: str) -> ast.Call:
    node = _parse(source).body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return node.value


def test_import_aliases_skip_star_imports() -> None:
    aliases = sqv._import_aliases(_parse("from os.path import *\nfrom os import sep"))
    assert aliases == {"sep": "os.sep"}


def test_call_status_literal_keyword() -> None:
    assert (
        sqv._call_status_literal(_call('record_check("id", status="pass")')) == "pass"
    )
    assert sqv._call_status_literal(_call('record_check("id", status="bogus")')) is None


def test_body_marks_pass_via_record_check_call() -> None:
    body = _parse('checks.append(record_check("id", "t", "pass", "e"))').body
    assert sqv._body_marks_pass(body, {}) is True


def test_function_mentions_returncode_via_attribute() -> None:
    func = _func("def run_command(proc):\n    return proc.returncode")
    assert sqv._function_mentions_returncode(func) is True


def test_record_check_has_result_return_variants() -> None:
    bare = _func("def record_check(raw):\n    return")
    assert sqv._record_check_has_result_return(bare) is False
    other = _func("def record_check(raw):\n    return [raw]")
    assert sqv._record_check_has_result_return(other) is False
    named = _func(
        """
        def record_check(raw):
            result = {"raw": raw}
            return result
        """
    )
    assert sqv._record_check_has_result_return(named) is True


def test_record_check_bounded_raw_without_raw_parameter() -> None:
    func = _func("def record_check(check_id):\n    return {}")
    assert sqv._record_check_has_bounded_raw(func, {}) is False


def test_record_check_bounded_raw_named_result_and_keyword_truncate() -> None:
    func = _func(
        """
        def record_check(check_id, raw):
            marker: int
            other = str(check_id)
            empty = truncate_output()
            result = {"raw": truncate_output(text=raw)}
            return result
        """
    )
    assert sqv._record_check_has_bounded_raw(func, {}) is True


def test_record_check_bounded_raw_no_returned_mapping() -> None:
    func = _func("def record_check(raw):\n    return None")
    assert sqv._record_check_has_bounded_raw(func, {}) is False


def test_json_dumps_ensure_ascii_after_other_keyword() -> None:
    call = _call("json.dumps(x, indent=2, ensure_ascii=False)")
    assert sqv._json_dumps_has_ensure_ascii_false(call) is True


def test_record_check_literal_arg_keyword_and_missing() -> None:
    call = _call('record_check(check_id="cpu-load")')
    assert sqv._record_check_literal_arg(call, 0, "check_id") == "cpu-load"
    assert sqv._record_check_literal_arg(call, 1, "title") is None


def test_line_span_snippet_without_lineno() -> None:
    assert sqv._line_span_snippet(["x"], ast.Module(body=[], type_ignores=[])) == ""


def test_check_script_quality_syntax_error() -> None:
    result = sqv.check_script_quality("def broken(:\n")
    assert result["approved"] is False
    assert result["fix_hints"][0]["type"] == "fix_syntax_error"


def test_check_script_quality_command_available_without_which_and_no_json() -> None:
    script = dedent(
        """
        def command_available(name):
            return True

        def truncate_output(text):
            return text[:10]

        def record_check(check_id, title, status, evidence, raw=""):
            return {"raw": truncate_output(raw)}

        print(record_check("cpu-load", "收集", "pass", "ok"))
        """
    )
    result = sqv.check_script_quality(script)
    assert "command_available 必須使用 shutil.which 檢查工具可用性" in result["issues"]
    assert "腳本必須使用 json.dumps 輸出 JSON" in result["issues"]
