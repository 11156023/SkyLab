"""Command paths are validated without resolving them against a directory."""

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    compile_check_plan,
    missing_execution_location,
)
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis


def analysis_for(argv):
    return TeacherJudgeRubricAnalysis(items=[{
        "id": "item", "title": "Command evidence", "target_node_key": "linux",
        "detectable": "auto", "judgement_mode": "teacher", "detection_method": "Collect evidence",
        "check_steps": [{
            "id": "step", "title": "Collect evidence",
            "collector": {"type": "command", "argv": argv},
        }],
    }])


@pytest.mark.parametrize("argv", [
    ["pip3", "list", "--path", "relative"],
    ["pip3", "list", "--path=relative"],
    ["pip3", "show", "requests", "--cert", "relative"],
    ["nginx", "-t", "-c", "relative"],
    ["nginx", "-t", "-p", "relative"],
    ["openssl", "x509", "-in", "relative", "-noout"],
    ["openssl", "verify", "relative"],
    ["openssl", "verify", "-CAfile", "relative", "/srv/cert.pem"],
    ["ssh-keygen", "-lfrelative"],
    ["date", "--reference=relative"],
    ["date", "-frelative"],
    ["find", "/srv", "-newer", "relative"],
    ["find", "-files0-from", "relative", "-name", "*.py"],
    ["journalctl", "--directory=relative", "-n", "20"],
    ["journalctl", "--file", "relative"],
    ["systemctl", "--root=relative", "is-enabled", "nginx"],
    ["curl", "--cacert", "relative", "http://localhost"],
    ["git", "-C", "relative", "status"],
    ["git", "-Crelative", "status"],
    ["git", "config", "--file=relative", "--list"],
    ["git", "-C", "/srv", "status", "relative"],
    ["git", "-C", "/srv", "ls-files", "relative"],
    ["git", "diff", "--no-index", "/srv/first", "relative"],
    ["docker", "compose", "--file", "relative", "config"],
    ["docker", "compose", "config", "--env-file=relative", "--project-directory=/srv"],
    ["npm", "list", "--prefix=relative"],
    ["grep", "--exclude-from=relative", "passed", "/srv/results.txt"],
    ["diff", "--exclude-from", "relative", "/srv/a", "/srv/b"],
    ["sort", "-Trelative", "/srv/results.txt"],
    ["xmllint", "--schema", "relative", "/srv/results.xml"],
    ["strings", "-n", "8", "relative"],
    ["od", "-An", "-N", "20", "relative"],
    ["hexdump", "-n", "20", "relative"],
    ["column", "-s", ",", "relative"],
    ["df", "relative"],
    ["df", "--output", "relative"],
    ["getcap", "-r", "relative"],
    ["test", "/srv/a", "-nt", "relative"],
    ["lsof", "-p", "123", "relative"],
    ["lsof", "+D", "relative"],
    ["lsblk", "-o", "NAME,SIZE", "relative"],
    ["lscpu", "--sysroot=relative"],
    ["dig", "-frelative"],
    ["ss", "-Frelative"],
    ["lspci", "-Frelative"],
    ["lsusb", "-Drelative"],
    ["curl", "-H", "@relative", "http://localhost"],
    ["curl", "--write-out=@relative", "http://localhost"],
    ["java", "-jar", "relative"],
    ["java", "-cp", "relative", "Main"],
    ["ruby", "-Irelative", "/srv/main.rb"],
    ["perl", "-Irelative", "/srv/main.pl"],
    ["rpm", "-qip", "relative"],
    ["mysql", "--version", "--socket=relative"],
    ["openssl", "x509", "-in", "/srv/cert.pem", "-rand", "/srv/random:relative"],
    ["openssl", "x509", "-in", "/srv/cert.pem", "-passin", "file:relative"],
    ["ruby", "-I/srv/lib:relative", "/srv/main.rb"],
])
def test_relative_path_options_block_readiness_and_compilation(argv):
    original = deepcopy(argv)
    relative = analysis_for(argv)
    blockers = get_script_generation_blockers(relative, [], require_typed_plan=True)
    assert blockers[0]["status"] == "missing_info"
    assert any("relative" in gap for gap in blockers[0]["missing_information"])
    with pytest.raises(CheckPlanContractError):
        compile_check_plan(relative, target_node_key="linux")
    assert argv == original

    absolute_argv = [arg.replace("relative", "/srv/evidence") for arg in argv]
    absolute = analysis_for(absolute_argv)
    assert get_script_generation_blockers(absolute, [], require_typed_plan=True) == []
    script, policy, _, plan = compile_check_plan(absolute, target_node_key="linux")
    assert policy["approved"] is True
    assert plan["items"][0]["check_steps"][0]["collector"]["argv"] == absolute_argv
    assert "cwd=" not in script


@pytest.mark.parametrize("argv", [
    ["git", "status"],
    ["git", "log", "-1"],
    ["docker", "compose", "config"],
    ["docker", "compose", "ps"],
    ["npm", "list"],
    ["npm", "ls"],
    ["grep", "-r", "passed"],
    ["ssh-keygen", "-l"],
    ["java", "Main"],
])
def test_implicit_directory_or_default_key_requires_explicit_location(argv):
    assert missing_execution_location({"type": "command", "argv": argv})
    with pytest.raises(CheckPlanContractError):
        compile_check_plan(analysis_for(argv), target_node_key="linux")


@pytest.mark.parametrize("argv", [
    ["python3", "--version"],
    ["systemctl", "is-active", "nginx"],
    ["journalctl", "-u", "nginx", "-n", "20"],
    ["pip3", "show", "requests"],
    ["pip3", "list"],
    ["git", "--version"],
    ["git", "-C", "/srv/project", "status"],
    ["git", "config", "--global", "--list"],
    ["git", "diff", "--no-index", "/srv/first", "/srv/second"],
    ["docker", "ps"],
    ["docker", "compose", "version"],
    ["docker", "compose", "--project-directory", "/srv", "config"],
    ["npm", "list", "--global"],
    ["npm", "view", "@scope/package", "version"],
    ["find", "/srv", "-name", "*.py", "-newermt", "2026-01-01"],
    ["grep", "--include=*.py", "passed", "/srv/results.txt"],
    ["jq", "--arg", "name", "student", ".name", "/srv/results.json"],
    ["python3", "/srv/main.py", "student-name", "42"],
    ["java", "-version"],
    ["java", "/srv/Main.java", "input.java", "-cp", "student-option"],
    ["java", "-jar", "/srv/app:1.jar", "input.jar"],
    ["lsof", "-i", ":80"],
    ["lsof", "+D", "/srv/project"],
    ["dig", "-t", "A", "example.org"],
    ["curl", "-H", "X-Trace: sample", "http://localhost"],
])
def test_non_path_values_and_independent_queries_are_not_mistaken_for_paths(argv):
    assert missing_execution_location({"type": "command", "argv": argv}) == []
    _, policy, _, _ = compile_check_plan(analysis_for(argv), target_node_key="linux")
    assert policy["approved"] is True


@pytest.mark.parametrize("argv", [
    ["git", "-C", "/srv/project", "reset", "--hard"],
    ["docker", "compose", "--project-directory", "/srv", "up"],
    ["docker", "compose", "--file", "/srv/compose.yml", "config", "--output=/srv/out"],
])
def test_explicit_directory_cannot_bypass_command_authorization(argv):
    with pytest.raises(CheckPlanContractError):
        compile_check_plan(analysis_for(argv), target_node_key="linux")


@pytest.mark.parametrize("argv", [
    ["git", "-C=/srv/project", "status"],
    ["nginx", "-t", "-c=/srv/nginx.conf"],
    ["docker", "compose", "-f=/srv/compose.yml", "config"],
    ["java", "-cp=/srv/classes", "Main"],
])
def test_short_option_equals_does_not_disguise_a_relative_value(argv):
    assert missing_execution_location({"type": "command", "argv": argv})
    with pytest.raises(CheckPlanContractError):
        compile_check_plan(analysis_for(argv), target_node_key="linux")


def test_git_scope_executes_from_an_unrelated_directory(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required for the local execution check")
    repository = tmp_path / "student project"
    repository.mkdir()
    subprocess.run([git, "init", "-q", str(repository)], check=True, capture_output=True)
    unrelated = tmp_path / "managed-runtime"
    unrelated.mkdir()
    script, _, _, _ = compile_check_plan(
        analysis_for(["git", "-C", str(repository), "rev-parse", "--show-toplevel"]),
        target_node_key="linux",
    )
    script_path = unrelated / "script.py"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script_path)], cwd=unrelated,
        check=True, capture_output=True, text=True, timeout=10,
    )
    check = json.loads(result.stdout)["checks"][0]
    raw = json.loads(check["raw"])
    assert Path(raw["stdout"].strip()) == repository
    assert raw["argv"] == ["git", "-C", str(repository), "rev-parse", "--show-toplevel"]
