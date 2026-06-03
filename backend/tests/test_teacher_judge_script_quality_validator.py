from __future__ import annotations

from textwrap import dedent

import pytest

from app.ai.teacher_judge.script_quality_validator import check_script_quality


def _check(script_content: str) -> dict:
    return check_script_quality(dedent(script_content).strip())


def _assert_blocked(script_content: str, *issue_keywords: str) -> None:
    result = _check(script_content)

    assert result["approved"] is False
    assert result["blocked"] is True

    if issue_keywords:
        issues_text = "\n".join(result["issues"]).lower()
        assert any(keyword.lower() in issues_text for keyword in issue_keywords), (
            f"expected one of {issue_keywords!r} in issues, got {result['issues']!r}"
        )


def test_quality_validator_blocks_stdout_truthiness_as_pass() -> None:
    _assert_blocked(
        """
        import json
        import subprocess

        completed = subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = "pass" if completed.stdout else "fail"
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": [{
                "id": "python-version",
                "title": "Python version",
                "status": status,
                "evidence": completed.stdout,
                "raw": completed.stdout,
            }],
            "errors": [],
        }))
        """,
        "stdout",
        "pass",
        "truth",
    )


@pytest.mark.parametrize(
    ("script_content", "issue_keywords"),
    [
        (
            """
            import json
            import subprocess

            try:
                subprocess.run(
                    ["python", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                status = "pass"
                evidence = "ok"
            except Exception:
                status = "pass"
                evidence = "fallback"

            print(json.dumps({
                "schema_version": "teacher_judge_result.v1",
                "summary": "checked",
                "checks": [{
                    "id": "python-version",
                    "title": "Python version",
                    "status": status,
                    "evidence": evidence,
                    "raw": "",
                }],
                "errors": [],
            }))
            """,
            ("except", "pass"),
        ),
        (
            """
            import json
            import subprocess

            try:
                subprocess.run(
                    ["python", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass

            print(json.dumps({
                "schema_version": "teacher_judge_result.v1",
                "summary": "checked",
                "checks": [{
                    "id": "python-version",
                    "title": "Python version",
                    "status": "pass",
                    "evidence": "completed",
                    "raw": "",
                }],
                "errors": [],
            }))
            """,
            ("except", "swallow", "pass"),
        ),
    ],
)
def test_quality_validator_blocks_swallowed_or_passed_generic_exceptions(
    script_content: str,
    issue_keywords: tuple[str, ...],
) -> None:
    _assert_blocked(script_content, *issue_keywords)


def test_quality_validator_blocks_unbounded_raw_stdout_stderr_capture() -> None:
    _assert_blocked(
        """
        import json
        import subprocess

        completed = subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": [{
                "id": "python-version",
                "title": "Python version",
                "status": "pass" if completed.returncode == 0 else "fail",
                "evidence": completed.stdout + completed.stderr,
                "raw": completed.stdout + completed.stderr,
            }],
            "errors": [],
        }))
        """,
        "raw",
        "stdout",
        "stderr",
        "truncate",
    )


def test_quality_validator_blocks_helper_shell_without_sanitized_raw() -> None:
    _assert_blocked(
        """
        import json
        import shutil
        import subprocess

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return text

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": raw,
            }

        checks = []
        if command_available("python"):
            completed = subprocess.run(
                ["python", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            checks.append(record_check(
                "python-version",
                "Python version",
                "pass" if completed.returncode == 0 else "fail",
                "ok",
                raw="demo",
            ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": checks,
            "errors": [],
        }))
        """,
        "record_check",
        "redact",
        "truncate",
    )


def test_quality_validator_blocks_external_tool_without_fallback() -> None:
    _assert_blocked(
        """
        import json
        import subprocess

        completed = subprocess.run(
            ["curl", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": [{
                "id": "curl-version",
                "title": "curl version",
                "status": "pass" if completed.returncode == 0 else "fail",
                "evidence": (completed.stdout or completed.stderr)[:200],
                "raw": (completed.stdout or completed.stderr)[:200],
            }],
            "errors": [],
        }))
        """,
        "shutil.which",
        "fallback",
        "tool",
    )


def test_quality_validator_blocks_run_command_without_returncode() -> None:
    _assert_blocked(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|api[_ -]?key|private[_ -]?key|bearer)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, str]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {"stdout": completed.stdout, "stderr": completed.stderr}

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        if command_available("python"):
            result = run_command(["python", "--version"])
            checks.append(record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "collected",
                raw=json.dumps(result, ensure_ascii=False),
            ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": [],
        }, ensure_ascii=False))
        """,
        "returncode",
    )


def test_quality_validator_blocks_json_dumps_without_ensure_ascii_false() -> None:
    _assert_blocked(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|api[_ -]?key|private[_ -]?key|bearer)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        result = run_command(["python", "--version"])
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": [record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "collected",
                raw=json.dumps(result),
            )],
            "errors": [],
        }))
        """,
        "ensure_ascii",
    )


def test_quality_validator_blocks_bare_key_redaction() -> None:
    _assert_blocked(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|key)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        result = run_command(["python", "--version"])
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": [record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "collected",
                raw=json.dumps(result, ensure_ascii=False),
            )],
            "errors": [],
        }, ensure_ascii=False))
        """,
        "key",
    )


def test_quality_validator_blocks_missing_metadata() -> None:
    _assert_blocked(
        """
        import json
        import re
        import shutil
        import subprocess

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|api[_ -]?key|private[_ -]?key|bearer)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        result = run_command(["python", "--version"])
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "collected",
            "checks": [record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "collected",
                raw=json.dumps(result, ensure_ascii=False),
            )],
            "errors": [],
        }, ensure_ascii=False))
        """,
        "metadata",
        "timestamp",
        "platform",
    )


def test_quality_validator_blocks_generic_id_and_check_title() -> None:
    _assert_blocked(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|api[_ -]?key|private[_ -]?key|bearer)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        result = run_command(["python", "--version"])
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": [record_check(
                "check-1",
                "檢查 Python 版本",
                "unknown",
                "collected",
                raw=json.dumps(result, ensure_ascii=False),
            )],
            "errors": [],
        }, ensure_ascii=False))
        """,
        "語意化",
        "收集",
    )


def test_quality_validator_blocks_warning_for_unknown_only_conditions() -> None:
    _assert_blocked(
        """
        import json
        import re
        import shutil
        import subprocess

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        if not command_available("python"):
            checks.append(record_check(
                "python-version",
                "Python version",
                "warning",
                "python not found",
            ))
        else:
            try:
                subprocess.run(
                    ["python", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                checks.append(record_check(
                    "python-version",
                    "Python version",
                    "warning",
                    "timeout",
                ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": checks,
            "errors": [],
        }))
        """,
        "unknown",
        "warning",
    )


@pytest.mark.parametrize(
    ("exception_name", "snippet"),
    [
        ("subprocess.TimeoutExpired", "timeout"),
        ("FileNotFoundError", "not found"),
        ("PermissionError", "permission"),
    ],
)
def test_quality_validator_blocks_terminal_failures_marked_as_pass(
    exception_name: str,
    snippet: str,
) -> None:
    _assert_blocked(
        f"""
        import json
        import subprocess

        try:
            subprocess.run(
                ["python", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            status = "pass"
            evidence = "ok"
        except {exception_name} as exc:
            status = "pass"
            evidence = str(exc)

        print(json.dumps({{
            "schema_version": "teacher_judge_result.v1",
            "summary": "checked",
            "checks": [{{
                "id": "python-version",
                "title": "Python version",
                "status": status,
                "evidence": evidence,
                "raw": "",
            }}],
            "errors": [],
        }}))
        """,
        snippet,
        "pass",
    )


def test_quality_validator_allows_minimal_compliant_script() -> None:
    result = _check(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"(token|password|secret|api[_ -]?key|private[_ -]?key|bearer)", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return {
                    "stdout": "",
                    "stderr": f"timeout after {exc.timeout}s",
                    "returncode": None,
                }
            except FileNotFoundError as exc:
                return {"stdout": "", "stderr": str(exc), "returncode": None}
            except PermissionError as exc:
                return {"stdout": "", "stderr": str(exc), "returncode": None}
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        python_bin = shutil.which("python")

        if not command_available("python") or python_bin is None:
            checks.append(record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "python not found",
            ))
        else:
            try:
                result = run_command([python_bin, "--version"])
            except subprocess.TimeoutExpired as exc:
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "unknown",
                    f"timeout after {exc.timeout}s",
                ))
            except FileNotFoundError as exc:
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "unknown",
                    str(exc)[:200],
                ))
            except PermissionError as exc:
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "unknown",
                    str(exc)[:200],
                ))
            else:
                snippet = str(result["stdout"] or result["stderr"] or "")
                returncode = result["returncode"]
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "pass" if returncode == 0 else "fail",
                    truncate_output(snippet),
                    raw=json.dumps(result, ensure_ascii=False),
                ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": [],
        }, ensure_ascii=False))
        """
    )

    assert result["approved"] is True
    assert result["blocked"] is False
    assert result["issues"] == []


# ── errors 記錄品質測試 ──


def test_blocks_bare_except_without_errors_append() -> None:
    _assert_blocked(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"stdout": "", "stderr": "timeout", "returncode": None}
            except FileNotFoundError:
                return {"stdout": "", "stderr": "not found", "returncode": None}
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        if command_available("python"):
            try:
                result = run_command(["python", "--version"])
            except:
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "fail",
                    "bare except caught",
                ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": [],
        }, ensure_ascii=False))
        """,
        "bare except",
        "errors",
    )


def test_allows_bare_except_with_errors_append() -> None:
    """bare except 有 errors.append → 通過"""
    result = _check(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        errors: list[str] = []

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"stdout": "", "stderr": "timeout", "returncode": None}
            except FileNotFoundError:
                return {"stdout": "", "stderr": "not found", "returncode": None}
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        if command_available("python"):
            try:
                result = run_command(["python", "--version"])
            except:
                errors.append(f"runtime.python_version: 發生未預期錯誤")
                checks.append(record_check(
                    "runtime.python_version",
                    "收集 Python 版本",
                    "unknown",
                    "bare except caught",
                ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": errors,
        }, ensure_ascii=False))
        """
    )

    assert result["approved"] is True
    assert result["blocked"] is False
    assert result["issues"] == []


def test_allows_run_command_generic_exception_with_structured_error_return() -> None:
    result = _check(
        """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        errors: list[str] = []

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except Exception as exc:
                return {"stdout": "", "stderr": str(exc), "returncode": None}
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        python_bin = shutil.which("python")
        if not command_available("python") or python_bin is None:
            checks.append(record_check(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "python not found",
            ))
        else:
            result = run_command([python_bin, "--version"])
            if result["returncode"] is None:
                errors.append("runtime.python_version: 指令執行失敗")
                status = "unknown"
            else:
                status = "pass" if result["returncode"] == 0 else "fail"
            checks.append(record_check(
                "runtime.python_version",
                "收集 Python 版本",
                status,
                str(result["stdout"] or result["stderr"] or ""),
                raw=json.dumps(result, ensure_ascii=False),
            ))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": errors,
        }, ensure_ascii=False))
        """
    )

    assert result["approved"] is True
    assert result["blocked"] is False
    assert result["issues"] == []


def test_allows_import_aliases_for_quality_call_detection() -> None:
    result = _check(
        """
        import json as js
        import platform
        import re
        import subprocess as sp
        from datetime import datetime, timezone
        from helpers import record_check as rc
        from shutil import which as shwhich
        from subprocess import TimeoutExpired as CmdTimeout

        errors: list[str] = []

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shwhich(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            try:
                completed = sp.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except CmdTimeout:
                return {"stdout": "", "stderr": "timeout", "returncode": None}
            except FileNotFoundError:
                return {"stdout": "", "stderr": "not found", "returncode": None}
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        if not command_available("python"):
            checks.append(rc(
                "runtime.python_version",
                "收集 Python 版本",
                "unknown",
                "python not found",
            ))
        else:
            result = run_command(["python", "--version"])
            if result["returncode"] is None:
                errors.append("runtime.python_version: 指令執行失敗")
                status = "unknown"
            else:
                status = "pass" if result["returncode"] == 0 else "fail"
            checks.append(rc(
                "runtime.python_version",
                "收集 Python 版本",
                status,
                str(result["stdout"] or result["stderr"] or ""),
                raw=js.dumps(result, ensure_ascii=False),
            ))

        print(js.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": errors,
        }, ensure_ascii=False))
        """
    )

    assert result["approved"] is True
    assert result["blocked"] is False
    assert result["issues"] == []


def test_blocks_except_exception_without_errors_append() -> None:
    script_content = """
        import json
        import platform
        import re
        import shutil
        import subprocess
        from datetime import datetime, timezone

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def redact_sensitive_text(text: str) -> str:
            return re.sub(r"secret", "[redacted]", text, flags=re.IGNORECASE)

        def command_available(command: str) -> bool:
            return shutil.which(command) is not None

        def run_command(argv: list[str], timeout: int = 5) -> dict[str, object]:
            return {"stdout": "", "stderr": "", "returncode": 0}

        def record_check(check_id: str, title: str, status: str, evidence: str, raw: str = "") -> dict[str, str]:
            return {
                "id": check_id, "title": title, "status": status,
                "evidence": evidence, "raw": truncate_output(redact_sensitive_text(raw)),
            }

        checks = []
        try:
            result = run_command(["python", "--version"])
        except Exception:
            checks.append(record_check("python", "Python", "fail", "exception"))

        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
            },
            "summary": "collected",
            "checks": checks,
            "errors": [],
        }, ensure_ascii=False))
    """
    _assert_blocked(
        script_content,
        "except Exception",
        "errors",
    )
    result = _check(script_content)
    hint = next(
        hint
        for hint in result["fix_hints"]
        if hint.get("type") == "add_errors_append_in_except"
    )
    assert hint["lineno"] > 0
    assert hint["end_lineno"] >= hint["lineno"]
    assert "except Exception" in hint["snippet"]
    assert hint["target"] == "collection_exception_handler"
    assert "errors.append" in hint["required_pattern"]
