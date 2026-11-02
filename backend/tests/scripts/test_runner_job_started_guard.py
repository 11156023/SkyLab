"""scripts/runner/job-started-guard.sh 只放行 main 上手動觸發的部署與建班 workflow。

直接用 bash 執行真實腳本，環境變數只留 PATH 與測試指定的 GITHUB_*（CI 自己的
GITHUB_* 一律移除，否則會被當成 job 的值）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[3] / "scripts/runner/job-started-guard.sh"
REPO = "SkyLab-tw/SkyLab"
DEPLOY = f"{REPO}/.github/workflows/deploy-pve-test.yml@refs/heads/main"
PROVISION = f"{REPO}/.github/workflows/provision-class-event.yml@refs/heads/main"


def _bash() -> str:
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        if candidate.exists():
            return str(candidate)
        pytest.skip("Git Bash required on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash required")
    return bash


def _run(**github: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.update(github)
    return subprocess.run(
        [_bash(), "-e", str(GUARD)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("workflow_ref", [DEPLOY, PROVISION])
def test_allows_manual_runs_of_the_two_workflows_on_main(workflow_ref: str) -> None:
    result = _run(
        GITHUB_REPOSITORY=REPO,
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_WORKFLOW_REF=workflow_ref,
    )
    assert result.returncode == 0, result.stderr
    assert "allowed" in result.stdout


@pytest.mark.parametrize(
    ("repository", "event", "workflow_ref"),
    [
        # 外部 PR 新增或改寫的 workflow
        (REPO, "pull_request", f"{REPO}/.github/workflows/evil.yml@refs/pull/7/merge"),
        (
            REPO,
            "pull_request",
            f"{REPO}/.github/workflows/deploy-pve-test.yml@refs/pull/7/merge",
        ),
        (REPO, "pull_request_target", DEPLOY),
        (REPO, "push", DEPLOY),
        (REPO, "issue_comment", DEPLOY),
        # 協作者分支上手動觸發
        (
            REPO,
            "workflow_dispatch",
            f"{REPO}/.github/workflows/deploy-pve-test.yml@refs/heads/feat/x",
        ),
        (REPO, "workflow_dispatch", f"{DEPLOY}-evil"),
        # main 上的其他 workflow
        (
            REPO,
            "workflow_dispatch",
            f"{REPO}/.github/workflows/docs-site.yml@refs/heads/main",
        ),
        # fork 或其他 repo
        ("someone/SkyLab", "workflow_dispatch", DEPLOY.replace(REPO, "someone/SkyLab")),
        ("skylab-tw/skylab", "workflow_dispatch", DEPLOY),
    ],
)
def test_refuses_everything_else(
    repository: str, event: str, workflow_ref: str
) -> None:
    result = _run(
        GITHUB_REPOSITORY=repository,
        GITHUB_EVENT_NAME=event,
        GITHUB_WORKFLOW_REF=workflow_ref,
    )
    assert result.returncode != 0
    assert "refusing this job" in result.stderr


@pytest.mark.parametrize(
    "missing", ["GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF"]
)
def test_refuses_when_a_variable_is_missing(missing: str) -> None:
    github = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW_REF": DEPLOY,
    }
    del github[missing]
    result = _run(**github)
    assert result.returncode != 0
    assert "<unset>" in result.stderr
