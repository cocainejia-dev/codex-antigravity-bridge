# Contributing

Keep changes small, explicit, and covered by a focused test. Preserve the
existing MCP tool names and their default safety behavior.

## Development Setup

Install the declared dev tooling in your virtual environment:

```powershell
python -m pip install -e "mcp-antigravity-bridge[dev]"
```

Manual `PYTHONPATH` configuration and global tooling installations are not required; the dev extra and project virtual environment provide all verification dependencies and resolve package provenance directly.

## Verification

Before opening a pull request, run the authoritative verification script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

You can also specify an explicit project interpreter if needed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -Python path\to\python.exe
```

For runtime changes, include the affected tool, input contract, user-visible
status fields, and a test that does not require a live `agy` login. Do not
include OAuth tokens, proxy credentials, machine-specific paths, or generated
build directories.
