from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_agy_bridge import setup


def test_resolve_proxy_rejects_embedded_credentials() -> None:
    with pytest.raises(setup.SetupError, match="embedded credentials"):
        setup.resolve_proxy("http://user:secret@127.0.0.1:7890")


def test_resolve_proxy_reads_lowercase_environment_names(monkeypatch) -> None:
    monkeypatch.setattr(
        setup.os,
        "environ",
        {"https_proxy": "http://127.0.0.1:7890"},
    )

    assert setup.resolve_proxy() == "http://127.0.0.1:7890"


def test_update_codex_config_preserves_unmanaged_settings(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[mcp_servers.codex-agy-bridge]\n"
        'args = ["-m", "codex_agy_bridge"]\n'
        "startup_timeout_sec = 120\n"
        "\n"
        "[mcp_servers.codex-agy-bridge.env]\n"
        'CUSTOM = "keep"\n'
        'HTTP_PROXY = "http://old:1"\n'
        "\n"
        "[projects]\n"
        'root = "keep"\n',
        encoding="utf-8",
    )

    setup.update_codex_config(config, r"C:\Python\python.exe", "http://127.0.0.1:7890")
    content = config.read_text(encoding="utf-8")

    assert 'command = "C:\\\\Python\\\\python.exe"' in content
    assert 'args = ["-m", "codex_agy_bridge"]' in content
    assert "startup_timeout_sec = 120" in content
    assert 'CUSTOM = "keep"' in content
    assert content.count('HTTP_PROXY = "http://127.0.0.1:7890"') == 1
    assert 'HTTP_PROXY = "http://old:1"' not in content
    assert 'root = "keep"' in content


def test_update_codex_config_preserves_existing_production_command(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[mcp_servers.codex-agy-bridge]\n"
        'command = "D:\\\\CODEX项目\\\\agy-supervisor-runtime\\\\.venv\\\\Scripts\\\\python.exe"\n'
        'args = ["-m", "codex_agy_bridge"]\n',
        encoding="utf-8",
    )

    setup.update_codex_config(config, r"C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe", None)

    assert "agy-supervisor-runtime" in config.read_text(encoding="utf-8")
    assert "Python312" not in config.read_text(encoding="utf-8")


def test_update_codex_config_fills_missing_command(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[mcp_servers.codex-agy-bridge]\n"
        'args = ["-m", "codex_agy_bridge"]\n',
        encoding="utf-8",
    )

    setup.update_codex_config(config, r"C:\Python\python.exe", None)

    assert 'command = "C:\\\\Python\\\\python.exe"' in config.read_text(encoding="utf-8")


def test_update_codex_config_preserves_existing_command_when_proxy_changes(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[mcp_servers.codex-agy-bridge]\n"
        'command = "D:\\\\production\\\\python.exe"\n'
        'args = ["-m", "codex_agy_bridge"]\n',
        encoding="utf-8",
    )

    setup.update_codex_config(config, r"C:\Python\python.exe", "http://127.0.0.1:7890")
    content = config.read_text(encoding="utf-8")

    assert 'command = "D:\\\\production\\\\python.exe"' in content
    assert 'HTTP_PROXY = "http://127.0.0.1:7890"' in content


def test_setup_first_registration_uses_current_interpreter(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    calls: list[tuple[str, ...]] = []
    current_python = r"C:\Python\python.exe"

    def fake_run_codex(codex: str, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("mcp", "add"):
            config.write_text(
                "[mcp_servers.codex-agy-bridge]\n"
                'args = ["-m", "codex_agy_bridge"]\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess([codex, *args], 0, stdout="", stderr="")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "codex.exe")
    monkeypatch.setattr(setup, "_run_codex", fake_run_codex)
    monkeypatch.setattr(setup, "_copy_skill", lambda destination: None)
    monkeypatch.setattr(setup.sys, "executable", current_python)

    assert setup.main(["--no-proxy"]) == 0

    assert ("mcp", "add", "codex-agy-bridge", "--", current_python, "-m", "codex_agy_bridge") in calls
    assert current_python.replace("\\", "\\\\") in config.read_text(encoding="utf-8")


def test_what_if_is_side_effect_free(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.delenv("AGY_PROXY_URL", raising=False)
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)

    assert setup.main(["--what-if", "--no-proxy"]) == 0
    output = capsys.readouterr().out

    assert "No files" in output
    assert not (tmp_path / ".codex").exists()


def test_packaged_skill_resource_matches_expected_files() -> None:
    source = Path(setup._resource_path())

    assert (source / "SKILL.md").is_file()
    assert (source / "agents" / "openai.yaml").is_file()
    assert list((source / "references").glob("*.md"))


def test_setup_copies_skill_with_continuity_contract(tmp_path: Path) -> None:
    dest = tmp_path / "skills" / "agy-supervisor"
    setup._copy_skill(dest)

    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "agy-supervisor-protocol.md").is_file()

    skill_text = (dest / "SKILL.md").read_text(encoding="utf-8")
    protocol_text = (dest / "references" / "agy-supervisor-protocol.md").read_text(
        encoding="utf-8"
    )

    assert "ACTIVE_IS_FINAL = NO" in skill_text
    assert "RUNNING_IS_STOP_CONDITION = NO" in skill_text
    assert "WAIT_WINDOW_EXPIRED_IS_FINAL = NO" in protocol_text
    assert "BOUNDED_WAIT_WINDOW_EXPIRED != TASK_TIMEOUT" in protocol_text
    assert "REPLACEMENT_WORKER = NO" in protocol_text
