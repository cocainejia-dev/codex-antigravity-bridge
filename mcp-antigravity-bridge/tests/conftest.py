"""Pytest configuration and package-level fixtures for mcp-antigravity-bridge tests."""

from __future__ import annotations

from pathlib import Path
import sys
import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from codex_agy_bridge.telemetry_hooks import reset_telemetry_ledgers  # noqa: E402


@pytest.fixture(autouse=True)
def auto_isolate_telemetry(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Route package tests to an isolated TEST-origin telemetry database."""
    reset_telemetry_ledgers()
    isolated_dir = tmp_path_factory.mktemp("isolated_telemetry")
    test_db = isolated_dir / "test_isolated_telemetry.sqlite3"
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_ORIGIN", "TEST")
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_DB", str(test_db))
    yield test_db
    reset_telemetry_ledgers()
