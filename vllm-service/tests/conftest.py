"""Shared pytest fixtures for vllm-service."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _restore_process_environment() -> Iterator[None]:
    """Undo environment changes a test leaves behind.

    The launcher loads ``.env.API`` into ``os.environ`` (``load_dotenv``) and
    ``Settings.apply_runtime_env`` exports HF/vLLM variables, so without this a
    value written by one test (e.g. ``ALLOWED_LOCAL_MEDIA_PATH=/``) leaks into
    every test that runs after it.
    """
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
