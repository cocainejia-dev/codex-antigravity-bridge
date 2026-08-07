from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_files_exist() -> None:
    expected = (
        ROOT / "skills" / "agy-supervisor" / "SKILL.md",
        ROOT / "skills" / "agy-supervisor" / "agents" / "openai.yaml",
        ROOT / "skills" / "agy-supervisor" / "references" / "agy-development-plan.md",
        ROOT / "scripts" / "install.ps1",
        ROOT / "scripts" / "install.sh",
        ROOT / "scripts" / "validate_skill.py",
    )
    assert all(path.is_file() for path in expected)


def test_skill_metadata_declares_interface_contract() -> None:
    metadata = (
        ROOT / "skills" / "agy-supervisor" / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert re.search(r"(?m)^interface:\s*$", metadata)
    assert re.search(r"(?m)^\s+display_name:\s+AGY Supervisor\s*$", metadata)
    assert re.search(
        r"(?m)^\s+short_description:\s+Let Codex supervise bounded Antigravity coding tasks\.\s*$",
        metadata,
    )
    assert re.search(
        r"(?m)^\s+default_prompt:\s+Use AGY Supervisor mode for this explicitly delegated coding task\.\s*$",
        metadata,
    )


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
        "agy_start",
        "agy_status",
        "supervisor mode",
        "multi-page",
        "docs/agy-plans",
    ):
        assert phrase in readme
