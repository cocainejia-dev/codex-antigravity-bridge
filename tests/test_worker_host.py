from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from codex_agy_bridge import worker_host
from codex_agy_bridge.contracts import TaskContract
from codex_agy_bridge.run_control import DuplicateRunError, DurableRunManager
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client


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


def test_worker_host_claim_replaces_launcher_pid_atomically(tmp_path: Path) -> None:
    manager = DurableRunManager(tmp_path / "claim.sqlite3")
    record = manager.run_start(_contract(tmp_path), run_id="claim-run", launch_mode="external_durable", auto_spawn=False)
    identity = manager.store.get_worker_identity(record.run_id) or {}
    identity.update({"pid": 1001, "worker_host_pid": 1001, "reservation_pid": 1001, "launch_state": "SPAWNED"})
    manager.store.update_worker_identity(record.run_id, identity)
    assert manager.store.update_worker_pid(record.run_id, 1001).pid == 1001
    assert manager.store.claim_worker_host(record.run_id, 1001, 2002) is True
    claimed = manager.store.get_worker_identity(record.run_id)
    assert claimed is not None
    assert claimed["pid"] == 2002
    assert claimed["reservation_pid"] == 1001
    assert claimed["launch_state"] == "ACTIVE"
    assert manager.store.get_run(record.run_id).pid == 2002
    assert manager.store.claim_worker_host(record.run_id, 1001, 3003) is False


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


@pytest.mark.skipif(os.name != "nt", reason="covers the Windows service-created process boundary")
def test_windows_wmi_worker_survives_mcp_job_cleanup(tmp_path: Path) -> None:
    """A WMI-created worker remains alive after MCP stdio cleanup."""
    pid_file = tmp_path / "worker.pid"
    child = tmp_path / "wmi-child.py"
    parent = tmp_path / "wmi-parent.py"
    child.write_text(
        f"import os,pathlib,time; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)\n",
        encoding="utf-8",
    )
    parent.write_text(
        "import os,pathlib,sys,time\n"
        "from codex_agy_bridge.worker_host import _spawn_windows_worker\n"
        f"pathlib.Path({str(tmp_path / 'parent.pid')!r}).write_text(str(os.getpid()))\n"
        f"_spawn_windows_worker([sys.executable,{str(child)!r}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    async def exercise() -> int:
        source = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(source), env.get("PYTHONPATH")) if value
        )
        params = StdioServerParameters(command=sys.executable, args=[str(parent)], cwd=str(tmp_path), env=env)
        async with stdio_client(params):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not pid_file.exists():
                await asyncio.sleep(0.05)
            assert pid_file.exists()
            parent_pid = int((tmp_path / "parent.pid").read_text())
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(parent_pid), "/F"],
                check=True,
                capture_output=True,
            )
            return int(pid_file.read_text())

    worker_pid = asyncio.run(exercise())
    time.sleep(0.5)
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, worker_pid)
    try:
        assert handle
    finally:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        subprocess.run(["taskkill", "/PID", str(worker_pid), "/F"], check=False, capture_output=True)
