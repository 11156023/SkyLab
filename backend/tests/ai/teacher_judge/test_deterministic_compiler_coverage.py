"""Branch coverage for app.ai.teacher_judge.deterministic_compiler."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    _command_argv_issue,
    _render_step_function,
    canonicalize_check_plan,
    missing_execution_location,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricItem,
)
from app.ai.teacher_judge.script_policy import PEER_IP_TOKEN


@pytest.mark.parametrize(
    "argv",
    [
        ["ls", " "],
        ["bash", "-c", "ls"],
        # _scan_options: long value at the end without its value
        ["curl", "http://localhost", "--max-time"],
        # _scan_options: long flag that is not whitelisted / carries a value
        ["curl", "--silent=1", "http://localhost"],
        # _scan_options: short value flag at the end without its value
        ["curl", "http://localhost", "-m"],
        # urlparse raises ValueError on a broken IPv6 literal
        ["curl", "http://[::1"],
        ["wget", "--post-data=x", "http://localhost"],
        # sed scripts that are not a single read-only command
        ["sed", "-n", "p\nd", "f.txt"],
        ["sed", "s", "f.txt"],
        ["sed", "s /a/b/", "f.txt"],
        ["sed", "s/a/b", "f.txt"],
        ["date", "--set=2020-01-01"],
        ["nc", "localhost", "80"],
        ["arp", "-d", "10.0.0.1"],
        ["resolvectl", "flush-caches"],
        ["file", "-C", "x"],
        ["psql", "-c", "select 1"],
        ["psql", "-o", "out.txt"],
        ["mysql", "--tee=out.txt"],
        ["nginx", "-q"],
        ["nginx", "-x"],
        ["service", "nginx", "restart"],
        ["docker", "run", "x"],
        ["docker", "network", "create", "x"],
        ["docker", "logs", "-f", "x"],
        ["ip", "link", "set", "eth0", "down"],
        ["git", "branch", "-D", "x"],
        ["git", "tag", "-d", "v1"],
    ],
)
def test_command_argv_issue_rejects(argv: list[str]) -> None:
    assert _command_argv_issue(argv) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "--max-time=5", "http://localhost"],
        ["sed", "-n", "s/a\\/b/c/g", "f.txt"],
        ["date", "-d", "now", "--iso-8601"],
        ["resolvectl", "status"],
        ["nginx", "-c", "/etc/nginx/nginx.conf", "-t"],
        ["service", "nginx", "status"],
        ["git", "branch", "--format=%(refname)"],
        ["git", "branch", "-av"],
        ["git", "tag", "-n5"],
    ],
)
def test_command_argv_issue_accepts(argv: list[str]) -> None:
    assert _command_argv_issue(argv) is None


def test_missing_execution_location_file_value_options() -> None:
    assert missing_execution_location({"type": "command", "argv": "ls"}) == []

    jq_gaps = missing_execution_location(
        {
            "type": "command",
            "argv": ["jq", "--slurpfile", "a", "data.json", ".", "x.json"],
        }
    )
    assert any("data.json" in gap for gap in jq_gaps)
    assert any("x.json" in gap for gap in jq_gaps)

    awk_gaps = missing_execution_location(
        {"type": "command", "argv": ["awk", "-f", "prog.awk", "input.txt"]}
    )
    assert any("prog.awk" in gap for gap in awk_gaps)
    assert any("input.txt" in gap for gap in awk_gaps)

    file_gaps = missing_execution_location(
        {"type": "command", "argv": ["file", "-f", "list.txt"]}
    )
    assert any("list.txt" in gap for gap in file_gaps)

    assert (
        missing_execution_location(
            {"type": "command", "argv": ["find", "-H", "/srv", "-name", "x"]}
        )
        == []
    )


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "item",
        "title": "Item",
        "detectable": "auto",
        "judgement_mode": "teacher",
        "target_node_key": "web",
        "check_steps": [
            {
                "id": "s",
                "title": "Stat",
                "collector": {"type": "file_stat", "path": "/etc/hosts"},
            }
        ],
    }
    item.update(overrides)
    return item


def _canonical_issues(items: list[dict[str, Any]], **kwargs: Any) -> list[str]:
    analysis = TeacherJudgeRubricAnalysis(
        items=[TeacherJudgeRubricItem.model_validate(item) for item in items]
    )
    with pytest.raises(CheckPlanContractError) as exc:
        canonicalize_check_plan(analysis, **kwargs)
    return [issue["message"] for issue in exc.value.issues]


def test_canonicalize_check_plan_item_level_issues() -> None:
    messages = _canonical_issues(
        [
            _item(id="  "),
            _item(id="dup"),
            _item(id="dup"),
            _item(id="no-node", target_node_key=None),
            _item(id="manual", detectable="manual"),
            _item(id="self-peer", peer_node_key="web"),
            _item(id="no-steps", check_steps=[]),
            _item(
                id="blank-step",
                check_steps=[
                    {"id": "  ", "title": "t", "collector": {"type": "peer_ping"}}
                ],
            ),
            _item(
                id="bad-token",
                check_steps=[
                    {
                        "id": "c1",
                        "title": "ping",
                        "collector": {
                            "type": "command",
                            "argv": ["ping", f"x{PEER_IP_TOKEN}"],
                        },
                    }
                ],
            ),
            _item(
                id="peer-command",
                peer_node_key="db",
                check_steps=[
                    {
                        "id": "c2",
                        "title": "ping",
                        "collector": {"type": "command", "argv": ["ping", "localhost"]},
                    }
                ],
            ),
            _item(
                id="peer-file",
                peer_node_key="db",
                check_steps=[
                    {
                        "id": "c3",
                        "title": "stat",
                        "collector": {"type": "file_stat", "path": "/etc/hosts"},
                    }
                ],
            ),
        ]
    )
    assert "檢查項目缺少穩定 id" in messages
    assert "檢查項目 id 重複" in messages
    assert "缺少 target_node_key" in messages
    assert "只有 detectable=auto 的項目可以進入腳本" in messages
    assert "peer_node_key 不可等於 target_node_key" in messages
    assert "缺少 typed check_steps" in messages
    assert "typed check step 缺少 id" in messages
    assert f"{PEER_IP_TOKEN} 必須是有 peer_node_key 的完整 argv 元素" in messages
    assert f"peer command 必須以 {PEER_IP_TOKEN} 作為完整 argv 元素" in messages
    assert "peer item 目前只支援 peer_ping 或帶 peer token 的 command" in messages


def test_canonicalize_check_plan_filters_other_nodes_and_rejects_empty() -> None:
    messages = _canonical_issues([_item()], target_node_key="db")
    assert messages == ["沒有可編譯的 typed Check Plan"]


def test_render_step_function_unknown_collector() -> None:
    source = _render_step_function(
        0, 0, 0, {"id": "s", "title": "t", "collector": {"type": "bogus"}}
    )
    assert "unsupported_collector" in source
