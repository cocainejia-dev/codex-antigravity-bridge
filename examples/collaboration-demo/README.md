# Collaboration Demo Contract

This directory documents the smallest useful three-part example project:

```text
demo-app/
|- backend/
|- frontend/
`- tests/
```

The backend task adds `GET /api/items`, the frontend task adds an items page,
and a test task verifies the shared API contract. The tasks are deliberately
owned by disjoint paths so the bridge can create independent worktrees.

See [shared-contract.md](shared-contract.md) for the fields and verification
commands that all tasks must use. See [docs/demo.md](../../docs/demo.md) for
the dry-run and real execution steps.
