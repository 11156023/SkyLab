from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest

from app.ai.pve_log import chat as chat_module
from app.ai.pve_log import ssh_exec as ssh_exec_module
from app.ai.pve_log.guest_diagnostics import (
    ERROR_CODE_COMMAND_FAILED,
    ERROR_CODE_RESOLVE_FAILED,
    GUEST_DIAGNOSTIC_PROBES,
    JOURNAL_MAX_ENTRIES,
    JOURNAL_MINIMUM_PRIORITY,
    JOURNAL_WINDOW_MINUTES,
    TOP_PROCESSES_LIMIT,
    GuestProbe,
    ProbeResult,
    build_guest_diagnostics_result,
    build_resource_section,
    combine_collection_status,
    combine_group_status,
    empty_guest_section,
    parse_failed_services,
    parse_guest_probe_results,
    parse_journal_entries,
    parse_process_list,
    parse_service_units,
    redact_sensitive_text,
    resource_detail_says_stopped,
)

# ---------------------------------------------------------------------------
# systemctl parsers
# ---------------------------------------------------------------------------


def test_parse_service_units_counts_active_and_sub_states() -> None:
    output = (
        "sshd.service              loaded active   running  OpenSSH server\n"
        "cron.service              loaded active   running  Regular background program\n"
        "nginx.service             loaded failed   failed   A high performance web server\n"
        "student-app.service       loaded active   exited   Student Application\n"
        "dropbear.service          loaded inactive dead     BSD_DROPBEAR daemon\n"
    )
    stats = parse_service_units(output)
    assert stats.total == 5
    assert stats.by_active_state == {
        "active": 3,
        "failed": 1,
        "inactive": 1,
    }
    assert stats.by_sub_state == {
        "running": 2,
        "failed": 1,
        "exited": 1,
        "dead": 1,
    }
    assert stats.skipped_rows == 0
    assert stats.hit_row_cap is False


def test_parse_service_units_skips_broken_rows_and_empty_output() -> None:
    stats = parse_service_units("only-one-column\n")
    assert stats.total == 0
    assert stats.skipped_rows == 1
    assert parse_service_units("").total == 0


def test_parse_failed_services_keeps_service_units_only() -> None:
    output = (
        "nginx.service        loaded failed failed A high performance web server\n"
        "cups.socket          loaded failed failed  -- printer socket\n"
        "student-app.service  loaded failed failed Student Application --password=abc123\n"
    )
    failed, skipped = parse_failed_services(output)
    assert skipped == 0
    names = [entry["name"] for entry in failed]
    assert names == ["nginx.service", "student-app.service"]
    assert failed[1]["description"].endswith("[REDACTED]")


# ---------------------------------------------------------------------------
# ps parser
# ---------------------------------------------------------------------------


def test_parse_process_list_parses_fields_and_redacts_args() -> None:
    output = (
        "  1234 www-data python3 82.5 12.3 3600 python3 main.py --password=secret123\n"
        "  1200 root kworker 0.0 0.1 7200 [kworker/0:1]\n"
        "  1199 root nginx 1.5 30.8 86400 nginx: worker process --api-key=sk-abc\n"
    )
    parsed = parse_process_list(output, limit=10)
    assert len(parsed.entries) == 3
    first = parsed.entries[0]
    assert first["pid"] == 1234
    assert first["user"] == "www-data"
    assert first["comm"] == "python3"
    assert first["cpu_pct"] == 82.5
    assert first["mem_pct"] == 12.3
    assert first["uptime_seconds"] == 3600
    assert "--password=[REDACTED]" in first["args"]
    assert "secret123" not in first["args"]
    assert parsed.skipped_rows == 0
    assert parsed.hit_row_cap is False


def test_parse_process_list_limits_and_counts_broken_rows() -> None:
    lines = [
        f"  {i} u{i} proc 1.0 1.0 100 /usr/bin/proc{i}"
        for i in range(1, 15)
    ]
    lines.insert(5, "broken-row")
    parsed = parse_process_list("\n".join(lines), limit=10)
    assert len(parsed.entries) == 10
    assert parsed.skipped_rows == 1
    assert parsed.hit_row_cap is True


def test_parse_process_list_skips_unparseable_numbers() -> None:
    output = "  abc root proc not-a-float 1.0 100 cmd\n"
    parsed = parse_process_list(output, limit=10)
    assert parsed.entries == []
    assert parsed.skipped_rows == 1


# ---------------------------------------------------------------------------
# journal parser
# ---------------------------------------------------------------------------


def test_parse_journal_entries_parses_and_orders_fields() -> None:
    output = (
        '{"__REALTIME_TIMESTAMP":"1725770400000000",'
        '"_SYSTEMD_UNIT":"student-app.service","PRIORITY":"3",'
        '"MESSAGE":"Main process exited, status=1/FAILURE"}\n'
        '{"__REALTIME_TIMESTAMP":"1725770401000000","PRIORITY":"2",'
        '"SYSLOG_IDENTIFIER":"kernel","MESSAGE":"oops"}\n'
    )
    parsed = parse_journal_entries(output)
    assert parsed.entries[0] == {
        "timestamp": "2024-09-08T04:40:00Z",
        "unit": "student-app.service",
        "priority": 3,
        "message": "Main process exited, status=1/FAILURE",
    }
    assert parsed.entries[1]["unit"] == "kernel"
    assert parsed.entries[1]["timestamp"] == "2024-09-08T04:40:01Z"
    assert parsed.skipped_rows == 0
    assert parsed.hit_row_cap is False


def test_parse_journal_entries_handles_broken_lines_and_cap() -> None:
    lines = ["{broken json"]
    lines += [
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1725770400000000",
                "PRIORITY": "4",
                "MESSAGE": f"entry {i}",
            }
        )
        for i in range(1, JOURNAL_MAX_ENTRIES + 5)
    ]
    parsed = parse_journal_entries("\n".join(lines))
    assert len(parsed.entries) == JOURNAL_MAX_ENTRIES
    assert parsed.skipped_rows == 1
    assert parsed.hit_row_cap is True


def test_parse_journal_entries_redacts_and_truncates_message() -> None:
    message_text = "password=hunter2 " + "x" * 400
    output = json.dumps(
        {
            "__REALTIME_TIMESTAMP": "1725770400000000",
            "PRIORITY": "3",
            "MESSAGE": message_text,
        }
    )
    parsed = parse_journal_entries(output)
    message = parsed.entries[0]["message"]
    assert len(message) <= 300
    assert "hunter2" not in message


def test_parse_journal_entries_tolerates_missing_fields() -> None:
    output = (
        '{"__REALTIME_TIMESTAMP":"not-a-number","PRIORITY":"x","MESSAGE":"m"}\n'
        '{"MESSAGE":"plain"}\n'
        "not json at all\n"
    )
    parsed = parse_journal_entries(output)
    assert parsed.entries[0]["timestamp"] is None
    assert parsed.entries[0]["priority"] is None
    assert parsed.entries[1]["timestamp"] is None
    assert parsed.entries[1]["priority"] is None
    assert parsed.skipped_rows == 1


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_sensitive_text_covers_cli_and_uri_and_env() -> None:
    text = (
        "python3 app.py --password=hunter2 --api-key=sk-123 --token abc "
        "http://admin:pw@db.internal:5432/x POSTGRES_PASSWORD=xyz"
    )
    redacted = redact_sensitive_text(text)
    assert "hunter2" not in redacted
    assert "sk-123" not in redacted
    assert "--token [REDACTED]" in redacted
    assert "admin:pw@" not in redacted
    assert "xyz" not in redacted
    assert "--password=[REDACTED]" in redacted
    assert "--api-key=[REDACTED]" in redacted
    assert "http://[REDACTED]@db.internal" in redacted


def test_redact_sensitive_text_redacts_private_key_blocks() -> None:
    text = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAA\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    assert redact_sensitive_text(text) == "[REDACTED PRIVATE KEY]"


# ---------------------------------------------------------------------------
# section status 組合
# ---------------------------------------------------------------------------


def test_combine_group_status_rules() -> None:
    assert combine_group_status(["ok", "ok"]) == "ok"
    assert combine_group_status(["ok", "error"]) == "partial"
    assert combine_group_status(["ok", "partial"]) == "partial"
    assert combine_group_status(["unavailable", "unavailable"]) == "unavailable"
    assert combine_group_status(["error", "error"]) == "error"


def test_combine_collection_status_rules() -> None:
    assert combine_collection_status(["ok", "ok", "ok", "ok"]) == "ok"
    assert combine_collection_status(["ok", "unavailable", "error", "ok"]) == "partial"
    assert combine_collection_status(["error", "error", "error", "error"]) == "error"


def test_probe_outcome_maps_systemd_unavailable() -> None:
    results = parse_guest_probe_results(
        {
            "service_units": ProbeResult(
                name="service_units",
                exit_code=1,
                stderr="System has not been booted with systemd as init system",
            ),
            "failed_services": ProbeResult(
                name="failed_services",
                exit_code=1,
                stderr="System has not been booted with systemd as init system",
            ),
            "processes_top_cpu": ProbeResult(
                name="processes_top_cpu",
                exit_code=0,
                stdout="  1 root systemd 0.0 0.1 100 /sbin/init\n",
            ),
            "processes_top_memory": ProbeResult(
                name="processes_top_memory",
                exit_code=0,
                stdout="  1 root systemd 0.0 0.1 100 /sbin/init\n",
            ),
            "recent_logs": ProbeResult(
                name="recent_logs",
                exit_code=1,
                stderr="No journal files were found.",
            ),
        }
    )
    assert results["services"]["collection_status"] == "unavailable"
    assert results["services"]["error_code"] == "systemdUnavailable"
    assert results["processes"]["collection_status"] == "ok"
    assert results["recent_logs"]["collection_status"] == "unavailable"


# ---------------------------------------------------------------------------
# 固定 JSON 契約
# ---------------------------------------------------------------------------


def test_empty_guest_section_keeps_fixed_shape() -> None:
    for section, status in (
        ("services", "error"),
        ("processes", "unavailable"),
        ("recent_logs", "error"),
    ):
        empty = empty_guest_section(section, status)
        assert empty["collection_status"] == status
    assert empty_guest_section("services", "error")["total"] == 0
    assert empty_guest_section("services", "error")["failed_services"] == []
    assert empty_guest_section("processes", "error")["limit_per_list"] == TOP_PROCESSES_LIMIT
    logs = empty_guest_section("recent_logs", "error")
    assert logs["window_minutes"] == JOURNAL_WINDOW_MINUTES
    assert logs["minimum_priority"] == JOURNAL_MINIMUM_PRIORITY
    assert logs["limit"] == JOURNAL_MAX_ENTRIES
    assert logs["may_be_truncated"] is False
    assert logs["entries"] == []


def test_build_guest_diagnostics_result_fixed_top_level_fields() -> None:
    result = build_guest_diagnostics_result(
        vmid=105,
        collected_at=datetime(2026, 9, 8, 14, 0, 0),
        collection_duration_ms=1380,
        resource=build_resource_section(
            {"summary": {"status": "running"}}, status="ok"
        ),
        sections={
            "services": empty_guest_section("services", "ok") | {"total": 83},
            "processes": empty_guest_section("processes", "ok"),
            "recent_logs": empty_guest_section("recent_logs", "partial"),
        },
        warnings=[
            "a",
            "a",
            "b",
            "",
        ],
    )
    assert list(result) == [
        "vmid",
        "collected_at",
        "collection_duration_ms",
        "collection_status",
        "resource",
        "services",
        "processes",
        "recent_logs",
        "warnings",
    ]
    assert result["collected_at"] == "2026-09-08T14:00:00Z"
    assert result["collection_status"] == "partial"
    assert result["warnings"] == ["a", "b"]
    assert result["services"]["total"] == 83


def test_resource_detail_says_stopped() -> None:
    assert resource_detail_says_stopped({"summary": {"status": "stopped"}})
    assert not resource_detail_says_stopped({"summary": {"status": "running"}})
    assert not resource_detail_says_stopped(None)
    assert not resource_detail_says_stopped({"summary": None})


# ---------------------------------------------------------------------------
# 單一 probe 失敗不得清空其他成功 sections
# ---------------------------------------------------------------------------


def test_probe_failure_preserves_other_sections() -> None:
    sections = parse_guest_probe_results(
        {
            "service_units": ProbeResult(
                name="service_units",
                exit_code=0,
                stdout=(
                    "a.service loaded active running A\n"
                    "b.service loaded failed failed B\n"
                ),
            ),
            "failed_services": ProbeResult(
                name="failed_services", error_code=ERROR_CODE_COMMAND_FAILED
            ),
            "processes_top_cpu": ProbeResult(
                name="processes_top_cpu",
                exit_code=0,
                stdout="  1 root init 0.0 0.1 100 /sbin/init\n",
            ),
            "processes_top_memory": ProbeResult(
                name="processes_top_memory",
                exit_code=0,
                stdout="  1 root init 0.0 0.1 100 /sbin/init\n",
            ),
            "recent_logs": ProbeResult(
                name="recent_logs",
                exit_code=1,
                stderr="No journal files were found.",
            ),
        }
    )
    assert sections["services"]["collection_status"] == "partial"
    assert sections["services"]["total"] == 2
    assert sections["services"]["by_active_state"]["failed"] == 1
    assert sections["services"]["failed_services"] == []
    assert sections["processes"]["collection_status"] == "ok"
    assert len(sections["processes"]["top_cpu"]) == 1
    assert sections["recent_logs"]["collection_status"] == "unavailable"


def test_probe_failure_produces_data_gap_warnings() -> None:
    warnings: list[str] = []
    parse_guest_probe_results(
        {
            probe.name: ProbeResult(name=probe.name, error_code=ERROR_CODE_RESOLVE_FAILED)
            for probe in GUEST_DIAGNOSTIC_PROBES
        },
        warnings=warnings,
    )
    assert warnings
    assert any("收集失敗" in warning for warning in warnings)


def test_journal_limit_sets_may_be_truncated() -> None:
    lines = [
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1725770400000000",
                "PRIORITY": "3",
                "MESSAGE": f"e{i}",
            }
        )
        for i in range(JOURNAL_MAX_ENTRIES)
    ]
    sections = parse_guest_probe_results(
        {
            "service_units": ProbeResult(
                name="service_units",
                exit_code=0,
                stdout="a.service loaded active running A\n",
            ),
            "failed_services": ProbeResult(
                name="failed_services", exit_code=0, stdout=""
            ),
            "processes_top_cpu": ProbeResult(
                name="processes_top_cpu",
                exit_code=0,
                stdout="  1 root init 0.0 0.1 100 /sbin/init\n",
            ),
            "processes_top_memory": ProbeResult(
                name="processes_top_memory",
                exit_code=0,
                stdout="  1 root init 0.0 0.1 100 /sbin/init\n",
            ),
            "recent_logs": ProbeResult(
                name="recent_logs", exit_code=0, stdout="\n".join(lines)
            ),
        }
    )
    logs = sections["recent_logs"]
    assert logs["returned_entries"] == JOURNAL_MAX_ENTRIES
    assert logs["may_be_truncated"] is True


# ---------------------------------------------------------------------------
# Tool 註冊與 dispatch
# ---------------------------------------------------------------------------


def test_guest_diagnostic_tool_is_registered_with_vmid_only() -> None:
    tools = {
        tool["function"]["name"]: tool["function"] for tool in chat_module._TOOLS
    }
    assert "get_guest_diagnostic_summary" in tools
    function = tools["get_guest_diagnostic_summary"]
    assert function["parameters"]["required"] == ["vmid"]
    assert set(function["parameters"]["properties"]) == {"vmid"}
    assert "get_guest_diagnostic_summary" in chat_module._ALLOWED_TOOL_NAMES


def test_guest_probe_commands_are_server_owned_and_bounded() -> None:
    for probe in GUEST_DIAGNOSTIC_PROBES:
        assert probe.max_output_bytes > 0
        assert probe.exec_timeout > 0
    journal_probe = next(
        probe for probe in GUEST_DIAGNOSTIC_PROBES if probe.name == "recent_logs"
    )
    assert "--since" in journal_probe.command
    assert f"-n {JOURNAL_MAX_ENTRIES}" in journal_probe.command
    assert "-p warning" in journal_probe.command
    ps_probe = next(
        probe for probe in GUEST_DIAGNOSTIC_PROBES if probe.name == "processes_top_cpu"
    )
    assert "etimes=" in ps_probe.command


class _FakeContext:
    def __init__(self, detail: dict[str, Any] | Exception) -> None:
        self._detail = detail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        allowed_vmids: set[int] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        if isinstance(self._detail, Exception):
            raise self._detail
        return self._detail


def _assert_fixed_sections(result: dict[str, Any]) -> None:
    assert set(result) == {
        "vmid",
        "collected_at",
        "collection_duration_ms",
        "collection_status",
        "resource",
        "services",
        "processes",
        "recent_logs",
        "warnings",
    }


@pytest.mark.asyncio
async def test_guest_tool_rejects_invalid_vmid_before_any_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext({"summary": {"status": "running"}})
    monkeypatch.setattr(
        ssh_exec_module,
        "run_guest_probe_batch",
        lambda *args, **kwargs: pytest.fail("must not run SSH for invalid vmid"),
    )
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": "not-a-number"},
        context=context,
        allowed_vmids=None,
    )
    _assert_fixed_sections(result)
    assert result["collection_status"] == "error"
    assert result["vmid"] is None
    assert context.calls == []


@pytest.mark.asyncio
async def test_guest_tool_rejects_out_of_scope_vmid_before_pve_and_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext({"summary": {"status": "running"}})
    monkeypatch.setattr(
        ssh_exec_module,
        "run_guest_probe_batch",
        lambda *args, **kwargs: pytest.fail("must not run SSH out of scope"),
    )
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": 999},
        context=context,
        allowed_vmids={105},
    )
    _assert_fixed_sections(result)
    assert result["collection_status"] == "error"
    assert result["resource"]["error_code"] == "scopeRestricted"
    assert context.calls == []


@pytest.mark.asyncio
async def test_guest_tool_skips_ssh_for_stopped_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext(
        {
            "summary": {"status": "stopped"},
            "status": {"status": "stopped"},
            "config": None,
            "network_interfaces": [],
        }
    )
    monkeypatch.setattr(
        ssh_exec_module,
        "run_guest_probe_batch",
        lambda *args, **kwargs: pytest.fail("must not SSH into a stopped VM"),
    )
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": 105},
        context=context,
        allowed_vmids={105},
    )
    _assert_fixed_sections(result)
    assert result["resource"]["collection_status"] == "ok"
    assert result["services"]["collection_status"] == "unavailable"
    assert result["processes"]["collection_status"] == "unavailable"
    assert result["recent_logs"]["collection_status"] == "unavailable"
    assert result["collection_status"] == "partial"
    assert context.calls == [("get_resource_detail", {"vmid": 105})]


@pytest.mark.asyncio
async def test_guest_tool_keeps_running_vm_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext({"summary": {"status": "running"}})

    async def _fake_batch(
        vmid: int,
        probes: tuple[GuestProbe, ...],
        *,
        session: Any = None,
        allowed_vmids: set[int] | None = None,
    ) -> dict[str, ProbeResult]:
        assert vmid == 105
        assert allowed_vmids == {105}
        return {
            probe.name: ProbeResult(
                name=probe.name,
                exit_code=0,
                stdout="a.service loaded active running A\n"
                if probe.name == "service_units"
                else "  1 root init 0.0 0.1 100 /sbin/init\n"
                if probe.name.startswith("processes")
                else "",
            )
            for probe in probes
        }

    monkeypatch.setattr(ssh_exec_module, "run_guest_probe_batch", _fake_batch)
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": 105},
        context=context,
        allowed_vmids={105},
    )
    _assert_fixed_sections(result)
    assert result["collection_status"] == "ok"
    assert result["resource"]["collection_status"] == "ok"
    assert result["services"]["total"] == 1
    assert result["processes"]["top_cpu"][0]["comm"] == "init"
    assert result["recent_logs"]["collection_status"] == "ok"
    assert result["recent_logs"]["entries"] == []


@pytest.mark.asyncio
async def test_guest_tool_collects_guest_when_pve_detail_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext(RuntimeError("proxmox unreachable"))

    async def _fake_batch(
        vmid: int,
        probes: tuple[GuestProbe, ...],
        *,
        session: Any = None,
        allowed_vmids: set[int] | None = None,
    ) -> dict[str, ProbeResult]:
        return {
            probe.name: ProbeResult(
                name=probe.name,
                exit_code=0,
                stdout="a.service loaded active running A\n"
                if probe.name == "service_units"
                else "  1 root init 0.0 0.1 100 /sbin/init\n"
                if probe.name.startswith("processes")
                else "",
            )
            for probe in probes
        }

    monkeypatch.setattr(ssh_exec_module, "run_guest_probe_batch", _fake_batch)
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": 105},
        context=context,
        allowed_vmids={105},
    )
    assert result["resource"]["collection_status"] == "error"
    assert result["services"]["collection_status"] == "ok"
    assert result["processes"]["collection_status"] == "ok"
    assert result["collection_status"] == "partial"
    assert any("PVE" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_guest_tool_marks_connection_failure_not_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeContext({"summary": {"status": "running"}})

    async def _fake_batch(
        vmid: int,
        probes: tuple[GuestProbe, ...],
        *,
        session: Any = None,
        allowed_vmids: set[int] | None = None,
    ) -> dict[str, ProbeResult]:
        return {
            probe.name: ProbeResult(
                name=probe.name, error_code=ERROR_CODE_RESOLVE_FAILED
            )
            for probe in probes
        }

    monkeypatch.setattr(ssh_exec_module, "run_guest_probe_batch", _fake_batch)
    result = await chat_module._execute_guest_diagnostics_tool(
        {"vmid": 105},
        context=context,
        allowed_vmids={105},
    )
    assert result["collection_status"] == "partial"
    assert result["services"]["collection_status"] == "error"
    assert result["services"]["error_code"] == "resolveFailed"
    assert result["resource"]["collection_status"] == "ok"


def test_guest_probe_batch_rejects_out_of_scope_vmid() -> None:
    with pytest.raises(ValueError):
        # Scope guard 在任何 PVE/SSH I/O 之前即拒絕。
        asyncio.run(
            ssh_exec_module.run_guest_probe_batch(
                999,
                GUEST_DIAGNOSTIC_PROBES,
                allowed_vmids={105},
            )
        )
