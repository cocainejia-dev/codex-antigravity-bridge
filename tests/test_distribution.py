from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_files_exist() -> None:
    expected = (
        ROOT / "skills" / "agy-supervisor" / "SKILL.md",
        ROOT / "skills" / "agy-supervisor" / "agents" / "openai.yaml",
        ROOT / "scripts" / "install.ps1",
        ROOT / "scripts" / "install.sh",
        ROOT / "scripts" / "validate_skill.py",
    )
    assert all(path.is_file() for path in expected)


def test_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_skill.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "skill validation passed" in result.stdout


def test_readme_documents_install_and_supervision() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "https://github.com/crazyzhang277/codex-antigravity-bridge.git",
        "agy_ask",
        "supervisor mode",
        "multi-page",
    ):
        assert phrase in readme
