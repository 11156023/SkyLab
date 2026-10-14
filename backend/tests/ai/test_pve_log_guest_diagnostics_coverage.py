"""Coverage tests for app.ai.pve_log.guest_diagnostics parsers and sections."""

import pytest

from app.ai.pve_log import guest_diagnostics as gd
from app.ai.pve_log.guest_diagnostics import ProbeResult


def test_find_group_rejects_unknown_section() -> None:
    with pytest.raises(KeyError):
        gd._find_group("nope")


def test_parse_service_units_skips_blank_lines_and_caps_rows(monkeypatch) -> None:
    monkeypatch.setattr(gd, "MAX_PROBE_ROWS", 2)
    output = "\n".join(
        [
            "",
            "a.service loaded active running A",
            "broken",
            "b.service loaded active running B",
            "c.service loaded failed failed C",
        ]
    )
    stats = gd.parse_service_units(output)
    assert stats.total == 2
    assert stats.skipped_rows == 1
    assert stats.hit_row_cap is True


def test_parse_failed_services_filters_and_caps() -> None:
    rows = ["", "short row", "x.socket loaded failed failed Socket"]
    rows += [f"s{i}.service loaded failed failed Service {i}" for i in range(25)]
    entries, skipped = gd.parse_failed_services("\n".join(rows))
    assert skipped == 1
    assert len(entries) == gd.MAX_FAILED_SERVICES
    assert entries[0]["name"] == "s0.service"


def test_parse_process_list_and_journal_skip_blank_lines() -> None:
    processes = gd.parse_process_list("\n1 root init 0.1 0.2 30 /sbin/init\n")
    assert [item["pid"] for item in processes.entries] == [1]

    journal = gd.parse_journal_entries('\n[1]\n{"MESSAGE": "hi"}\n')
    assert journal.skipped_rows == 1
    assert journal.entries[0]["message"] == "hi"


def test_probe_outcome_branches() -> None:
    assert gd._probe_outcome(None) == (gd.STATUS_ERROR, gd.ERROR_CODE_COMMAND_FAILED)
    assert gd._probe_outcome(ProbeResult(name="x", exit_code=1, stdout="data")) == (
        gd.STATUS_PARTIAL,
        gd.ERROR_CODE_COMMAND_FAILED,
    )
    assert gd._probe_outcome(ProbeResult(name="x", exit_code=1, stdout=" ")) == (
        gd.STATUS_ERROR,
        gd.ERROR_CODE_COMMAND_FAILED,
    )


def test_sections_report_skipped_capped_and_truncated_output(monkeypatch) -> None:
    monkeypatch.setattr(gd, "MAX_PROBE_ROWS", 1)
    process_rows = ["bad row here x y z", "x root comm 1 1 1 args"]
    process_rows += [f"{pid} root p 1.0 1.0 5 cmd" for pid in range(1, 13)]
    results = {
        "service_units": ProbeResult(
            name="service_units",
            exit_code=0,
            stdout="bad\na.service loaded active running A\nb.service loaded active running B",
            truncated=True,
        ),
        "failed_services": ProbeResult(
            name="failed_services",
            exit_code=0,
            stdout="short row\nz.service loaded failed failed Z",
            truncated=True,
        ),
        "processes_top_cpu": ProbeResult(
            name="processes_top_cpu",
            exit_code=0,
            stdout="\n".join(process_rows),
            truncated=True,
        ),
        "processes_top_memory": ProbeResult(
            name="processes_top_memory", exit_code=0, stdout=""
        ),
        "recent_logs": ProbeResult(
            name="recent_logs",
            exit_code=0,
            stdout='not json\n{"MESSAGE": "warn"}',
            truncated=True,
        ),
    }
    warnings: list[str] = []
    sections = gd.parse_guest_probe_results(results, warnings=warnings)

    assert sections["services"]["collection_status"] == gd.STATUS_PARTIAL
    assert sections["services"]["total"] == 1
    assert sections["services"]["failed_services"][0]["name"] == "z.service"
    assert sections["processes"]["collection_status"] == gd.STATUS_PARTIAL
    assert len(sections["processes"]["top_cpu"]) == gd.TOP_PROCESSES_LIMIT
    assert sections["recent_logs"]["may_be_truncated"] is True
    assert sections["recent_logs"]["returned_entries"] == 1
    assert warnings
