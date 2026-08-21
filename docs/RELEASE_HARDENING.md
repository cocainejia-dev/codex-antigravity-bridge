# Release Hardening Checklist

This checklist records the public-release boundary for version `0.1.0`.

- License: [Apache-2.0](../LICENSE)
- Python: `>=3.10`
- Current phase: Phase 11.4, release hardening
- Next phase: Phase 11.5, CI, packaging, and release candidate
- Supported operation: headless stdio MCP on platforms supported by the
  installed Python and `agy` CLI; visible `terminal` mode is Windows-only.

## Runtime state and recovery

Runtime SQLite databases, logs, PIDs, heartbeats, coverage caches, and temporary
worktrees are machine-local transient state. They are excluded by `.gitignore`
and must never be committed. Discover existing state read-only with
`scripts/handoff-status.ps1` and reconcile before retrying an interrupted task.
Legacy repositories are historical references, not normal-use dependencies.

Recovery treats unknown or incompatible state as a fail-closed condition. It
does not silently migrate, copy, delete, or resume an ambiguous job.

## Provenance and configuration

The repository identity marker provides a portable project identity; its
machine-local path is only a diagnostic hint. `scripts/runtime-provenance.ps1`
still requires the repository-local interpreter and verifies that bridge modules
resolve beneath the current source tree. A mismatch fails before dispatch.

Use the installer or a project virtual environment to resolve Python. Public
examples use generic paths and do not require a developer's drive, username,
legacy clone, global editable install, or manual `PYTHONPATH`.

## External-provider boundary

Missing `agy`, expired login, provider errors, rate limits, network/proxy
failures, and missing Windows ConPTY support are external environment/provider
conditions. The bridge reports actionable errors and does not fabricate success,
silently fall back to legacy code, or change production code to mask them.

## Verification and release boundary

```powershell
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q mcp-antigravity-bridge/src
powershell -ExecutionPolicy Bypass -File .\scripts\handoff-status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\runtime-provenance.ps1
git diff --check
```

Phase 11.4 documents and hardens portability, configuration, diagnostics,
security, and release metadata. CI, build artifacts, package-index publication,
and release-candidate tagging are explicitly deferred to Phase 11.5.
