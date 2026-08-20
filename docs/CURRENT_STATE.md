# Current State

## Git-authoritative

- `CURRENT_PHASE`: Phase 11.2R Release Stabilization complete through R2.
- `CURRENT_TASK`: release-readiness handoff consolidation; no active feature task.
- `CURRENT_HEAD`: `55dc397` (repository handoff consolidation).
- `LAST_VERIFIED_COMMITS`: `c5db248` (R1), `fe55396` (W1), `3cded09` (R2).
- R1 effective progress: PASS.
- W1 timeout/liveness semantics: PASS.
- R2 source provenance: PASS.
- `READY_FOR_R3`: YES; do not enter automatically.

## Release blockers and deferred work

- Ruff remains `DEFERRED_EXISTING_ENVIRONMENT_BLOCKER`.
- Next recommended task: `R3_REPRODUCIBLE_VERIFICATION_TOOLING`.
- Later roadmap: Phase 11.3 clean-room E2E, 11.4 release hardening, 11.5 CI /
  packaging, 11.6 publication.

## Verification

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

The current verified baseline is `281 passed` with the repository source bound
by the verification controller. A warning from `pydantic_settings` is known.

## Machine-local facts

- AGY executable: `C:\Users\28760\AppData\Local\agy\bin\agy.EXE`.
- AGY/MCP registration and Codex config live outside Git and must be reconciled.
- A global editable `.pth` may point at the legacy bridge clone; verification
  must override it through controller-owned `PYTHONPATH` and attest resolved
  module paths.
- Durable job databases and active processes live under
  `C:\Users\28760\AppData\Local\codex-agy-vnext`; they are evidence/runtime,
  not portable project state.

Use `scripts/handoff-status.ps1` to rediscover current machine-local state.
