"""Coverage tests for app.ai.system_config file loading."""

import pytest

from app.ai import system_config


def test_missing_config_file_returns_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_config, "CONFIG_FILE", tmp_path / "missing.json")
    config = system_config.load_system_ai_config()
    assert config == system_config.SystemAIConfig()


def test_non_object_config_is_rejected(monkeypatch, tmp_path) -> None:
    path = tmp_path / "system-ai.json"
    path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(system_config, "CONFIG_FILE", path)
    with pytest.raises(ValueError, match="JSON object"):
        system_config.load_system_ai_config()
