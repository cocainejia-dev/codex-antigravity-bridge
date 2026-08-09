from __future__ import annotations

from pathlib import Path

import pytest

from codex_agy_bridge import setup


def test_resolve_proxy_rejects_embedded_credentials() -> None:
    with pytest.raises(setup.SetupError, match="embedded credentials"):
        setup.resolve_proxy("http://user:secret@127.0.0.1:7890")


def test_update_codex_config_preserves_unmanaged_settings(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.codex-agy-bridge]\n'
        'command = "python"\n'
        'args = ["-m", "codex_agy_bridge"]\n'
        'startup_timeout_sec = 120\n'
        '\n'
        '[mcp_servers.codex-agy-bridge.env]\n'
        'CUSTOM = "keep"\n'
        'HTTP_PROXY = "http://old:1"\n'
        '\n'
        '[projects]\n'
        'root = "keep"\n',
        encoding="utf-8",
    )

    setup.update_codex_config(config, r"C:\Python\python.exe", "http://127.0.0.1:7890")
    content = config.read_text(encoding="utf-8")

    assert 'command = "C:\\\\Python\\\\python.exe"' in content
    assert 'args = ["-m", "codex_agy_bridge"]' in content
    assert 'startup_timeout_sec = 120' in content
    assert 'CUSTOM = "keep"' in content
    assert content.count('HTTP_PROXY = "http://127.0.0.1:7890"') == 1
    assert 'HTTP_PROXY = "http://old:1"' not in content
    assert 'root = "keep"' in content


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
