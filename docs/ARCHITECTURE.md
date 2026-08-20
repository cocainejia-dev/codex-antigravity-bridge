# Architecture

```text
User -> Codex -> VNext controller -> durable async AGY job -> Antigravity
      -> isolated worktree diff -> harvest -> deterministic verification
      -> safe commit -> final Codex review -> WAITING_FOR_USER_DIRECTION
```

The controller owns task contracts, worktree boundaries, durable status,
source-provenance binding, reconciliation, and acceptance. Antigravity owns
bounded implementation inside its assigned worktree. Codex independently
reviews the resulting diff and tests.

Important boundaries:

- Worker process completion is not implementation progress.
- Soft execution budget is not a stall timeout.
- Client/RPC disconnect is not worker terminal failure.
- Timeout recovery must reconcile process, heartbeat, worktree activity, and
  late diff before retrying.
- Git state and live runtime state are separate authority domains.
- Verification must attest `codex_agy_bridge.__file__` and
  `codex_agy_bridge.server.__file__` under the target source root.
