"""Branch coverage for app.ai.teacher_judge.script_policy."""

from __future__ import annotations

import pytest

from app.ai.teacher_judge.script_policy import (
    PEER_IP_TOKEN,
    aggregate_check_status,
    check_peer_runtime_policy,
    check_script_policy,
    dangerous_command_issue,
    normalize_managed_script_checks,
)


@pytest.mark.parametrize(
    ("statuses", "incomplete", "expected"),
    [
        (["pass"], True, "unknown"),
        ([], False, "unknown"),
        (["bogus"], False, "unknown"),
        (["pass", "fail"], False, "fail"),
        (["skipped"], False, "skipped"),
        (["pass", "skipped"], False, "unknown"),
        (["pass", "collected"], False, "collected"),
        (["pass"], False, "pass"),
    ],
)
def test_aggregate_check_status(
    statuses: list[str], incomplete: bool, expected: str
) -> None:
    assert aggregate_check_status(statuses, incomplete=incomplete) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm -r build", "禁止使用 rm 遞迴刪除檔案"),
        ("find . -delete", "禁止使用 find -delete"),
        ("apt -y install vim", "禁止安裝套件"),
        ("git branch -d x", "通用受控指令只允許唯讀 Git 子命令"),
        ("ls -la", None),
    ],
)
def test_dangerous_command_issue_token_rules(
    command: str, expected: str | None
) -> None:
    assert dangerous_command_issue(command) == expected


def test_normalize_managed_script_checks_without_list_returns_input() -> None:
    data = {"checks": "nope"}
    assert normalize_managed_script_checks(data) is data


_HEADER = (
    "schema_version = 'teacher_judge_result.v1'\n"
    "payload = {'checks': [], 'errors': [], "
    "'metadata': {'timestamp': 't', 'platform': 'p'}}\n"
    "print(payload)\n"
)


def test_check_script_policy_syntax_error() -> None:
    result = check_script_policy(_HEADER + "def (:\n")
    assert result["approved"] is False
    assert result["issues"][0].startswith("Python 語法錯誤")
    assert result["fix_hints"][0]["type"] == "fix_syntax_error"


def test_check_script_policy_flags_open_network_and_loops() -> None:
    script = _HEADER + (
        "from os import *\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import requests\n"
        "import httpx\n"
        "x = ['a']\n"
        "subprocess.run(['ls', x], timeout=1)\n"
        "open('f', 'w')\n"
        "open('f', mode='a')\n"
        "open('f', mode=x)\n"
        "open('f')\n"
        "Path('p').open('w')\n"
        "requests.head('http://localhost', timeout=1)\n"
        "requests.post('http://localhost', timeout=1)\n"
        "requests.put('http://example.com')\n"
        "requests.patch(url='http://localhost', timeout=1)\n"
        "requests.delete('http://localhost', timeout=1)\n"
        "requests.request('POST', 'http://localhost', timeout=1)\n"
        "requests.request(method='get', url='http://localhost', timeout=1)\n"
        "httpx.request()\n"
        "while True:\n"
        "    pass\n"
    )
    result = check_script_policy(script)
    issues = result["issues"]
    assert result["approved"] is False
    assert "禁止以寫入模式開啟檔案" in issues
    assert "通用網路請求只允許 GET/HEAD" in issues
    assert "禁止使用會送出或修改資料的網路請求" in issues
    assert "網路請求必須設定 timeout" in issues
    assert "網路請求只允許 literal localhost/127.0.0.1/::1 URL" in issues
    assert "禁止無限制 while True 迴圈" in issues
    modes = {
        hint.get("mode")
        for hint in result["fix_hints"]
        if hint["type"] == "remove_write_mode"
    }
    assert modes == {"w", "a"}
    functions = {
        hint.get("function")
        for hint in result["fix_hints"]
        if hint["type"] == "remove_write_mode"
    }
    assert "pathlib.Path.open" in functions


def _peer_snapshot(*extra: dict[str, object]) -> dict[str, object]:
    return {
        "items": [
            {
                "id": "db-to-web",
                "peer_node_key": "web",
                "check_steps": [
                    "not-a-dict",
                    {"parameters": {"argv": ["ping", PEER_IP_TOKEN]}},
                ],
            },
            *extra,
        ]
    }


def test_peer_policy_step_contract_issues_and_invalid_python() -> None:
    snapshot = _peer_snapshot(
        {
            "id": "curl-peer",
            "peer_node_key": "web",
            "check_steps": [{"argv": ["curl", PEER_IP_TOKEN]}],
        },
        {"title": "no-token", "peer_node_key": "web", "check_steps": []},
        {"peer_node_key": "web", "check_steps": None},
    )
    result = check_peer_runtime_policy("def (:\n", snapshot)
    issues = result["issues"]
    assert "目前只允許將 peer IP 傳給 ping 的 argv" in issues
    assert f"peer 項目 no-token 必須使用 {PEER_IP_TOKEN}" in issues
    assert f"peer 項目 未命名 必須使用 {PEER_IP_TOKEN}" in issues
    assert "peer runtime 腳本不是有效的 Python" in issues
    assert result["risk_level"] == "high"
    assert all(
        hint["type"] == "fix_peer_runtime_contract" for hint in result["fix_hints"]
    )


def test_peer_policy_missing_context_literals() -> None:
    result = check_peer_runtime_policy("print('x')\n", _peer_snapshot())
    issues = result["issues"]
    assert "peer 檢查必須從固定的 runtime_context.json 讀取 context" in issues
    assert "peer runtime context 必須只讀取 peers/<node_key>/ip_address" in issues
    assert "peer runtime 必須處理 resolution_status=unavailable" in issues
    assert "找不到從 runtime context 到 ping argv 的 peer IP dataflow" in issues
    assert "腳本未限定宣告的 peer node_key：web" in issues
    assert "腳本未從 runtime_context.peers 讀取宣告的 peer node_key：web" in issues


_RICH_PEER_SCRIPT = """
import json
import subprocess
from pathlib import Path

counter: int
context = json.loads(Path("runtime_context.json").read_text())
peers_section = context.get("peers")
other = peers_section.get("web")
unrelated = {}.get("peers").get("web")
peer = context.get("peers").get("web")
resolution_status = peer["resolution_status"]
ip_address = peer["ip_address"]
if resolution_status != "ready":
    print("unavailable")
cmd = "ping"
argv = [cmd, ip_address]
result = subprocess.run(argv, capture_output=True, timeout=30)
subprocess.run(["ls"], timeout=30)
subprocess.run(str(ip_address), timeout=30)
subprocess.run(["ping", "-c", "1", ip_address, "10.0.0.1"], timeout=30)
subprocess.run(["curl", ip_address], timeout=30)
subprocess.run(["ping", ip_address], timeout=30, input=ip_address)
print(ip_address)
""".strip()


def test_peer_policy_rich_dataflow_violations() -> None:
    result = check_peer_runtime_policy(_RICH_PEER_SCRIPT, _peer_snapshot())
    issues = result["issues"]
    assert result["approved"] is False
    assert "peer IP 只能流入 ping argv，不得流向其他命令" in issues
    assert "peer IP 必須流入可靜態確認的 ping argv list" in issues
    assert "peer ping 不得寫死其他 IP 或 CIDR" in issues
    assert "peer IP 只能出現在 ping 的第一個 argv 參數" in issues
    assert "peer IP 只能流入 ping argv，不得流向其他參數" in issues
    assert "peer IP 不得流向 ping 以外的 API、檔案或輸出" in issues
    # The declared peer path is read via chained ``.get`` calls.
    assert not any("未從 runtime_context.peers" in issue for issue in issues)
    assert not any("未限定宣告的 peer" in issue for issue in issues)
