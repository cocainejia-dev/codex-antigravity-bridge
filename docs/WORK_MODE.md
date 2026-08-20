# Repository Work Mode

This is the repository-safe summary of the project's programming work rules.
The full source document is external to this repository and is machine-local.

- Understand the task before editing; for medium or large work use Spec ->
  Plan -> Implement -> Verify -> Review -> Handoff.
- Read existing code, tests, architecture, and Git state before writing.
- Diagnose bugs from evidence before applying a fix.
- Prefer the smallest reversible change and existing project patterns.
- Never claim completion without actual command output, tests, and diff review.
- Preserve user changes; never reset, clean, revert, or force-write unknown state.
- Treat secrets, global configuration, production operations, and destructive
  actions as high-risk and out of scope without explicit authorization.
- Codex plans and reviews. Antigravity performs bounded implementation when
  delegated. A worker report is not independent verification.
- Reconcile active workers after timeout before any retry; never duplicate work.
