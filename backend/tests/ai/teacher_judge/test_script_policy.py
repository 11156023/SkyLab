"""Safety regression tests shared by deterministic script compilation."""

from __future__ import annotations

import pytest

from app.ai.teacher_judge.script_policy import (
    check_script_policy,
    validate_managed_script_output,
)


def test_script_policy_blocks_destructive_commands() -> None:
    result = check_script_policy(
        "import subprocess\nsubprocess.run('rm -rf /', shell=True)"
    )

    assert result["approved"] is False
    assert result["blocked"] is True
    assert any("rm -rf" in issue for issue in result["issues"])


@pytest.mark.parametrize(
    "argv",
    [
        '["bash", "-c", "echo hello"]',
        '["sh", "-c", "cat .env"]',
        '["git", "commit", "-m", "change"]',
    ],
)
def test_script_policy_blocks_shell_launchers_and_writing_git(argv: str) -> None:
    result = check_script_policy(
        f"""
import json
import subprocess

subprocess.run({argv}, timeout=5)
print(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is False


@pytest.mark.parametrize(
    "argv",
    [
        '["echo", "hello"]',
        '["git", "status", "--short"]',
        '["ping", "-c", "1", "127.0.0.1"]',
    ],
)
def test_script_policy_allows_read_only_commands(argv: str) -> None:
    result = check_script_policy(
        f"""
import json
import subprocess

subprocess.run({argv}, cwd="/srv/student/project", timeout=5)
print(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


def test_script_policy_blocks_external_network_requests() -> None:
    result = check_script_policy(
        """
import json
import requests

requests.get("https://example.com/collect", timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "checks": [], "errors": []}))
""".strip()
    )

    assert result["approved"] is False
    assert any("localhost" in issue for issue in result["issues"])


def test_script_policy_allows_localhost_get_with_timeout() -> None:
    result = check_script_policy(
        """
import json
import requests

requests.get("http://127.0.0.1:5678/health", timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


def test_validate_managed_script_output_contract() -> None:
    valid = validate_managed_script_output(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "now", "platform": "test"},
            "summary": "ok",
            "checks": [
                {
                    "id": "service",
                    "title": "Service check",
                    "status": "pass",
                    "evidence": "running",
                    "raw": "",
                }
            ],
            "errors": [],
        }
    )
    invalid = validate_managed_script_output(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "now", "platform": "test"},
            "checks": [{"id": "service", "title": "Service check", "status": "done"}],
            "errors": [],
        }
    )

    assert valid["valid"] is True
    assert valid["checks_count"] == 1
    assert invalid["valid"] is False
