# AGY Supervisor Last-Stable Recovery

## Scope and Authority

- Recovery base: `8eabc389ccc383ada740b66901abbc1acf7b37f6`.
- Base parent: `5c35e4d6148ecc081adeb2d7b86a7a45d93695e2` (`origin/main`).
- Recovery branch: `recovery/agy-supervisor-last-stable-20260904`.
- Recovery commits: `a6419f05724625d2a68dbc681bd6cea335fa02e3` plus the final documentation closure commit recorded below.
- Implementation executor: Codex. AGY was used only for isolated runtime verification.

## Forensic Discovery

| Path | Type | Evidence |
| --- | --- | --- |
| `D:\CODEX项目\agy-bridge` | canonical Git repo | `main` at `5c35e4d`; 8 user dirty path-document files; remote `origin` is the authoritative GitHub repository |
| `D:\CODEX项目\agy-bridge-recovery-20260904` | isolated recovery worktree | `8eabc389` base, then `a6419f0`; clean after commit |
| `D:\CodexData\.codex\skills\agy-supervisor` | installed skill | initially stale; restored from recovery resource and now parity-verified |
| `D:\CODEX项目\agy-bridge\mcp-antigravity-bridge\src\codex_agy_bridge\resources\agy-supervisor` | packaged resource | stale in `main`, continuity-complete in recovery |
| `D:\软件开发\codex-antigravity-vnext` | legacy reference | absent on this machine |
| `D:\软件开发\codex-antigravity-bridge` | legacy reference | absent on this machine |

No additional related repositories, worktrees, stashes, local post-`8eabc` commits,
reflog commits, or unreachable objects were found. No active durable worker was
present before acceptance. The only pre-existing dirty changes were canonical
path corrections in `AGENTS.md`, `.recovery/*`, and takeover/recovery docs;
they were carried over unchanged as commit `a6419f0`.

## Recovery Diff Classification

| Source | Classification | Recovered content |
| --- | --- | --- |
| `8eabc389` | `PROVEN_STABLE` | continuity contract, bounded-wait semantics, hard-timeout distinction, no replacement worker, pressure tests, setup/parity tests |
| canonical dirty diff | `LIKELY_STABLE` and machine-required | eight path/identity documentation updates pointing to `D:\CODEX项目\agy-bridge` |
| resource/installed copies | forensic evidence only | no unique required changes; installed copy was stale and was not used as source |
| reflog/dangling/legacy repos | `UNVERIFIED` / absent | no candidate content recovered |

No Supervisor implementation repair was necessary. No architecture, durable
protocol, database, or compatibility refactor was made.

## Semantic Recovery Matrix

| Feature | `5c35e4d` main | `8eabc389` / recovery | Verification |
| --- | --- | --- | --- |
| `ACTIVE_IS_FINAL` | missing | `NO` | 23 targeted skill/setup tests pass |
| running/queued stop condition | incomplete | `RUNNING_IS_STOP_CONDITION=NO`, `QUEUED_IS_STOP_CONDITION=NO` | targeted tests and live waits |
| bounded wait expiry | ambiguous | `WAIT_WINDOW_EXPIRED_IS_FINAL=NO`; distinct from task timeout/failure | 4 running bounded waits |
| healthy heartbeat continuity | incomplete | continue supervision/reconcile | live jobs `5b2c...`, `94dbc...` |
| duplicate/replacement worker | not encoded in skill | `REPLACEMENT_WORKER=NO` | live report: no additional workers; duplicate count 0 |
| run APIs and `TaskContract` | present | preserved unchanged | 491 full tests pass |
| timeout reconciliation | partial guidance | hard timeout and `AGY_PRINT_TIMEOUT` reconciliation guidance | hard-timeout job `9221...` |
| repo/resource/installed parity | resource stale | recovery copies byte-identical | SHA-256 parity below |

## Verification Evidence

- Targeted: `pytest -q tests/test_agy_supervisor_skill.py mcp-antigravity-bridge/tests/test_setup.py` -> `23 passed`.
- Full: `pytest -q` -> `491 passed in 150.35s`.
- Ruff: `All checks passed!`.
- Compileall: `COMPILEALL=PASS`.
- Diff check: `DIFF_CHECK=PASS`.
- Recovery source provenance: `SOURCE_PROVENANCE=PASS`.
- Isolated setup copy: `ISOLATED_INSTALL=PASS`.
- Production installed skill restoration: `PRODUCTION_SKILL_RESTORE=PASS`.
- All four recovery/resource/installed skill files checked byte-identical.

SHA-256 values:

- `SKILL.md`: `0509F007D32F70340F466C0BF6580858C9ABD679FE68628A4778AF3F90E26B23`.
- `references/agy-supervisor-protocol.md`: `0475D8E92CDFB03A65BC650A8334EF47BC3BC40DBE8562D9F7A11D9118AA7F38`.

## Live Runtime Acceptance

AGY ran only in `D:\CODEX项目\agy-runtime-acceptance-20260904`, an empty isolated
Git repository. The first call was correctly classified `permission_blocked`
(headless command permission denied, no output); its worktree was reconciled
clean and no retry was issued for that worker. One explicitly authorized,
no-write corrective call then passed:

- Job `94dbcaaa7f514bd59dbe5499af4d761a`: `completed`, 55 seconds.
- Bounded waits: at least four `running` + `HEALTHY` windows before terminal.
- Final report: three sequential waits totaling over 27 seconds; initial and
  final worktree clean; no files changed; no extra workers.
- Hard-timeout job `9221b57e35fe49dc86473fa6809a3b8c`: terminal `failed` with
  `error_kind=AGY_PRINT_TIMEOUT`, worker not alive, and `RECONCILE_FIRST` guidance.
  This is distinct from bounded wait expiry and was not retried.

## GitHub Preservation

`recovery/agy-supervisor-last-stable-20260904` was pushed successfully and
verified with `git ls-remote` at `02e41f5d1e9d747e6812fee62522ccc07e322220`
before this documentation-only closure update. The PR was created as #1 with
head SHA `02e41f5d1e9d747e6812fee62522ccc07e322220`, base `main`, and
auto-merge disabled. The exact-head Hosted CI run was `33879466885` and
completed successfully for Ubuntu 3.10/3.12 and Windows 3.10/3.12.

## Fresh Host and Stable Marker

- `INSTALLED_SKILL_PATH`: `D:\CodexData\.codex\skills\agy-supervisor\SKILL.md`.
- `INSTALLED_SKILL_SHA256`: `0509F007D32F70340F466C0BF6580858C9ABD679FE68628A4778AF3F90E26B23`.
- Fresh `codex exec` loaded the same absolute path and SHA-256; literal continuity markers were both present.
- `FRESH_HOST_PROVENANCE`: `PASS`.
- `PRODUCTION_SUPERVISOR_SANITY`: `PASS` by hash-bound reuse of job `94dbcaaa7f514bd59dbe5499af4d761a`; no new implementation or Bridge restart was needed.
- `STABLE_SUPERVISOR_SHA`: final recovery documentation closure commit (the exact full SHA is the tag target after this commit).
- `STABLE_SUPERVISOR_TAG`: `agy-supervisor-stable-20260904`.

The stable tag must target the final documentation closure commit, not the
pre-closure `main`, `8eabc389`, or the earlier `02e41f5` head. The tag is an
annotated marker only; no GitHub Release is created.

`main` and `codex/agy-supervisor-continuity-20260827` were not modified.

## Subsequent Acceptance-Hardening Chain

The immutable rollback candidate remains `agy-supervisor-stable-20260904`
(`436291b6f8b0511f997a5ababe54f0e0cddc23a5`). The acceptance-hardening work is
developed from GitHub `main` at `cb0878df34a50543bcbb7f282b2777b385b868b8` on
`codex/agy-supervisor-acceptance-hardening-20260905`; after merge and exact-main
CI verification, the new annotated marker is
`agy-supervisor-stable-20260905`, targeting that verified merge commit. Normal
source remains `main`; rollback candidates remain immutable stable tags.
