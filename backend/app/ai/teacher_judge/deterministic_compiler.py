"""Typed Teacher Judge Check Plan validation and deterministic script compiler.

The Finalizer owns interpretation of teacher requirements. This module owns the
machine contract after that interpretation: it accepts only typed collectors
and assertions, emits one stable script per executor node, and never calls an
LLM.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any
from urllib.parse import urlparse

from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.script_policy import (
    PEER_IP_TOKEN,
    SHELL_LAUNCHERS,
    check_script_policy,
    dangerous_command_issue,
)
from app.ai.teacher_judge.script_quality_validator import check_script_quality

CHECK_PLAN_SCHEMA_VERSION = "teacher_judge_check_plan.v1"
DETERMINISTIC_COMPILER_VERSION = "teacher_judge_compiler.v2"
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
    if argv[0].strip().lower() in SHELL_LAUNCHERS:
        return False
    joined = " ".join(argv).lower()
    return not any(token in joined for token in ("|", ">", "<", "$(", "`", "&&", ";"))


# command collector 的 argv[0] 白名單：腳本以 root 在每台學生機上跑，argv 又是
# LLM 從老師上傳的文件解析出來的（文件可能由第三方提供），黑名單擋不住
# curl -o / useradd / crontab 這類「合法但有副作用」的指令。這裡只放真正
# 唯讀的診斷工具；需要新的收集方式時在這裡加，而不是放寬成黑名單。
# 白名單內的指令若有會執行其他程式、寫檔或改系統狀態的旗標／子命令，
# 由下面 _command_argv_issue 逐一限制（旗標比對一律區分大小寫、會拆開
# -sXPOST 這種合併寫法）。
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
        # 建置工具只允許查版本（go 另外允許 go run <file>.go）
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
# apt / yum / pip / npm / snap：子命令必須是第一個參數（前面不可夾旗標，
# 否則 `yum -q install` 會把 -q 當成子命令）
_PACKAGE_READ_SUBCOMMANDS = frozenset(
    {"list", "show", "search", "policy", "info", "freeze", "ls", "view",
     "version", "status", "cat"}
)
_VERSION_ONLY_ARGS = (["--version"], ["-V"], ["-v"], ["version"], ["-dumpversion"])
_DPKG_READ_ACTIONS = frozenset(
    {"-l", "--list", "-s", "--status", "-L", "--listfiles", "-S", "--search",
     "-p", "--print-avail", "-W", "--show", "--get-selections",
     "--print-architecture", "--print-foreign-architectures", "--version",
     "-V", "--verify", "--audit", "-C"}
)
_DPKG_WRITE_ACTIONS = frozenset(
    {"-i", "--install", "-r", "--remove", "-P", "--purge", "--unpack",
     "--configure", "--triggers-only", "--set-selections", "--clear-selections",
     "--update-avail", "--merge-avail", "--clear-avail", "--forget-old-unavail",
     "--add-architecture", "--remove-architecture", "-A", "--record-avail"}
)
_RPM_WRITE_ACTIONS = frozenset(
    {"-i", "--install", "-U", "--upgrade", "-F", "--freshen", "-e", "--erase",
     "--reinstall", "--import", "--rebuilddb", "--initdb", "--setperms",
     "--setugids", "--restore", "--addsign", "--resign", "--delsign"}
)
# branch／tag／remote／config 也能建立或修改設定，只在列出／讀取的寫法下
# 放行（見 _git_listing_issue）
_GIT_READ_SUBCOMMANDS = frozenset(
    {"status", "log", "show", "diff", "rev-parse", "ls-files", "describe",
     "--version", "version", "shortlog", "blame", "ls-tree", "cat-file",
     "rev-list", "show-ref", "for-each-ref"}
)
_GIT_LISTING_SUBCOMMANDS = frozenset({"branch", "tag", "remote", "config"})
_GIT_BRANCH_LIST_FLAGS = frozenset(
    {"-a", "-r", "-l", "--list", "-v", "-vv", "--show-current", "--all",
     "--remotes", "--verbose", "--no-color", "--contains", "--merged",
     "--no-merged"}
)
_GIT_BRANCH_COMMIT_FLAGS = frozenset({"--contains", "--merged", "--no-merged"})
_GIT_LIST_PREFIX_FLAGS = ("--format=", "--sort=", "--contains=", "--merged=",
                          "--no-merged=")
_GIT_CONFIG_READ_FLAGS = frozenset(
    {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l"}
)
_GIT_CONFIG_WRITE_FLAGS = frozenset(
    {"--add", "--unset", "--unset-all", "--replace-all", "--rename-section",
     "--remove-section", "--edit", "-e"}
)
_GIT_CONFIG_VALUE_FLAGS = frozenset({"-f", "--file", "--blob", "--type", "--default"})
# git 2.46 起的 `git config <動作>` 寫法中會寫入或開編輯器的動作
_GIT_CONFIG_WRITE_ACTIONS = frozenset(
    {"set", "unset", "rename-section", "remove-section", "edit"}
)
_NC_DENY_LONG = frozenset(
    {"--exec", "--sh-exec", "--lua-exec", "--listen", "--output",
     "--append-output", "--keep-open", "--broker", "--chat"}
)
_IP_READ_SUBCOMMANDS = frozenset({"addr", "address", "a", "link", "l", "route", "r",
                                  "neigh", "n", "-4", "-6", "-br", "-brief", "-o", "-s"})
_FW_READ_FLAGS = frozenset({"-L", "--list", "-S", "--list-rules", "-n", "-v", "-t",
                            "--line-numbers", "list", "status", "ruleset"})
_FW_WRITE_PARTS = frozenset(
    {"-A", "-I", "-D", "-F", "-X", "-P", "-Z", "-N", "-E", "-R", "--append",
     "--insert", "--delete", "--flush", "--delete-chain", "--policy", "--zero",
     "--new-chain", "--rename-chain", "--replace", "add", "delete", "flush",
     "insert", "replace", "create", "destroy", "rename", "import", "allow",
     "deny", "reject", "limit", "route", "default", "reload", "logging",
     "prepend", "enable", "disable", "reset"}
)
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# curl／wget 旗標白名單（其餘旗標一律拒絕）
_CURL_SHORT_FLAGS = "sSfIiLkvgGNq46"
_CURL_SHORT_VALUES = "mwHA"
_CURL_LONG_FLAGS = frozenset(
    {"--silent", "--show-error", "--fail", "--fail-with-body", "--head",
     "--include", "--location", "--insecure", "--verbose", "--globoff",
     "--compressed", "--ipv4", "--ipv6", "--http1.0", "--http1.1", "--http2",
     "--no-progress-meter", "--no-buffer", "--no-keepalive", "--path-as-is",
     "--progress-bar", "--raw", "--tcp-nodelay", "--get", "--basic", "--digest"}
)
_CURL_LONG_VALUES = frozenset(
    {"--max-time", "--connect-timeout", "--write-out", "--header",
     "--user-agent", "--retry", "--retry-delay", "--retry-max-time",
     "--max-redirs", "--url", "--user", "--cacert", "--noproxy"}
)
_WGET_SHORT_FLAGS = "qSv46"
_WGET_SHORT_VALUES = "OTt"
_WGET_LONG_FLAGS = frozenset(
    {"--spider", "--quiet", "--server-response", "--no-verbose", "--verbose",
     "--no-check-certificate", "--inet4-only", "--inet6-only", "--no-proxy"}
)
_WGET_LONG_VALUES = frozenset(
    {"--output-document", "--timeout", "--tries", "--header", "--user-agent",
     "--max-redirect"}
)

# 直譯器：(禁止的短旗標字母, 後面接參數值的短旗標字母)。合併寫法
# （-cCODE、-pe）逐字母檢查，遇到吃參數值的字母就停。
_INTERPRETER_SHORT_RULES: dict[str, tuple[str, str]] = {
    "python": ("cm", "WX"),
    "python3": ("cm", "WX"),
    "python.exe": ("cm", "WX"),
    "py": ("cm", "WX"),
    "node": ("epr", ""),
    "nodejs": ("epr", ""),
    "perl": ("eEmMi", "IFCxd"),
    "ruby": ("eri", "ICFEKWx"),
    "php": ("raSBRFE", "cdfzt"),
}
_INTERPRETER_DENY_LONG = frozenset(
    {"--command", "--eval", "--print", "--require", "--import", "--loader",
     "--experimental-loader", "--exec", "--module", "--run"}
)
# 直譯器只能執行學生作業檔：檔案必須是該語言的原始碼副檔名，且不可指到
# 系統目錄或套件目錄，否則 `python3 /usr/bin/pip3 install x`、
# `node .../npm-cli.js install x` 就能以 root 安裝並執行任意套件。
_PYTHON_SCRIPT_EXTENSIONS = (".py",)
_NODE_SCRIPT_EXTENSIONS = (".js", ".mjs", ".cjs")
_INTERPRETER_SCRIPT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": _PYTHON_SCRIPT_EXTENSIONS,
    "python3": _PYTHON_SCRIPT_EXTENSIONS,
    "python.exe": _PYTHON_SCRIPT_EXTENSIONS,
    "py": _PYTHON_SCRIPT_EXTENSIONS,
    "node": _NODE_SCRIPT_EXTENSIONS,
    "nodejs": _NODE_SCRIPT_EXTENSIONS,
    "perl": (".pl",),
    "ruby": (".rb",),
    "php": (".php",),
}
# 會切換工作目錄或改從 PATH／其他位置找腳本的旗標（ruby -C、-S，perl -x、-S），
# 讓上面的路徑檢查失準，一律不收
_INTERPRETER_DENY_VALUE_LETTERS: dict[str, str] = {
    "perl": "x",
    "ruby": "Cx",
    # php -d auto_prepend_file=... / -c 自訂 ini 會先載入別的 PHP 檔
    "php": "dc",
}
_INTERPRETER_EXTRA_DENIED: dict[str, str] = {"perl": "S", "ruby": "S"}
# php 的 -f <檔案> 就是要執行的腳本
_INTERPRETER_SCRIPT_VALUE_LETTERS: dict[str, str] = {"php": "f"}
_SYSTEM_PATH_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/lib", "/opt/", "/snap/", "/etc/", "/var/lib/",
    "/boot/", "/proc/", "/sys/", "/dev/",
)
_PACKAGE_PATH_SEGMENTS = frozenset({"site-packages", "dist-packages", "node_modules"})
_BUILD_TOOLS = frozenset({"gcc", "g++", "make", "cmake", "rustc", "cargo"})

_SED_ADDRESS = r"(?:\d+(?:~\d+)?|\$|/(?:[^/\\\n]|\\.)*/I?)"
_SED_PREFIX = rf"(?:{_SED_ADDRESS}(?:\s*,\s*(?:{_SED_ADDRESS}|[+~]\d+))?)?\s*!?\s*"
_SED_SIMPLE_COMMAND = re.compile(rf"^\s*{_SED_PREFIX}(?:[pdn=]|q\d*)\s*$")
_SED_SUBSTITUTE_START = re.compile(rf"^\s*{_SED_PREFIX}s")
_SED_SUBSTITUTE_FLAGS = frozenset("gpiImM0123456789")
_SED_LONG_FLAGS = frozenset(
    {"--quiet", "--silent", "--regexp-extended", "--posix", "--separate",
     "--unbuffered", "--null-data", "--debug", "--sandbox"}
)

_HOSTNAME_READ_FLAGS = frozenset(
    {"-f", "--fqdn", "--long", "-s", "--short", "-i", "--ip-address", "-I",
     "--all-ip-addresses", "-d", "--domain", "-a", "--alias", "-A",
     "--all-fqdns", "-y", "--yp", "--nis", "-V", "--version"}
)
_HOSTNAMECTL_READ_SUBCOMMANDS = frozenset(
    {"status", "hostname", "icon-name", "chassis", "deployment", "location"}
)
_TIMEDATECTL_READ_SUBCOMMANDS = frozenset(
    {"status", "show", "list-timezones", "timesync-status", "show-timesync"}
)
_LOGINCTL_READ_SUBCOMMANDS = frozenset(
    {"list-sessions", "session-status", "show-session", "list-users",
     "user-status", "show-user", "list-seats", "seat-status", "show-seat"}
)
_RESOLVECTL_READ_SUBCOMMANDS = frozenset(
    {"status", "query", "statistics", "show-cache", "show-server-state"}
)
_JOURNALCTL_WRITE_PREFIXES = (
    "--vacuum", "--rotate", "--flush", "--sync", "--relinquish-var",
    "--smart-relinquish-var", "--setup-keys", "--update-catalog",
)
# redis-cli 只檢查第一個位置參數（命令名稱），後面是 key／section 等參數
_REDIS_READ_COMMANDS = frozenset(
    {"ping", "info", "dbsize", "time", "role", "lastsave", "get", "mget",
     "exists", "ttl", "pttl", "type", "strlen", "getrange", "llen", "lrange",
     "lindex", "scard", "smembers", "sismember", "hget", "hgetall", "hmget",
     "hkeys", "hvals", "hlen", "hexists", "zcard", "zrange", "zscore", "keys",
     "scan"}
)
# 兩段式命令：第二個位置參數也要是唯讀動作
_REDIS_READ_SUBCOMMANDS = {"config": frozenset({"get"})}
_REDIS_VALUE_FLAGS = frozenset(
    {"-h", "-p", "-a", "-n", "-s", "-u", "-t", "-r", "-i", "-d", "--user",
     "--pass", "--sni", "--cacert", "--cacertdir", "--cert", "--key"}
)
_SERVER_CHECK_FLAGS = frozenset({"-t", "-T", "-v", "-V", "configtest", "-q", "-S", "-M"})
_SERVER_VALUE_FLAGS = frozenset({"-c", "-f", "-p"})
_OPENSSL_WRITE_FLAGS = frozenset(
    {"-out", "-keyout", "-writerand", "-sess_out", "-msgfile", "-keylogfile"}
)


def _short_letters(arg: str) -> str:
    """Letters of a single-dash option cluster (``-sSL`` -> ``sSL``), else ''."""

    if len(arg) > 1 and arg.startswith("-") and not arg.startswith("--"):
        return arg[1:]
    return ""


def _cluster_has(args: list[str], denied: str, value_letters: str = "") -> bool:
    """Whether any short option cluster carries a denied letter.

    Scanning a cluster stops at the first letter that takes a value, so an
    attached value (``-Wignore``) is not mistaken for more flags.
    """

    for arg in args:
        for letter in _short_letters(arg):
            if letter in value_letters:
                break
            if letter in denied:
                return True
    return False


def _scan_options(
    args: list[str],
    *,
    short_flags: str,
    short_values: str = "",
    long_flags: frozenset[str] = frozenset(),
    long_values: frozenset[str] = frozenset(),
) -> tuple[list[str], list[tuple[str, str]]] | None:
    """Whitelist-parse options; ``None`` when any option is not allowed."""

    positionals: list[str] = []
    values: list[tuple[str, str]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg.startswith("--"):
            name, has_inline, inline = arg.partition("=")
            if name in long_values:
                if has_inline:
                    values.append((name, inline))
                elif index < len(args):
                    values.append((name, args[index]))
                    index += 1
                else:
                    return None
            elif name not in long_flags or has_inline:
                return None
            continue
        letters = _short_letters(arg)
        if not letters:
            positionals.append(arg)
            continue
        for position, letter in enumerate(letters):
            if letter in short_values:
                attached = letters[position + 1 :]
                if attached:
                    values.append((f"-{letter}", attached))
                elif index < len(args):
                    values.append((f"-{letter}", args[index]))
                    index += 1
                else:
                    return None
                break
            if letter not in short_flags:
                return None
    return positionals, values


def _positionals(args: list[str], value_flags: frozenset[str] = frozenset()) -> list[str]:
    result: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in value_flags:
            skip = True
            continue
        if arg.startswith("-") and arg != "-":
            continue
        result.append(arg)
    return result


def _is_local_http_url(value: str) -> bool:
    candidate = value if "://" in value else f"http://{value}"
    try:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and host in _LOCAL_HTTP_HOSTS


def _curl_issue(args: list[str]) -> str | None:
    scanned = _scan_options(
        args,
        short_flags=_CURL_SHORT_FLAGS,
        short_values=_CURL_SHORT_VALUES,
        long_flags=_CURL_LONG_FLAGS,
        long_values=_CURL_LONG_VALUES,
    )
    if scanned is None or any(
        "%output" in value.lower()
        for flag, value in scanned[1]
        if flag in {"-w", "--write-out"}
    ):
        return "command collector 不允許把下載內容寫入檔案或送出資料"
    positionals, values = scanned
    urls = positionals + [value for flag, value in values if flag == "--url"]
    if not urls or not all(_is_local_http_url(url) for url in urls):
        return "command collector 的 HTTP 探測只允許 localhost"
    return None


def _wget_issue(args: list[str]) -> str | None:
    normalized = ["--no-verbose" if arg == "-nv" else arg for arg in args]
    scanned = _scan_options(
        normalized,
        short_flags=_WGET_SHORT_FLAGS,
        short_values=_WGET_SHORT_VALUES,
        long_flags=_WGET_LONG_FLAGS,
        long_values=_WGET_LONG_VALUES,
    )
    if scanned is None:
        return "command collector 不允許把下載內容寫入檔案或送出資料"
    positionals, values = scanned
    outputs = [value for flag, value in values if flag in {"-O", "--output-document"}]
    if any(output != "-" for output in outputs) or (
        "--spider" not in normalized and not outputs
    ):
        return "command collector 的 wget 只允許 --spider 或 -O - 輸出到 stdout"
    if not positionals or not all(_is_local_http_url(url) for url in positionals):
        return "command collector 的 HTTP 探測只允許 localhost"
    return None


def _interpreter_operand(command: str, args: list[str]) -> tuple[str | None, bool]:
    """``(script operand, uses a denied value flag)`` of an interpreter argv."""

    _denied, value_letters = _INTERPRETER_SHORT_RULES[command]
    deny_values = _INTERPRETER_DENY_VALUE_LETTERS.get(command, "")
    script_values = _INTERPRETER_SCRIPT_VALUE_LETTERS.get(command, "")
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            return (args[index] if index < len(args) else None), False
        if arg.startswith("--"):
            continue
        letters = _short_letters(arg)
        if not letters:
            # 第一個非旗標參數就是腳本（`-` 代表 stdin，交給副檔名檢查擋下）
            return arg, False
        for position, letter in enumerate(letters):
            if letter not in value_letters:
                continue
            if letter in deny_values:
                return None, True
            value = letters[position + 1 :]
            if not value and index < len(args):
                value = args[index]
                index += 1
            if letter in script_values:
                return value, False
            break
    return None, False


def _script_path_is_unsafe(operand: str, extensions: tuple[str, ...], cwd: str | None) -> bool:
    path = operand.replace("\\", "/")
    if not path.lower().endswith(extensions):
        return True
    is_absolute = path.startswith("/") or re.match(r"^[A-Za-z]:/", path) is not None
    if cwd and not is_absolute:
        path = posixpath.join(cwd.replace("\\", "/"), path)
    normalized = re.sub(r"^/+", "/", posixpath.normpath(path)).lower()
    segments = normalized.split("/")
    return (
        ".." in segments
        or any(segment in _PACKAGE_PATH_SEGMENTS for segment in segments)
        or normalized.startswith(_SYSTEM_PATH_PREFIXES)
        or re.match(r"^[a-z]:/(?:windows|program files[^/]*|programdata)/", normalized)
        is not None
    )


def _interpreter_issue(command: str, args: list[str], cwd: str | None = None) -> str | None:
    denied, value_letters = _INTERPRETER_SHORT_RULES[command]
    denied += _INTERPRETER_EXTRA_DENIED.get(command, "")
    if any(arg.partition("=")[0] in _INTERPRETER_DENY_LONG for arg in args) or (
        _cluster_has(args, denied, value_letters)
    ):
        return "command collector 不允許 interpreter inline code 或 eval"
    operand, denied_value = _interpreter_operand(command, args)
    if denied_value:
        return "command collector 不允許直譯器切換目錄或從其他位置搜尋腳本"
    if operand is not None and _script_path_is_unsafe(
        operand, _INTERPRETER_SCRIPT_EXTENSIONS[command], cwd
    ):
        return (
            "command collector 的直譯器只允許執行學生作業的原始碼檔"
            "（需為該語言副檔名，且不可位於系統或套件目錄）"
        )
    return None


def _sed_script_is_read_only(script: str) -> bool:
    """Allow one print/delete/quit command or one substitute without e/w."""

    if "\n" in script or "\r" in script:
        return False
    if _SED_SIMPLE_COMMAND.match(script):
        return True
    match = _SED_SUBSTITUTE_START.match(script)
    if match is None:
        return False
    rest = script[match.end() :]
    if not rest or rest[0] == "\\" or rest[0].isspace():
        return False
    delimiter = rest[0]
    index = 1
    separators = 0
    while index < len(rest):
        if rest[index] == "\\":
            index += 2
            continue
        if rest[index] == delimiter:
            separators += 1
            if separators == 2:
                break
        index += 1
    else:
        return False
    return all(flag in _SED_SUBSTITUTE_FLAGS for flag in rest[index + 1 :].strip())


def _sed_issue(args: list[str]) -> str | None:
    scanned = _scan_options(
        args,
        short_flags="nErsuz",
        short_values="e",
        long_flags=_SED_LONG_FLAGS,
        long_values=frozenset({"--expression"}),
    )
    if scanned is None:
        return "command collector 不允許原地修改檔案或從檔案載入 sed 指令"
    positionals, values = scanned
    scripts = [value for _flag, value in values]
    if not scripts:
        scripts = positionals[:1]
    if not scripts or not all(_sed_script_is_read_only(script) for script in scripts):
        return "command collector 的 sed 只允許列印或不帶 e／w 旗標的取代"
    return None


def _date_issue(args: list[str]) -> str | None:
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in {"-d", "--date", "-r", "--reference", "-f", "--file"}:
            skip = True
        elif arg.startswith("--"):
            if not arg.startswith(
                ("--date=", "--reference=", "--file=", "--iso-8601", "--rfc-3339",
                 "--rfc-email", "--utc", "--universal", "--debug", "--resolution")
            ):
                return "command collector 只允許用 date 顯示時間，不允許設定"
        elif _short_letters(arg):
            letters = _short_letters(arg)
            if letters[0] not in "dfrI" and not set(letters) <= set("uRI"):
                return "command collector 只允許用 date 顯示時間，不允許設定"
        elif not arg.startswith("+"):
            # date MMDDhhmm 這種位置參數會直接設定系統時間
            return "command collector 只允許用 date 顯示時間，不允許設定"
    return None


def _uniq_issue(args: list[str]) -> str | None:
    # 第二個位置參數是輸出檔
    if len(_positionals(args, frozenset({"-f", "-s", "-w"}))) > 1:
        return "command collector 不允許 uniq 寫出輸出檔"
    return None


def _nc_issue(args: list[str]) -> str | None:
    if any(arg.partition("=")[0] in _NC_DENY_LONG for arg in args) or _cluster_has(
        args, "eclok", "wpsiqxXIOT"
    ):
        return "command collector 不允許 nc 執行程式或監聽"
    if not any("z" in _short_letters(arg) for arg in args):
        return "command collector 只允許 nc -z 做連接埠探測"
    return None


def _package_issue(command: str, args: list[str], lowered: list[str]) -> str | None:
    if command in {"dpkg", "dpkg-query"}:
        first = args[0].partition("=")[0] if args else ""
        if first not in _DPKG_READ_ACTIONS or any(
            arg.partition("=")[0] in _DPKG_WRITE_ACTIONS for arg in args
        ):
            return "command collector 只允許查詢套件，不允許安裝或移除"
        return None
    if command == "rpm":
        first = args[0] if args else ""
        is_query = first.startswith("-q") or first in {
            "--query", "--verify", "--version", "--querytags",
        } or first.startswith("-V")
        if not is_query or any(
            arg.partition("=")[0] in _RPM_WRITE_ACTIONS
            or arg.startswith(("--eval", "--define", "--pipe", "--macros", "--rcfile", "-D", "-E"))
            or "%(" in arg
            for arg in args
        ):
            return "command collector 只允許查詢套件，不允許安裝或移除"
        return None
    if args in _VERSION_ONLY_ARGS:
        return None
    sub = lowered[0] if lowered else ""
    # `npm version major` 會改寫 package.json 並跑 lifecycle scripts，
    # version 只在單獨出現（查版本）時才收
    if sub not in _PACKAGE_READ_SUBCOMMANDS or (sub == "version" and len(args) > 1):
        return "command collector 只允許查詢套件，不允許安裝或移除"
    return None


def _firewall_issue(command: str, args: list[str], lowered: list[str]) -> str | None:
    if (
        not any(flag in _FW_READ_FLAGS for flag in lowered)
        or any(part in _FW_WRITE_PARTS for part in args)
        or (command == "iptables" and _cluster_has(args, "AIDFXPZNER", "t"))
        or (
            command == "nft"
            and (_cluster_has(args, "fi") or any(arg in {"--file", "--interactive"} for arg in args))
        )
    ):
        return "command collector 只允許列出防火牆規則"
    return None


def _network_config_issue(command: str, args: list[str]) -> str | None:
    if command == "ss" and (_cluster_has(args, "K", "AfF") or "--kill" in args):
        return "command collector 只允許查詢網路設定"
    if command == "ifconfig" and (
        any(arg.startswith("-") and arg not in {"-a", "-s", "-v"} for arg in args)
        or len(_positionals(args)) > 1
    ):
        return "command collector 只允許查詢網路設定"
    if command == "route" and _positionals(args, frozenset({"-A"})):
        return "command collector 只允許查詢網路設定"
    if command == "arp" and (
        _cluster_has(args, "dsf", "iHAt")
        or any(arg.partition("=")[0] in {"--delete", "--set", "--file"} for arg in args)
    ):
        return "command collector 只允許查詢網路設定"
    if command == "resolvectl":
        positionals = _positionals(args)
        if positionals and positionals[0] not in _RESOLVECTL_READ_SUBCOMMANDS:
            return "command collector 只允許查詢網路設定"
    return None


def _system_state_issue(command: str, args: list[str]) -> str | None:
    message = "command collector 只允許查詢系統狀態，不允許修改設定"
    if command == "env" and args:
        # env 會把後面的參數當成程式執行
        return message
    if command == "date":
        return _date_issue(args)
    if command == "hostname" and any(arg not in _HOSTNAME_READ_FLAGS for arg in args):
        return message
    if command in {"hostnamectl", "timedatectl", "loginctl"}:
        allowed = {
            "hostnamectl": _HOSTNAMECTL_READ_SUBCOMMANDS,
            "timedatectl": _TIMEDATECTL_READ_SUBCOMMANDS,
            "loginctl": _LOGINCTL_READ_SUBCOMMANDS,
        }[command]
        positionals = _positionals(args)
        max_positionals = 2 if command == "loginctl" else 1
        if positionals and (
            positionals[0] not in allowed or len(positionals) > max_positionals
        ):
            return message
    if command == "journalctl" and any(
        arg.startswith(_JOURNALCTL_WRITE_PREFIXES) for arg in args
    ):
        return message
    if command == "dmesg" and (
        _cluster_has(args, "cCnDE", "fls")
        or any(
            arg.startswith(("--clear", "--read-clear", "--console-"))
            for arg in args
        )
    ):
        return message
    return None


def _text_tool_issue(command: str, args: list[str], lowered: list[str]) -> str | None:
    if command == "sed":
        return _sed_issue(args)
    if command == "find" and any(
        flag in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                 "-fprint0", "-fprintf", "-fls"}
        for flag in lowered
    ):
        return "command collector 不允許 find 執行、刪除或寫檔"
    if command == "sort" and (
        any(arg.startswith(("--output", "--compress-program")) for arg in args)
        or _cluster_has(args, "o", "ktST")
    ):
        return "command collector 不允許 sort 寫出檔案或執行壓縮程式"
    if command == "uniq":
        return _uniq_issue(args)
    if command == "awk" and (
        any(
            re.search(r"\bsystem\s*\(", part)
            or "getline" in part
            # 程式內的 @load "rwarray"（writea 寫檔）／@include 與 -l／-i 同義
            or "@load" in part
            or "@include" in part
            for part in lowered
        )
        or any(arg.startswith(("-i", "-l", "--include", "--load")) for arg in args)
    ):
        return "command collector 不允許 awk 執行外部程式或載入擴充"
    if command == "yq" and (
        any(
            arg.startswith(("--inplace", "--in-place", "--split-exp"))
            for arg in args
        )
        # mikefarah yq 的 -s／--split-exp 會依結果各寫出一個檔案
        or _cluster_has(args, "is", "Iop")
    ):
        return "command collector 不允許 yq 原地修改或寫出檔案"
    if command == "xmllint" and any(arg.startswith(("--output", "-o")) for arg in args):
        return "command collector 不允許 xmllint 寫出檔案"
    if command == "file" and (
        "--compile" in args or _cluster_has(args, "C", "mfFeP")
    ):
        return "command collector 不允許 file 編譯 magic 檔"
    return None


def _service_tool_issue(command: str, args: list[str], lowered: list[str]) -> str | None:
    if command in {"psql", "mysql", "mariadb", "redis-cli"} and any(
        flag in {"-c", "--command", "-e", "--execute", "-f", "--file"} for flag in lowered
    ):
        return "command collector 不允許對資料庫送出語句"
    if command == "psql" and any(
        arg.startswith(("-o", "--output", "-L", "--log-file")) for arg in args
    ):
        return "command collector 不允許對資料庫送出語句"
    if command in {"mysql", "mariadb"} and any(
        arg.startswith(("--tee", "--pager", "--init-command", "--plugin-dir"))
        for arg in args
    ):
        return "command collector 不允許對資料庫送出語句"
    if command == "redis-cli":
        redis_positionals = [part.lower() for part in _positionals(args, _REDIS_VALUE_FLAGS)]
        redis_command = redis_positionals[0] if redis_positionals else ""
        redis_sub = redis_positionals[1] if len(redis_positionals) > 1 else ""
        if any(
            arg.startswith(("--eval", "--rdb", "--functions-rdb", "--pipe")) or arg == "-x"
            for arg in args
        ) or (
            redis_positionals
            and redis_command not in _REDIS_READ_COMMANDS
            and redis_sub not in _REDIS_READ_SUBCOMMANDS.get(redis_command, frozenset())
        ):
            return "command collector 不允許對資料庫送出語句"
    if command in {"nginx", "apache2ctl", "httpd", "sshd"}:
        server_message = "command collector 只允許用伺服器程式檢查設定或查版本"
        has_check = False
        skip = False
        for arg in args:
            if skip:
                skip = False
                continue
            if arg in _SERVER_VALUE_FLAGS:
                skip = True
                continue
            if arg not in _SERVER_CHECK_FLAGS:
                return server_message
            has_check = has_check or arg in {"-t", "-T", "-v", "-V", "configtest"}
        if not has_check:
            return server_message
    if command == "openssl" and (
        (lowered and lowered[0] not in {"version", "x509", "s_client", "verify", "rsa", "ec"})
        or any(arg in _OPENSSL_WRITE_FLAGS for arg in args)
    ):
        return "command collector 只允許用 openssl 檢視憑證"
    if command == "ssh-keygen":
        scanned = _scan_options(args, short_flags="lyevq", short_values="fEm")
        if scanned is None or scanned[0] or not _cluster_has(args, "lye", "fEm"):
            return "command collector 只允許用 ssh-keygen 檢視指紋"
    return None


def _git_branch_is_listing(args: list[str]) -> bool:
    list_mode = False
    positionals: list[str] = []
    after_commit_flag = False
    for arg in args:
        if after_commit_flag and not arg.startswith("-"):
            # --contains／--merged／--no-merged 後面可接一個 commit
            after_commit_flag = False
            continue
        after_commit_flag = False
        if arg in _GIT_BRANCH_LIST_FLAGS:
            # 只認 --list 當成「後面是 pattern」：git 2.19 以前的 -l 是
            # --create-reflog，`git branch -l foo` 會建立分支
            list_mode = list_mode or arg == "--list"
            after_commit_flag = arg in _GIT_BRANCH_COMMIT_FLAGS
        elif arg.startswith(_GIT_LIST_PREFIX_FLAGS):
            continue
        elif (letters := _short_letters(arg)) and set(letters) <= set("arlv"):
            continue
        elif arg.startswith("-"):
            return False
        else:
            positionals.append(arg)
    # 沒有 --list 時的位置參數是要建立的分支名稱
    return list_mode or not positionals


def _git_tag_is_listing(args: list[str]) -> bool:
    list_mode = False
    positionals: list[str] = []
    for arg in args:
        if arg in {"-l", "--list"}:
            list_mode = True
        elif arg == "-n" or re.fullmatch(r"-n\d+", arg) or arg.startswith(
            _GIT_LIST_PREFIX_FLAGS
        ):
            continue
        elif arg.startswith("-"):
            return False
        else:
            positionals.append(arg)
    return list_mode or not positionals


def _git_remote_is_listing(args: list[str]) -> bool:
    rest = list(args)
    while rest and rest[0] in {"-v", "--verbose"}:
        rest.pop(0)
    if not rest:
        return True
    action, operands = rest[0], rest[1:]
    if action == "get-url":
        names = [arg for arg in operands if arg not in {"--push", "--all"}]
        return len(names) == 1 and not names[0].startswith("-")
    if action == "show":
        names = [arg for arg in operands if arg != "-n"]
        return not any(name.startswith("-") for name in names)
    return False


def _git_config_is_read(args: list[str]) -> bool:
    if any(
        arg.partition("=")[0] in _GIT_CONFIG_WRITE_FLAGS for arg in args
    ) or _cluster_has(args, "e", "f"):
        return False
    if any(arg in _GIT_CONFIG_READ_FLAGS for arg in args):
        return True
    positionals = _positionals(args, _GIT_CONFIG_VALUE_FLAGS)
    # 只有一個 key（讀取）；兩個以上是寫入 `git config key value`
    return len(positionals) == 1 and positionals[0].lower() not in _GIT_CONFIG_WRITE_ACTIONS


def _git_issue(args: list[str], lowered: list[str]) -> str | None:
    message = "command collector 只允許唯讀 Git 子命令"
    sub = lowered[0] if lowered else ""
    if any(arg.startswith("--output") for arg in args):
        return message
    if sub in _GIT_READ_SUBCOMMANDS:
        return None
    rest = args[1:]
    listing = {
        "branch": _git_branch_is_listing,
        "tag": _git_tag_is_listing,
        "remote": _git_remote_is_listing,
        "config": _git_config_is_read,
    }.get(sub)
    if listing is None or not listing(rest):
        return message
    return None


def _command_argv_issue(argv: list[str], cwd: str | None = None) -> str | None:
    if not _is_safe_command_argv(argv):
        return "command collector argv 含 shell launcher 或控制字元"
    command = argv[0].replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    args = [part.strip() for part in argv[1:]]
    lowered = [part.lower() for part in args]

    if command not in _READ_ONLY_COMMANDS:
        return f"command collector 只允許唯讀／診斷命令（{command} 不在白名單）"

    if command in _INTERPRETER_SHORT_RULES:
        if issue := _interpreter_issue(command, args, cwd):
            return issue
    if command in _BUILD_TOOLS and args not in _VERSION_ONLY_ARGS:
        return "command collector 對建置工具只允許查詢版本"
    if command == "go" and args != ["version"] and not (
        len(args) > 1
        and args[0] == "run"
        and args[1].endswith(".go")
        and not any("@" in arg or "://" in arg for arg in args[1:])
    ):
        return "command collector 對 go 只允許查詢版本或 go run <檔案>.go"
    if issue := _text_tool_issue(command, args, lowered):
        return issue
    if issue := _system_state_issue(command, args):
        return issue
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
        if sub == "compose" and lowered[1:2] == ["config"] and (
            _cluster_has(args[2:], "o")
            or any(arg.startswith("--output") for arg in args[2:])
        ):
            return "command collector 不允許 compose config 寫出檔案"
    if command in {"apt", "apt-cache", "dpkg", "dpkg-query", "rpm", "yum", "dnf",
                   "pip", "pip3", "npm", "snap"}:
        if issue := _package_issue(command, args, lowered):
            return issue
    if command == "git":
        if issue := _git_issue(args, lowered):
            return issue
    if command == "curl":
        if issue := _curl_issue(args):
            return issue
    if command == "wget":
        if issue := _wget_issue(args):
            return issue
    if command in {"nc", "ncat"}:
        if issue := _nc_issue(args):
            return issue
    if command == "ip":
        sub = next((part for part in lowered if not part.startswith("-")), "")
        if (
            sub not in _IP_READ_SUBCOMMANDS
            or any(part in {"-b", "-batch", "--batch", "-force"} for part in lowered)
            or any(
                part in {"add", "del", "delete", "set", "flush", "replace", "change",
                         "append", "prepend", "exec"}
                for part in lowered
            )
        ):
            return "command collector 只允許查詢網路設定"
    if command in {"iptables", "nft", "ufw"}:
        if issue := _firewall_issue(command, args, lowered):
            return issue
    if issue := _network_config_issue(command, args):
        return issue
    if issue := _service_tool_issue(command, args, lowered):
        return issue
    # git 的 branch／tag／remote／config 已由 _git_issue 限定為列出／讀取
    return dangerous_command_issue(" ".join(argv), git_args_checked=command == "git")


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
                command_issue = _command_argv_issue(argv, collector.get("cwd"))
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


# Plan fields whose values shape the generated code or are policy-relevant
# (URLs for the localhost check, argv); every other string is inert data.
_POLICY_VIEW_KEPT_KEYS = frozenset(
    {
        "type",
        "method",
        "url",
        "argv",
        "read_mode",
        "normalize",
        "operator",
        "judgement_mode",
        "schema_version",
        "compiler_version",
    }
)


def _policy_view_plan(value: Any) -> Any:
    """Return the plan with inert data strings replaced by a neutral placeholder."""

    if isinstance(value, dict):
        return {
            key: item if key in _POLICY_VIEW_KEPT_KEYS else _policy_view_plan(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_policy_view_plan(item) for item in value]
    if isinstance(value, str):
        return "x"
    return value


def compile_check_plan(
    analysis: TeacherJudgeRubricAnalysis,
    *,
    target_node_key: str,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return script, deterministic policy, review metadata and canonical plan."""

    plan = canonicalize_check_plan(analysis, target_node_key=target_node_key)
    script_content = _render_script(plan)
    # 靜態契約只審編譯器產生的程式碼；標題、id、預期文字等資料字面值換成
    # 中性佔位字，免得「Git reset 練習」這類標題被 deny pattern 誤擋。
    # argv 已由 _command_argv_issue 逐步驗過，URL 保留給網路檢查。
    policy_view = _render_script(_policy_view_plan(plan))
    policy = dict(check_script_policy(policy_view))
    quality = dict(check_script_quality(policy_view))
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
