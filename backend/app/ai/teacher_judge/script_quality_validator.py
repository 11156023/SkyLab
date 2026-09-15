"""Quality validator for Teacher Judge managed scripts."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.teacher_judge._types import CheckResult, FixHint

# Helpers every managed script needs regardless of how evidence is collected.
REQUIRED_HELPERS = {
    "truncate_output",
    "record_check",
}
# Helpers that only make sense when the script actually invokes external
# commands; requiring them unconditionally rejected valid stdlib-only checks.
COMMAND_REQUIRED_HELPERS = {
    "command_available",
    "run_command",
}
KNOWN_HELPERS = REQUIRED_HELPERS | COMMAND_REQUIRED_HELPERS

UNKNOWN_ONLY_EXCEPTIONS = {
    "subprocess.TimeoutExpired",
    "TimeoutExpired",
    "FileNotFoundError",
    "PermissionError",
}

def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                aliases[local_name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                if alias.name in KNOWN_HELPERS:
                    aliases[local_name] = alias.name
                else:
                    aliases[local_name] = f"{node.module}.{alias.name}"

    return aliases


def _call_name(node: ast.AST, aliases: dict[str, str] | None = None) -> str | None:
    aliases = aliases or {}
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func, aliases)
    return None


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


def _body_marks_pass(body: list[ast.stmt], aliases: dict[str, str]) -> bool:
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Assign) and _literal_str(node.value) == "pass":
                return True
            if isinstance(node, ast.Call):
                if _call_name(node.func, aliases) == "record_check" and _call_has_pass_status(node):
                    return True
    return False


def _except_name(handler: ast.ExceptHandler, aliases: dict[str, str]) -> str | None:
    if handler.type is None:
        return None
    if isinstance(handler.type, ast.Name):
        return aliases.get(handler.type.id, handler.type.id)
    if isinstance(handler.type, ast.Attribute):
        base = _call_name(handler.type.value, aliases)
        return f"{base}.{handler.type.attr}" if base else handler.type.attr
    return None


def _collect_commands_needing_which(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func, aliases) != "subprocess.run" or not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, (ast.List, ast.Tuple)) or not first_arg.elts:
            continue
        command = _literal_str(first_arg.elts[0])
        if command:
            commands.add(command)
    return commands


def _collect_which_commands(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func, aliases) != "shutil.which" or not node.args:
            continue
        command = _literal_str(node.args[0])
        if command:
            commands.add(command)
    return commands


def _calls_named_helper(
    tree: ast.AST,
    helper_name: str,
    aliases: dict[str, str],
    *,
    skip_functions: set[str] | None = None,
) -> bool:
    skip_functions = skip_functions or set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in skip_functions:
            continue
        if isinstance(node, ast.Call) and _call_name(node.func, aliases) == helper_name:
            return True
    return False


def _scan_calls_once(
    tree: ast.AST,
    aliases: dict[str, str],
) -> tuple[list[ast.Call], set[str], set[str], set[str]]:
    """Collect call-derived facts in a single AST walk.

    Pure optimization: merges the five separate full-tree walks previously done
    for json.dumps collection, _collect_commands_needing_which,
    _collect_which_commands and _calls_named_helper (record_check / run_command
    / command_available) into one pass. Name resolution reuses one _call_name
    per Call node with the same aliases logic. The historical skip_functions
    behaviour is preserved as-is (all calls are inspected, including those
    inside helper definitions), so gate verdicts are unchanged.
    """
    json_dumps_calls: list[ast.Call] = []
    subprocess_commands: set[str] = set()
    which_commands: set[str] = set()
    helper_calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func, aliases)
        if name == "json.dumps":
            json_dumps_calls.append(node)
        elif name == "subprocess.run":
            if node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, (ast.List, ast.Tuple)) and first_arg.elts:
                    command = _literal_str(first_arg.elts[0])
                    if command:
                        subprocess_commands.add(command)
        elif name == "shutil.which":
            if node.args:
                command = _literal_str(node.args[0])
                if command:
                    which_commands.add(command)
        if name in ("record_check", "run_command", "command_available"):
            helper_calls.add(name)
    return json_dumps_calls, subprocess_commands, which_commands, helper_calls


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
    aliases: dict[str, str],
) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_name(node.func, aliases) == helper_name
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


def _record_check_has_bounded_raw(
    function_def: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, str],
) -> bool:
    return _function_uses_helper(function_def, "truncate_output", aliases)


def _body_record_check_statuses(
    body: list[ast.stmt],
    aliases: dict[str, str],
) -> set[str]:
    statuses: set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and _call_name(node.func, aliases) == "record_check":
                status = _call_status_literal(node)
                if status:
                    statuses.add(status)
    return statuses


def _condition_checks_availability(node: ast.AST, aliases: dict[str, str]) -> bool:
    return any(
        isinstance(child, ast.Call)
        and _call_name(child.func, aliases) in {"command_available", "shutil.which"}
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


def _except_handler_appends_errors(handler: ast.ExceptHandler, aliases: dict[str, str]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            _call_name(node.func, aliases) == "errors.append"
            or (
                isinstance(node.func, ast.Attribute)
                and _call_name(node.func.value, aliases) == "errors"
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
    warnings: list[str] = []

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
    aliases = _import_aliases(tree)
    parents = _parent_map(tree)
    helper_defs = _function_definitions(tree)

    # Single-pass call scan (replaces five separate full-tree walks for
    # json.dumps / subprocess commands / which commands / helper calls).
    # Legacy collectors (_collect_commands_needing_which,
    # _collect_which_commands, _calls_named_helper) are kept as compat
    # wrappers and no longer used on this hot path.
    json_dumps_calls, commands, which_commands, helper_calls = _scan_calls_once(
        tree, aliases
    )

    # Command helpers are only required when the script actually invokes
    # external commands; a stdlib-only check must not be rejected for missing
    # run_command/command_available.
    required_helpers = set(REQUIRED_HELPERS)
    if commands:
        required_helpers |= COMMAND_REQUIRED_HELPERS
    missing_helpers = sorted(required_helpers - set(helper_defs))
    if missing_helpers:
        issues.append("腳本缺少必要 helper：" + ", ".join(missing_helpers))
        for helper_name in missing_helpers:
            fix_hints.append({"type": "add_helper_function", "name": helper_name, "description": f"腳本缺少必要 helper：{helper_name}"})

    record_check_def = helper_defs.get("record_check")
    if record_check_def and not _record_check_has_bounded_raw(record_check_def, aliases):
        issues.append(
            "record_check 必須統一透過 truncate_output 控制 raw 大小"
        )
        fix_hints.append({"type": "add_truncate_in_record_check", "description": "record_check 必須統一透過 truncate_output 控制 raw 大小"})

    command_available_def = helper_defs.get("command_available")
    if command_available_def and not _function_uses_helper(
        command_available_def, "shutil.which", aliases
    ):
        issues.append("command_available 必須使用 shutil.which 檢查工具可用性")
        fix_hints.append({"type": "use_shutil_which", "function": "command_available", "description": "command_available 必須使用 shutil.which 檢查工具可用性"})

    run_command_def = helper_defs.get("run_command")
    if run_command_def and not _function_mentions_returncode(run_command_def):
        issues.append("run_command 必須回傳 returncode")
        fix_hints.append({"type": "add_returncode_to_run_command", "description": "run_command 必須回傳 returncode"})

    if "record_check" not in helper_calls:
        issues.append("腳本必須透過 record_check 建立檢查結果")
        fix_hints.append({"type": "add_record_check_calls", "description": "腳本必須透過 record_check 建立檢查結果"})

    if commands and "run_command" not in helper_calls:
        issues.append("腳本必須透過 run_command 執行收集指令")
        fix_hints.append({"type": "add_run_command_calls", "description": "腳本必須透過 run_command 執行收集指令"})

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

    for command in sorted(commands - which_commands):
        issues.append(f"外部工具 `{command}` 缺少 shutil.which 可用性檢查")
        fix_hints.append({"type": "add_command_availability_check", "command": command, "description": f"外部工具 `{command}` 缺少 shutil.which 可用性檢查"})

    if commands and "command_available" not in helper_calls:
        issues.append("腳本必須透過 command_available 檢查外部工具可用性")
        fix_hints.append({"type": "add_command_availability_check", "description": "腳本必須透過 command_available 檢查外部工具可用性"})

    # Collect ExceptHandler nodes during the main walk below and reuse them for
    # the errors-completeness check, instead of a second full-tree walk. Order
    # matches `ast.walk` BFS in both cases, and the first-hit `break` is kept.
    except_handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func, aliases) == "record_check":
            check_id = _record_check_literal_arg(node, 0, "check_id")
            if check_id and _is_generic_check_id(check_id):
                # 文案類建議：不阻斷審查，僅作為非阻斷提示回傳。
                warning = f"record_check id `{check_id}` 建議使用語意化穩定 ID"
                if warning not in warnings:
                    warnings.append(warning)
            title = _record_check_literal_arg(node, 1, "title")
            if title and "檢查" in title:
                warning = f"record_check title `{title}` 建議使用收集語意，不要使用檢查"
                if warning not in warnings:
                    warnings.append(warning)
        if isinstance(node, ast.If):
            if _condition_checks_availability(node.test, aliases):
                statuses = _body_record_check_statuses(node.body, aliases)
                if "warning" in statuses:
                    issues.append("工具缺失時應回傳 unknown，不可使用 warning")
                    fix_hints.append({"type": "fix_status_semantics", "description": "工具缺失時應回傳 unknown，不可使用 warning"})
        elif isinstance(node, ast.ExceptHandler):
            except_handlers.append(node)
            exception_name = _except_name(node, aliases)
            statuses = _body_record_check_statuses(node.body, aliases)
            if _body_marks_pass(node.body, aliases):
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
    # Reuses handlers collected in the main walk above (same BFS order).
    for handler in except_handlers:
        except_name = _except_name(handler, aliases)
        if (
            except_name in {None, "Exception"}
            and not _except_handler_appends_errors(handler, aliases)
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
        "warnings": warnings,
    }


def collect_record_check_ids(script_content: str) -> set[str]:
    """Collect literal record_check ids for rubric coverage validation."""
    try:
        tree = ast.parse(script_content)
    except SyntaxError:
        return set()
    aliases = _import_aliases(tree)
    ids: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _call_name(node.func, aliases) == "record_check"
        ):
            check_id = _record_check_literal_arg(node, 0, "check_id")
            if check_id:
                ids.add(check_id)
    return ids
