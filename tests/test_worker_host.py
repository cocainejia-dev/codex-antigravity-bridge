from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from codex_agy_bridge import worker_host
from codex_agy_bridge.contracts import TaskContract
from codex_agy_bridge.run_control import DuplicateRunError, DurableRunManager


def _contract(workdir: Path, task_id: str = "durable-host-test") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="durable host lifecycle",
        base_head="abcdef1234567890",
        workdir=str(workdir),
        allowed_paths=["src/worker.py"],
        forbidden_paths=["secrets.env"],
        acceptance_criteria=["worker completes"],
        verification_commands=[],
    )


def test_external_start_persists_worker_identity_and_allows_fresh_observer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 43210

    monkeypatch.setattr(worker_host, "spawn_worker_host", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr("codex_agy_bridge.run_control.is_pid_alive", lambda pid: pid == 43210)
    manager = DurableRunManager(tmp_path / "runs.sqlite3")
    record = manager.run_start(
        _contract(tmp_path),
        run_id="durable-host-run",
        launch_mode="external_durable",
        auto_spawn=True,
    )
    identity = manager.store.get_worker_identity(record.run_id)
    assert identity is not None
    assert identity["worker_type"] == "external_durable"
    assert identity["worker_host_pid"] == 43210
    assert identity["launch_state"] == "SPAWNED"
    assert identity["runtime_interpreter"] == os.sys.executable
    fresh = DurableRunManager(tmp_path / "runs.sqlite3")
    observed = fresh.run_observe(record.run_id)
    assert observed.is_alive is True
    assert observed.state.value == "CREATED"


def test_external_start_never_replays_an_active_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 43211

    starts = []
    monkeypatch.setattr(worker_host, "spawn_worker_host", lambda *args, **kwargs: (starts.append(args), FakeProcess())[1])
    manager = DurableRunManager(tmp_path / "runs.sqlite3")
    manager.run_start(_contract(tmp_path), run_id="same-run", launch_mode="external_durable", auto_spawn=True)
    with pytest.raises(DuplicateRunError):
        manager.run_start(_contract(tmp_path), run_id="replacement", launch_mode="external_durable", auto_spawn=True)
    assert len(starts) == 1


@pytest.mark.skipif(os.name != "nt", reason="covers the Windows detached-process boundary")
def test_windows_detached_worker_survives_parent_exit(tmp_path: Path) -> None:
    """Exercise the OS boundary independently of AGY or mocked PID probes."""
    marker = tmp_path / "worker-survived.txt"
    worker_code = f"import pathlib,time; time.sleep(0.4); pathlib.Path({str(marker)!r}).write_text('alive')"
    code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{worker_code!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
        "creationflags=0x8|0x200, close_fds=True);"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=5)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.read_text(encoding="utf-8") == "alive"
