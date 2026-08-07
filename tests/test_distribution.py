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
        ROOT / "README.en.md",
        ROOT / "PROGRESS.en.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.en.md",
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
        "监督模式",
        "多页面协同",
        "docs/agy-plans",
        "README.en.md",
        "PROGRESS.en.md",
        "docs/README.md",
    ):
        assert phrase in readme


def test_readmes_document_all_runtime_modes() -> None:
    chinese = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README.en.md").read_text(encoding="utf-8")

    for phrase in (
        "模式总览",
        "Codex 普通开发",
        "单次同步委派",
        "异步独立任务",
        "协同开发 MVP",
        "headless",
        "terminal",
        "最多 4 个任务",
    ):
        assert phrase in chinese

    for phrase in (
        "Runtime Modes",
        "Normal Codex development",
        "Synchronous delegation",
        "Async isolated task",
        "Collaboration MVP",
        "headless",
        "terminal",
        "four tasks",
    ):
        assert phrase in english


def test_progress_documents_runtime_modes_and_lifecycle() -> None:
    progress = (ROOT / "PROGRESS.md").read_text(encoding="utf-8")

    for phrase in (
        "运行模式 · Runtime Modes",
        "Codex 普通开发",
        "Synchronous delegation",
        "协同开发 MVP",
        "显示方式 · Display Options",
        "协同生命周期 · Collaboration Lifecycle",
        "ready_for_review",
        "最多 4 个任务",
    ):
        assert phrase in progress


def test_installers_support_per_user_proxy_configuration() -> None:
    windows_installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    posix_installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    for phrase in ("ProxyUrl", "HTTP_PROXY", "HTTPS_PROXY", "mcp_servers.codex-agy-bridge.env"):
        assert phrase in windows_installer
    for phrase in ("PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY", "mcp_servers.codex-agy-bridge.env"):
        assert phrase in posix_installer
