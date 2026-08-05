"""Bridge to the Google Antigravity CLI (agy -p, headless mode)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


class AgyError(RuntimeError):
    """Raised when the Antigravity CLI fails or produces unusable output."""


class AgyTimeoutError(AgyError):
    """Raised when the CLI call exceeds the configured timeout."""


@dataclass
class AgyResult:
    """Parsed outcome of a single Antigravity CLI call."""

    text: str
    raw: dict[str, Any] | None = None
    exit_code: int = 0


def find_agy(binary: str | None) -> str:
    """Locate the agy CLI, preferring explicit input over env var / PATH."""
    if binary:
        return binary
    env_bin = os.environ.get("AGY_BIN")
    if env_bin:
        return env_bin
    found = shutil.which("agy")
    if found:
        return found
    # Windows default install location (git-bash / PowerShell installers).
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = os.path.join(local, "agy", "bin", "agy.exe")
        if os.path.isfile(candidate):
            return candidate
    raise AgyError(
        "agy CLI not found. Install it (see README) or set AGY_BIN to its path."
    )


def _parse_result(stdout: str) -> str:
    """Extract CLI response text from stdout, raising AgyError on failures."""
    raw: dict[str, Any] | None = None
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        raw = None

    if raw is None:
        return stdout

    status = raw.get("status")
    error = raw.get("error") or ""
    if isinstance(status, str) and status.upper() != "OK":
        detail = error or f"agy status: {status}"
        if "auth" in detail.lower():
            raise AgyError(
                "agy authentication required: run `agy` once interactively to log in. "
                f"(detail: {detail})"
            )
        raise AgyError(detail)

    response = raw.get("response")
    return response if isinstance(response, str) else ""


@dataclass
class AgyRunner:
    """Invokes the Antigravity CLI headlessly (agy -p --output-format json)."""

    timeout_seconds: float = 300.0
    agy_binary: str | None = None

    def run_prompt(self, prompt: str, cwd: str | None = None) -> AgyResult:
        binary = find_agy(self.agy_binary)
        cmd = [binary, "-p", prompt, "--output-format", "json"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgyTimeoutError(
                f"agy timed out after {self.timeout_seconds}s (prompt: {prompt[:80]!r})"
            ) from exc
        except OSError as exc:
            raise AgyError(f"agy failed to start ({binary}): {exc}") from exc

        stdout = (proc.stdout or "").strip()

        try:
            text = _parse_result(stdout)
        except AgyError:
            raise

        if not text.strip():
            detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
            raise AgyError(f"agy returned empty response ({detail[:200]})")

        raw: dict[str, Any] | None = None
        try:
            raw = json.loads(stdout) if stdout else None
        except json.JSONDecodeError:
            raw = None

        return AgyResult(text=text, raw=raw, exit_code=proc.returncode)
