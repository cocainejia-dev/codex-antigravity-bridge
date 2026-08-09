"""Install the bridge skill and register the MCP server with Codex."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import urlsplit


_SERVER_HEADER = "[mcp_servers.codex-agy-bridge]"
_ENV_HEADER = "[mcp_servers.codex-agy-bridge.env]"
_MANAGED_ENV = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
_PROXY_ENV = ("AGY_PROXY_URL", "PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


class SetupError(RuntimeError):
    """An actionable setup failure that is safe to show to the user."""


def _normalise_proxy(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    candidate = value.strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"}
            or not parsed.hostname
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
    except ValueError:
        return None
    return candidate


def resolve_proxy(explicit: str | None = None, no_proxy: bool = False) -> str | None:
    if no_proxy:
        return None
    if explicit:
        proxy = _normalise_proxy(explicit)
        if proxy is None:
            raise SetupError(
                "Invalid --proxy-url. Use http://host:port or socks5://host:port "
                "without embedded credentials."
            )
        return proxy
    for name in _PROXY_ENV:
        proxy = _normalise_proxy(os.environ.get(name))
        if proxy:
            return proxy
    return None


def _resource_path() -> object:
    return files("codex_agy_bridge").joinpath("resources", "agy-supervisor")


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured) if configured else Path.home() / ".codex").expanduser()


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _section_end(lines: list[str], start: int) -> int:
    return next(
        (index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
        len(lines),
    )


def update_codex_config(config_path: Path, python_executable: str, proxy: str | None) -> None:
    if not config_path.is_file():
        raise SetupError(f"Codex config was not found at {config_path}.")

    lines = config_path.read_text(encoding="utf-8").splitlines()
    if _SERVER_HEADER not in lines:
        raise SetupError(f"MCP server section was not found in {config_path}.")

    server_start = lines.index(_SERVER_HEADER)
    server_end = _section_end(lines, server_start)
    command_indices = [
        index
        for index in range(server_start + 1, server_end)
        if lines[index].lstrip().startswith("command")
    ]
    command = f"command = {_toml_string(python_executable)}"
    if command_indices:
        lines[command_indices[0]] = command
    else:
        lines.insert(server_start + 1, command)

    env_start = lines.index(_ENV_HEADER) if _ENV_HEADER in lines else -1
    if env_start >= 0:
        env_end = _section_end(lines, env_start)
        for index in range(env_end - 1, env_start, -1):
            key = lines[index].split("=", 1)[0].strip()
            if key in _MANAGED_ENV:
                lines.pop(index)
    if proxy:
        env_start = lines.index(_ENV_HEADER) if _ENV_HEADER in lines else -1
        if env_start < 0:
            server_start = lines.index(_SERVER_HEADER)
            server_end = _section_end(lines, server_start)
            lines[server_end:server_end] = ["", _ENV_HEADER]
            env_start = server_end + 1
        insert_at = env_start + 1
        entries = [
            f"HTTP_PROXY = {_toml_string(proxy)}",
            f"HTTPS_PROXY = {_toml_string(proxy)}",
            f"ALL_PROXY = {_toml_string(proxy)}",
            'NO_PROXY = "localhost,127.0.0.1"',
        ]
        lines[insert_at:insert_at] = entries

    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_codex(codex: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [codex, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_skill(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    with as_file(_resource_path()) as source:
        shutil.copytree(source, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what-if", action="store_true", help="print the setup plan only")
    parser.add_argument("--proxy-url", help="explicit proxy URL without embedded credentials")
    parser.add_argument("--no-proxy", action="store_true", help="remove managed proxy settings")
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.proxy_url and args.no_proxy:
        raise SystemExit("--proxy-url and --no-proxy are mutually exclusive")

    if sys.version_info < (3, 10):
        raise SetupError("Python 3.10 or newer is required.")
    proxy = resolve_proxy(args.proxy_url, args.no_proxy)
    codex = shutil.which("codex")
    codex_home = _codex_home()
    config_path = codex_home / "config.toml"
    skill_destination = codex_home / "skills" / "agy-supervisor"

    if args.what_if:
        print(f"Python: {sys.executable}")
        print(f"Codex: {codex or 'not found'}")
        print(f"Skill destination: {skill_destination}")
        print(f"Codex config: {config_path}")
        print(f"Proxy: {'configured' if proxy else 'disabled'}")
        print("No files, Codex configuration, MCP registrations, or agy processes will be changed.")
        return 0

    if not codex:
        raise SetupError("Required command 'codex' was not found. Install Codex CLI and retry.")

    result = _run_codex(codex, "mcp", "list")
    if result.returncode != 0:
        raise SetupError("Could not inspect Codex MCP configuration. Run 'codex mcp list' manually.")
    if "codex-agy-bridge" not in result.stdout:
        result = _run_codex(
            codex,
            "mcp",
            "add",
            "codex-agy-bridge",
            "--",
            sys.executable,
            "-m",
            "codex_agy_bridge",
        )
        if result.returncode != 0:
            raise SetupError("Codex MCP registration failed. Run 'codex mcp list' manually.")

    _copy_skill(skill_destination)
    update_codex_config(config_path, sys.executable, proxy)
    print(f"Installed agy-supervisor at {skill_destination}")
    print("Configured codex-agy-bridge with the current Python interpreter.")
    if shutil.which("agy"):
        print("agy was found. Run it interactively once to complete login.")
    else:
        print("Warning: agy was not found. Install Antigravity CLI and complete login before use.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except SetupError as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
