"""Typed Teacher Judge Check Plan validation and deterministic script compiler.

The Finalizer owns interpretation of teacher requirements. This module owns the
machine contract after that interpretation: it accepts only typed collectors
and assertions, emits one stable script per executor node, and never calls an
LLM.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.script_policy import (
    _dangerous_command_issue,
    check_script_policy,
)
from app.ai.teacher_judge.script_quality_validator import check_script_quality

CHECK_PLAN_SCHEMA_VERSION = "teacher_judge_check_plan.v1"
DETERMINISTIC_COMPILER_VERSION = "teacher_judge_compiler.v2"
RESULT_SCHEMA_VERSION = "teacher_judge_result.v1"
PEER_IP_TOKEN = "{{peer.ip}}"
_ASSERTION_TYPES_BY_COLLECTOR = {
    "command": {"returncode_equals", "text_equals", "text_contains", "number_compare", "json_path_equals"},
    "file_text": {"text_equals", "text_contains", "number_compare", "json_path_equals"},
    "file_stat": {"exists"},
    "localhost_http": {"text_equals", "text_contains", "number_compare", "json_path_equals"},
    "peer_ping": {"returncode_equals", "text_equals", "text_contains", "number_compare", "json_path_equals"},
}


class CheckPlanContractError(ValueError):
    """A typed plan cannot safely be compiled."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        message = "; ".join(
            str(issue.get("message") or "invalid Check Plan") for issue in issues
        )
        super().__init__(message)


def _issue(item_id: str | None, message: str, *, step_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"message": message}
    if item_id:
        result["item_id"] = item_id
    if step_id:
        result["step_id"] = step_id
    return result


def _is_safe_command_argv(argv: list[str]) -> bool:
    if not argv or any(not isinstance(part, str) or not part.strip() for part in argv):
        return False
    if argv[0].strip().lower() in {
        "bash",
        "sh",
        "zsh",
        "fish",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
    }:
        return False
    joined = " ".join(argv).lower()
    return not any(token in joined for token in ("|", ">", "<", "$(", "`", "&&", ";"))


# command collector 的 argv[0] 白名單：腳本以 root 在每台學生機上跑，argv 又是
# LLM 從老師上傳的文件解析出來的（文件可能由第三方提供），黑名單擋不住
# curl -o / useradd / crontab 這類「合法但有副作用」的指令。這裡只放真正
# 唯讀的診斷工具；需要新的收集方式時在這裡加，而不是放寬成黑名單。
_READ_ONLY_COMMANDS = frozenset(
    {
        # 檔案／目錄觀察
        "ls", "cat", "head", "tail", "stat", "file", "wc", "grep", "egrep", "fgrep",
        "find", "du", "df", "readlink", "realpath", "basename", "dirname", "test",
        "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "diff", "cmp",
        "sort", "uniq", "cut", "tr", "awk", "sed", "jq", "yq", "xmllint", "column",
        "strings", "od", "hexdump", "base64", "printf", "echo", "true", "false",
        # 系統／程序狀態
        "uname", "hostname", "hostnamectl", "uptime", "id", "whoami", "who", "w",
        "date", "env", "printenv", "ps", "pgrep", "top", "free", "vmstat", "iostat",
        "lscpu", "lsblk", "lsmod", "lspci", "lsusb", "dmesg", "journalctl", "last",
        "getent", "getcap", "lsof", "nproc", "timedatectl", "loginctl",
        # 網路觀察
        "ss", "netstat", "ip", "ifconfig", "ping", "ping6", "traceroute", "tracepath",
        "dig", "nslookup", "host", "curl", "wget", "nc", "ncat", "arp",
        "route", "iptables", "nft", "ufw", "resolvectl",
        # 服務／套件狀態（子命令另外限制）
        "systemctl", "service", "docker", "podman", "dpkg", "dpkg-query", "apt",
        "apt-cache", "rpm", "yum", "dnf", "pip", "pip3", "npm", "git", "snap",
        # 直譯器：查版本，或執行學生作業檔看輸出（機器是學生自己的，執行檔案
        # 不會擴大攻擊面；inline code／-m 仍禁止）
        "python", "python3", "python.exe", "py", "node", "nodejs", "java", "go",
        "ruby", "perl", "php",
        "gcc", "g++", "make", "cmake", "rustc", "cargo", "psql", "pg_isready",
        "mysql", "mariadb", "redis-cli", "nginx", "apache2ctl", "httpd", "sshd",
        "openssl", "ssh-keygen",
    }
)

# 白名單內但帶副作用的子命令／旗標
_SYSTEMCTL_READ_SUBCOMMANDS = frozenset(
    {"status", "is-active", "is-enabled", "is-failed", "show", "list-units",
     "list-unit-files", "list-timers", "cat", "list-dependencies"}
)
_DOCKER_READ_SUBCOMMANDS = frozenset(
    {"ps", "images", "inspect", "logs", "version", "info", "stats", "port",
     "top", "network", "volume", "compose"}
)
_DOCKER_READ_THIRD = {"network": {"ls", "inspect"}, "volume": {"ls", "inspect"},
                      "compose": {"ps", "config", "version", "ls"}}
_PACKAGE_READ_SUBCOMMANDS = frozenset(
    {"list", "show", "search", "policy", "info", "--version", "-V", "freeze",
     "ls", "view", "version", "-l", "-s", "-q", "-qa", "-qi", "status", "cat"}
)
_GIT_READ_SUBCOMMANDS = frozenset(
    {"status", "log", "show", "diff", "branch", "rev-parse", "remote", "config",
     "ls-files", "describe", "tag", "--version"}
)
_NETWORK_FETCH_WRITE_FLAGS = frozenset(
    {"-o", "--output", "-O", "--remote-name", "--output-document", "-T",
     "--upload-file", "-d", "--data", "--data-binary", "--data-raw", "-F",
     "--form", "-X", "--request", "--post-data", "--post-file"}
)
_NC_DENY_FLAGS = frozenset({"-e", "-c", "--exec", "--sh-exec", "-l", "--listen"})
_IP_READ_SUBCOMMANDS = frozenset({"addr", "address", "a", "link", "l", "route", "r",
                                  "neigh", "n", "-4", "-6", "-br", "-brief", "-o", "-s"})
_FW_READ_FLAGS = frozenset({"-L", "--list", "-S", "--list-rules", "-n", "-v", "-t",
                            "--line-numbers", "list", "status", "ruleset"})


def _command_argv_issue(argv: list[str]) -> str | None:
    if not _is_safe_command_argv(argv):
        return "command collector argv 含 shell launcher 或控制字元"
    command = argv[0].replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    args = [part.strip() for part in argv[1:]]
    lowered = [part.lower() for part in args]

    if command not in _READ_ONLY_COMMANDS:
        return f"command collector 只允許唯讀／診斷命令（{command} 不在白名單）"

    if command in {"python", "python3", "python.exe", "py", "node", "nodejs", "perl", "ruby", "php"}:
        if any(flag in {"-c", "--command", "-e", "--eval", "-r", "--exec", "-m"} for flag in lowered):
            return "command collector 不允許 interpreter inline code 或 eval"
    if command == "sed" and any(flag == "-i" or flag.startswith("-i") for flag in lowered):
        return "command collector 不允許原地修改檔案"
    if command == "find" and any(flag in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for flag in lowered):
        return "command collector 不允許 find 執行或刪除"
    if command in {"awk"} and any("system(" in part or "getline" in part for part in lowered):
        return "command collector 不允許 awk 執行外部程式"
    if command in {"tee", "dd"}:
        return "command collector 只允許唯讀／診斷命令"
    if command in {"systemctl", "service"}:
        sub = next((part for part in lowered if not part.startswith("-")), "")
        if command == "service":
            sub = lowered[1] if len(lowered) > 1 else ""
        if sub not in _SYSTEMCTL_READ_SUBCOMMANDS:
            return "command collector 只允許查詢服務狀態，不允許啟停或啟用服務"
    if command in {"docker", "podman"}:
        sub = lowered[0] if lowered else ""
        if sub not in _DOCKER_READ_SUBCOMMANDS:
            return "command collector 只允許唯讀的容器查詢子命令"
        if sub in _DOCKER_READ_THIRD:
            third = lowered[1] if len(lowered) > 1 else ""
            if third not in _DOCKER_READ_THIRD[sub]:
                return "command collector 只允許唯讀的容器查詢子命令"
        if sub == "logs" and any(flag in {"-f", "--follow"} for flag in lowered):
            return "command collector 不允許持續跟隨的 docker logs"
    if command in {"apt", "apt-cache", "dpkg", "dpkg-query", "rpm", "yum", "dnf",
                   "pip", "pip3", "npm", "snap"}:
        sub = lowered[0] if lowered else ""
        if sub not in _PACKAGE_READ_SUBCOMMANDS:
            return "command collector 只允許查詢套件，不允許安裝或移除"
    if command == "git":
        sub = lowered[0] if lowered else ""
        if sub not in _GIT_READ_SUBCOMMANDS:
            return "command collector 只允許唯讀 Git 子命令"
    if command in {"curl", "wget"}:
        if any(flag in _NETWORK_FETCH_WRITE_FLAGS or flag.startswith("--output") for flag in lowered):
            return "command collector 不允許把下載內容寫入檔案或送出資料"
        if not any(part.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]",
                                     "https://127.0.0.1", "https://localhost")) for part in lowered):
            return "command collector 的 HTTP 探測只允許 localhost"
    if command in {"nc", "ncat"}:
        if any(flag in _NC_DENY_FLAGS for flag in lowered):
            return "command collector 不允許 nc 執行程式或監聽"
        if not any(flag in {"-z", "-zv", "-vz"} for flag in lowered):
            return "command collector 只允許 nc -z 做連接埠探測"
    if command == "ip":
        sub = next((part for part in lowered if not part.startswith("-")), "")
        if sub not in _IP_READ_SUBCOMMANDS or any(part in {"add", "del", "set", "flush", "replace", "change"} for part in lowered):
            return "command collector 只允許查詢網路設定"
    if command in {"iptables", "nft", "ufw"}:
        if not any(flag in _FW_READ_FLAGS for flag in lowered) or any(
            part in {"-A", "-I", "-D", "-F", "-X", "-P", "add", "delete", "flush",
                     "insert", "allow", "deny", "enable", "disable", "reset"}
            for part in args
        ):
            return "command collector 只允許列出防火牆規則"
    if command in {"psql", "mysql", "mariadb", "redis-cli"} and any(
        flag in {"-c", "--command", "-e", "--execute", "-f", "--file"} for flag in lowered
    ):
        return "command collector 不允許對資料庫送出語句"
    if command in {"nginx", "apache2ctl", "httpd", "sshd"} and not any(
        flag in {"-t", "-T", "-v", "-V", "configtest"} for flag in args
    ):
        return "command collector 只允許用伺服器程式檢查設定或查版本"
    if command == "openssl" and lowered and lowered[0] not in {"version", "x509", "s_client", "verify", "rsa", "ec"}:
        return "command collector 只允許用 openssl 檢視憑證"
    if command == "ssh-keygen" and not any(flag in {"-l", "-lf", "-y", "-e"} for flag in lowered):
        return "command collector 只允許用 ssh-keygen 檢視指紋"
    return _dangerous_command_issue(" ".join(argv))


def canonicalize_check_plan(
    analysis: TeacherJudgeRubricAnalysis,
    *,
    target_node_key: str | None = None,
    require_target_node: bool = True,
) -> dict[str, Any]:
    """Validate and serialize a complete typed plan.

    Flat legacy steps are intentionally rejected here. They remain readable by
    Chat and old Artifact readers, but a new Save/Create must be finalized into
    this typed contract before a script can be written.
    """

    issues: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    seen_step_ids_by_node: dict[str, set[str]] = {}
    for item in analysis.items:
        item_id = str(item.id).strip()
        if not item_id:
            issues.append(_issue(None, "檢查項目缺少穩定 id"))
            continue
        if item_id in seen_item_ids:
            issues.append(_issue(item_id, "檢查項目 id 重複"))
            continue
        seen_item_ids.add(item_id)
        node_key = str(item.target_node_key or "").strip()
        if require_target_node and not node_key:
            issues.append(_issue(item_id, "缺少 target_node_key"))
            continue
        if target_node_key is not None and node_key != target_node_key:
            continue
        if item.detectable != "auto":
            issues.append(_issue(item_id, "只有 detectable=auto 的項目可以進入腳本"))
            continue
        if item.peer_node_key and item.peer_node_key == node_key:
            issues.append(_issue(item_id, "peer_node_key 不可等於 target_node_key"))
        if not item.check_steps:
            issues.append(_issue(item_id, "缺少 typed check_steps"))
            continue

        steps: list[dict[str, Any]] = []
        seen_step_ids = seen_step_ids_by_node.setdefault(node_key, set())
        for step in item.check_steps:
            step_id = str(step.id or "").strip()
            if step.collector is None:
                issues.append(
                    _issue(
                        item_id,
                        "check step 仍是 flat legacy shape，需由 Finalizer 轉成 collector",
                        step_id=step_id or None,
                    )
                )
                continue
            if not step_id:
                issues.append(_issue(item_id, "typed check step 缺少 id"))
                continue
            if step_id in seen_step_ids:
                issues.append(_issue(item_id, "同一 node 內 check step id 重複", step_id=step_id))
                continue
            seen_step_ids.add(step_id)
            collector = step.collector.model_dump(mode="json")
            assertion = step.assertion.model_dump(mode="json") if step.assertion else None
            collector_type = str(collector.get("type") or "")
            if collector_type == "command":
                argv = list(collector.get("argv") or [])
                command_issue = _command_argv_issue(argv)
                if command_issue:
                    issues.append(
                        _issue(item_id, command_issue, step_id=step_id)
                    )
                peer_parts = [part for part in argv if PEER_IP_TOKEN in part]
                if peer_parts and (not item.peer_node_key or any(part != PEER_IP_TOKEN for part in peer_parts)):
                    issues.append(
                        _issue(
                            item_id,
                            f"{PEER_IP_TOKEN} 必須是有 peer_node_key 的完整 argv 元素",
                            step_id=step_id,
                        )
                    )
            if item.judgement_mode == "ai" and assertion is None:
                issues.append(_issue(item_id, "system judgement 必須提供 assertion", step_id=step_id))
            if item.judgement_mode == "teacher" and assertion is not None:
                issues.append(_issue(item_id, "teacher judgement 不可帶 assertion", step_id=step_id))
            if assertion is not None and assertion.get("type") not in _ASSERTION_TYPES_BY_COLLECTOR.get(
                collector_type, set()
            ):
                issues.append(
                    _issue(
                        item_id,
                        f"{collector_type} collector 不支援 {assertion.get('type')} assertion",
                        step_id=step_id,
                    )
                )
            if collector_type == "peer_ping" and not item.peer_node_key:
                issues.append(_issue(item_id, "peer_ping 必須指定 peer_node_key", step_id=step_id))
            elif item.peer_node_key and collector_type == "peer_ping":
                pass
            elif item.peer_node_key and collector_type == "command":
                argv = list(collector.get("argv") or [])
                if PEER_IP_TOKEN not in argv:
                    issues.append(
                        _issue(
                            item_id,
                            f"peer command 必須以 {PEER_IP_TOKEN} 作為完整 argv 元素",
                            step_id=step_id,
                        )
                    )
            elif item.peer_node_key:
                issues.append(_issue(item_id, "peer item 目前只支援 peer_ping 或帶 peer token 的 command", step_id=step_id))
            steps.append(
                {
                    "id": step_id,
                    "title": str(step.title or item.title).strip()[:240],
                    "collector": collector,
                    **({"assertion": assertion} if assertion is not None else {}),
                    "judgement_mode": "system" if item.judgement_mode == "ai" else "teacher",
                    "peer_node_key": item.peer_node_key,
                }
            )

        if steps:
            plan_items.append(
                {
                    "id": item_id,
                    "title": item.title,
                    "target_node_key": node_key,
                    "peer_node_key": item.peer_node_key,
                    "judgement_mode": "system" if item.judgement_mode == "ai" else "teacher",
                    "check_steps": steps,
                }
            )

    if not plan_items and not issues:
        issues.append(_issue(None, "沒有可編譯的 typed Check Plan"))
    if issues:
        raise CheckPlanContractError(issues)
    return {
        "schema_version": CHECK_PLAN_SCHEMA_VERSION,
        "compiler_version": DETERMINISTIC_COMPILER_VERSION,
        "items": plan_items,
    }


def _json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _python_literal(value: Any) -> str:
    """Render a Python source literal; JSON `null` is not valid Python."""

    return "None" if value is None else repr(value)


def _render_step_function(index: int, item_index: int, step_index: int, step: dict[str, Any]) -> str:
    check_id = _json_literal(step["id"])
    title = _json_literal(step["title"])
    collector = step["collector"]
    collector_type = collector["type"]
    lines = [
        f"def _collect_{index}():",
        f"    step = PLAN['items'][{item_index}]['check_steps'][{step_index}]",
        "    try:",
    ]
    if collector_type == "command":
        peer_key = step.get("peer_node_key")
        lines.extend(
            [
                f"        argv = json.loads({_json_literal(collector['argv'])!r})",
                "        peer_ip = None",
                "        if '{{peer.ip}}' in argv:",
                "            context = json.loads(Path('runtime_context.json').read_text(encoding='utf-8'))",
                f"            peer = context['peers'][{_python_literal(peer_key)}]",
                "            resolution_status = peer['resolution_status']",
                "            ip_address = peer['ip_address']",
                "            if resolution_status != 'ready' or not ip_address:",
                f"                errors.append({_json_literal(step['id'] + ': peer_unavailable')})",
                f"                return record_check({check_id}, {title}, 'unknown', 'peer unavailable', {{'error_code': 'peer_unavailable'}})",
                "            argv = [ip_address if value == '{{peer.ip}}' else value for value in argv]",
                "        if not command_available(argv[0]):",
                f"            errors.append({_json_literal(step['id'] + ': command_missing')})",
                f"            return record_check({check_id}, {title}, 'unknown', 'command unavailable', {{'error_code': 'command_missing'}})",
                f"        collected = run_command(argv, {_python_literal(collector.get('cwd'))}, {int(collector.get('timeout_seconds', 30))})",
                f"        collected['raw']['argv'] = json.loads({_json_literal(collector['argv'])!r})",
            ]
        )
    elif collector_type == "file_text":
        lines.extend(
            [
                f"        path = Path({_json_literal(collector['path'])})",
                "        with path.open('rb') as handle:",
                "            if handle.seekable():",
                "                handle.seek(0, 2)",
                "                size = handle.tell()",
                f"                handle.seek(max(0, size - {int(collector.get('max_chars', 12000)) * 4}))",
                f"            raw_bytes = handle.read({int(collector.get('max_chars', 12000)) * 4 + 1})",
                "        text = raw_bytes.decode('utf-8', errors='replace')",
                f"        if {_json_literal(collector.get('read_mode', 'full'))} == 'tail':",
                f"            text = '\\n'.join(text.splitlines()[-{int(collector.get('lines') or 1):}])",
                f"        elif {_json_literal(collector.get('read_mode', 'full'))} == 'head':",
                f"            text = '\\n'.join(text.splitlines()[:{int(collector.get('lines') or 1):}])",
                f"        collected = {{'ok': True, 'value': text[:{int(collector.get('max_chars', 12000))}], 'raw': {{'text': text[:{int(collector.get('max_chars', 12000))}]}}}}",
            ]
        )
    elif collector_type == "file_stat":
        lines.extend(
            [
                f"        path = Path({_json_literal(collector['path'])})",
                "        exists = path.exists()",
                "        collected = {'ok': True, 'value': exists, 'raw': {'exists': exists}}",
            ]
        )
    elif collector_type == "localhost_http":
        method = collector.get("method", "GET")
        request = (
            f"urllib.request.Request({_json_literal(collector['url'])}, method={_json_literal(method)})"
        )
        lines.extend(
            [
                f"        request = {request}",
                f"        with urllib.request.urlopen(request, timeout={int(collector.get('timeout_seconds', 10))}) as response:",
                f"            body = response.read({int(collector.get('max_chars', 12000)) + 1})",
                "        text = body.decode('utf-8', errors='replace')",
                f"        collected = {{'ok': True, 'value': text[:{int(collector.get('max_chars', 12000))}], 'status_code': getattr(response, 'status', None), 'raw': {{'status_code': getattr(response, 'status', None), 'text': text[:{int(collector.get('max_chars', 12000))}]}}}}",
            ]
        )
    elif collector_type == "peer_ping":
        peer_key = step.get("peer_node_key")
        lines.extend(
            [
                "        context = json.loads(Path('runtime_context.json').read_text(encoding='utf-8'))",
                f"        peer = context['peers'][{_python_literal(peer_key)}]",
                "        resolution_status = peer['resolution_status']",
                "        ip_address = peer['ip_address']",
                "        if resolution_status != 'ready' or not ip_address:",
                f"            errors.append({_json_literal(step['id'] + ': peer_unavailable')})",
                f"            return record_check({check_id}, {title}, 'unknown', 'peer unavailable', {{'error_code': 'peer_unavailable'}})",
                "        if not command_available('ping'):",
                f"            errors.append({_json_literal(step['id'] + ': command_missing')})",
                f"            return record_check({check_id}, {title}, 'unknown', 'ping unavailable', {{'error_code': 'command_missing'}})",
                "        argv = ['ping', '-c', '1', ip_address]",
                f"        collected = run_command(argv, None, {int(collector.get('timeout_seconds', 10))})",
                "        collected['raw']['argv'] = ['ping', '-c', '1', '{{peer.ip}}']",
            ]
        )
    else:
        lines.append("        collected = {'ok': False, 'error_code': 'unsupported_collector'}")
    lines.extend(
        [
            "        status, evidence, raw = judge(step, collected)",
            "        if raw.get('error_code'):",
            f"            errors.append({_json_literal(step['id'])} + ': ' + str(raw['error_code']))",
            f"        return record_check({check_id}, {title}, status, evidence, raw)",
            "    except FileNotFoundError as exc:",
            "        message = '找不到檔案或目錄：' + str(exc)",
            f"        errors.append({_json_literal(step['id'])} + ': path_not_found')",
            f"        return record_check({check_id}, {title}, 'unknown', message, {{'error_code': 'path_not_found', 'error_message': message, 'error': str(exc)}})",
            "    except Exception as exc:",
            f"        errors.append({_json_literal(step['id'])} + ': ' + str(exc))",
            "        message = '收集資料時發生未預期錯誤：' + str(exc)",
            f"        return record_check({check_id}, {title}, 'unknown', message, {{'error_code': 'runtime_exception', 'error_message': message, 'error': str(exc)}})",
        ]
    )
    return "\n".join(lines)


def _render_script(plan: dict[str, Any]) -> str:
    functions: list[str] = []
    calls: list[str] = []
    index = 0
    for item_index, item in enumerate(plan["items"]):
        for step_index, step in enumerate(item["check_steps"]):
            functions.append(_render_step_function(index, item_index, step_index, step))
            calls.append(f"checks.append(_collect_{index}())")
            index += 1
    plan_json = _json_literal(plan)
    return (
        "import json\n"
        "import platform\n"
        "import subprocess\n"
        "import urllib.request\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n\n"
        f"PLAN = json.loads({plan_json!r})\n"
        "errors: list[str] = []\n\n"
        "def truncate_output(value, limit=4000):\n"
        "    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)\n"
        "    return text[:limit]\n\n"
        "def record_check(check_id, title, status, evidence, raw=''):\n"
        "    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)\n"
        "    return {'id': check_id, 'title': title, 'status': status, 'evidence': truncate_output(evidence), 'raw': truncate_output(raw_text)}\n\n"
        "def command_available(command):\n"
        "    import shutil\n"
        "    return bool(shutil.which(command))\n\n"
        "def run_command(argv, cwd, timeout):\n"
        "    command_raw = {'cwd': cwd, 'timeout_seconds': timeout}\n"
        "    try:\n"
        "        completed = subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False)\n"
        "        return {'ok': True, 'value': completed.stdout, 'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode, 'raw': {**command_raw, 'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode}}\n"
        "    except subprocess.TimeoutExpired as exc:\n"
        "        message = f'指令執行逾時（{timeout} 秒）'\n"
        "        return {'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': 'command_timeout', 'error_message': message, 'raw': {**command_raw, 'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': 'command_timeout', 'error_message': message}}\n"
        "    except OSError as exc:\n"
        "        if cwd and (isinstance(exc, FileNotFoundError) or getattr(exc, 'winerror', None) == 267):\n"
        "            error_code = 'working_directory_not_found'\n"
        "            message = f'工作目錄不存在：{cwd}'\n"
        "        else:\n"
        "            error_code = 'command_exception'\n"
        "            message = '指令無法執行：' + str(exc)\n"
        "        return {'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': error_code, 'error_message': message, 'raw': {**command_raw, 'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': error_code, 'error_message': message, 'error': str(exc)}}\n"
        "    except Exception as exc:\n"
        "        message = '指令無法執行：' + str(exc)\n"
        "        return {'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': 'command_exception', 'error_message': message, 'raw': {**command_raw, 'stdout': '', 'stderr': str(exc), 'returncode': None, 'error_code': 'command_exception', 'error_message': message, 'error': str(exc)}}\n\n"
        "def command_failure_message(collected):\n"
        "    returncode = collected.get('returncode')\n"
        "    detail = str(collected.get('stderr') or collected.get('stdout') or '').strip().splitlines()\n"
        "    message = f'指令執行失敗（returncode {returncode}）'\n"
        "    return message + (f'：{detail[0][:300]}' if detail else '')\n\n"
        "def _json_path(value, path):\n"
        "    current = value\n"
        "    for part in path.lstrip('$.').split('.'):\n"
        "        if not part:\n"
        "            continue\n"
        "        if not isinstance(current, dict) or part not in current:\n"
        "            return None\n"
        "        current = current[part]\n"
        "    return current\n\n"
        "def judge(step, collected):\n"
        "    if not collected.get('ok'):\n"
        "        return 'unknown', str(collected.get('error_message') or collected.get('error') or collected.get('error_code') or '收集失敗'), dict(collected.get('raw') or collected)\n"
        "    assertion = step.get('assertion') or {}\n"
        "    kind = assertion.get('type')\n"
        "    actual = collected.get('value')\n"
        "    expected = assertion.get('expected')\n"
        "    returncode = collected.get('returncode')\n"
        "    if returncode is not None and returncode != 0 and (step.get('judgement_mode') == 'teacher' or kind != 'returncode_equals'):\n"
        "        message = command_failure_message(collected)\n"
        "        raw = dict(collected.get('raw') or collected)\n"
        "        raw.update({'error_code': 'command_failed', 'error_message': message})\n"
        "        return 'unknown', message, raw\n"
        "    if step.get('judgement_mode') == 'teacher':\n"
        "        return 'collected', str(collected.get('value') or ''), dict(collected.get('raw') or collected)\n"
        "    if kind == 'returncode_equals':\n"
        "        passed = collected.get('returncode') == expected\n"
        "    elif kind == 'text_equals':\n"
        "        value = str(actual or '')\n"
        "        if assertion.get('normalize', 'strip') == 'strip':\n"
        "            value = value.strip()\n"
        "        passed = value == expected\n"
        "    elif kind == 'text_contains':\n"
        "        value = str(actual or '')\n"
        "        if assertion.get('normalize') == 'strip':\n"
        "            value = value.strip()\n"
        "        passed = expected in value\n"
        "    elif kind == 'exists':\n"
        "        passed = actual is expected\n"
        "    elif kind == 'number_compare':\n"
        "        number = float(actual)\n"
        "        operators = {'eq': number == expected, 'ne': number != expected, 'gt': number > expected, 'gte': number >= expected, 'lt': number < expected, 'lte': number <= expected}\n"
        "        passed = operators.get(assertion.get('operator'), False)\n"
        "    elif kind == 'json_path_equals':\n"
        "        parsed = actual if isinstance(actual, (dict, list)) else json.loads(str(actual))\n"
        "        passed = _json_path(parsed, str(assertion.get('path') or '')) == expected\n"
        "    else:\n"
        "        return 'unknown', 'unsupported assertion', {'error_code': 'unsupported_assertion'}\n"
        "    raw = dict(collected.get('raw') or collected)\n"
        "    if not passed and kind == 'returncode_equals':\n"
        "        message = f'指令檢查未通過：預期 returncode {expected}，實際為 {returncode}'\n"
        "        raw.update({'error_code': 'unexpected_returncode', 'error_message': message})\n"
        "        return 'fail', message, raw\n"
        "    return ('pass' if passed else 'fail'), str(actual), raw\n\n"
        + "\n\n".join(functions)
        + "\n\ndef main():\n    checks = []\n"
        + "\n    " + "\n    ".join(calls)
        + "\n    result = {\n        'schema_version': 'teacher_judge_result.v1',\n        'metadata': {'timestamp': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform()},\n        'summary': f'{len(checks)} checks collected',\n        'checks': checks,\n        'errors': errors,\n    }\n    print(json.dumps(result, ensure_ascii=False))\n\nif __name__ == '__main__':\n    main()\n"
    )


def compile_check_plan(
    analysis: TeacherJudgeRubricAnalysis,
    *,
    target_node_key: str,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return script, deterministic policy, review metadata and canonical plan."""

    plan = canonicalize_check_plan(analysis, target_node_key=target_node_key)
    script_content = _render_script(plan)
    policy = dict(check_script_policy(script_content))
    quality = dict(check_script_quality(script_content))
    if not policy.get("approved") or not quality.get("approved"):
        raise CheckPlanContractError(
            [
                {"message": "deterministic compiler 輸出的腳本未通過靜態安全契約", "policy": policy, "quality": quality}
            ]
        )
    policy.update(
        {
            "source": "deterministic_compiler",
            "compiler_version": DETERMINISTIC_COMPILER_VERSION,
            "quality_approved": True,
            "coverage": {
                "approved": True,
                "mappings": [
                    {"check_id": step["id"], "rubric_item_ids": [item["id"]]}
                    for item in plan["items"]
                    for step in item["check_steps"]
                ],
                "available_check_ids": [
                    step["id"] for item in plan["items"] for step in item["check_steps"]
                ],
                "uncovered_items": [],
                "issues": [],
            },
        }
    )
    review = {
        "approved": True,
        "mode": "deterministic_compiler",
        "compiler_version": DETERMINISTIC_COMPILER_VERSION,
        "issues": [],
    }
    return script_content, policy, review, plan


__all__ = [
    "CHECK_PLAN_SCHEMA_VERSION",
    "CheckPlanContractError",
    "DETERMINISTIC_COMPILER_VERSION",
    "PEER_IP_TOKEN",
    "canonicalize_check_plan",
    "compile_check_plan",
]
