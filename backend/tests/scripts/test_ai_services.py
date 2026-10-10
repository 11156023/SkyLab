from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts/test_ai_services.py"
SPEC = importlib.util.spec_from_file_location("ai_services_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
ALL_PUBLIC_SCENARIOS = probe.PUBLIC_SCENARIOS
ALL_SYSTEM_SCENARIOS = probe.SYSTEM_SCENARIOS


@pytest.fixture(autouse=True)
def small_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "REPETITIONS", 1)
    monkeypatch.setattr(probe, "PUBLIC_SCENARIOS", ALL_PUBLIC_SCENARIOS[:1])
    monkeypatch.setattr(
        probe, "SYSTEM_SCENARIOS", (ALL_SYSTEM_SCENARIOS[0], ALL_SYSTEM_SCENARIOS[3])
    )


KEY = "ccai_test_secret"
USAGE = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
STATUS = {
    "limit_per_minute": 20,
    "current_usage": 0,
    "remaining": 20,
    "disabled": False,
}


def test_all_visible_models_and_modes_use_one_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.headers["authorization"] == f"Bearer {KEY}"
        if request.url.path.endswith("/rate-limit/status"):
            return httpx.Response(200, json=STATUS)
        if request.url.path.endswith("/usage/my"):
            return httpx.Response(200, json={"total_requests": 10})
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}
            )
        body = json.loads(request.content)
        calls.append((body["model"], body["stream"]))
        assert body["max_tokens"] == 512
        assert body["messages"] == [
            {"role": "user", "content": ALL_PUBLIC_SCENARIOS[0]["prompt"]}
        ]
        if not body["stream"]:
            return httpx.Response(
                200,
                headers={"x-request-id": "r1"},
                json={
                    "choices": [
                        {"message": {"content": "OK"}, "finish_reason": "stop"}
                    ],
                    "usage": USAGE,
                    "model": body["model"],
                },
            )
        assert body["stream_options"] == {"include_usage": True}
        # 包含多行 data、comment、空 choices usage event。
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                ': ping\n\ndata: {"choices":\ndata: [{"delta":{"content":"O"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"K"},"finish_reason":"stop"}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {KEY}"},
    ) as client:
        report = probe.run_checks(client, KEY)
    assert calls == [("a", False), ("a", True), ("b", False), ("b", True)]
    summary = report["summary"]
    assert summary["planned"] == summary["tested"] == summary["passed"] == 4
    assert summary["failed"] == summary["untested"] == summary["semantic_failed"] == 0
    assert summary["needs_review"] == 4
    assert len(summary["by_scenario"]) == 4
    assert report["results"][0]["request_id"] == "r1"
    assert report["results"][1]["first_content_ms"] is not None
    assert report["results"][1]["stream_done"] is True
    assert report["usage_before"] == report["usage_after"] == {"total_requests": 10}
    assert KEY not in str(report) + capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"choices": [{"message": {"content": ""}}], "usage": USAGE}, "answer_empty"),
        ({"choices": [{"message": {"content": "OK"}}]}, "usage_missing_or_invalid"),
        (
            {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": True},
            },
            "usage_missing_or_invalid",
        ),
        ({"error": {"message": KEY}}, "response_error_event"),
        ([], "response_not_object"),
    ],
)
def test_http_200_alone_does_not_pass(body: object, error: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = probe.probe_model(client, "a", stream=False)
    assert result["status_code"] == 200
    assert result["ok"] is False
    assert result["error"] == error


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ('data: {"choices":[{"delta":{"content":"OK"}}]}\n\n', "stream_done_missing"),
        (
            'data: {"error":{"message":"secret"}}\n\ndata: [DONE]\n\n',
            "response_error_event",
        ),
        ("data: {invalid}\n\ndata: [DONE]\n\n", "response_not_json"),
        ("data: [DONE]\n\n", "answer_empty"),
    ],
)
def test_incomplete_or_error_stream_fails(body: str, error: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )
        )
    ) as client:
        result = probe.probe_model(client, "a", stream=True)
    assert result["ok"] is False
    assert result["error"] == error


def test_429_captures_retry_after_without_resending_or_raw_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "60"}, json={"detail": KEY})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe.probe_model(client, "a", stream=False)
    assert calls == 1
    assert result["error"] == "http_429"
    assert result["retry_after"] == "60"
    assert KEY not in str(result)


def test_waits_for_shared_budget_without_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter([{**STATUS, "remaining": 0}, STATUS])
    waits: list[int] = []
    monkeypatch.setattr(probe, "rate_status", lambda _: next(statuses))
    monkeypatch.setattr(probe.time, "sleep", waits.append)
    probe.wait_for_budget(None)
    assert waits == [5]


def test_wait_deadline_prevents_unbounded_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter([0, 71])
    monkeypatch.setattr(probe, "rate_status", lambda _: {**STATUS, "remaining": 0})
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    with pytest.raises(probe.ProbeError, match="budget_unavailable"):
        probe.wait_for_budget(None)


def test_failed_auth_stops_and_reports_untested_models() -> None:
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rate-limit/status"):
            return httpx.Response(200, json=STATUS)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        if request.method == "POST":
            posts.append(str(request.url))
            return httpx.Response(401)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_checks(client, KEY)
    assert len(posts) == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["untested"] == 3
    assert report["error"] == "authentication_or_permission_failed"


def test_server_echoed_secrets_are_redacted_in_all_report_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rate-limit/status"):
            return httpx.Response(200, json={**STATUS, "extra": KEY})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": KEY}]})
        if request.method == "GET":
            return httpx.Response(200, json={KEY: KEY})
        return httpx.Response(429, headers={"x-request-id": KEY, "retry-after": KEY})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_checks(client, KEY)
    assert KEY not in str(report) + capsys.readouterr().out
    assert "<redacted>" in str(report)


def test_hidden_input_fallback_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import warnings

    def fallback(_: str) -> str:
        warnings.warn("echo fallback", probe.getpass.GetPassWarning, stacklevel=1)
        raise AssertionError("must not read plaintext")

    monkeypatch.setattr(probe.getpass, "getpass", fallback)
    monkeypatch.setattr(probe, "REPORT_FILE", tmp_path / "latest.json")
    assert probe.main() == 2
    assert not probe.REPORT_FILE.exists()


LOGIN_TOKEN = "test.login.token"
CREDENTIAL_ID = "00000000-0000-0000-0000-000000000001"
NODE = {
    "node": "pve",
    "status": "online",
    "cpu_usage": 0.25,
    "cpu_cores": 8,
    "mem_used_bytes": 100,
    "mem_total_bytes": 200,
    "mem_used_pct": 50,
}


def test_login_identity_fetches_own_key_then_tests_only_requested_services(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["authorization"] == f"Bearer {LOGIN_TOKEN}"
        if request.url.path.endswith("/users/me"):
            return httpx.Response(
                200,
                json={"id": "user-1", "role": "admin", "email": "private@example.test"},
            )
        if request.url.path.endswith("/credentials/my"):
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "data": [
                        {"id": "revoked", "revoked_at": "2026-01-01T00:00:00Z"},
                        {"id": "expired", "expires_at": "2000-01-01T00:00:00Z"},
                        {"id": CREDENTIAL_ID, "expires_at": None},
                    ],
                },
            )
        if request.url.path.endswith(CREDENTIAL_ID):
            return httpx.Response(200, json={"api_key": KEY})
        if request.url.path.endswith("/usage/my"):
            return httpx.Response(200, json={"total_calls": 2})
        if request.url.path.endswith("/monitoring/template-calls"):
            return httpx.Response(200, json={"data": [], "count": 0})
        if request.url.path.endswith("/navigation/resolve"):
            return httpx.Response(
                200,
                json={
                    "action": "navigate",
                    "primary": {"path": "/ai-api", "title": "AI API"},
                },
            )
        assert request.url.path.endswith("/pve-log/chat")
        return httpx.Response(
            200,
            json={
                "reply": "CPU 25%，記憶體 50%",
                "needs_confirmation": False,
                "tools_called": [{"name": "get_nodes", "result": {"items": [NODE]}}],
                "messages": [{"confirm_token": "must-not-save"}],
            },
        )

    def public_checks(
        client: httpx.Client, key: str, **kwargs: object
    ) -> dict[str, object]:
        assert key == KEY
        assert client.headers["authorization"] == f"Bearer {KEY}"
        assert kwargs["additional_secrets"] == (LOGIN_TOKEN,)
        return {"error": None, "summary": {"tested": 2, "failed": 0, "untested": 0}}

    monkeypatch.setattr(probe, "run_checks", public_checks)
    with (
        httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {LOGIN_TOKEN}"},
        ) as login,
        httpx.Client() as models,
    ):
        report = probe.run_site_checks(login, models, LOGIN_TOKEN)
    assert report["ok"] is True
    assert report["credential_id"] == CREDENTIAL_ID
    assert report["system_ai"][0]["semantic_check"]["status"] == "passed"
    assert report["system_ai"][1]["response"]["evidence"]["data"] == [NODE]
    assert report["system_usage_after"] == {"total_calls": 2}
    assert all("teacher" not in path for path in calls)
    assert all("ssh/confirm" not in path for path in calls)
    text = str(report) + capsys.readouterr().out
    assert KEY not in text and LOGIN_TOKEN not in text
    assert "private@example.test" not in text and "must-not-save" not in text


def test_missing_ai_key_still_tests_navigation_and_reports_admin_requirement() -> None:
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"role": "student"})
        if request.url.path.endswith("/credentials/my"):
            return httpx.Response(200, json={"count": 0, "data": []})
        if request.method == "POST":
            posts.append(request.url.path)
            return httpx.Response(
                200, json={"action": "clarify", "clarification_question": "請確認需求"}
            )
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_site_checks(client, client, LOGIN_TOKEN)
    assert report["ok"] is False
    assert report["public_models"]["error"] == "no_active_owned_ai_credential"
    assert report["system_ai"][1]["status"] == "permission_denied"
    assert posts == ["/api/v1/ai/navigation/resolve"]


def test_invalid_login_does_not_start_any_model_calls() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_site_checks(client, client, LOGIN_TOKEN)
    assert report["ok"] is False
    assert report["error"] == "users/me: HTTP 401"
    assert requests == ["/api/v1/users/me"]


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"reply": "hello", "tools_called": []}, "pve_data_missing"),
        (
            {
                "reply": "hello",
                "tools_called": [
                    {
                        "name": "get_nodes",
                        "result": {"items": [NODE], "error": "private"},
                    }
                ],
            },
            "pve_collection_error",
        ),
        (
            {
                "reply": "hello",
                "tools_called": [{"name": "get_nodes", "result": {"items": [{}]}}],
            },
            "pve_metrics_invalid",
        ),
        (
            {
                "reply": "hello",
                "needs_confirmation": True,
                "tools_called": [
                    {"name": "ssh_exec", "result": {"confirm_token": KEY}}
                ],
            },
            "pve_assistant_requested_confirmation",
        ),
    ],
)
def test_pve_needs_actual_node_evidence_and_never_confirms_ssh(
    body: object, error: str
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe.probe_system(client, "pve_assistant")
    assert result["status"] != "passed"
    assert result["error"] == error
    assert paths == ["/api/v1/ai/pve-log/chat"]
    assert "confirm_token" not in str(result)


def test_both_credentials_redacted_when_public_model_echoes_login_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rate-limit/status"):
            return httpx.Response(200, json=STATUS)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": LOGIN_TOKEN}]})
        if request.method == "GET":
            return httpx.Response(200, json={"echo": LOGIN_TOKEN})
        return httpx.Response(429, headers={"x-request-id": LOGIN_TOKEN})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_checks(client, KEY, additional_secrets=(LOGIN_TOKEN,))
    assert LOGIN_TOKEN not in str(report) + capsys.readouterr().out


def test_three_attempts_cover_every_model_prompt_and_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    monkeypatch.setattr(probe, "REPETITIONS", 3)
    monkeypatch.setattr(probe, "PUBLIC_SCENARIOS", ALL_PUBLIC_SCENARIOS)
    questions: list[tuple[str, str, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rate-limit/status"):
            return httpx.Response(200, json=STATUS)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        if request.method == "GET":
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        questions.append(
            (body["model"], body["messages"][0]["content"], body["stream"])
        )
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_checks(client, KEY)
    assert len(questions) == 24
    assert len(set(questions)) == 8
    assert all(questions.count(item) == 3 for item in set(questions))
    assert report["summary"]["failed"] == 24
    assert report["summary"]["failure_counts"] == {"http_429": 24}
    assert all(
        row["failed"] == 3 and row["e2e_output_tokens_per_second"] is None
        for row in report["summary"]["by_scenario"]
    )


def test_json_semantics_do_not_confuse_bool_and_integer() -> None:
    import json

    scenario = ALL_PUBLIC_SCENARIOS[1]
    assert (
        probe.evaluate_answer(json.dumps(scenario["expected"]), scenario)["status"]
        == "passed"
    )
    wrong = {**scenario["expected"], "requires_gpu": 0}
    assert probe.evaluate_answer(json.dumps(wrong), scenario)["status"] == "failed"
    assert (
        probe.evaluate_answer("```json\n{}\n```", scenario)["reason"]
        == "answer_not_json"
    )
    assert (
        probe.evaluate_answer("任意說明", ALL_PUBLIC_SCENARIOS[0])["status"]
        == "needs_review"
    )


def test_token_rate_uses_entire_request_and_stream_window_only_measures_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    timestamps = iter([0.0, 0.1, 0.3, 0.5])
    monkeypatch.setattr(
        probe, "time", SimpleNamespace(perf_counter=lambda: next(timestamps))
    )
    body = (
        'data: {"choices":[{"delta":{"content":"O"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"K"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
        "data: [DONE]\n\n"
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body
            )
        )
    ) as client:
        result = probe.probe_model(client, "a", stream=True)
    assert result["duration_ms"] == 500
    assert result["first_content_ms"] == 100
    assert result["last_content_ms"] == 300
    assert result["stream_content_duration_ms"] == 200
    assert result["e2e_output_tokens_per_second"] == 4.0
    assert probe.token_rate(2, 0) is None
    assert probe.token_rate(True, 100) is None


def test_navigation_flow_checks_steps_without_submitting_requests() -> None:
    scenario = ALL_SYSTEM_SCENARIOS[2]
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "action": "guide",
                "flow_id": "request_machine",
                "steps": [
                    {"path": path, "title": "step"}
                    for path in scenario["expected_steps"]
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe.probe_system(client, "navigation", scenario=scenario)
    assert result["semantic_check"]["status"] == "passed"
    assert paths == ["/api/v1/ai/navigation/resolve"]


@pytest.mark.parametrize(
    ("tool_name", "data"),
    [
        (
            "get_storage",
            {
                "items": [
                    {
                        "node": "pve",
                        "storage": "local",
                        "avail_bytes": 50,
                        "used_bytes": 50,
                        "total_bytes": 100,
                        "used_pct": 50,
                    }
                ]
            },
        ),
        (
            "get_cluster",
            {
                "cluster_name": "test",
                "node_count": 2,
                "is_cluster": True,
                "quorate": False,
            },
        ),
    ],
)
def test_pve_storage_and_cluster_preserve_unhealthy_facts(
    tool_name: str, data: dict[str, object]
) -> None:
    evidence = probe.pve_evidence([{"name": tool_name, "result": data}], tool_name)
    assert evidence["error"] is None
    assert evidence["data"]
    if tool_name == "get_cluster":
        assert evidence["data"][0]["quorate"] is False


def test_truncated_pve_result_stays_marked_partial() -> None:
    result = {"truncated": True, "partial_result": [NODE], "original_chars": 9000}
    evidence = probe.pve_evidence(
        [{"name": "get_nodes", "result": result}], "get_nodes"
    )
    assert evidence["truncated"] is True
    assert evidence["data"] == [NODE]


def test_monitoring_is_time_window_observation_with_reported_usage_only() -> None:
    user_id = CREDENTIAL_ID
    result = {
        "service": "pve_assistant",
        "started_at": "2026-10-09T00:00:00+00:00",
        "completed_at": "2026-10-09T00:00:05+00:00",
    }
    records = [
        {
            "id": "main",
            "user_id": user_id,
            "call_type": "pve_chat",
            "output_tokens": 20,
            "request_duration_ms": 2000,
            "usage_reported": True,
            "status": "success",
            "user_email": "private@example.test",
            "error_message": KEY,
        },
        {
            "id": "guard",
            "user_id": user_id,
            "call_type": "pve_chat_adherence",
            "output_tokens": 0,
            "request_duration_ms": 1000,
            "usage_reported": False,
            "status": "error",
        },
        {
            "id": "other",
            "user_id": "other-user",
            "call_type": "pve_chat",
            "output_tokens": 999,
        },
        {
            "id": "teacher",
            "user_id": user_id,
            "call_type": "teacher_judge_chat",
            "output_tokens": 999,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["user_id"] == user_id
        assert request.url.params["start_date"] == result["started_at"]
        return httpx.Response(200, json={"data": records, "count": len(records)})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        observed = probe.observe_model_calls(client, user_id, result)
    assert observed["association"] == "time_window"
    assert len(observed["calls"]) == 2
    assert observed["calls"][0]["e2e_output_tokens_per_second"] == 10.0
    assert observed["calls"][1]["e2e_output_tokens_per_second"] is None
    assert "private@example.test" not in str(observed)
    assert KEY not in str(observed)


def test_monitoring_denied_does_not_invent_tokens_or_speed() -> None:
    result = {"service": "navigation", "started_at": "start", "completed_at": "end"}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(403))
    ) as client:
        observed = probe.observe_model_calls(client, CREDENTIAL_ID, result)
    assert observed["status"] == "unavailable"
    assert observed["calls"] == []


def test_summary_separates_request_failures_from_semantic_mismatch() -> None:
    results = [
        {
            "model": "a",
            "scenario_id": "extract_vm",
            "stream": False,
            "ok": True,
            "error": None,
            "duration_ms": 2000,
            "usage": {"completion_tokens": 20},
            "semantic_check": {"status": "failed"},
        },
        {
            "model": "a",
            "scenario_id": "extract_vm",
            "stream": False,
            "ok": False,
            "error": "response_error_event",
            "duration_ms": 100,
            "usage": None,
            "semantic_check": {"status": "not_evaluated"},
        },
    ]
    summary = probe.summarize_public(results, 3)
    assert (
        summary["passed"]
        == summary["failed"]
        == summary["semantic_failed"]
        == summary["untested"]
        == 1
    )
    assert summary["by_scenario"][0]["failure_rate"] == 0.5
    assert summary["by_scenario"][0]["e2e_output_tokens_per_second"] == 10.0


def test_all_system_questions_repeat_three_times_and_keep_failure_types_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from collections import Counter

    monkeypatch.setattr(probe, "REPETITIONS", 3)
    monkeypatch.setattr(probe, "SYSTEM_SCENARIOS", ALL_SYSTEM_SCENARIOS)
    monkeypatch.setattr(
        probe,
        "run_checks",
        lambda *args, **kwargs: {
            "error": None,
            "summary": {"tested": 2, "failed": 0, "untested": 0},
        },
    )
    counts: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"id": CREDENTIAL_ID, "role": "admin"})
        if request.url.path.endswith("/credentials/my"):
            return httpx.Response(
                200, json={"count": 1, "data": [{"id": CREDENTIAL_ID}]}
            )
        if request.url.path.endswith(CREDENTIAL_ID):
            return httpx.Response(200, json={"api_key": KEY})
        if request.url.path.endswith("/template-calls"):
            return httpx.Response(200, json={"count": 0, "data": []})
        if request.method == "GET":
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        question = body.get("query", body.get("message"))
        scenario = next(
            item for item in ALL_SYSTEM_SCENARIOS if item["prompt"] == question
        )
        counts[scenario["id"]] += 1
        if scenario["service"] == "navigation":
            if "expected_path" in scenario:
                path = scenario["expected_path"]
                if scenario["id"] == "nav_resources" and counts[scenario["id"]] == 1:
                    path = "/wrong"
                return httpx.Response(
                    200, json={"action": "navigate", "primary": {"path": path}}
                )
            return httpx.Response(
                200,
                json={
                    "action": "guide",
                    "flow_id": scenario["expected_flow"],
                    "steps": [{"path": path} for path in scenario["expected_steps"]],
                },
            )
        tool = scenario["expected_tool"]
        if tool == "get_storage" and counts[scenario["id"]] == 3:
            return httpx.Response(503)
        data = (
            {"items": [NODE]}
            if tool == "get_nodes"
            else {
                "items": [
                    {
                        "storage": "local",
                        "avail_bytes": 1,
                        "used_bytes": 1,
                        "total_bytes": 2,
                        "used_pct": 50,
                    }
                ]
            }
            if tool == "get_storage"
            else {"node_count": 2, "is_cluster": True, "quorate": False}
        )
        return httpx.Response(
            200,
            json={
                "reply": "實際資料摘要",
                "tools_called": [{"name": tool, "result": data}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_site_checks(client, client, LOGIN_TOKEN)
    assert len(report["system_ai"]) == 18
    assert all(count == 3 for count in counts.values())
    assert len(counts) == 6
    summary = report["system_summary"]
    assert summary["failed"] == 1
    assert summary["semantic_failed"] == 1
    assert summary["untested"] == 0
    assert summary["failure_counts"] == {"http_503": 1}
    assert report["ok"] is False
