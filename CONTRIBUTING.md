# Contributing

Keep changes small, explicit, and covered by a focused test. Preserve the
existing MCP tool names and their default safety behavior.

Before opening a pull request, run:

```powershell
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q mcp-antigravity-bridge/src
python -m build mcp-antigravity-bridge
git diff --check
```

For runtime changes, include the affected tool, input contract, user-visible
status fields, and a test that does not require a live `agy` login. Do not
include OAuth tokens, proxy credentials, machine-specific paths, or generated
build directories.
