# Contributing

Keep changes small, explicit, and covered by a focused test. Preserve existing
MCP tool names, error contracts, and default safety behaviors.

## Development Setup

Install the package in editable mode with development dependencies in your project virtual environment:

```powershell
python -m pip install -e ".\mcp-antigravity-bridge[dev,winpty]"
```

Manual `PYTHONPATH` configuration and global tooling installations are not required;
the dev extra and project virtual environment provide all verification dependencies
and resolve package provenance directly.

## Verification

Before opening a pull request, run the authoritative verification script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

You can also specify an explicit project interpreter if needed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -Python C:\path\to\python.exe
```

Or run the individual verification steps directly:

```powershell
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q mcp-antigravity-bridge/src
git diff --check
```

Runtime changes must include the affected tool, input contract, user-visible
status fields, and a test that does not require a live `agy` login. Do not
include OAuth tokens, proxy credentials, machine-specific paths, or generated
build directories.

## Architecture and Safety Rules

- Review [docs/RELEASE_HARDENING.md](docs/RELEASE_HARDENING.md) before public-release changes.
- See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/RECOVERY.md](docs/RECOVERY.md) for controller authority and recovery.
- Keep source provenance fail-closed; do not import stale global or legacy installations.
- Never commit runtime databases, `*.pid`, logs, ephemeral worktrees, or coverage caches.
- Review [SECURITY.md](SECURITY.md) and never include OAuth tokens, proxy credentials, private configuration, or machine-specific paths.
