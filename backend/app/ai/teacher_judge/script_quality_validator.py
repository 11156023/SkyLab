"""Quality validator for Teacher Judge managed scripts."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.teacher_judge._types import CheckResult, FixHint

REQUIRED_HELPERS = {
    "truncate_output",
    "redact_sensitive_text",
    "command_available",
    "run_command",
    "record_check",
}

UNKNOWN_ONLY_EXCEPTIONS = {
    "subprocess.TimeoutExpired",
    "TimeoutExpired",
    "FileNotFoundError",
    "PermissionError",
}

_DIRECT_RAW_PATTERN = re.compile(
    r"""["']raw["']\s*:\s*[^\n]*(stdout|stderr)""",
    re.IGNORECASE,
)
_STDOUT_PASS_TERNARY_PATTERN = re.compile(
    r"""["']pass["']\s+if\s+[^\n]*(stdout|stderr)|if\s+[^\n]*(stdout|stderr)[^\n]*else\s+["']pass["']""",
    re.IGNORECASE,
)


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _node_mentions_stream(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in {"stdout", "stderr"}:
            return True
        if isinstance(child, ast.Attribute) and child.attr in {"stdout", "stderr"}:
            return True
    return False


def _call_status_literal(node: ast.Call) -> str | None:
    for arg in node.args:
        literal = _literal_str(arg)
        if literal in {"pass", "fail", "warning", "unknown", "skipped"}:
            return literal
    for keyword in node.keywords:
        if keyword.arg == "status":
            literal = _literal_str(keyword.value)
            if literal in {"pass", "fail", "warning", "unknown", "skipped"}:
                return literal
    return None


def _call_has_pass_status(node: ast.Call) -> bool:
    return _call_status_literal(node) == "pass"


def _body_marks_pass(body: list[ast.stmt]) -> bool:
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Assign) and _literal_str(node.value) == "pass":
                return True
            if isinstance(node, ast.Call):
                if _call_name(node.func) == "record_check" and _call_has_pass_status(node):
                    return True
    return False


def _except_name(handler: ast.ExceptHandler) -> str | None:
    if handler.type is None:
        return None
    if isinstance(handler.type, ast.Name):
        return handler.type.id
    if isinstance(handler.type, ast.Attribute):
        base = _call_name(handler.type.value)
        return f"{base}.{handler.type.attr}" if base else handler.type.attr
    return None


def _collect_commands_needing_which(tree: ast.AST) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "subprocess.run" or not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, (ast.List, ast.Tuple)) or not first_arg.elts:
            continue
        command = _literal_str(first_arg.elts[0])
        if command:
            commands.add(command)
    return commands


def _collect_which_commands(tree: ast.AST) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "shutil.which" or not node.args:
            continue
        command = _literal_str(node.args[0])
        if command:
            commands.add(command)
    return commands


def _calls_named_helper(
    tree: ast.AST,
    helper_name: str,
    *,
    skip_functions: set[str] | None = None,
) -> bool:
    skip_functions = skip_functions or set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in skip_functions:
            continue
        if isinstance(node, ast.Call) and _call_name(node.func) == helper_name:
            return True
    return False


def _function_definitions(
    tree: ast.AST,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _function_uses_helper(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    helper_name: str,
) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_name(node.func) == helper_name
        for node in ast.walk(function_def)
    )


def _function_mentions_returncode(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for node in ast.walk(function_def):
        if isinstance(node, ast.Constant) and node.value == "returncode":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "returncode":
            return True
    return False


def _redaction_uses_bare_key(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for node in ast.walk(function_def):
        literal = _literal_str(node)
        if not literal:
            continue
        lowered = literal.lower()
        for match in re.finditer(r"key", lowered):
            prefix = lowered[max(0, match.start() - 20) : match.start()]
            if "api" not in prefix and "private" not in prefix:
                return True
    return False


def _record_check_has_sanitized_raw(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return _function_uses_helper(
        function_def, "truncate_output"
    ) and _function_uses_helper(function_def, "redact_sensitive_text")


def _record_check_raw_arg(call: ast.Call) -> ast.AST | None:
    if len(call.args) >= 5:
        return call.args[4]
    for keyword in call.keywords:
        if keyword.arg == "raw":
            return keyword.value
    return None


def _body_record_check_statuses(body: list[ast.stmt]) -> set[str]:
    statuses: set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and _call_name(node.func) == "record_check":
                status = _call_status_literal(node)
                if status:
                    statuses.add(status)
    return statuses


def _condition_checks_availability(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and _call_name(child.func) in {"command_available", "shutil.which"}
        for child in ast.walk(node)
    )


def _json_dumps_has_ensure_ascii_false(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "ensure_ascii":
            continue
        return (
            isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
        )
    return False


def _record_check_literal_arg(
    call: ast.Call,
    position: int,
    keyword_name: str,
) -> str | None:
    if len(call.args) > position:
        return _literal_str(call.args[position])
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return _literal_str(keyword.value)
    return None


def _except_handler_appends_errors(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            _call_name(node.func) == "errors.append"
            or (
                isinstance(node.func, ast.Attribute)
                and _call_name(node.func.value) == "errors"
            )
        )
        for node in ast.walk(handler)
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function_name(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return None


def _handler_returns_structured_command_error(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        keys = {_literal_str(key) for key in node.value.keys}
        if {"stdout", "stderr", "returncode"}.issubset(keys):
            return True
    return False


def _allows_helper_scoped_generic_except(
    handler: ast.ExceptHandler,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return (
        _enclosing_function_name(handler, parents) == "run_command"
        and _handler_returns_structured_command_error(handler)
    )


def _line_span_snippet(
    lines: list[str],
    node: ast.AST,
    *,
    max_lines: int = 8,
) -> str:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None) or lineno
    if not isinstance(lineno, int) or not isinstance(end_lineno, int):
        return ""
    start = max(lineno, 1)
    end = min(end_lineno, len(lines), start + max_lines - 1)
    return "\n".join(
        f"{line_number:04d}|{lines[line_number - 1]}"
        for line_number in range(start, end + 1)
    )


def _exception_fix_hint(
    handler: ast.ExceptHandler,
    exception_name: str | None,
    lines: list[str],
    *,
    target: str,
) -> FixHint:
    lineno = getattr(handler, "lineno", None)
    end_lineno = getattr(handler, "end_lineno", None)
    required_pattern = (
        'except Exception as exc:\n'
        '    errors.append(f"<check_id>: 未預期錯誤: {str(exc)[:200]}")\n'
        '    checks.append(record_check("<check_id>", "收集 ...", "unknown", "未預期錯誤"))'
    )
    hint: FixHint = {
        "type": "add_errors_append_in_except",
        "exception": exception_name or "bare except",
        "description": "bare except / except Exception 應追加 errors 條目",
        "target": target,
        "snippet": _line_span_snippet(lines, handler),
        "required_pattern": required_pattern,
    }
    if isinstance(lineno, int):
        hint["lineno"] = lineno
    if isinstance(end_lineno, int):
        hint["end_lineno"] = end_lineno
    return hint


def _is_generic_check_id(check_id: str) -> bool:
    normalized = check_id.strip().lower()
    return (
        normalized in {"check", "check-1", "item", "item-1", "stable_check_id"}
        or normalized.startswith("check-")
        or normalized.startswith("item-")
    )


def check_script_quality(script_content: str) -> CheckResult:
    """Return quality validation results for a generated managed script."""
    issues: list[str] = []
    fix_hints: list[FixHint] = []

    try:
        tree = ast.parse(script_content)
    except SyntaxError as exc:
        return {
            "approved": False,
            "blocked": True,
            "risk_level": "high",
            "issues": [f"Python 語法錯誤：{exc.msg}"],
            "fix_hints": [{"type": "fix_syntax_error", "description": f"Python 語法錯誤：{exc.msg}"}],
        }

    script_lines = script_content.splitlines()
    parents = _parent_map(tree)
    helper_defs = _function_definitions(tree)
    missing_helpers = sorted(REQUIRED_HELPERS - set(helper_defs))
    if missing_helpers:
        issues.append("腳本缺少必要 helper：" + ", ".join(missing_helpers))
        for helper_name in missing_helpers:
            fix_hints.append({"type": "add_helper_function", "name": helper_name, "description": f"腳本缺少必要 helper：{helper_name}"})

    record_check_def = helper_defs.get("record_check")
    if record_check_def and not _record_check_has_sanitized_raw(record_check_def):
        issues.append(
            "record_check 必須統一透過 redact_sensitive_text 與 truncate_output 處理 raw"
        )
        fix_hints.append({"type": "add_sanitize_in_record_check", "description": "record_check 必須統一透過 redact_sensitive_text 與 truncate_output 處理 raw"})

    command_available_def = helper_defs.get("command_available")
    if command_available_def and not _function_uses_helper(
        command_available_def, "shutil.which"
    ):
        issues.append("command_available 必須使用 shutil.which 檢查工具可用性")
        fix_hints.append({"type": "use_shutil_which", "function": "command_available", "description": "command_available 必須使用 shutil.which 檢查工具可用性"})

    run_command_def = helper_defs.get("run_command")
    if run_command_def and not _function_mentions_returncode(run_command_def):
        issues.append("run_command 必須回傳 returncode")
        fix_hints.append({"type": "add_returncode_to_run_command", "description": "run_command 必須回傳 returncode"})

    redaction_def = helper_defs.get("redact_sensitive_text")
    if redaction_def and _redaction_uses_bare_key(redaction_def):
        issues.append("redact_sensitive_text 不可使用過度寬泛的裸 key 規則")
        fix_hints.append({"type": "fix_redaction_pattern", "function": "redact_sensitive_text", "description": "redact_sensitive_text 不可使用過度寬泛的裸 key 規則"})

    if not _calls_named_helper(tree, "record_check", skip_functions={"record_check"}):
        issues.append("腳本必須透過 record_check 建立檢查結果")
        fix_hints.append({"type": "add_record_check_calls", "description": "腳本必須透過 record_check 建立檢查結果"})

    if not _calls_named_helper(tree, "run_command", skip_functions={"run_command"}):
        issues.append("腳本必須透過 run_command 執行收集指令")
        fix_hints.append({"type": "add_run_command_calls", "description": "腳本必須透過 run_command 執行收集指令"})

    json_dumps_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == "json.dumps"
    ]
    if not json_dumps_calls:
        issues.append("腳本必須使用 json.dumps 輸出 JSON")
        fix_hints.append({"type": "add_json_param", "function": "json.dumps", "param": "ensure_ascii", "value": False, "description": "腳本必須使用 json.dumps 輸出 JSON"})
    elif any(not _json_dumps_has_ensure_ascii_false(call) for call in json_dumps_calls):
        issues.append("json.dumps 必須設定 ensure_ascii=False")
        fix_hints.append({"type": "add_json_param", "function": "json.dumps", "param": "ensure_ascii", "value": False, "description": "json.dumps 必須設定 ensure_ascii=False"})

    if (
        '"metadata"' not in script_content
        and "'metadata'" not in script_content
        or '"timestamp"' not in script_content
        and "'timestamp'" not in script_content
        or '"platform"' not in script_content
        and "'platform'" not in script_content
    ):
        issues.append("輸出 JSON metadata 必須包含 timestamp 與 platform")
        fix_hints.append({"type": "add_output_field", "field": "metadata", "description": "輸出 JSON metadata 必須包含 timestamp 與 platform"})

    if _DIRECT_RAW_PATTERN.search(script_content):
        issues.append("raw 不可直接保存 stdout/stderr，必須先脫敏並截斷")
        fix_hints.append({"type": "sanitize_raw_field", "description": "raw 不可直接保存 stdout/stderr，必須先脫敏並截斷"})

    if _STDOUT_PASS_TERNARY_PATTERN.search(script_content):
        issues.append("不能用 stdout/stderr truthiness 直接判定 pass")
        fix_hints.append({"type": "remove_stdout_truthiness_check", "description": "不能用 stdout/stderr truthiness 直接判定 pass"})

    commands = _collect_commands_needing_which(tree)
    which_commands = _collect_which_commands(tree)
    for command in sorted(commands - which_commands):
        issues.append(f"外部工具 `{command}` 缺少 shutil.which 可用性檢查")
        fix_hints.append({"type": "add_command_availability_check", "command": command, "description": f"外部工具 `{command}` 缺少 shutil.which 可用性檢查"})

    if commands and not _calls_named_helper(
        tree,
        "command_available",
        skip_functions={"command_available"},
    ):
        issues.append("腳本必須透過 command_available 檢查外部工具可用性")
        fix_hints.append({"type": "add_command_availability_check", "description": "腳本必須透過 command_available 檢查外部工具可用性"})

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == "record_check":
            check_id = _record_check_literal_arg(node, 0, "check_id")
            if check_id and _is_generic_check_id(check_id):
                issues.append("record_check id 必須是語意化穩定 ID")
                fix_hints.append({"type": "rename_check_id", "current": check_id, "description": "record_check id 必須是語意化穩定 ID"})
            title = _record_check_literal_arg(node, 1, "title")
            if title and "檢查" in title:
                issues.append("record_check title 請使用收集語意，不要使用檢查")
                fix_hints.append({"type": "rename_title", "current": title, "description": "record_check title 請使用收集語意，不要使用檢查"})
            raw_arg = _record_check_raw_arg(node)
            if raw_arg is not None and _node_mentions_stream(raw_arg):
                issues.append(
                    "record_check raw 不可直接餵 stdout/stderr，必須先整理為 snippet 再交給 helper"
                )
                fix_hints.append({"type": "sanitize_record_check_raw", "description": "record_check raw 不可直接餵 stdout/stderr，必須先整理為 snippet 再交給 helper"})

        if isinstance(node, ast.If):
            if _node_mentions_stream(node.test) and _body_marks_pass(node.body):
                issues.append("不能用 stdout/stderr 是否有內容直接判定 pass")
                fix_hints.append({"type": "remove_stdout_truthiness_check", "description": "不能用 stdout/stderr 是否有內容直接判定 pass"})
            if _condition_checks_availability(node.test):
                statuses = _body_record_check_statuses(node.body)
                if "warning" in statuses:
                    issues.append("工具缺失時應回傳 unknown，不可使用 warning")
                    fix_hints.append({"type": "fix_status_semantics", "description": "工具缺失時應回傳 unknown，不可使用 warning"})
        elif isinstance(node, ast.ExceptHandler):
            exception_name = _except_name(node)
            statuses = _body_record_check_statuses(node.body)
            if _body_marks_pass(node.body):
                if exception_name in {
                    None,
                    "Exception",
                    "FileNotFoundError",
                    "PermissionError",
                }:
                    issues.append("例外處理不可吞錯後直接標成 pass")
                    fix_hints.append({"type": "fix_exception_handling", "exception": exception_name or "bare except", "description": "例外處理不可吞錯後直接標成 pass"})
                elif exception_name in {"subprocess.TimeoutExpired", "TimeoutExpired"}:
                    issues.append("timeout 例外不可標成 pass")
                    fix_hints.append({"type": "fix_timeout_status", "description": "timeout 例外不可標成 pass"})
            if exception_name in {None, "Exception"}:
                if any(isinstance(stmt, ast.Pass) for stmt in node.body):
                    issues.append("不可使用 except Exception/bare except 後直接 swallow/pass")
                    fix_hints.append({"type": "fix_exception_handling", "description": "不可使用 except Exception/bare except 後直接 swallow/pass"})
            if exception_name in UNKNOWN_ONLY_EXCEPTIONS and "warning" in statuses:
                issues.append(f"{exception_name} 應回傳 unknown，不可使用 warning")
                fix_hints.append({"type": "fix_status_semantics", "exception": exception_name, "description": f"{exception_name} 應回傳 unknown，不可使用 warning"})

    # ── errors 記錄完整性檢查 ──
    # 收集項目的 bare except / except Exception 必須有 errors.append。
    # run_command helper 可以把錯誤轉成結構化回傳值，再由呼叫點寫入 errors。
    for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
        except_name = _except_name(handler)
        if (
            except_name in {None, "Exception"}
            and not _except_handler_appends_errors(handler)
            and not _allows_helper_scoped_generic_except(handler, parents)
        ):
            issues.append("bare except / except Exception 後未將錯誤記錄到 errors")
            target = (
                "helper_exception_handler"
                if _enclosing_function_name(handler, parents)
                else "collection_exception_handler"
            )
            fix_hints.append(
                _exception_fix_hint(handler, except_name, script_lines, target=target)
            )
            break

    deduped = list(dict.fromkeys(issues))
    approved = not deduped
    return {
        "approved": approved,
        "blocked": bool(deduped),
        "risk_level": "low" if approved else "high",
        "issues": deduped,
        "fix_hints": fix_hints,
    }
