from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "verify-ai-api-cutover.sh"
)


@pytest.mark.skipif(os.name == "nt", reason="Deployment smoke script runs on Linux")
def test_smoke_checks_each_unique_public_model() -> None:
    requested_models: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return None

        def _send_json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            assert self.path == "/api/v1/ai-proxy/models"
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": "node-a"},
                        {"id": "node-b"},
                        {"id": "node-a"},
                    ],
                }
            )

        def do_POST(self) -> None:
            assert self.path == "/api/v1/ai-proxy/chat/completions"
            assert self.headers["Authorization"] == "Bearer ccai_test"
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload["max_tokens"] == 8
            assert payload["stream"] is False
            requested_models.append(payload["model"])
            self._send_json(
                {
                    "object": "chat.completion",
                    "model": payload["model"],
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "AI_API_PUBLIC_BASE_URL": (
                f"http://127.0.0.1:{server.server_port}/api/v1"
            ),
            "AI_API_SMOKE_KEY": "ccai_test",
            "AI_API_SMOKE_PYTHON": sys.executable,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert requested_models == ["node-a", "node-b"]
    assert "passed for all 2 public models" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Deployment smoke script runs on Linux")
def test_smoke_continues_after_one_public_model_fails() -> None:
    requested_models: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return None

        def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            assert self.path == "/api/v1/ai-proxy/models"
            self._send_json(
                {
                    "object": "list",
                    "data": [{"id": "broken"}, {"id": "healthy"}],
                }
            )

        def do_POST(self) -> None:
            assert self.path == "/api/v1/ai-proxy/chat/completions"
            assert self.headers["Authorization"] == "Bearer ccai_test"
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            requested_models.append(payload["model"])
            if payload["model"] == "broken":
                self._send_json({"error": {"message": "model unavailable"}}, 503)
                return
            self._send_json(
                {
                    "object": "chat.completion",
                    "model": payload["model"],
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "AI_API_PUBLIC_BASE_URL": (
                f"http://127.0.0.1:{server.server_port}/api/v1"
            ),
            "AI_API_SMOKE_KEY": "ccai_test",
            "AI_API_SMOKE_PYTHON": sys.executable,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode != 0
    assert requested_models == ["broken", "healthy"]
    assert "Passed: healthy" in result.stdout
    assert "failed for 1/2 public models: broken" in result.stderr
