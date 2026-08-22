from __future__ import annotations

from importlib.metadata import version

import codex_agy_bridge


def test_package_version_matches_project_metadata() -> None:
    expected = version("codex-agy-bridge")
    assert expected == "0.1.0"
    assert codex_agy_bridge.__version__ == expected
