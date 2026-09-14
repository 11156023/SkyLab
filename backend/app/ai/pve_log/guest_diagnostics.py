"""Guest 廣度診斷的固定契約層（純函式，不做網路 I/O）。

本模組只負責：
  1. 固定 probe 定義（server-owned，模型不可控任何 shell 內容）。
  2. systemctl / ps / journalctl 輸出 parser。
  3. 敏感資訊遮蔽（process args、journal message、service description）。
  4. section collection_status（ok/partial/unavailable/error）與整體狀態組裝。
  5. 固定 JSON 結果組裝（欄位名稱與型別固定，不依發行版改變）。

工具只收集、解析、遮蔽與截斷，不判斷根因或健康狀態；SSH 執行由
``app.ai.pve_log.ssh_exec`` 的 server-owned batch runner 完成，agent dispatch
由 ``app.ai.pve_log.chat`` 完成。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.i18n import t

# ---------------------------------------------------------------------------
# 固定限制（契約的一部分）
# ---------------------------------------------------------------------------

TOP_PROCESSES_LIMIT = 10
JOURNAL_MAX_ENTRIES = 100
JOURNAL_WINDOW_MINUTES = 60
JOURNAL_MINIMUM_PRIORITY = "warning"
MAX_FAILED_SERVICES = 20
MAX_PROBE_ROWS = 2000
MAX_ARGS_CHARS = 200
MAX_MESSAGE_CHARS = 300
MAX_SERVICE_DESCRIPTION_CHARS = 120
MAX_WARNINGS = 20

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"

ERROR_CODE_PROBE_TIMEOUT = "probeTimedOut"
ERROR_CODE_COMMAND_FAILED = "commandFailed"
ERROR_CODE_CONNECTION_FAILED = "connectionFailed"
ERROR_CODE_RESOLVE_FAILED = "resolveFailed"
ERROR_CODE_SYSTEMD_UNAVAILABLE = "systemdUnavailable"
ERROR_CODE_SCOPE_RESTRICTED = "scopeRestricted"

# ---------------------------------------------------------------------------
# 固定 probe 定義
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuestProbe:
    name: str
    command: str
    max_output_bytes: int
    exec_timeout: int


@dataclass(frozen=True, slots=True)
class GuestProbeGroup:
    section: str
    probes: tuple[GuestProbe, ...]


_PROBE_SERVICE_UNITS = GuestProbe(
    name="service_units",
    command=(
        "systemctl list-units --type=service --all "
        "--no-legend --plain --no-pager"
    ),
    max_output_bytes=256 * 1024,
    exec_timeout=10,
)
_PROBE_FAILED_SERVICES = GuestProbe(
    name="failed_services",
    command="systemctl --failed --type=service --no-legend --plain --no-pager",
    max_output_bytes=64 * 1024,
    exec_timeout=10,
)
_PROBE_PROCESSES_TOP_CPU = GuestProbe(
    name="processes_top_cpu",
    command=(
        "ps -eo pid=,user=,comm=,%cpu=,%mem=,etimes=,args= --sort=-%cpu"
    ),
    max_output_bytes=512 * 1024,
    exec_timeout=10,
)
_PROBE_PROCESSES_TOP_MEMORY = GuestProbe(
    name="processes_top_memory",
    command=(
        "ps -eo pid=,user=,comm=,%cpu=,%mem=,etimes=,args= --sort=-%mem"
    ),
    max_output_bytes=512 * 1024,
    exec_timeout=10,
)
_PROBE_RECENT_LOGS = GuestProbe(
    name="recent_logs",
    command=(
        'journalctl --since "-1 hour" -p warning -n '
        f"{JOURNAL_MAX_ENTRIES} --no-pager -o json"
    ),
    max_output_bytes=512 * 1024,
    exec_timeout=15,
)

GUEST_DIAGNOSTIC_PROBE_GROUPS: tuple[GuestProbeGroup, ...] = (
    GuestProbeGroup("services", (_PROBE_SERVICE_UNITS, _PROBE_FAILED_SERVICES)),
    GuestProbeGroup(
        "processes", (_PROBE_PROCESSES_TOP_CPU, _PROBE_PROCESSES_TOP_MEMORY)
    ),
    GuestProbeGroup("recent_logs", (_PROBE_RECENT_LOGS,)),
)

GUEST_DIAGNOSTIC_PROBES: tuple[GuestProbe, ...] = tuple(
    probe for group in GUEST_DIAGNOSTIC_PROBE_GROUPS for probe in group.probes
)


def _find_group(section: str) -> GuestProbeGroup:
    for group in GUEST_DIAGNOSTIC_PROBE_GROUPS:
        if group.section == section:
            return group
    raise KeyError(section)


# ---------------------------------------------------------------------------
# Probe 執行結果（由 ssh_exec 的 batch runner 回傳）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    error_code: str | None = None


# ---------------------------------------------------------------------------
# 敏感資訊遮蔽
# ---------------------------------------------------------------------------

_CLI_SECRET_ASSIGN = re.compile(
    r"(?i)(--?[a-z0-9_-]*(?:password|passwd|secret|api[_-]?key|apikey|token))"
    r"\s*[=:]\s*(\S+)"
)
_CLI_SECRET_SPACE = re.compile(
    r"(?i)(--?[a-z0-9_-]*(?:password|passwd|secret|api[_-]?key|apikey|token))"
    r"\s+(\S+)"
)
_URI_CREDENTIALS = re.compile(
    r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"
)
_ENV_SECRET = re.compile(
    r"(?i)((?:password|passwd|secret|api[_-]?key|apikey|token)\s*[:=]\s*)"
    r"([^\s,;]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.+?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_text(value: str) -> str:
    """遮蔽 CLI secret、URI credentials、環境變數 secret 與 private key block。

    與 ssh_exec 的輸出遮蔽同方向：寧可多遮，不可漏遮。
    """
    redacted = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    redacted = _CLI_SECRET_ASSIGN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", redacted
    )
    redacted = _CLI_SECRET_SPACE.sub(
        lambda match: f"{match.group(1)} [REDACTED]", redacted
    )
    redacted = _URI_CREDENTIALS.sub(
        lambda match: f"{match.group(1)}[REDACTED]@", redacted
    )
    redacted = _ENV_SECRET.sub(
        lambda match: f"{match.group(1)}[REDACTED]", redacted
    )
    return redacted


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceUnitStats:
    total: int
    by_active_state: dict[str, int]
    by_sub_state: dict[str, int]
    skipped_rows: int
    hit_row_cap: bool


@dataclass(frozen=True, slots=True)
class ProcessList:
    entries: list[dict[str, Any]]
    skipped_rows: int
    hit_row_cap: bool


@dataclass(frozen=True, slots=True)
class JournalEntries:
    entries: list[dict[str, Any]]
    skipped_rows: int
    hit_row_cap: bool


def parse_service_units(output: str) -> ServiceUnitStats:
    """解析 ``systemctl list-units --no-legend --plain`` 輸出。

    欄位格式固定為 UNIT LOAD ACTIVE SUB DESCRIPTION；破損列略過並計數。
    """
    total = 0
    skipped = 0
    hit_cap = False
    active_counts: Counter[str] = Counter()
    sub_counts: Counter[str] = Counter()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(None, 4)
        if len(fields) < 4:
            skipped += 1
            continue
        if total >= MAX_PROBE_ROWS:
            hit_cap = True
            break
        total += 1
        active_counts[fields[2]] += 1
        sub_counts[fields[3]] += 1
    return ServiceUnitStats(
        total=total,
        by_active_state=dict(active_counts),
        by_sub_state=dict(sub_counts),
        skipped_rows=skipped,
        hit_row_cap=hit_cap,
    )


def parse_failed_services(output: str) -> tuple[list[dict[str, str]], int]:
    """解析 ``systemctl --failed --no-legend --plain`` 輸出。

    回傳 (failed_services, skipped_rows)；只保留 ``.service`` units。
    """
    entries: list[dict[str, str]] = []
    skipped = 0
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(None, 4)
        if len(fields) < 5:
            skipped += 1
            continue
        name = fields[0]
        if not name.endswith(".service"):
            continue
        if len(entries) >= MAX_FAILED_SERVICES:
            break
        entries.append(
            {
                "name": name,
                "description": _truncate(
                    redact_sensitive_text(fields[4]), MAX_SERVICE_DESCRIPTION_CHARS
                ),
            }
        )
    return entries, skipped


def parse_process_list(output: str, *, limit: int = TOP_PROCESSES_LIMIT) -> ProcessList:
    """解析 ``ps -eo pid=,user=,comm=,%cpu=,%mem=,etimes=,args=`` 輸出。

    解析失敗的單行略過並計數，不讓整個 section 失敗；args 遮蔽並截斷。
    """
    entries: list[dict[str, Any]] = []
    skipped = 0
    hit_cap = False
    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if len(entries) >= limit:
            hit_cap = True
            break
        fields = line.split(None, 6)
        if len(fields) < 6:
            skipped += 1
            continue
        try:
            pid = int(fields[0])
            cpu_pct = float(fields[3])
            mem_pct = float(fields[4])
            etimes = int(fields[5])
        except ValueError:
            skipped += 1
            continue
        args_text = fields[6] if len(fields) > 6 else ""
        entries.append(
            {
                "pid": pid,
                "user": fields[1],
                "comm": fields[2],
                "cpu_pct": cpu_pct,
                "mem_pct": mem_pct,
                "uptime_seconds": etimes,
                "args": _truncate(
                    redact_sensitive_text(args_text), MAX_ARGS_CHARS
                ),
            }
        )
    return ProcessList(
        entries=entries, skipped_rows=skipped, hit_row_cap=hit_cap
    )


def _journal_timestamp(record: Mapping[str, Any]) -> str | None:
    raw = record.get("__REALTIME_TIMESTAMP")
    if raw is None:
        return None
    try:
        micros = int(str(raw))
    except ValueError:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _journal_priority(record: Mapping[str, Any]) -> int | None:
    try:
        return int(str(record.get("PRIORITY") or "").strip())
    except ValueError:
        return None


def parse_journal_entries(
    output: str, *, limit: int = JOURNAL_MAX_ENTRIES
) -> JournalEntries:
    """解析 ``journalctl -o json`` 逐行 JSON 輸出。

    每筆只保留 timestamp、unit、priority、message；message 遮蔽並截斷。
    達到 limit 後若仍有非空行，``hit_row_cap`` 為 True（可能被截斷）。
    """
    entries: list[dict[str, Any]] = []
    skipped = 0
    hit_cap = False
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(entries) >= limit:
            hit_cap = True
            break
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(record, dict):
            skipped += 1
            continue
        unit = (
            str(
                record.get("_SYSTEMD_UNIT")
                or record.get("SYSLOG_IDENTIFIER")
                or record.get("_COMM")
                or ""
            )
        ).strip()
        message = str(record.get("MESSAGE") or "")
        entries.append(
            {
                "timestamp": _journal_timestamp(record),
                "unit": unit,
                "priority": _journal_priority(record),
                "message": _truncate(
                    redact_sensitive_text(message), MAX_MESSAGE_CHARS
                ),
            }
        )
    return JournalEntries(
        entries=entries, skipped_rows=skipped, hit_row_cap=hit_cap
    )


# ---------------------------------------------------------------------------
# Section status 組裝
# ---------------------------------------------------------------------------

_SYSTEMD_UNAVAILABLE_MARKERS = (
    "system has not been booted with systemd",
    "failed to connect to bus",
    "no journal files",
)


def _is_systemd_unavailable(result: ProbeResult) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in text for marker in _SYSTEMD_UNAVAILABLE_MARKERS)


def _probe_outcome(result: ProbeResult | None) -> tuple[str, str | None]:
    """回傳 (probe_status_hint, error_code)。

    ok = exit 0 且可解析；partial = 非 0 但有 stdout 可解析；
    unavailable = systemd/journal 不可用；error = 沒有可信資料。
    """
    if result is None:
        return STATUS_ERROR, ERROR_CODE_COMMAND_FAILED
    if result.error_code:
        return STATUS_ERROR, result.error_code
    if result.exit_code == 0:
        return STATUS_OK, None
    if _is_systemd_unavailable(result):
        return STATUS_UNAVAILABLE, ERROR_CODE_SYSTEMD_UNAVAILABLE
    if result.stdout.strip():
        return STATUS_PARTIAL, ERROR_CODE_COMMAND_FAILED
    return STATUS_ERROR, ERROR_CODE_COMMAND_FAILED


def combine_group_status(statuses: Sequence[str]) -> str:
    """同一 section 內多個 probe 的狀態組合。"""
    if all(s == STATUS_OK for s in statuses):
        return STATUS_OK
    if all(s == STATUS_UNAVAILABLE for s in statuses):
        return STATUS_UNAVAILABLE
    if any(s in {STATUS_OK, STATUS_PARTIAL} for s in statuses):
        return STATUS_PARTIAL
    return STATUS_ERROR


def combine_collection_status(statuses: Sequence[str]) -> str:
    """整體 collection_status（依固定契約：沒有可信資料即 error）。"""
    if all(s == STATUS_OK for s in statuses):
        return STATUS_OK
    if any(s in {STATUS_OK, STATUS_PARTIAL} for s in statuses):
        return STATUS_PARTIAL
    return STATUS_ERROR


def _append_probe_failure_warnings(
    statuses: Sequence[str], warnings: list[str]
) -> None:
    for status in statuses:
        if status == STATUS_UNAVAILABLE:
            warnings.append(t("pveLog.guestDiagSystemdUnavailable"))
        elif status == STATUS_ERROR:
            warnings.append(t("pveLog.guestDiagWarnProbeFailed"))


def _first_error_code(*codes: str | None) -> str | None:
    for code in codes:
        if code:
            return code
    return None


# ---------------------------------------------------------------------------
# Section 解析
# ---------------------------------------------------------------------------


def _parse_services_group(
    results: Mapping[str, ProbeResult], warnings: list[str]
) -> dict[str, Any]:
    units_probe, failed_probe = _find_group("services").probes
    units_result = results.get(units_probe.name)
    failed_result = results.get(failed_probe.name)
    units_status, units_error = _probe_outcome(units_result)
    failed_status, failed_error = _probe_outcome(failed_result)

    total = 0
    by_active: dict[str, int] = {}
    by_sub: dict[str, int] = {}
    if units_status in {STATUS_OK, STATUS_PARTIAL} and units_result is not None:
        stats = parse_service_units(units_result.stdout)
        total = stats.total
        by_active = stats.by_active_state
        by_sub = stats.by_sub_state
        if stats.skipped_rows:
            warnings.append(
                t("pveLog.guestDiagWarnUnparsedRows", count=stats.skipped_rows)
            )
        if stats.hit_row_cap:
            units_status = STATUS_PARTIAL
            warnings.append(t("pveLog.guestDiagWarnRowLimit"))
        if units_result.truncated:
            units_status = STATUS_PARTIAL
            warnings.append(t("pveLog.guestDiagWarnOutputTruncated"))

    failed: list[dict[str, str]] = []
    if failed_status in {STATUS_OK, STATUS_PARTIAL} and failed_result is not None:
        failed, skipped = parse_failed_services(failed_result.stdout)
        if skipped:
            warnings.append(t("pveLog.guestDiagWarnUnparsedRows", count=skipped))
        if failed_result.truncated:
            failed_status = STATUS_PARTIAL
            warnings.append(t("pveLog.guestDiagWarnOutputTruncated"))

    _append_probe_failure_warnings(
        (units_status, failed_status), warnings
    )
    section_status = combine_group_status([units_status, failed_status])
    section: dict[str, Any] = {
        "collection_status": section_status,
        "total": total,
        "by_active_state": by_active,
        "by_sub_state": by_sub,
        "failed_services": failed,
    }
    error_code = _first_error_code(units_error, failed_error)
    if section_status in {STATUS_ERROR, STATUS_UNAVAILABLE} and error_code:
        section["error_code"] = error_code
    return section


def _parse_processes_group(
    results: Mapping[str, ProbeResult], warnings: list[str]
) -> dict[str, Any]:
    cpu_probe, mem_probe = _find_group("processes").probes
    cpu_result = results.get(cpu_probe.name)
    mem_result = results.get(mem_probe.name)
    cpu_status, cpu_error = _probe_outcome(cpu_result)
    mem_status, mem_error = _probe_outcome(mem_result)

    top_cpu: list[dict[str, Any]] = []
    top_memory: list[dict[str, Any]] = []
    if cpu_status in {STATUS_OK, STATUS_PARTIAL} and cpu_result is not None:
        parsed = parse_process_list(cpu_result.stdout, limit=TOP_PROCESSES_LIMIT)
        top_cpu = parsed.entries
        if parsed.skipped_rows:
            warnings.append(
                t("pveLog.guestDiagWarnUnparsedRows", count=parsed.skipped_rows)
            )
        if parsed.hit_row_cap or cpu_result.truncated:
            cpu_status = STATUS_PARTIAL
            warnings.append(t("pveLog.guestDiagWarnRowLimit"))
        if cpu_result.truncated:
            warnings.append(t("pveLog.guestDiagWarnOutputTruncated"))

    if mem_status in {STATUS_OK, STATUS_PARTIAL} and mem_result is not None:
        parsed = parse_process_list(mem_result.stdout, limit=TOP_PROCESSES_LIMIT)
        top_memory = parsed.entries
        if parsed.skipped_rows:
            warnings.append(
                t("pveLog.guestDiagWarnUnparsedRows", count=parsed.skipped_rows)
            )
        if parsed.hit_row_cap or mem_result.truncated:
            mem_status = STATUS_PARTIAL
            warnings.append(t("pveLog.guestDiagWarnRowLimit"))
        if mem_result.truncated:
            warnings.append(t("pveLog.guestDiagWarnOutputTruncated"))

    _append_probe_failure_warnings((cpu_status, mem_status), warnings)
    section_status = combine_group_status([cpu_status, mem_status])
    section: dict[str, Any] = {
        "collection_status": section_status,
        "limit_per_list": TOP_PROCESSES_LIMIT,
        "top_cpu": top_cpu,
        "top_memory": top_memory,
    }
    error_code = _first_error_code(cpu_error, mem_error)
    if section_status in {STATUS_ERROR, STATUS_UNAVAILABLE} and error_code:
        section["error_code"] = error_code
    return section


def _parse_recent_logs_group(
    results: Mapping[str, ProbeResult], warnings: list[str]
) -> dict[str, Any]:
    probe = _find_group("recent_logs").probes[0]
    result = results.get(probe.name)
    status, error_code = _probe_outcome(result)

    entries: list[dict[str, Any]] = []
    may_be_truncated = False
    if status in {STATUS_OK, STATUS_PARTIAL} and result is not None:
        parsed = parse_journal_entries(result.stdout, limit=JOURNAL_MAX_ENTRIES)
        entries = parsed.entries
        if parsed.skipped_rows:
            warnings.append(
                t("pveLog.guestDiagWarnUnparsedRows", count=parsed.skipped_rows)
            )
        may_be_truncated = bool(
            parsed.hit_row_cap
            or result.truncated
            or len(entries) >= JOURNAL_MAX_ENTRIES
        )
        if result.truncated:
            warnings.append(t("pveLog.guestDiagWarnOutputTruncated"))
        if may_be_truncated:
            warnings.append(
                t("pveLog.guestDiagWarnJournalLimit", limit=JOURNAL_MAX_ENTRIES)
            )

    _append_probe_failure_warnings((status,), warnings)
    section: dict[str, Any] = {
        "collection_status": status,
        "window_minutes": JOURNAL_WINDOW_MINUTES,
        "minimum_priority": JOURNAL_MINIMUM_PRIORITY,
        "returned_entries": len(entries),
        "limit": JOURNAL_MAX_ENTRIES,
        "may_be_truncated": may_be_truncated,
        "entries": entries,
    }
    if status in {STATUS_ERROR, STATUS_UNAVAILABLE} and error_code:
        section["error_code"] = error_code
    return section


def parse_guest_probe_results(
    results: Mapping[str, ProbeResult],
    *,
    warnings: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """把 batch runner 回傳的 probe 結果解析成固定 sections。

    各 section 獨立解析；單一 probe 失敗不得清空其他成功結果。
    """
    collected_warnings: list[str] = warnings if warnings is not None else []
    sections: dict[str, dict[str, Any]] = {}
    for group in GUEST_DIAGNOSTIC_PROBE_GROUPS:
        if group.section == "services":
            sections["services"] = _parse_services_group(
                results, collected_warnings
            )
        elif group.section == "processes":
            sections["processes"] = _parse_processes_group(
                results, collected_warnings
            )
        elif group.section == "recent_logs":
            sections["recent_logs"] = _parse_recent_logs_group(
                results, collected_warnings
            )
    return sections


# ---------------------------------------------------------------------------
# 固定 JSON 結果組裝
# ---------------------------------------------------------------------------


def empty_guest_section(
    section: str, status: str, *, error_code: str | None = None
) -> dict[str, Any]:
    """組出固定 shape 的空 section（error/unavailable 時使用）。"""
    data: dict[str, Any]
    if section == "services":
        data = {
            "collection_status": status,
            "total": 0,
            "by_active_state": {},
            "by_sub_state": {},
            "failed_services": [],
        }
    elif section == "processes":
        data = {
            "collection_status": status,
            "limit_per_list": TOP_PROCESSES_LIMIT,
            "top_cpu": [],
            "top_memory": [],
        }
    else:
        data = {
            "collection_status": status,
            "window_minutes": JOURNAL_WINDOW_MINUTES,
            "minimum_priority": JOURNAL_MINIMUM_PRIORITY,
            "returned_entries": 0,
            "limit": JOURNAL_MAX_ENTRIES,
            "may_be_truncated": False,
            "entries": [],
        }
    if error_code:
        data["error_code"] = error_code
    return data


def build_resource_section(
    resource_detail: Mapping[str, Any] | None,
    *,
    status: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "collection_status": status,
        "data": dict(resource_detail) if resource_detail else None,
    }
    if error_code:
        section["error_code"] = error_code
    return section


def resource_detail_says_stopped(resource_detail: Mapping[str, Any] | None) -> bool:
    summary = (resource_detail or {}).get("summary")
    if not isinstance(summary, Mapping):
        return False
    return str(summary.get("status") or "").strip().lower() == "stopped"


def build_guest_diagnostics_result(
    *,
    vmid: int | None,
    collected_at: datetime,
    collection_duration_ms: int,
    resource: Mapping[str, Any],
    sections: Mapping[str, Mapping[str, Any]],
    warnings: Sequence[str],
) -> dict[str, Any]:
    """組出固定頂層 JSON（欄位名稱與型別固定）。"""
    status_list = [
        str(resource.get("collection_status", STATUS_ERROR)),
        str(sections.get("services", {}).get("collection_status", STATUS_ERROR)),
        str(sections.get("processes", {}).get("collection_status", STATUS_ERROR)),
        str(sections.get("recent_logs", {}).get("collection_status", STATUS_ERROR)),
    ]
    ordered_warnings: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning and warning not in seen:
            seen.add(warning)
            ordered_warnings.append(warning)
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    return {
        "vmid": vmid,
        "collected_at": collected_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "collection_duration_ms": int(collection_duration_ms),
        "collection_status": combine_collection_status(status_list),
        "resource": dict(resource),
        "services": dict(sections.get("services", {})),
        "processes": dict(sections.get("processes", {})),
        "recent_logs": dict(sections.get("recent_logs", {})),
        "warnings": ordered_warnings[:MAX_WARNINGS],
    }
