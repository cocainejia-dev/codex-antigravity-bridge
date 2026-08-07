"""Headless runner for the Google Antigravity CLI (`agy`).

`agy -p "<prompt>"` gates its stdout on `isatty()` in some builds (upstream
issue #76): when stdout is not attached to a real terminal it can emit
nothing and exit 0. This module tries the plain subprocess path first and,
when the result comes back empty, re-runs `agy` inside a freshly allocated
pseudo-terminal (ConPTY on Windows via `pywinpty`, `pty` on POSIX), then
strips ANSI / TUI chrome from the captured output.

Reference implementations:
- https://github.com/rhishi99/agy-headless-bridge (pty/ConPTY fix)
- https://github.com/sshahzaiib/agy-bridge (MCP bridge, TypeScript)
"""

from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

# --- ANSI / TUI noise stripping -------------------------------------------

_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_OTHER = re.compile(r"\x1b[@-Z\\-_]")
# Box-drawing / spinner glyphs agy uses for its TUI chrome.
_SPINNER = set(
    "⠁⠂⠄⡀⢀⠠⠐⠈⣾⣽⣻⢿⡿⣟⣯⣷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    "│─┌┐└┘├┤┬┴┼╭╮╰╯═║╔╗╚╝▌▐█▏▕"
)


def clean_agy_output(raw: str) -> str:
    """Strip ANSI escapes, carriage-return repaints and TUI chrome."""
    text = _ANSI_OSC.sub("", raw)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OTHER.sub("", text)
    text = text.replace("\r\n", "\n")
    out = []
    for line in text.split("\n"):
        visible = line.split("\r")[-1]
        had_chrome = any(c in _SPINNER for c in visible)
        stripped = "".join(c for c in visible if c not in _SPINNER).strip()
        if had_chrome and not stripped:
            continue  # pure decoration line
        out.append(stripped if had_chrome else visible.rstrip())
    return "\n".join(out).strip()


# --- agy discovery ---------------------------------------------------------


def find_agy() -> Optional[str]:
    """Locate the `agy` binary. Honors $AGY_PATH, then PATH, then OS defaults."""
    explicit = os.environ.get("AGY_PATH")
    if explicit and os.path.exists(explicit):
        return explicit

    found = shutil.which("agy") or shutil.which("agy.exe")
    if found:
        return found

    home = os.path.expanduser("~")
    if sys.platform == "win32":
        candidates = [
            os.path.join(home, "AppData", "Local", "agy", "bin", "agy.exe"),
            os.path.join(home, "AppData", "Roaming", "agy", "bin", "agy.exe"),
        ]
    else:
        candidates = [
            os.path.join(home, ".local", "bin", "agy"),
            "/opt/antigravity/bin/agy",
            "/usr/local/bin/agy",
        ]
    return next((c for c in candidates if os.path.exists(c)), None)


def _windows_short_path(path: str) -> str:
    """Return an ASCII 8.3 path when Windows provides one."""
    if sys.platform != "win32" or path.isascii():
        return path

    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
    if not length or length >= len(buffer):
        return path
    return buffer.value


# --- runners ---------------------------------------------------------------


@dataclass
class AgyResult:
    text: str
    exit_code: int
    used_pty: bool = False


def run_agy(
    prompt: str,
    workdir: Optional[str] = None,
    timeout: float = 300.0,
    output_format: Optional[str] = None,
    dangerously_skip_permissions: bool = False,
) -> AgyResult:
    """Run `agy -p <prompt>` headlessly and return cleaned text output.

    Falls back to a pseudo-terminal when the direct call returns empty
    (the isatty gate from upstream issue #76).
    """
    binary = find_agy()
    if binary is None:
        raise FileNotFoundError(
            "agy binary not found. Install it (Windows: "
            "`irm https://antigravity.google/cli/install.ps1 | iex`) or set AGY_PATH."
        )

    args = [binary, "-p", prompt]
    if output_format:
        args += ["--output-format", output_format]
    if dangerously_skip_permissions:
        args += ["--dangerously-skip-permissions"]

    launch_workdir = workdir
    pty_workdir = workdir
    if sys.platform == "win32" and workdir and not workdir.isascii():
        ascii_workdir = _windows_short_path(workdir)
        if ascii_workdir.isascii():
            launch_workdir = ascii_workdir
            pty_workdir = ascii_workdir
        else:
            # Fall back to an inherited cwd if Windows has no short path.
            args += ["--add-dir", workdir]
            launch_workdir = None
            pty_workdir = None

    direct = _run_subprocess(args, launch_workdir, timeout)
    direct_text = clean_agy_output(direct.stdout)
    if direct_text:
        return AgyResult(
            text=direct_text,
            exit_code=direct.returncode,
            used_pty=False,
        )

    pty_text, exit_code = _run_with_pty(args, pty_workdir, timeout)
    return AgyResult(text=clean_agy_output(pty_text), exit_code=exit_code, used_pty=True)


def _run_subprocess(args: list[str], workdir: Optional[str], timeout: float) -> subprocess.CompletedProcess:
    kwargs: dict = {"cwd": workdir, "capture_output": True, "text": True, "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash
    try:
        return subprocess.run(args, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"agy timed out after {timeout}s") from exc


def _run_with_pty(args: list[str], workdir: Optional[str], timeout: float) -> tuple[str, int]:
    """Run agy attached to a fresh pty so it believes stdout is a terminal."""
    if sys.platform == "win32":
        return _run_with_conpty(args, workdir, timeout)
    return _run_with_posix_pty(args, workdir, timeout)


def _run_with_conpty(args: list[str], workdir: Optional[str], timeout: float) -> tuple[str, int]:
    try:
        from pywinpty import PtyProcess  # type: ignore
    except ImportError:
        return "", -1

    proc = PtyProcess.spawn(args, cwd=workdir)
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"agy timed out after {timeout}s")
            try:
                chunk = proc.read(4096)
            except EOFError:
                break
            if not chunk:
                if proc.isalive():
                    time.sleep(0.1)
                    continue
                break
            chunks.append(chunk)
    finally:
        proc.terminate(force=True)
    return "".join(chunks), proc.exitstatus


def _run_with_posix_pty(args: list[str], workdir: Optional[str], timeout: float) -> tuple[str, int]:
    import pty

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            args,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=workdir,
            close_fds=True,
        )
    finally:
        os.close(slave)

    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise TimeoutError(f"agy timed out after {timeout}s")
            ready, _, _ = select.select([master], [], [], min(1.0, remaining))
            if ready:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data.decode("utf-8", errors="replace"))
            elif proc.poll() is not None:
                break
    finally:
        os.close(master)
        proc.wait()
    return "".join(chunks), proc.returncode
