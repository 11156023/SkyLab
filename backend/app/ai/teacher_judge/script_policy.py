"""Deterministic policy checks for Teacher Judge managed scripts."""

from __future__ import annotations

import ast
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

SENSITIVE_PATH_PATTERN = re.compile(
    r"(?:\.ssh(?:[/\\]|\b)|\.env(?:[/\\]|\b)|id_rsa\b|private[-_ ]key)",
    flags=re.IGNORECASE,
)

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


def _literal_path_text(node: ast.AST | None) -> str | None:
    if literal := _literal_str(node):
        return literal
    if isinstance(node, ast.Call) and node.args:
        return _literal_path_text(node.args[0])
    return None


def _pathlib_call_path_text(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Attribute):
        return None
    return _literal_path_text(node.func.value)


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
        method = _literal_str(node.args[0]).upper() if node.args else ""
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
    for pattern, message in DENY_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL):
            return message
    if SENSITIVE_PATH_PATTERN.search(normalized):
        return "禁止讀取敏感檔案或金鑰"

    tokens = re.split(r"\s+", normalized.strip())
    if not tokens:
        return None
    command = tokens[0]
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

    for pattern, message in DENY_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL):
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
                path_arg_index = 0 if call_name != "pathlib.Path.open" else None
                path_text = (
                    _literal_path_text(node.args[path_arg_index])
                    if path_arg_index is not None and node.args
                    else _pathlib_call_path_text(node)
                )
                if path_text and SENSITIVE_PATH_PATTERN.search(path_text):
                    issues.append("禁止讀取敏感檔案或金鑰")
                    fix_hints.append({"type": "remove_sensitive_path", "description": "禁止讀取敏感檔案或金鑰", "path": path_text})
                if _is_write_mode(_open_mode(node, mode_arg_index=mode_arg_index)):
                    issues.append("禁止以寫入模式開啟檔案")
                    fix_hints.append({"type": "remove_write_mode", "function": call_name, "mode": _open_mode(node, mode_arg_index=mode_arg_index)})
            if call_name in {"pathlib.Path.read_text", "pathlib.Path.read_bytes"}:
                path_text = _pathlib_call_path_text(node)
                if path_text and SENSITIVE_PATH_PATTERN.search(path_text):
                    issues.append("禁止讀取敏感檔案或金鑰")
                    fix_hints.append({"type": "remove_sensitive_path", "description": "禁止讀取敏感檔案或金鑰", "path": path_text})
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
