# Release Checklist

## 0.1.0 RC

- [ ] Confirm `pyproject.toml` is the only authoritative version source.
- [ ] Confirm `codex_agy_bridge.__version__` matches installed metadata.
- [ ] Run the repository verification entry point from a fresh environment.
- [ ] Run full repository and bridge pytest suites.
- [ ] Run changed-scope Ruff and compileall.
- [ ] Build both wheel and sdist artifacts.
- [ ] Install the wheel in a clean environment with source checkout excluded.
- [ ] Verify imports, metadata version, and artifact contents.
- [ ] Run source, artifact, CI, and private-data secret scans.
- [ ] Review scope and risk, then create one controller-owned commit.

Live account-authenticated AGY acceptance is a separate, explicitly authorized
step. It is not a public CI dependency and must not run during deterministic
CI or package verification.

Publication is out of scope for Phase 11.5: do not push, tag, create a GitHub
release, or upload to PyPI.
