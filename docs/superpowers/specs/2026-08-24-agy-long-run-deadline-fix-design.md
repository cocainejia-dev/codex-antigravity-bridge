# AGY Long-Run Deadline Fix

## Scope

Fix the production AGY invocation path so a normal headless coding task does
not inherit the CLI print-mode default of five minutes. Do not alter proxy
configuration, add retries, or make the runtime unbounded.

## Policy

`TaskContract.max_runtime` remains the public task wall-clock budget. Its
default becomes 1800 seconds; caller-supplied values remain exact and the
existing maximum ceiling and cancellation behavior remain intact.

The runner derives an internal AGY print-mode timeout from that wall-clock
budget. The normal target is 900 seconds, constrained below the outer budget
to reserve supervision margin. The final AGY argv always includes an explicit
`--print-timeout` value.

Stall observation remains independent from the wall-clock budget. It may
continue to use bounded liveness checks, but it must not make the former
`300 + 3 * 60` path an effective fixed task deadline.

## Errors And Recovery

The known AGY CLI messages `timeout waiting for response` and print-mode
timeout diagnostics classify as `AGY_PRINT_TIMEOUT`, not proxy/network
failure. This classification must not trigger proxy rediscovery or an
immediate fresh worker. Existing reconciliation and same-worktree continuation
paths preserve partial progress.

## Verification

Regression tests cover the default and explicit wall-clock budgets, derived
print timeout formatting and propagation, print-timeout classification, proxy
classification preservation, bounded supervision, cancellation, duplicate
worker prevention, and partial-progress continuation. Completion additionally
requires real bare AGY work past 300 seconds, real Bridge work past 600
seconds, local checks, GitHub push, and four green hosted CI jobs.
