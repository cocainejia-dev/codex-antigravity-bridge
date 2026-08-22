"""Read-only diagnostics and health check for codex-agy-bridge."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    details: str
    what_to_do_next: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "details": self.details,
            "what_to_do_next": self.what_to_do_next,
        }


@dataclass(frozen=True)
class DoctorReport:
    overall_status: CheckStatus
    checks: list[CheckResult]
    platform: str
    python_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "platform": self.platform,
            "python_version": self.python_version,
            "checks": [c.to_dict() for c in self.checks],
        }


_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "AGY_PROXY_URL",
    "PROXY_URL",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "agy_proxy_url",
    "proxy_url",
)


def _normalize_path_str(path_str: str) -> str:
    norm = os.path.normcase(os.path.normpath(path_str))
    return norm


def _paths_equal(path_a: str | Path, path_b: str | Path) -> bool:
    try:
        norm_a = _normalize_path_str(str(Path(path_a).resolve()))
        norm_b = _normalize_path_str(str(Path(path_b).resolve()))
        return norm_a == norm_b
    except Exception:
        return _normalize_path_str(str(path_a)) == _normalize_path_str(str(path_b))


def check_python(
    version_info: tuple[int, int, int] | None = None,
    executable: str | None = None,
) -> CheckResult:
    v_info = version_info or sys.version_info[:3]
    exe = executable or sys.executable
    v_str = f"{v_info[0]}.{v_info[1]}.{v_info[2]}"

    if v_info >= (3, 10):
        return CheckResult(
            name="Python",
            status=CheckStatus.PASS,
            details=f"Python {v_str} ({exe})",
        )
    return CheckResult(
        name="Python",
        status=CheckStatus.FAIL,
        details=f"Python {v_str} is older than required 3.10 ({exe})",
        what_to_do_next="Upgrade to Python 3.10 or newer.",
    )


def check_git(
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd_fn: Callable[[list[str], float], subprocess.CompletedProcess[str]] | None = None,
) -> CheckResult:
    which = which_fn or shutil.which
    git_path = which("git")
    if not git_path:
        return CheckResult(
            name="Git",
            status=CheckStatus.FAIL,
            details="Git executable was not found in PATH",
            what_to_do_next="Install Git and ensure 'git' is available in your PATH.",
        )

    runner = run_cmd_fn or (
        lambda cmd, timeout: subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    )

    try:
        res = runner([git_path, "--version"], 5.0)
        if res.returncode == 0:
            version_str = res.stdout.strip() or "Git available"
            return CheckResult(
                name="Git",
                status=CheckStatus.PASS,
                details=f"{version_str} ({git_path})",
            )
        return CheckResult(
            name="Git",
            status=CheckStatus.FAIL,
            details=f"Git check failed with return code {res.returncode}: {res.stderr.strip()}",
            what_to_do_next="Verify Git installation and permissions.",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Git",
            status=CheckStatus.FAIL,
            details="Git version query timed out after 5 seconds",
            what_to_do_next="Ensure Git is functioning properly and does not hang.",
        )
    except Exception as exc:
        return CheckResult(
            name="Git",
            status=CheckStatus.FAIL,
            details=f"Error executing Git: {exc}",
            what_to_do_next="Verify Git installation in PATH.",
        )


def check_agy_version(
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd_fn: Callable[[list[str], float], subprocess.CompletedProcess[str]] | None = None,
) -> CheckResult:
    which = which_fn or shutil.which
    agy_path = which("agy")
    if not agy_path:
        return CheckResult(
            name="Antigravity CLI (agy)",
            status=CheckStatus.FAIL,
            details="Antigravity CLI ('agy') executable was not found in PATH",
            what_to_do_next="Install Google Antigravity CLI ('agy') and ensure it is in your system PATH.",
        )

    runner = run_cmd_fn or (
        lambda cmd, timeout: subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    )

    try:
        res = runner([agy_path, "--version"], 5.0)
        if res.returncode == 0:
            ver_text = res.stdout.strip() or res.stderr.strip() or "version query succeeded"
            return CheckResult(
                name="Antigravity CLI (agy)",
                status=CheckStatus.PASS,
                details=f"{ver_text} ({agy_path})",
            )
        return CheckResult(
            name="Antigravity CLI (agy)",
            status=CheckStatus.WARN,
            details=f"agy found at {agy_path} but exited with code {res.returncode}",
            what_to_do_next="Verify agy installation by running 'agy' in a terminal.",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Antigravity CLI (agy)",
            status=CheckStatus.WARN,
            details=f"agy found at {agy_path} but version query timed out",
            what_to_do_next="Ensure agy is responsive and not hanging.",
        )
    except Exception as exc:
        return CheckResult(
            name="Antigravity CLI (agy)",
            status=CheckStatus.WARN,
            details=f"agy found at {agy_path} but query failed: {exc}",
            what_to_do_next="Verify agy installation by running 'agy' in a terminal.",
        )


def check_import_provenance(
    import_checker: Callable[[str], tuple[bool, str | None, str | None]] | None = None,
) -> CheckResult:
    if import_checker:
        ok, location, error = import_checker("codex_agy_bridge")
        if ok and location:
            return CheckResult(
                name="Import Provenance",
                status=CheckStatus.PASS,
                details=f"codex_agy_bridge imported from {location}",
            )
        return CheckResult(
            name="Import Provenance",
            status=CheckStatus.FAIL,
            details=f"Failed to import codex_agy_bridge: {error or 'unknown error'}",
            what_to_do_next="Verify PYTHONPATH or package installation ('pip install -e .').",
        )

    try:
        import codex_agy_bridge

        pkg_file = getattr(codex_agy_bridge, "__file__", None)
        if pkg_file and Path(pkg_file).is_file():
            return CheckResult(
                name="Import Provenance",
                status=CheckStatus.PASS,
                details=f"codex_agy_bridge imported from {Path(pkg_file).resolve()}",
            )
        return CheckResult(
            name="Import Provenance",
            status=CheckStatus.FAIL,
            details="codex_agy_bridge is missing a valid __file__ attribute",
            what_to_do_next="Ensure codex-agy-bridge is installed properly.",
        )
    except Exception as exc:
        return CheckResult(
            name="Import Provenance",
            status=CheckStatus.FAIL,
            details=f"Import failed: {exc}",
            what_to_do_next="Ensure codex_agy_bridge is importable and on PYTHONPATH.",
        )


def check_codex_mcp(
    which_fn: Callable[[str], str | None] | None = None,
    codex_home: Path | None = None,
    python_executable: str | None = None,
) -> CheckResult:
    which = which_fn or shutil.which
    home = codex_home or (Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
    config_path = home / "config.toml"
    current_python = python_executable or sys.executable
    codex_cli = which("codex")

    if not config_path.is_file():
        if not codex_cli:
            return CheckResult(
                name="Codex MCP Registration",
                status=CheckStatus.FAIL,
                details=f"Codex CLI not found and config file missing at {config_path}",
                what_to_do_next="Install Codex CLI and run 'codex-agy-bridge-setup'.",
            )
        return CheckResult(
            name="Codex MCP Registration",
            status=CheckStatus.FAIL,
            details=f"Codex config file not found at {config_path}",
            what_to_do_next="Run 'codex-agy-bridge-setup' to register the bridge MCP server.",
        )

    try:
        content = config_path.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult(
            name="Codex MCP Registration",
            status=CheckStatus.FAIL,
            details=f"Failed to read Codex config at {config_path}: {exc}",
            what_to_do_next="Check read permissions for Codex config.toml.",
        )

    if "[mcp_servers.codex-agy-bridge]" not in content:
        return CheckResult(
            name="Codex MCP Registration",
            status=CheckStatus.FAIL,
            details=f"[mcp_servers.codex-agy-bridge] section missing in {config_path}",
            what_to_do_next="Run 'codex-agy-bridge-setup' to register the bridge with Codex.",
        )

    configured_command: str | None = None
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if stripped == "[mcp_servers.codex-agy-bridge]":
                in_section = True
            elif in_section:
                in_section = False
        elif in_section and stripped.startswith("command"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                try:
                    configured_command = json.loads(parts[1].strip())
                except Exception:
                    configured_command = parts[1].strip().strip('"').strip("'")
                break

    if not configured_command:
        return CheckResult(
            name="Codex MCP Registration",
            status=CheckStatus.WARN,
            details=f"Registered in {config_path} but 'command' entry was not parsed",
            what_to_do_next="Run 'codex-agy-bridge-setup' to reconfigure MCP interpreter.",
        )

    if _paths_equal(configured_command, current_python):
        return CheckResult(
            name="Codex MCP Registration",
            status=CheckStatus.PASS,
            details=f"Registered in {config_path} with matching interpreter ({configured_command})",
        )

    return CheckResult(
        name="Codex MCP Registration",
        status=CheckStatus.WARN,
        details=(
            f"Registered in {config_path} with interpreter '{configured_command}', "
            f"which differs from current interpreter '{current_python}'"
        ),
        what_to_do_next="Run 'codex-agy-bridge-setup' to update Codex MCP to the current Python interpreter.",
    )


def check_runtime_provenance(
    executable: str | None = None,
    cwd: Path | None = None,
) -> CheckResult:
    exe = executable or sys.executable
    curr_cwd = cwd or Path.cwd()
    is_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)

    try:
        cwd_resolved = curr_cwd.resolve()
        cwd_accessible = cwd_resolved.exists() and os.access(str(cwd_resolved), os.R_OK)
    except Exception:
        cwd_accessible = False
        cwd_resolved = curr_cwd

    if not cwd_accessible:
        return CheckResult(
            name="Runtime Provenance",
            status=CheckStatus.FAIL,
            details=f"Working directory '{curr_cwd}' is not accessible or does not exist",
            what_to_do_next="Change to an accessible directory before running bridge commands.",
        )

    venv_label = "active virtualenv" if is_venv else "system / global environment"
    return CheckResult(
        name="Runtime Provenance",
        status=CheckStatus.PASS,
        details=f"Interpreter: {exe}, CWD: {cwd_resolved}, Environment: {venv_label}",
    )


def check_proxy_presence(
    env: Mapping[str, str] | None = None,
) -> CheckResult:
    active_env = env if env is not None else os.environ
    present_vars: list[str] = []
    insecure_vars: list[str] = []

    for key in _PROXY_ENV_NAMES:
        val = active_env.get(key)
        if val and val.strip():
            present_vars.append(key)
            try:
                parsed = urlsplit(val.strip())
                if parsed.username is not None or parsed.password is not None:
                    insecure_vars.append(key)
            except Exception:
                pass

    present_unique = sorted(set(present_vars))
    insecure_unique = sorted(set(insecure_vars))

    if insecure_unique:
        return CheckResult(
            name="Proxy Presence",
            status=CheckStatus.WARN,
            details=(
                f"Proxy variables detected with embedded credentials in {', '.join(insecure_unique)} "
                "(embedded credentials are prohibited; values redacted)"
            ),
            what_to_do_next="Remove embedded credentials from proxy environment variables (use http://host:port).",
        )

    if present_unique:
        return CheckResult(
            name="Proxy Presence",
            status=CheckStatus.PASS,
            details=f"Proxy variables present: {', '.join(present_unique)} (values redacted for security)",
        )

    return CheckResult(
        name="Proxy Presence",
        status=CheckStatus.PASS,
        details="No proxy environment variables detected",
    )


def check_headless_availability(
    which_fn: Callable[[str], str | None] | None = None,
    platform: str | None = None,
    winpty_available: bool | None = None,
) -> CheckResult:
    which = which_fn or shutil.which
    plat = platform or sys.platform
    agy_path = which("agy")

    if not agy_path:
        return CheckResult(
            name="Headless Availability",
            status=CheckStatus.FAIL,
            details="Headless execution unavailable: 'agy' CLI was not found in PATH",
            what_to_do_next="Install Google Antigravity CLI ('agy') and ensure it is in PATH.",
        )

    if plat == "win32":
        has_winpty = (
            winpty_available
            if winpty_available is not None
            else (importlib.util.find_spec("winpty") is not None)
        )
        if has_winpty:
            return CheckResult(
                name="Headless Availability",
                status=CheckStatus.PASS,
                details="Headless execution ready on Windows (ConPTY / pywinpty available)",
            )
        return CheckResult(
            name="Headless Availability",
            status=CheckStatus.WARN,
            details="Windows headless execution lacks 'pywinpty'; agy may produce empty output in non-interactive pipes",
            what_to_do_next="Install pywinpty: 'pip install pywinpty' or 'pip install codex-agy-bridge[winpty]'.",
        )

    return CheckResult(
        name="Headless Availability",
        status=CheckStatus.PASS,
        details="Headless execution ready (POSIX pty emulation available)",
    )


def check_auth_state(
    auth_checker: Callable[[], tuple[bool, str]] | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> CheckResult:
    if auth_checker:
        has_auth, desc = auth_checker()
        if has_auth:
            return CheckResult(
                name="Auth State",
                status=CheckStatus.PASS,
                details=f"Antigravity authentication credentials detected ({desc}) [tokens redacted]",
            )
        return CheckResult(
            name="Auth State",
            status=CheckStatus.WARN,
            details=f"No Antigravity credentials found ({desc})",
            what_to_do_next="Run 'agy' interactively once in a terminal to complete authentication.",
        )

    active_env = env if env is not None else os.environ
    env_keys = ("GEMINI_API_KEY", "ANTIGRAVITY_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
    for k in env_keys:
        if active_env.get(k):
            return CheckResult(
                name="Auth State",
                status=CheckStatus.PASS,
                details=f"Authentication indicator '{k}' is set in environment [token value redacted]",
            )

    home = home_dir or Path.home()
    candidate_paths = [
        home / ".gemini",
        home / ".antigravity",
        home / ".antigravity-ide",
        home / ".antigravity-ide-cli",
    ]
    for p in candidate_paths:
        if p.is_dir():
            return CheckResult(
                name="Auth State",
                status=CheckStatus.PASS,
                details=f"User profile credential store present at {p.name} [tokens redacted]",
            )

    return CheckResult(
        name="Auth State",
        status=CheckStatus.WARN,
        details="No active Antigravity authentication profile or credential store found",
        what_to_do_next="Run 'agy' interactively once in a terminal to complete login.",
    )


def check_install_health(
    dependency_checker: Callable[[], tuple[bool, list[str]]] | None = None,
) -> CheckResult:
    if dependency_checker:
        ok, missing = dependency_checker()
        if ok:
            return CheckResult(
                name="Install Health",
                status=CheckStatus.PASS,
                details="Core package dependencies and modules are intact",
            )
        return CheckResult(
            name="Install Health",
            status=CheckStatus.FAIL,
            details=f"Missing or broken dependencies: {', '.join(missing)}",
            what_to_do_next="Reinstall package dependencies: 'pip install -e .' or 'pip install mcp'.",
        )

    missing: list[str] = []
    for mod in ("mcp", "codex_agy_bridge.server", "codex_agy_bridge.setup", "codex_agy_bridge.verification"):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            missing.append(f"{mod} ({exc})")

    if not missing:
        return CheckResult(
            name="Install Health",
            status=CheckStatus.PASS,
            details="Core dependencies (mcp) and bridge submodules are intact",
        )

    return CheckResult(
        name="Install Health",
        status=CheckStatus.FAIL,
        details=f"Broken or missing components: {'; '.join(missing)}",
        what_to_do_next="Reinstall bridge dependencies: 'pip install -e .' or 'pip install mcp'.",
    )


def check_skill_installation(
    codex_home: Path | None = None,
) -> CheckResult:
    home = codex_home or (Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
    skill_dir = home / "skills" / "agy-supervisor"
    skill_md = skill_dir / "SKILL.md"

    if skill_md.is_file():
        return CheckResult(
            name="Skill Installation",
            status=CheckStatus.PASS,
            details=f"agy-supervisor skill is installed at {skill_dir}",
        )

    if skill_dir.is_dir():
        return CheckResult(
            name="Skill Installation",
            status=CheckStatus.WARN,
            details=f"Skill directory exists at {skill_dir} but SKILL.md is missing",
            what_to_do_next="Run 'codex-agy-bridge-setup' to reinstall the agy-supervisor skill.",
        )

    return CheckResult(
        name="Skill Installation",
        status=CheckStatus.FAIL,
        details=f"agy-supervisor skill is not installed at {skill_dir}",
        what_to_do_next="Run 'codex-agy-bridge-setup' to install the agy-supervisor skill.",
    )


def check_windows_conpty(
    platform: str | None = None,
    winpty_available: bool | None = None,
) -> CheckResult:
    plat = platform or sys.platform
    if plat == "win32":
        has_winpty = (
            winpty_available
            if winpty_available is not None
            else (importlib.util.find_spec("winpty") is not None)
        )
        if has_winpty:
            return CheckResult(
                name="Windows ConPTY",
                status=CheckStatus.PASS,
                details="Windows ConPTY support is available via 'winpty' (pywinpty)",
            )
        return CheckResult(
            name="Windows ConPTY",
            status=CheckStatus.WARN,
            details="Windows ConPTY support is not available ('winpty' not installed)",
            what_to_do_next="Install pywinpty: 'pip install pywinpty' or 'pip install codex-agy-bridge[winpty]'.",
        )

    return CheckResult(
        name="Windows ConPTY",
        status=CheckStatus.PASS,
        details="Not on Windows; ConPTY is not required on POSIX systems",
    )


def run_doctor(
    version_info: tuple[int, int, int] | None = None,
    executable: str | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd_fn: Callable[[list[str], float], subprocess.CompletedProcess[str]] | None = None,
    import_checker: Callable[[str], tuple[bool, str | None, str | None]] | None = None,
    codex_home: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    winpty_available: bool | None = None,
    auth_checker: Callable[[], tuple[bool, str]] | None = None,
    dependency_checker: Callable[[], tuple[bool, list[str]]] | None = None,
    home_dir: Path | None = None,
) -> DoctorReport:
    plat = platform or sys.platform
    v_info = version_info or sys.version_info[:3]
    v_str = f"{v_info[0]}.{v_info[1]}.{v_info[2]}"

    checks: list[CheckResult] = [
        check_python(version_info=v_info, executable=executable),
        check_git(which_fn=which_fn, run_cmd_fn=run_cmd_fn),
        check_agy_version(which_fn=which_fn, run_cmd_fn=run_cmd_fn),
        check_import_provenance(import_checker=import_checker),
        check_codex_mcp(which_fn=which_fn, codex_home=codex_home, python_executable=executable),
        check_runtime_provenance(executable=executable, cwd=cwd),
        check_proxy_presence(env=env),
        check_headless_availability(which_fn=which_fn, platform=plat, winpty_available=winpty_available),
        check_auth_state(auth_checker=auth_checker, env=env, home_dir=home_dir),
        check_install_health(dependency_checker=dependency_checker),
        check_skill_installation(codex_home=codex_home),
        check_windows_conpty(platform=plat, winpty_available=winpty_available),
    ]

    has_fail = any(c.status == CheckStatus.FAIL for c in checks)
    has_warn = any(c.status == CheckStatus.WARN for c in checks)

    if has_fail:
        overall = CheckStatus.FAIL
    elif has_warn:
        overall = CheckStatus.WARN
    else:
        overall = CheckStatus.PASS

    return DoctorReport(
        overall_status=overall,
        checks=checks,
        platform=plat,
        python_version=v_str,
    )


def format_report_text(report: DoctorReport) -> str:
    lines: list[str] = [
        "=" * 70,
        "Codex Antigravity Bridge Doctor",
        "=" * 70,
        f"Platform: {report.platform} | Python: {report.python_version}",
        "",
    ]

    for c in report.checks:
        status_tag = f"[{c.status.value}]"
        lines.append(f"{status_tag:<8} {c.name}: {c.details}")

    pass_count = sum(1 for c in report.checks if c.status == CheckStatus.PASS)
    warn_count = sum(1 for c in report.checks if c.status == CheckStatus.WARN)
    fail_count = sum(1 for c in report.checks if c.status == CheckStatus.FAIL)

    lines.extend([
        "",
        "-" * 70,
        f"Result: {report.overall_status.value} ({pass_count} passed, {warn_count} warning(s), {fail_count} failed)",
        "=" * 70,
    ])

    actionable = [c for c in report.checks if c.status in (CheckStatus.FAIL, CheckStatus.WARN) and c.what_to_do_next]
    if actionable:
        lines.extend([
            "",
            "=" * 70,
            "WHAT TO DO NEXT:",
            "=" * 70,
        ])
        for c in actionable:
            lines.append(f"- {c.name} [{c.status.value}]: {c.what_to_do_next}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-agy-bridge doctor",
        description="Run read-only diagnostics for codex-agy-bridge environment.",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="output report in JSON format",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with non-zero code on warnings as well as failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    report = run_doctor()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report_text(report))

    if report.overall_status == CheckStatus.FAIL:
        return 1
    if args.strict and report.overall_status == CheckStatus.WARN:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
