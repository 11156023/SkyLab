"""Dashboard contracts and real promtool checks without a running deployment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "monitoring/grafana/dashboards"
FIXTURE = ROOT / "monitoring/tests/latency.test.yml"


def dashboards():
    return [
        json.loads((DASHBOARDS / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("skylab-platform", "skylab-ai")
    ]


def promtool():
    path = os.environ.get("PROMTOOL_BIN") or shutil.which("promtool")
    if not path:
        pytest.skip(
            "promtool evaluator unavailable; JSON parsing is not PromQL validation"
        )
    return path


def test_dashboard_structure_classification_and_fixture_sync():
    platform, ai = dashboards()
    for dashboard in (platform, ai):
        ids = [p["id"] for p in dashboard["panels"]]
        assert len(ids) == len(set(ids))
        for p in dashboard["panels"]:
            refs = [t["refId"] for t in p.get("targets", [])]
            assert len(refs) == len(set(refs))
    panels = {p["id"]: p for p in platform["panels"]}
    assert all(
        'workload="general_api"' in t["expr"]
        for key in (5, 10, 11)
        for t in panels[key]["targets"]
    )
    assert 'workload="ai_generation"' in panels[25]["targets"][0]["expr"]
    model_panel = next(p for p in ai["panels"] if p["id"] == 11)
    assert all(
        "source, model, request_type, stream" in t["expr"]
        for t in model_panel["targets"]
    )
    assert 'outcome="success"' in model_panel["targets"][-1]["expr"]
    queries = {
        t["expr"]
        for d in (platform, ai)
        for p in d["panels"]
        for t in p.get("targets", [])
        if "expr" in t
    }
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    # NaN != itself; count proves no-completion quantiles are NaN, not zero.
    nan_checks = {f"count(({query}) != ({query}))" for query in queries}
    assert all(
        test["expr"] in queries or test["expr"] in nan_checks
        for group in fixture["tests"]
        for test in group["promql_expr_test"]
    )


def test_all_dashboard_expressions_parse_with_promtool(tmp_path):
    tool = promtool()
    queries = sorted(
        {
            t["expr"]
            for d in dashboards()
            for p in d["panels"]
            for t in p.get("targets", [])
            if "expr" in t
        }
    )
    rules = {
        "groups": [
            {
                "name": "dashboard_syntax",
                "rules": [
                    {"record": f"test:dashboard_{index}", "expr": expr}
                    for index, expr in enumerate(queries)
                ],
            }
        ]
    }
    path = tmp_path / "dashboard.rules.yml"
    path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    result = subprocess.run(
        [tool, "check", "rules", str(path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_latency_queries_evaluate_expected_values_with_promtool():
    result = subprocess.run(
        [promtool(), "test", "rules", str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
