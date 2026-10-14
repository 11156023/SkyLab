"""Generated LiteLLM config.yaml may carry literal upstream keys: always 0600."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_litellm_config import write_private_text

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def permissive_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def test_new_file_is_owner_only(tmp_path, permissive_umask):
    target = tmp_path / "nested" / "config.yaml"

    write_private_text(target, "model_list: []\n")

    assert target.read_text(encoding="utf-8") == "model_list: []\n"
    assert _mode(target) == 0o600


def test_existing_world_readable_file_is_tightened_in_place(tmp_path, permissive_umask):
    target = tmp_path / "config.yaml"
    target.write_text("old: content that is much longer than the replacement\n", encoding="utf-8")
    target.chmod(0o644)
    inode = target.stat().st_ino

    write_private_text(target, "new: 1\n")

    assert target.read_text(encoding="utf-8") == "new: 1\n"
    assert _mode(target) == 0o600
    # LiteLLM bind-mounts this exact file; replacing the inode would hide the update.
    assert target.stat().st_ino == inode


def test_failed_chmod_keeps_previous_config(tmp_path, monkeypatch):
    target = tmp_path / "config.yaml"
    target.write_text("previous: config\n", encoding="utf-8")

    def deny(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted", str(target))

    monkeypatch.setattr(os, "chmod", deny)
    with pytest.raises(PermissionError):
        write_private_text(target, "new: 1\n")

    assert target.read_text(encoding="utf-8") == "previous: config\n"


def test_generator_cli_writes_owner_only_config(tmp_path, permissive_umask):
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps([{
        "alias": "remote", "deployment": "remote", "served_model_name": "m",
        "api_base": "http://192.0.2.20:8103/v1/", "apikeys": "literal-upstream-key",
    }]))
    output_path = tmp_path / "config.yaml"
    output_path.write_text("stale\n")
    output_path.chmod(0o644)

    result = subprocess.run(
        [
            sys.executable, str(SERVICE_ROOT / "tools/generate_litellm_config.py"),
            "--models", str(models_path), "--output", str(output_path),
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "literal-upstream-key" in output_path.read_text()
    assert _mode(output_path) == 0o600
