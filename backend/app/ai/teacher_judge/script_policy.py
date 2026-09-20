"""Deterministic policy checks for Teacher Judge managed scripts."""

from __future__ import annotations

import ast
import ipaddress
import json
import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

if TYPE_CHECKING:
    from app.ai.teacher_judge._types import CheckResult, FixHint, ScriptValidationResult

ALLOWED_RESULT_STATUSES = {"pass", "fail", "warning", "unknown", "skipped"}


class ManagedScriptCheck(BaseModel):
    id: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=240)
    status: Literal["pass", "fail", "warning", "unknown", "skipped"]
    evidence: str = Field(default="", max_length=4000)
    raw: str = Field(default="", max_length=4000)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_RESULT_STATUSES:
            raise ValueError(f"unsupported status: {value}")
        return normalized


class ManagedScriptMetadata(BaseModel):
    timestamp: str = Field(..., min_length=1, max_length=120)
    platform: str = Field(..., min_length=1, max_length=240)


class ManagedScriptResult(BaseModel):
    schema_version: Literal["teacher_judge_result.v1"]
    metadata: ManagedScriptMetadata
    summary: str = Field(default="", max_length=2000)
    checks: list[ManagedScriptCheck] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != "teacher_judge_result.v1":
            raise ValueError("schema_version must be teacher_judge_result.v1")
        return value


DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-rf\b", "禁止使用 rm -rf 刪除檔案"),
    (r"\bdel\s+/s\b", "禁止使用 del /s 刪除檔案"),
    (r"\bremove-item\b.*\b-recurse\b", "禁止使用 Remove-Item -Recurse"),
    (r"\bfind\b.*\b-delete\b", "禁止使用 find -delete"),
    (r"\bdrop\s+database\b", "禁止刪除資料庫"),
    (r"\btruncate\s+table\b", "禁止清空資料表"),
    (r"\bdelete\s+from\b(?![^;\n]+\bwhere\b)", "禁止無條件 delete from"),
    (r"\bshutdown\b", "禁止關機"),
    (r"\breboot\b", "禁止重啟"),
    (r"\bapt(?:-get)?\s+install\b", "禁止安裝系統套件"),
    (r"\bpip\s+install\b", "禁止安裝 Python 套件"),
    (r"\bnpm\s+install\b", "禁止安裝 npm 套件"),
    (r"\bchmod\b|\bchown\b|\bsystemctl\s+(?:enable|disable|restart|stop|start)\b", "禁止修改系統設定或服務狀態"),
    (r"\breset\b|\bcleanup\b|\bclean\s+up\b|\bfix\b|\brepair\b", "禁止產生修復、清理或重設類反向操作"),
)

# Pre-compiled once at import: same patterns/flags as the previous inline
# re.search(pattern, text, re.IGNORECASE | re.DOTALL) calls. Pure optimization,
# match verdicts and fix-hint pattern strings are unchanged.
_COMPILED_DENY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), pattern, message)
    for pattern, message in DENY_PATTERNS
)

_WHITESPACE_SPLIT = re.compile(r"\s+")

SHELL_LAUNCHERS = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "powershell",
    "powershell.exe",
    "pwsh",
    "sh",
    "zsh",
}
GIT_WRITE_SUBCOMMANDS = {
    "add",
    "am",
    "apply",
    "bisect",
    "branch",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "config",
    "fetch",
    "gc",
    "init",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "remote",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "submodule",
    "switch",
    "tag",
    "worktree",
}

DENY_AST_CALLS: dict[str, str] = {
    "os.system": "禁止使用 os.system 執行 shell 指令",
    "os.popen": "禁止使用 os.popen 執行 shell 指令",
    "os.remove": "禁止刪除檔案",
    "os.unlink": "禁止刪除檔案",
    "os.rmdir": "禁止刪除目錄",
    "pathlib.Path.unlink": "禁止刪除檔案",
    "pathlib.Path.rmdir": "禁止刪除目錄",
    "pathlib.Path.write_text": "禁止寫入檔案",
    "pathlib.Path.write_bytes": "禁止寫入檔案",
    "pathlib.Path.rename": "禁止移動或重新命名檔案",
    "pathlib.Path.replace": "禁止取代檔案",
    "pathlib.Path.chmod": "禁止修改檔案權限",
    "shutil.rmtree": "禁止遞迴刪除目錄",
    "shutil.move": "禁止移動檔案",
    "shutil.copy": "禁止寫入檔案",
    "shutil.copy2": "禁止寫入檔案",
    "shutil.copyfile": "禁止寫入檔案",
    "socket.socket": "禁止直接使用 socket 連線",
    "socket.create_connection": "禁止直接使用 socket 連線",
    "requests.Session": "禁止使用可重用網路 session",
    "httpx.Client": "禁止使用可重用網路 client",
    "httpx.AsyncClient": "禁止使用可重用網路 client",
    "subprocess.call": "請使用 subprocess.run 並設定 timeout",
    "subprocess.Popen": "禁止直接使用 subprocess.Popen",
}


NETWORK_CALLS = {
    "requests.get",
    "requests.head",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.request",
    "httpx.get",
    "httpx.head",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.request",
    "urllib.request.urlopen",
}
WRITE_NETWORK_CALLS = {
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
}


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    tracked_modules = {"io", "os", "pathlib", "requests", "httpx", "socket", "shutil"}
    tracked_prefixes = ("subprocess", "urllib.request")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                if alias.name in tracked_modules or alias.name.startswith(tracked_prefixes):
                    aliases[local_name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module in tracked_modules or module.startswith(tracked_prefixes):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local_name = alias.asname or alias.name
                    aliases[local_name] = f"{module}.{alias.name}"

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


def _has_timeout_keyword(node: ast.Call) -> bool:
    return any(keyword.arg == "timeout" for keyword in node.keywords)


def _keyword_is_true(node: ast.Call, keyword_name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg != keyword_name:
            continue
        return isinstance(keyword.value, ast.Constant) and keyword.value.value is True
    return False


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_command_text(node: ast.AST) -> str | None:
    if literal := _literal_str(node):
        return literal
    if isinstance(node, (ast.List, ast.Tuple)):
        parts: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            parts.append(item.value)
        return " ".join(parts)
    return None


def _open_mode(node: ast.Call, mode_arg_index: int = 1) -> str:
    if len(node.args) > mode_arg_index:
        mode = _literal_str(node.args[mode_arg_index])
        if mode:
            return mode
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = _literal_str(keyword.value)
            if mode:
                return mode
    return "r"


def _is_write_mode(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))


def _is_local_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _network_method_and_url(call_name: str, node: ast.Call) -> tuple[str, str | None]:
    method = "GET"
    url_arg_index = 0
    if call_name.endswith(".head"):
        method = "HEAD"
    elif call_name.endswith(".post"):
        method = "POST"
    elif call_name.endswith(".put"):
        method = "PUT"
    elif call_name.endswith(".patch"):
        method = "PATCH"
    elif call_name.endswith(".delete"):
        method = "DELETE"
    elif call_name.endswith(".request"):
        method = (_literal_str(node.args[0]) or "").upper() if node.args else ""
        url_arg_index = 1

    url = _literal_str(node.args[url_arg_index]) if len(node.args) > url_arg_index else None
    for keyword in node.keywords:
        if keyword.arg == "method":
            method = (_literal_str(keyword.value) or "").upper()
        if keyword.arg == "url":
            url = _literal_str(keyword.value)
    return method, url


def _network_issues(call_name: str, node: ast.Call) -> list[str]:
    issues: list[str] = []
    method, url = _network_method_and_url(call_name, node)

    if call_name.endswith(".request") and method not in {"GET", "HEAD"}:
        issues.append("通用網路請求只允許 GET/HEAD")
    if call_name in WRITE_NETWORK_CALLS or method in {"POST", "PUT", "PATCH", "DELETE"}:
        issues.append("禁止使用會送出或修改資料的網路請求")
    if not _has_timeout_keyword(node):
        issues.append("網路請求必須設定 timeout")
    if not url or not _is_local_url(url):
        issues.append("網路請求只允許 literal localhost/127.0.0.1/::1 URL")
    return issues


def _dangerous_command_issue(command_text: str) -> str | None:
    normalized = command_text.lower()
    for compiled, _pattern, message in _COMPILED_DENY_PATTERNS:
        if compiled.search(normalized):
            return message
    tokens = _WHITESPACE_SPLIT.split(normalized.strip())
    if not tokens:
        return None
    command = tokens[0]
    if command in SHELL_LAUNCHERS:
        return "禁止透過 shell launcher 間接執行指令"
    if command == "git" and len(tokens) > 1 and tokens[1] in GIT_WRITE_SUBCOMMANDS:
        return "通用受控指令只允許唯讀 Git 子命令"
    if command == "rm" and any(token in {"-r", "-rf", "-fr"} for token in tokens[1:]):
        return "禁止使用 rm 遞迴刪除檔案"
    if command == "find" and "-delete" in tokens:
        return "禁止使用 find -delete"
    if command in {"shutdown", "reboot"}:
        return "禁止關機或重啟"
    if command in {"apt", "apt-get", "pip", "npm"} and "install" in tokens:
        return "禁止安裝套件"
    return None


def validate_managed_script_output(payload: str | dict[str, Any]) -> ScriptValidationResult:
    """Validate managed script JSON output contract."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        result = ManagedScriptResult.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return {
            "valid": False,
            "error": str(exc),
            "schema_version": "teacher_judge_result.v1",
        }

    return {
        "valid": True,
        "error": None,
        "schema_version": result.schema_version,
        "checks_count": len(result.checks),
    }


def check_script_policy(script_content: str) -> CheckResult:
    """Return deterministic allow/block result for a Python managed script."""
    issues: list[str] = []
    fix_hints: list[FixHint] = []
    normalized = script_content.lower()

    for compiled, pattern, message in _COMPILED_DENY_PATTERNS:
        if compiled.search(normalized):
            issues.append(message)
            fix_hints.append({"type": "remove_dangerous_pattern", "description": message, "pattern": pattern})

    if "teacher_judge_result.v1" not in script_content:
        issues.append("腳本必須輸出 teacher_judge_result.v1 schema_version")
        fix_hints.append({"type": "add_output_field", "field": "schema_version", "value": "teacher_judge_result.v1"})
    if "print(" not in normalized:
        issues.append("腳本必須透過 stdout 輸出 JSON 結果")
        fix_hints.append({"type": "add_print_output_json", "description": "腳本必須使用 print() 輸出 JSON"})
    if '"checks"' not in script_content and "'checks'" not in script_content:
        issues.append("腳本輸出 JSON 必須包含 checks 欄位")
        fix_hints.append({"type": "add_output_field", "field": "checks"})
    if '"errors"' not in script_content and "'errors'" not in script_content:
        issues.append("腳本輸出 JSON 必須包含 errors 欄位")
        fix_hints.append({"type": "add_output_field", "field": "errors"})
    if '"metadata"' not in script_content and "'metadata'" not in script_content:
        issues.append("腳本輸出 JSON 必須包含 metadata 欄位")
        fix_hints.append({"type": "add_output_field", "field": "metadata"})
    if '"timestamp"' not in script_content and "'timestamp'" not in script_content:
        issues.append("metadata 必須包含 timestamp")
        fix_hints.append({"type": "add_output_field", "field": "metadata.timestamp"})
    if '"platform"' not in script_content and "'platform'" not in script_content:
        issues.append("metadata 必須包含 platform")
        fix_hints.append({"type": "add_output_field", "field": "metadata.platform"})

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
    aliases = _import_aliases(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func, aliases)
            if call_name in DENY_AST_CALLS:
                issues.append(DENY_AST_CALLS[call_name])
                fix_hints.append({"type": "replace_dangerous_call", "function": call_name, "description": DENY_AST_CALLS[call_name]})
            if call_name in {"open", "io.open", "pathlib.Path.open"}:
                mode_arg_index = 0 if call_name == "pathlib.Path.open" else 1
                # Compute once and reuse for the check and the hint (was called twice).
                mode = _open_mode(node, mode_arg_index=mode_arg_index)
                if _is_write_mode(mode):
                    issues.append("禁止以寫入模式開啟檔案")
                    fix_hints.append({"type": "remove_write_mode", "function": call_name, "mode": mode})
            if call_name == "subprocess.run" and _keyword_is_true(node, "shell"):
                issues.append("禁止使用 shell=True 執行指令")
                fix_hints.append({"type": "remove_keyword_param", "function": "subprocess.run", "param": "shell", "description": "禁止使用 shell=True 執行指令"})
            if call_name == "subprocess.run" and node.args:
                command_text = _literal_command_text(node.args[0])
                if command_text:
                    dangerous_issue = _dangerous_command_issue(command_text)
                    if dangerous_issue:
                        issues.append(dangerous_issue)
                        fix_hints.append({"type": "remove_dangerous_command", "command": command_text, "description": dangerous_issue})
            if call_name in NETWORK_CALLS:
                net_issues = _network_issues(call_name, node)
                issues.extend(net_issues)
                for issue in net_issues:
                    fix_hints.append({"type": "fix_network_call", "call": call_name, "description": issue})
            if call_name == "subprocess.run" and not _has_timeout_keyword(node):
                issues.append("subprocess.run 必須設定 timeout")
                fix_hints.append({"type": "add_keyword_param", "function": "subprocess.run", "param": "timeout", "value": 30, "description": "subprocess.run 必須設定 timeout"})
        elif isinstance(node, (ast.While, ast.For)):
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant):
                if node.test.value is True:
                    issues.append("禁止無限制 while True 迴圈")
                    fix_hints.append({"type": "remove_infinite_loop", "description": "禁止無限制 while True 迴圈"})

    deduped = list(dict.fromkeys(issues))
    approved = not deduped
    return {
        "approved": approved,
        "blocked": not approved,
        "risk_level": "low" if approved else "high",
        "issues": deduped,
        "fix_hints": fix_hints,
    }


def check_peer_runtime_policy(
    script_content: str,
    rubric_snapshot: dict[str, Any],
) -> CheckResult:
    """Validate the reserved peer-IP data flow for a generated child script.

    A peer address is never substituted into approved source. The script must
    read the per-target ``runtime_context.json`` file, select the declared
    logical peer's ``ip_address`` and pass that value only to a literal
    ``ping`` argv. This is intentionally conservative: unsupported peer
    primitives remain a review blocker instead of silently widening network
    access.
    """

    issues: list[str] = []
    fix_hints: list[FixHint] = []
    peer_items = [
        item
        for item in rubric_snapshot.get("items") or []
        if isinstance(item, dict) and str(item.get("peer_node_key") or "").strip()
    ]
    if not peer_items:
        return {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        }

    expected_peers = {
        str(item.get("peer_node_key") or "").strip() for item in peer_items
    }
    peer_token = "{{peer.ip}}"
    for item in peer_items:
        steps = item.get("check_steps") or []
        argv_with_token: list[list[str]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            argv = step.get("argv")
            if not isinstance(argv, list):
                parameters = step.get("parameters")
                argv = parameters.get("argv") if isinstance(parameters, dict) else None
            if isinstance(argv, list) and peer_token in argv:
                argv_with_token.append([str(part) for part in argv])
        if not argv_with_token:
            issues.append(
                f"peer 項目 {item.get('id') or item.get('title') or '未命名'} 必須使用 {peer_token}"
            )
        elif any(argv[0].strip().lower() != "ping" for argv in argv_with_token):
            issues.append("目前只允許將 peer IP 傳給 ping 的 argv")

    try:
        tree = ast.parse(script_content)
    except SyntaxError:
        issues.append("peer runtime 腳本不是有效的 Python")
        tree = None

    if tree is not None:
        literal_strings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if "runtime_context.json" not in literal_strings:
            issues.append("peer 檢查必須從固定的 runtime_context.json 讀取 context")
        if "peers" not in literal_strings or "ip_address" not in literal_strings:
            issues.append("peer runtime context 必須只讀取 peers/<node_key>/ip_address")
        handles_non_ready_status = any(
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id in {"resolution_status", "status"}
            and any(
                isinstance(operator, ast.NotEq)
                for operator in node.ops
            )
            for node in ast.walk(tree)
        )
        if "resolution_status" not in literal_strings or (
            "unavailable" not in literal_strings and not handles_non_ready_status
        ):
            issues.append("peer runtime 必須處理 resolution_status=unavailable")

        aliases = _import_aliases(tree)
        peer_context_names: set[str] = set()
        peer_names: set[str] = set()
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]

        def targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
            raw_targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            return [target.id for target in raw_targets if isinstance(target, ast.Name)]

        context_names: set[str] = set()
        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            values = {
                child.value
                for child in ast.walk(value)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if "runtime_context.json" in values:
                context_names.update(targets(assignment))

        for _ in range(len(assignments) + 1):
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                names = {
                    child.id
                    for child in ast.walk(value)
                    if isinstance(child, ast.Name)
                }
                if names & context_names:
                    for target in targets(assignment):
                        if target not in context_names:
                            context_names.add(target)
                            changed = True
            if not changed:
                break

        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            values = {
                child.value
                for child in ast.walk(value)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if "peers" in values:
                peer_context_names.update(targets(assignment))

        for _ in range(len(assignments) + 1):
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                names = {
                    child.id
                    for child in ast.walk(value)
                    if isinstance(child, ast.Name)
                }
                if names & peer_context_names:
                    for target in targets(assignment):
                        if target not in peer_context_names:
                            peer_context_names.add(target)
                            changed = True
            if not changed:
                break

        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            values = {
                child.value
                for child in ast.walk(value)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            names = {
                child.id
                for child in ast.walk(value)
                if isinstance(child, ast.Name)
            }
            if "ip_address" in values and names & peer_context_names:
                peer_names.update(targets(assignment))

        # Propagate simple list/alias assignments so ``argv = ["ping", peer_ip]``
        # and ``run_command(argv, ...)`` can be checked without interpreting code.
        for _ in range(len(assignments) + 1):
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                names = {
                    child.id
                    for child in ast.walk(value)
                    if isinstance(child, ast.Name)
                }
                if names & peer_names:
                    for target in targets(assignment):
                        if target not in peer_names:
                            peer_names.add(target)
                            changed = True
            if not changed:
                break

        def contains_peer_name(node: ast.AST) -> bool:
            return any(
                isinstance(child, ast.Name) and child.id in peer_names
                for child in ast.walk(node)
            )

        def contains_context_name(node: ast.AST) -> bool:
            return any(
                isinstance(child, ast.Name) and child.id in context_names
                for child in ast.walk(node)
            )

        def literal_subscript_key(node: ast.AST | None) -> str | None:
            if not isinstance(node, ast.Subscript):
                return None
            return _literal_str(node.slice)

        def has_declared_peer_context_path(peer_key: str) -> bool:
            for candidate in ast.walk(tree):
                if isinstance(candidate, ast.Subscript):
                    if literal_subscript_key(candidate) != peer_key:
                        continue
                    parent = candidate.value
                    if (
                        isinstance(parent, ast.Subscript)
                        and literal_subscript_key(parent) == "peers"
                        and contains_context_name(parent.value)
                    ):
                        return True
                if not isinstance(candidate, ast.Call):
                    continue
                if not (
                    isinstance(candidate.func, ast.Attribute)
                    and candidate.func.attr == "get"
                    and candidate.args
                    and _literal_str(candidate.args[0]) == peer_key
                ):
                    continue
                parent = candidate.func.value
                if not isinstance(parent, ast.Call):
                    continue
                if not (
                    isinstance(parent.func, ast.Attribute)
                    and parent.func.attr == "get"
                    and parent.args
                    and _literal_str(parent.args[0]) == "peers"
                    and contains_context_name(parent.func.value)
                ):
                    continue
                return True
            return False

        def looks_like_ip_or_cidr(value: str) -> bool:
            try:
                ipaddress.ip_interface(value)
            except ValueError:
                return False
            return True

        argv_command_by_name: dict[str, str] = {}
        for assignment in assignments:
            value = assignment.value
            if not isinstance(value, (ast.List, ast.Tuple)) or not value.elts:
                continue
            if contains_peer_name(value):
                for literal in value.elts[1:]:
                    if (
                        isinstance(literal, ast.Constant)
                        and isinstance(literal.value, str)
                        and looks_like_ip_or_cidr(literal.value)
                    ):
                        issues.append("peer ping 不得寫死其他 IP 或 CIDR")
            first = value.elts[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            for target in targets(assignment):
                argv_command_by_name[target] = first.value.strip().lower()

        peer_command_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func, aliases)
            if call_name not in {"run_command", "subprocess.run"}:
                continue
            if not any(contains_peer_name(argument) for argument in node.args):
                continue
            peer_command_calls += 1
            first_argument = node.args[0] if node.args else None
            valid_ping_argv = False
            if isinstance(first_argument, ast.Name):
                if argv_command_by_name.get(first_argument.id) == "ping":
                    valid_ping_argv = True
                else:
                    issues.append("peer IP 只能流入 ping argv，不得流向其他命令")
            elif not isinstance(first_argument, (ast.List, ast.Tuple)):
                issues.append("peer IP 必須流入可靜態確認的 ping argv list")
                continue
            else:
                first_argv_element = (
                    first_argument.elts[0] if first_argument.elts else None
                )
                valid_ping_argv = (
                    isinstance(first_argv_element, ast.Constant)
                    and str(first_argv_element.value).lower() == "ping"
                )
                if not valid_ping_argv:
                    issues.append("peer IP 只能流入 ping argv，不得流向其他命令")
                for literal in first_argument.elts[1:]:
                    if (
                        isinstance(literal, ast.Constant)
                        and isinstance(literal.value, str)
                        and looks_like_ip_or_cidr(literal.value)
                    ):
                        issues.append("peer ping 不得寫死其他 IP 或 CIDR")
            if any(contains_peer_name(keyword.value) for keyword in node.keywords):
                issues.append("peer IP 只能出現在 ping 的第一個 argv 參數")
            if not valid_ping_argv:
                continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not contains_peer_name(node):
                continue
            call_name = _call_name(node.func, aliases)
            if call_name in {"run_command", "subprocess.run"}:
                argument_nodes = list(node.args) + [
                    keyword.value for keyword in node.keywords
                ]
                if any(
                    contains_peer_name(argument) for argument in argument_nodes[1:]
                ):
                    issues.append("peer IP 只能流入 ping argv，不得流向其他參數")
                continue
            issues.append("peer IP 不得流向 ping 以外的 API、檔案或輸出")

        if not peer_names or peer_command_calls == 0:
            issues.append("找不到從 runtime context 到 ping argv 的 peer IP dataflow")

        for peer_key in expected_peers:
            if peer_key not in literal_strings:
                issues.append(f"腳本未限定宣告的 peer node_key：{peer_key}")
            if not has_declared_peer_context_path(peer_key):
                issues.append(
                    f"腳本未從 runtime_context.peers 讀取宣告的 peer node_key：{peer_key}"
                )

    deduped = list(dict.fromkeys(issues))
    for issue in deduped:
        fix_hints.append(
            {
                "type": "fix_peer_runtime_contract",
                "description": issue,
                "target": "peer_runtime_context",
                "required_pattern": "runtime_context.json -> peers[node_key].ip_address -> ping argv",
            }
        )
    return {
        "approved": not deduped,
        "blocked": bool(deduped),
        "risk_level": "high" if deduped else "low",
        "issues": deduped,
        "fix_hints": fix_hints,
    }
