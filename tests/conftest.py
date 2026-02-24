"""Shared test fixtures for ttygeist."""

import os

import pytest

from ttygeist.buffer_manager import BufferManager


@pytest.fixture
def small_buffer():
    """Buffer with small limits for easy overflow testing."""
    return BufferManager(max_size_bytes=100, line_limit=5)


@pytest.fixture
def default_buffer():
    """Buffer with reasonable defaults."""
    return BufferManager(max_size_bytes=1024 * 1024, line_limit=1000)


@pytest.fixture
def tmp_config_file(tmp_path):
    """Write a temporary YAML config and return its path."""

    def _write(content: str) -> str:
        p = tmp_path / "config.yaml"
        p.write_text(content)
        return str(p)

    return _write


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all TTYGEIST_* env vars for a clean test."""
    for key in list(os.environ):
        if key.startswith("TTYGEIST_"):
            monkeypatch.delenv(key)
