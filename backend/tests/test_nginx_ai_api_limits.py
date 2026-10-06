"""Regression checks for AI-specific nginx ingress limits."""

from pathlib import Path


def test_ai_routes_apply_limits_with_and_without_trailing_slash() -> None:
    config = (
        Path(__file__).parents[2] / "nginx" / "default.conf.template"
    ).read_text(encoding="utf-8")

    expected = {
        "location = /api/v1/ai-api {": "client_max_body_size 16k;",
        "location ^~ /api/v1/ai-api/ {": "client_max_body_size 16k;",
        "location = /api/v1/ai-proxy {": "client_max_body_size 1m;",
        "location ^~ /api/v1/ai-proxy/ {": "client_max_body_size 1m;",
    }
    for location, body_limit in expected.items():
        block = config.split(location, 1)[1].split("}", 1)[0]
        assert body_limit in block
        assert "client_body_timeout 10s;" in block
        assert "proxy_read_timeout 130s;" in block
        assert "proxy_send_timeout 130s;" in block
        assert "proxy_pass http://backend_pool;" in block
