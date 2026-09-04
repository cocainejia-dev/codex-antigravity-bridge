# New-Provider Codex Git Takeover Contract

Copy this entire document into a new Codex conversation after switching
provider, relay, account, or model. It is a cold-start control contract, not a
history summary. The Git repository and fresh runtime discovery are authoritative;
the old provider, old chat, and this document are not proof of current state.

## 1. Scope and hard stop

This takeover is handoff packaging only. Do **not** begin Phase 11.5, create a
new AGY worker, implement a release feature, publish to GitHub, push, tag, or
change provider credentials, global Python, MCP configuration, production
Python, or tests. Do not reopen R1, W1, R2, R3, Phase 11.3, or Phase 11.4
without fresh regression evidence. Finish the takeover report and stop at
`WAITING_FOR_USER_DIRECTION`.

## 2. Canonical authority and workspace separation

The sole authoritative repository on the current machine is:

`D:\CODEX项目\agy-bridge`

That is a machine-local path hint, not a portable release requirement. A Codex
Project/workspace may be elsewhere. First locate and verify the real Git root;
never create a second source tree, run `git init`, copy project history, or use
the current chat workspace as a substitute for the canonical repository.

The old repositories are reference-only and must not receive writes, commits,
dispatches, verification, or release operations:

- `D:\软件开发\codex-antigravity-vnext` = `PRE_MIGRATION_REFERENCE_ONLY`
- `D:\软件开发\codex-antigravity-bridge` = `LEGACY_REFERENCE_ONLY`

## 3. Mandatory read-only cold start

Before changing any file, run from the discovered repository root:

```powershell
git rev-parse --show-toplevel
git rev-parse HEAD
git branch --show-current
git status --short
git log --oneline -10
git diff --stat
git diff
git worktree list
powershell -ExecutionPolicy Bypass -File .\scripts\handoff-status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\runtime-provenance.ps1
```

Read `AGENTS.md`, `docs/WORK_MODE.md`, `docs/CURRENT_STATE.md`,
`docs/RECOVERY.md`, `docs/ARCHITECTURE.md`, `docs/RELEASE_PLAN.md`,
`docs/LEGACY.md`, `CONTRIBUTING.md`, `SECURITY.md`,
`.recovery/current-round.json`, and `.recovery/repository-identity.json`.
If the identity, root, status, or provenance checks conflict, fail closed and
do not create a continuation plan.

## 4. Runtime reconciliation before any future dispatch

Use the repository's read-only durable-job discovery. Inspect unfinished
collaborations, process/PID state, heartbeat, worktree activity, partial diffs,
and unharvested completion. Classify each relevant record as `LIVE`,
`TERMINAL`, or `AMBIGUOUS`:

- `LIVE`: do not replace or duplicate it.
- `TERMINAL`: harvest/classify evidence before any later action.
- `AMBIGUOUS`: fail closed; preserve evidence and request direction.

An RPC or wait timeout is not worker death. A soft budget is not a stall
timeout. An `AGY_PROXY_ERROR`, quota, authentication, network, or backend
failure is an external provider/infrastructure condition, not proof of a Bridge
regression. Always reconcile before replacement. Keep `DUPLICATE_WORKER=0`.

## 5. Provenance and provider independence

Verify the active MCP identity and repository-local runtime interpreter. The
canonical runtime is `codex-agy-vnext`; the repository `.venv` is the accepted
Python source. Attest that these modules resolve below the current canonical
source root:

- `codex_agy_bridge`
- `codex_agy_bridge.server`
- `codex_agy_bridge.agy_jobs`
- `codex_agy_bridge.agy_runner`

An MCP response alone is not provenance evidence. Do not assume any old
provider, relay URL, token, API key, OAuth session, account, model, quota, or
credential exists. Do not write any of those values to Git. The contract is:

```text
OLD_PROVIDER_REQUIRED = NO
OLD_PROVIDER_CREDENTIAL_REQUIRED = NO
OLD_CHAT_REQUIRED = NO
```

## 6. Existing state and next direction

Read live repository state rather than trusting this prompt. Handoff packaging
started from checkpoint `939bfb8`; the live HEAD may now include this document,
so discover it yourself. Expect `main` with a clean worktree and:

```text
R1 = PASS
W1 = PASS
R2 = PASS
R3 = PASS
PHASE_11_3_CLEANROOM_E2E = PASS
PHASE_11_4_TECHNICAL_HARDENING = PASS
PHASE_11_4_RELEASE_HARDENING = PASS
READY_FOR_PHASE11_5_RC = YES
CURRENT_TASK = WAITING_FOR_USER_DIRECTION
```

If live state agrees, the next recommended direction is
`PHASE11.5_CI_PACKAGING_RELEASE_CANDIDATE`. Takeover completion does not
authorize that work. Report first and wait for the user.

## 7. Authority model and no-chat memory

Keep the architecture:

```text
User -> Codex architecture/diagnosis/review
     -> canonical controller -> durable AGY runtime -> Antigravity worktree
     -> harvest -> deterministic verification -> safe commit
     -> independent review -> WAITING_FOR_USER_DIRECTION
```

Codex owns diagnosis, TaskContract, scope, review, verification, risk, and
acceptance. AGY may implement bounded tasks only after explicit later
authorization. The controller owns durable-state reconciliation, harvest, and
commits. The user controls the next direction. Default:
`CODEX_DIRECT_IMPLEMENTATION = NO`.

Long-term state must come from Git, recovery metadata, and read-only runtime
discovery. Do not ask the user to paste old chats, migration prompts, or phase
reports. `OLD_CHAT_REQUIRED = NO`.

## 8. Required first takeover report

Before any modification, print this report and stop:

```text
# NEW_PROVIDER_CODEX_GIT_TAKEOVER

RESULT =
CODEX_PROVIDER = UNKNOWN / CURRENT_ENVIRONMENT
CANONICAL_REPO =
GIT_ROOT =
HEAD =
BRANCH =
WORKTREE_CLEAN =
THIS_IS_CANONICAL_REPO =
AGENTS_READ =
CURRENT_STATE_READ =
RECOVERY_READ =
REPOSITORY_IDENTITY_READ =
HANDOFF_STATUS =
RUNTIME_PROVENANCE_STATUS =
ACTIVE_AGY_JOB =
ACTIVE_WORKER_CLASSIFICATION =
DUPLICATE_WORKER =
ACTIVE_MCP_IDENTITY =
ACTIVE_RUNTIME_PYTHON =
ACTIVE_BRIDGE_SOURCE =
ACTIVE_SERVER_SOURCE =
ACTIVE_AGY_JOBS_SOURCE =
ACTIVE_AGY_RUNNER_SOURCE =
ACTIVE_RUNTIME_SOURCE_MATCHES_CANONICAL_REPO =
STATE_CONFLICT =
CURRENT_PHASE =
CURRENT_TASK =
NEXT_SAFE_ACTION =
OLD_PROVIDER_REQUIRED = NO
OLD_CHAT_REQUIRED = NO
NEW_CODEX_CAN_CONTINUE_FROM_REPOSITORY = YES / NO
```

The safe final action for this handoff is `WAITING_FOR_USER_DIRECTION`.
