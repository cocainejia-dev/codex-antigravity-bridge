"""Pytest configuration and global fixtures for codex-agy-bridge root tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import pytest

# Ensure mcp-antigravity-bridge/src is first on sys.path and imported cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "mcp-antigravity-bridge" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import codex_agy_bridge  # noqa: E402
_local_pkg_path = str(SRC_DIR / "codex_agy_bridge")
if hasattr(codex_agy_bridge, "__path__") and _local_pkg_path not in codex_agy_bridge.__path__:
    codex_agy_bridge.__path__.insert(0, _local_pkg_path)

from codex_agy_bridge.telemetry_hooks import reset_telemetry_ledgers  # noqa: E402


@pytest.fixture(autouse=True)
def auto_isolate_telemetry(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Automatically isolate all telemetry to a dedicated temp directory and route origin to TEST."""
    reset_telemetry_ledgers()
    isolated_dir = tmp_path_factory.mktemp("isolated_telemetry")
    test_db = isolated_dir / "test_isolated_telemetry.sqlite3"
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_ORIGIN", "TEST")
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_DB", str(test_db))
    yield test_db
    reset_telemetry_ledgers()
