# Architecture

```text
User -> Codex -> canonical controller -> canonical isolated MCP runtime
      -> durable async AGY job -> Antigravity worktree derived from canonical
      -> harvest -> deterministic verification -> safe commit
      -> independent Codex review -> WAITING_FOR_USER_DIRECTION
```

The canonical controller owns task contracts, worktree boundaries, durable
status, source-provenance binding, reconciliation, and acceptance. Antigravity
owns bounded implementation inside a worktree derived from the canonical
repository. Codex independently reviews the resulting diff and tests.

Important boundaries:

- Worker process completion is not implementation progress.
- Soft execution budget is not a stall timeout.
- Client/RPC disconnect is not worker terminal failure.
- Timeout recovery must reconcile process, heartbeat, worktree activity, and
  late diff before retrying.
- Git state and live runtime state are separate authority domains.
- Verification must attest `codex_agy_bridge.__file__`,
  `codex_agy_bridge.server.__file__`, `agy_jobs.__file__`, and
  `agy_runner.__file__` under the target canonical source root.
- The canonical MCP uses `codex-agy-vnext` and the repository-local `.venv`;
  the legacy editable installation is never an active source dependency.
