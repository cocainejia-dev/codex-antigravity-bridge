"""In-process bridge to the Google Antigravity Python SDK."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from google.antigravity import Agent, LocalAgentConfig


class AgyError(RuntimeError):
    """Raised when the Antigravity SDK fails or returns no response."""


class AgyTimeoutError(AgyError):
    """Raised when an SDK call exceeds the configured timeout."""


@dataclass
class AgyResult:
    """Outcome of a single Antigravity SDK call."""

    text: str
    raw: dict[str, Any] | None = None
    exit_code: int = 0


async def _run_sdk(
    prompt: str,
    cwd: str | None,
    api_key: str | None,
    model: str | None,
) -> str:
    """Create a short-lived SDK agent and collect its response."""
    config_kwargs: dict[str, Any] = {}
    if cwd:
        config_kwargs["workspaces"] = [os.path.abspath(cwd)]
    if api_key:
        config_kwargs["api_key"] = api_key
    if model:
        config_kwargs["model"] = model

    config = LocalAgentConfig(**config_kwargs)
    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        text = await response.text()

    if not isinstance(text, str) or not text.strip():
        raise AgyError("Antigravity SDK returned an empty response")
    return text


def _run_async(coro_factory: Callable[[], Any]) -> Any:
    """Run a coroutine from sync code, including callers already in an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: list[Any] = []
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(asyncio.run(coro_factory()))
        except BaseException as exc:  # Re-raise in the calling thread.
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


@dataclass
class AgyRunner:
    """Invoke Antigravity in-process through the official Python SDK."""

    timeout_seconds: float = 300.0
    api_key: str | None = None
    model: str | None = None

    def run_prompt(self, prompt: str, cwd: str | None = None) -> AgyResult:
        if not prompt.strip():
            raise AgyError("prompt must not be empty")

        async def call() -> str:
            return await asyncio.wait_for(
                _run_sdk(prompt, cwd, self.api_key, self.model),
                timeout=self.timeout_seconds,
            )

        try:
            text = _run_async(call)
        except asyncio.TimeoutError as exc:
            raise AgyTimeoutError(
                f"Antigravity SDK timed out after {self.timeout_seconds}s"
            ) from exc
        except AgyError:
            raise
        except Exception as exc:
            raise AgyError(f"Antigravity SDK failed: {exc}") from exc

        return AgyResult(text=text)
