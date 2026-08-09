## Summary

<!-- What changed and why? -->

## Verification

- [ ] `python -m pytest -q`
- [ ] `python scripts/validate_skill.py`
- [ ] `python -m build mcp-antigravity-bridge`
- [ ] `git diff --check`

## Safety

- [ ] No OAuth tokens, proxy credentials, or machine-specific paths are included.
- [ ] Existing MCP defaults and manual merge boundaries are preserved.
