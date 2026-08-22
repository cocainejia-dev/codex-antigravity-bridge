"""Deterministic tests for codex-agy-bridge doctor diagnostics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from codex_agy_bridge import __main__ as bridge_main
from codex_agy_bridge import doctor, setup
from codex_agy_bridge.doctor import (
    CheckResult,
    CheckStatus,
    DoctorReport,
    check_agy_version,
    check_auth_state,
    check_codex_mcp,
    check_git,
    check_headless_availability,
    check_import_provenance,
    check_install_health,
    check_proxy_presence,
    check_python,
    check_runtime_provenance,
    check_skill_installation,
    check_windows_conpty,
    format_report_text,
    run_doctor,
)


def test_check_python_pass() -> None:
    res = check_python(version_info=(3, 11, 4), executable="/usr/bin/python3")
    assert res.status == CheckStatus.PASS
    assert "3.11.4" in res.details
    assert "/usr/bin/python3" in res.details
    assert res.what_to_do_next is None


def test_check_python_fail() -> None:
    res = check_python(version_info=(3, 9, 10), executable="/usr/bin/python3.9")
    assert res.status == CheckStatus.FAIL
    assert "3.9.10" in res.details
    assert "older than required 3.10" in res.details
    assert res.what_to_do_next is not None
    assert "Upgrade to Python 3.10" in res.what_to_do_next


def test_check_git_pass() -> None:
    which_fn = lambda name: "/usr/bin/git" if name == "git" else None
    run_cmd_fn = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, stdout="git version 2.43.0\n", stderr="")

    res = check_git(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.PASS
    assert "git version 2.43.0" in res.details
    assert "/usr/bin/git" in res.details


def test_check_git_missing() -> None:
    res = check_git(which_fn=lambda name: None)
    assert res.status == CheckStatus.FAIL
    assert "not found in PATH" in res.details
    assert res.what_to_do_next is not None


def test_check_git_nonzero_exit() -> None:
    which_fn = lambda name: "/usr/bin/git"
    run_cmd_fn = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 127, stdout="", stderr="permission denied")

    res = check_git(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.FAIL
    assert "return code 127" in res.details
    assert "permission denied" in res.details


def test_check_git_timeout() -> None:
    which_fn = lambda name: "/usr/bin/git"

    def run_cmd_fn(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, timeout)

    res = check_git(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.FAIL
    assert "timed out" in res.details


def test_check_git_exception() -> None:
    which_fn = lambda name: "/usr/bin/git"

    def run_cmd_fn(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise OSError("Exec format error")

    res = check_git(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.FAIL
    assert "Error executing Git" in res.details


def test_check_agy_version_pass() -> None:
    which_fn = lambda name: "/usr/local/bin/agy"
    run_cmd_fn = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, stdout="agy 0.5.1\n", stderr="")

    res = check_agy_version(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.PASS
    assert "agy 0.5.1" in res.details


def test_check_agy_version_missing() -> None:
    res = check_agy_version(which_fn=lambda name: None)
    assert res.status == CheckStatus.FAIL
    assert "executable was not found in PATH" in res.details
    assert res.what_to_do_next is not None


def test_check_agy_version_nonzero() -> None:
    which_fn = lambda name: "/usr/local/bin/agy"
    run_cmd_fn = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="crash")

    res = check_agy_version(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.WARN
    assert "exited with code 1" in res.details


def test_check_agy_version_timeout() -> None:
    which_fn = lambda name: "/usr/local/bin/agy"

    def run_cmd_fn(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, timeout)

    res = check_agy_version(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.WARN
    assert "timed out" in res.details


def test_check_agy_version_exception() -> None:
    which_fn = lambda name: "/usr/local/bin/agy"

    def run_cmd_fn(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise OSError("Process failed")

    res = check_agy_version(which_fn=which_fn, run_cmd_fn=run_cmd_fn)
    assert res.status == CheckStatus.WARN
    assert "query failed" in res.details


def test_check_import_provenance_pass() -> None:
    res = check_import_provenance(import_checker=lambda mod: (True, "/src/codex_agy_bridge/__init__.py", None))
    assert res.status == CheckStatus.PASS
    assert "/src/codex_agy_bridge/__init__.py" in res.details


def test_check_import_provenance_fail() -> None:
    res = check_import_provenance(import_checker=lambda mod: (False, None, "ModuleNotFoundError: No module named 'mcp'"))
    assert res.status == CheckStatus.FAIL
    assert "No module named 'mcp'" in res.details
    assert res.what_to_do_next is not None


def test_check_codex_mcp_matching_interpreter(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.codex-agy-bridge]\ncommand = "C:\\\\Python312\\\\python.exe"\n',
        encoding="utf-8",
    )
    res = check_codex_mcp(
        which_fn=lambda name: "C:\\bin\\codex.exe",
        codex_home=tmp_path,
        python_executable="C:\\Python312\\python.exe",
    )
    assert res.status == CheckStatus.PASS
    assert "matching interpreter" in res.details


def test_check_codex_mcp_windows_path_normalization(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.codex-agy-bridge]\ncommand = "c:/python312/python.exe"\n',
        encoding="utf-8",
    )
    res = check_codex_mcp(
        which_fn=lambda name: "C:\\bin\\codex.exe",
        codex_home=tmp_path,
        python_executable="C:\\Python312\\python.exe",
    )
    assert res.status == CheckStatus.PASS
    assert "matching interpreter" in res.details


def test_check_codex_mcp_different_interpreter(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.codex-agy-bridge]\ncommand = "/usr/bin/python3.10"\n',
        encoding="utf-8",
    )
    res = check_codex_mcp(
        which_fn=lambda name: "/usr/bin/codex",
        codex_home=tmp_path,
        python_executable="/usr/bin/python3.12",
    )
    assert res.status == CheckStatus.WARN
    assert "differs from current interpreter" in res.details
    assert res.what_to_do_next is not None


def test_check_codex_mcp_missing_section(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.other]\ncommand = "other"\n', encoding="utf-8")
    res = check_codex_mcp(
        which_fn=lambda name: "/usr/bin/codex",
        codex_home=tmp_path,
        python_executable="/usr/bin/python3",
    )
    assert res.status == CheckStatus.FAIL
    assert "section missing" in res.details


def test_check_codex_mcp_missing_command(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.codex-agy-bridge]\nargs = []\n', encoding="utf-8")
    res = check_codex_mcp(
        which_fn=lambda name: "/usr/bin/codex",
        codex_home=tmp_path,
        python_executable="/usr/bin/python3",
    )
    assert res.status == CheckStatus.WARN
    assert "command' entry was not parsed" in res.details


def test_check_codex_mcp_missing_config(tmp_path: Path) -> None:
    res = check_codex_mcp(
        which_fn=lambda name: "/usr/bin/codex",
        codex_home=tmp_path / "nonexistent",
        python_executable="/usr/bin/python3",
    )
    assert res.status == CheckStatus.FAIL
    assert "config file not found" in res.details


def test_check_runtime_provenance_pass(tmp_path: Path) -> None:
    res = check_runtime_provenance(executable="/usr/bin/python3", cwd=tmp_path)
    assert res.status == CheckStatus.PASS
    assert "/usr/bin/python3" in res.details


def test_check_runtime_provenance_inaccessible(tmp_path: Path) -> None:
    nonexistent = tmp_path / "missing_dir"
    res = check_runtime_provenance(executable="/usr/bin/python3", cwd=nonexistent)
    assert res.status == CheckStatus.FAIL
    assert "is not accessible" in res.details


def test_check_proxy_presence_none() -> None:
    res = check_proxy_presence(env={})
    assert res.status == CheckStatus.PASS
    assert "No proxy environment variables detected" in res.details


def test_check_proxy_presence_redacted() -> None:
    env = {
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "ALL_PROXY": "socks5://127.0.0.1:1080",
    }
    res = check_proxy_presence(env=env)
    assert res.status == CheckStatus.PASS
    assert "HTTP_PROXY" in res.details
    assert "HTTPS_PROXY" in res.details
    assert "ALL_PROXY" in res.details
    assert "7890" not in res.details
    assert "1080" not in res.details
    assert "127.0.0.1" not in res.details
    assert "values redacted" in res.details


def test_check_proxy_presence_with_credentials_redacted() -> None:
    env = {
        "HTTP_PROXY": "http://supersecretuser:supersecretpass@proxy.example.com:8080",
    }
    res = check_proxy_presence(env=env)
    assert res.status == CheckStatus.WARN
    assert "HTTP_PROXY" in res.details
    assert "supersecretuser" not in res.details
    assert "supersecretpass" not in res.details
    assert "proxy.example.com" not in res.details
    assert "embedded credentials" in res.details


def test_check_headless_availability_missing_agy() -> None:
    res = check_headless_availability(which_fn=lambda name: None)
    assert res.status == CheckStatus.FAIL
    assert "CLI was not found in PATH" in res.details


def test_check_headless_availability_posix() -> None:
    res = check_headless_availability(which_fn=lambda name: "/usr/bin/agy", platform="linux")
    assert res.status == CheckStatus.PASS
    assert "POSIX" in res.details


def test_check_headless_availability_windows_with_winpty() -> None:
    res = check_headless_availability(which_fn=lambda name: "C:\\bin\\agy.exe", platform="win32", winpty_available=True)
    assert res.status == CheckStatus.PASS
    assert "ConPTY / pywinpty available" in res.details


def test_check_headless_availability_windows_without_winpty() -> None:
    res = check_headless_availability(which_fn=lambda name: "C:\\bin\\agy.exe", platform="win32", winpty_available=False)
    assert res.status == CheckStatus.WARN
    assert "lacks 'pywinpty'" in res.details
    assert res.what_to_do_next is not None


def test_check_auth_state_custom_checker_pass() -> None:
    res = check_auth_state(auth_checker=lambda: (True, "mock auth detected"))
    assert res.status == CheckStatus.PASS
    assert "mock auth detected" in res.details
    assert "tokens redacted" in res.details


def test_check_auth_state_custom_checker_warn() -> None:
    res = check_auth_state(auth_checker=lambda: (False, "no profile"))
    assert res.status == CheckStatus.WARN
    assert "no profile" in res.details
    assert res.what_to_do_next is not None


def test_check_auth_state_env_token_redacted() -> None:
    secret_key = "AIzaSySecretApiKey1234567890"
    res = check_auth_state(env={"GEMINI_API_KEY": secret_key})
    assert res.status == CheckStatus.PASS
    assert "GEMINI_API_KEY" in res.details
    assert secret_key not in res.details
    assert "token value redacted" in res.details


def test_check_auth_state_profile_dir(tmp_path: Path) -> None:
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    res = check_auth_state(env={}, home_dir=tmp_path)
    assert res.status == CheckStatus.PASS
    assert ".gemini" in res.details
    assert "tokens redacted" in res.details


def test_check_auth_state_missing(tmp_path: Path) -> None:
    res = check_auth_state(env={}, home_dir=tmp_path)
    assert res.status == CheckStatus.WARN
    assert "No active Antigravity authentication" in res.details


def test_check_install_health_pass() -> None:
    res = check_install_health(dependency_checker=lambda: (True, []))
    assert res.status == CheckStatus.PASS
    assert "intact" in res.details


def test_check_install_health_fail() -> None:
    res = check_install_health(dependency_checker=lambda: (False, ["mcp", "codex_agy_bridge.server"]))
    assert res.status == CheckStatus.FAIL
    assert "mcp" in res.details
    assert res.what_to_do_next is not None


def test_check_skill_installation_pass(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "agy-supervisor" / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("# Skill", encoding="utf-8")

    res = check_skill_installation(codex_home=tmp_path)
    assert res.status == CheckStatus.PASS
    assert "is installed" in res.details


def test_check_skill_installation_missing_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "agy-supervisor"
    skill_dir.mkdir(parents=True, exist_ok=True)

    res = check_skill_installation(codex_home=tmp_path)
    assert res.status == CheckStatus.WARN
    assert "SKILL.md is missing" in res.details


def test_check_skill_installation_missing_dir(tmp_path: Path) -> None:
    res = check_skill_installation(codex_home=tmp_path)
    assert res.status == CheckStatus.FAIL
    assert "is not installed" in res.details


def test_check_windows_conpty_win32_available() -> None:
    res = check_windows_conpty(platform="win32", winpty_available=True)
    assert res.status == CheckStatus.PASS
    assert "ConPTY support is available" in res.details


def test_check_windows_conpty_win32_missing() -> None:
    res = check_windows_conpty(platform="win32", winpty_available=False)
    assert res.status == CheckStatus.WARN
    assert "not available" in res.details


def test_check_windows_conpty_posix() -> None:
    res = check_windows_conpty(platform="linux")
    assert res.status == CheckStatus.PASS
    assert "Not on Windows" in res.details


def test_run_doctor_all_pass(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.codex-agy-bridge]\ncommand = "/bin/python3"\n', encoding="utf-8")
    skill_md = tmp_path / "skills" / "agy-supervisor" / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("# Skill", encoding="utf-8")

    report = run_doctor(
        version_info=(3, 11, 0),
        executable="/bin/python3",
        which_fn=lambda name: f"/bin/{name}",
        run_cmd_fn=lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, stdout="1.0.0", stderr=""),
        import_checker=lambda name: (True, "/path/to/pkg", None),
        codex_home=tmp_path,
        cwd=tmp_path,
        env={},
        platform="linux",
        auth_checker=lambda: (True, "mock auth"),
        dependency_checker=lambda: (True, []),
    )

    assert report.overall_status == CheckStatus.PASS
    assert len(report.checks) == 12
    assert all(c.status == CheckStatus.PASS for c in report.checks)


def test_run_doctor_with_warnings(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.codex-agy-bridge]\ncommand = "/bin/python3.10"\n', encoding="utf-8")
    skill_md = tmp_path / "skills" / "agy-supervisor" / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("# Skill", encoding="utf-8")

    report = run_doctor(
        version_info=(3, 11, 0),
        executable="/bin/python3.11",
        which_fn=lambda name: f"/bin/{name}",
        run_cmd_fn=lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, stdout="1.0.0", stderr=""),
        import_checker=lambda name: (True, "/path/to/pkg", None),
        codex_home=tmp_path,
        cwd=tmp_path,
        env={},
        platform="linux",
        auth_checker=lambda: (True, "mock auth"),
        dependency_checker=lambda: (True, []),
    )

    assert report.overall_status == CheckStatus.WARN


def test_run_doctor_with_failure() -> None:
    report = run_doctor(
        version_info=(3, 8, 0),
        executable="/bin/python3.8",
        which_fn=lambda name: None,
        run_cmd_fn=lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
        import_checker=lambda name: (False, None, "error"),
        platform="linux",
        auth_checker=lambda: (False, "none"),
        dependency_checker=lambda: (False, ["mcp"]),
    )

    assert report.overall_status == CheckStatus.FAIL


def test_format_report_text() -> None:
    report = DoctorReport(
        overall_status=CheckStatus.WARN,
        checks=[
            CheckResult("Python", CheckStatus.PASS, "Python 3.12.0"),
            CheckResult("Git", CheckStatus.WARN, "Git slow", what_to_do_next="Check Git."),
        ],
        platform="win32",
        python_version="3.12.0",
    )
    formatted = format_report_text(report)
    assert "Codex Antigravity Bridge Doctor" in formatted
    assert "[PASS]   Python: Python 3.12.0" in formatted
    assert "[WARN]   Git: Git slow" in formatted
    assert "Result: WARN (1 passed, 1 warning(s), 0 failed)" in formatted
    assert "WHAT TO DO NEXT:" in formatted
    assert "- Git [WARN]: Check Git." in formatted


def test_doctor_main_json(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "run_doctor",
        lambda **kwargs: DoctorReport(
            overall_status=CheckStatus.PASS,
            checks=[CheckResult("Python", CheckStatus.PASS, "3.12.0")],
            platform="linux",
            python_version="3.12.0",
        ),
    )
    code = doctor.main(["--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["overall_status"] == "PASS"
    assert data["checks"][0]["name"] == "Python"


def test_doctor_main_strict_exit_code(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "run_doctor",
        lambda **kwargs: DoctorReport(
            overall_status=CheckStatus.WARN,
            checks=[CheckResult("Git", CheckStatus.WARN, "Git warn", what_to_do_next="Fix Git")],
            platform="linux",
            python_version="3.12.0",
        ),
    )
    code = doctor.main(["--strict"])
    assert code == 1

    code_lenient = doctor.main([])
    assert code_lenient == 0


def test_bridge_main_doctor_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: list[list[str]] = []
    monkeypatch.setattr(doctor, "main", lambda argv: (called_with.append(argv), 0)[1])

    ret1 = bridge_main.main(["doctor", "--json"])
    assert ret1 == 0
    assert called_with == [["--json"]]

    ret2 = bridge_main.main(["--doctor", "--strict"])
    assert ret2 == 0
    assert called_with == [["--json"], ["--strict"]]


def test_setup_main_doctor_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: list[list[str]] = []
    monkeypatch.setattr(doctor, "main", lambda argv: (called_with.append(argv), 0)[1])

    ret1 = setup.main(["doctor"])
    assert ret1 == 0
    assert called_with == [[]]

    ret2 = setup.main(["--doctor"])
    assert ret2 == 0
    assert called_with == [[], []]
