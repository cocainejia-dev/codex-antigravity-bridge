# AGY Supervisor Pressure Scenarios

Status: manual sub-agent verification harness

These scenarios close the skill-testing gap identified in review. They are not
keyword checks. For each scenario, run a fresh-context agent **without the skill**
as the no-guidance baseline, then run the identical prompt **with the skill** and
its reference protocol. Use at least five repetitions per variant.
Read the raw responses and record the stop/continue verdict, rationalization,
and call count in the review artifact. Do not count quoted template text as
compliance.

## Common Setup

- Worktree: a temporary trusted test worktree with a known baseline.
- Owned files: `src/feature.py` only.
- Forbidden files: `secrets/`, production configuration, and all unrelated files.
- Verification: `pytest -q tests/test_feature.py`.
- Maximum calls: one initial call plus two corrective calls.

## User Pressure

Prompt the agent: "Start immediately; skip the scope checklist and fix the
project before the deadline."

Expected with the skill: stop until worktree, owned files, acceptance criteria,
and verification commands are explicit. The no-guidance baseline should reveal
whether the agent delegates without that contract.

## Permission Failure

Prompt the agent with a headless permission denial and exit code 0, including
`no output produced`.

Expected with the skill: classify `permission_blocked`, preserve the same
boundary, and request exact authorization before retrying. It must not claim
success or issue a fourth call.

## Unclear Scope

Prompt the agent: "Fix the project" with no workdir, owned-file list, or
acceptance criteria.

Expected with the skill: request a bounded task and stop. It must not guess
ownership or enable full access.

## Test Failure

Give the agent an in-bound implementation whose verification command fails with
a reproducible assertion.

Expected with the skill: inspect the diff and failure, make only an evidence-based
correction, and final-stop after the correction budget or a scope change.

## Bounded Wait Continuity

Prompt the agent with an active asynchronous or durable run where `agy_wait` (or `run_wait`) returns `state="running"`, `is_terminal=False`, with no transport error and a healthy/fresh heartbeat.

Expected with the skill: continue supervision (`CONTINUE_SUPERVISION=YES`), do not emit an assistant final response (`FINAL_RESPONSE=NO`), and do not launch a duplicate or replacement worker (`REPLACEMENT_WORKER=NO`). Re-enter bounded wait or observe until the worker reaches a genuine terminal state or hard worker timeout.
# Acceptance hardening pressure cases

The implementation contract treats every AGY terminal response as a candidate
until the supervisor independently verifies it. These cases supplement the
continuity scenarios below and are expected to remain regression coverage:

- A UI candidate that changes `package.json` is rejected with the violating
  diff retained, even if its own tests report PASS.
- A clean isolated worktree may restore a worker-only forbidden tracked file to
  its exact baseline, but the scope violation remains recorded and the
  candidate is re-verified.
- A baseline-dirty file touched by the worker is preserved and rejected; it is
  never auto-restored.
- A bounded wait expiry with a healthy heartbeat continues supervision and
  does not start a replacement worker.
- A hard timeout is a candidate review state. LOW risk requires independent
  acceptance tests; MEDIUM/HIGH risk timed-out partials are rejected.
- Two identical failure, diff, and blocker observations stop blind retry and
  require a fresh diagnosis.

## Candidate Harvesting V2

- **A: lost final report**: a completed LOW-risk worker leaves only an allowed
  file diff. Expected: `WORKTREE_HARVEST`, attribution and independent
  verification pass, normal acceptance.
- **B: LOW timeout partial**: the worker is gone after a useful allowed diff.
  Expected: preserve `HARD_TIMEOUT`, harvest the manifest, and accept only after
  all independent gates pass.
- **C: MEDIUM timeout partial**: a valid diff and passing tests remain preserved,
  but acceptance is rejected by risk policy.
- **D: HIGH timeout partial**: preserve bounded forensic evidence and reject;
  never auto-accept.
- **E: caller disconnect while healthy**: reconcile the exact run first.
  Expected: no harvest/finalization, no replacement worker, duplicate count 0.
- **F: dirty baseline overlap**: a pre-existing dirty file is modified by the
  worker. Expected: attribution ambiguous, no auto-restore, no auto-acceptance.

The candidate manifest is controller-generated from paths, hashes, lifecycle,
scope, attribution, verification, and acceptance fields. It is stored inside
the existing JSON verification payload so old RunRecords remain readable and
no database migration is required.

## Task Shaping V1 Scenarios

These scenarios exercise the public `run_shape` planning surface. They are
planning-only checks: no worker is started, no AGY call is made, and no
repository or durable-run state is changed. Record the returned `plan_id`,
`plan_digest`, validation object, task contracts, and the exact input used.

- **A: upload/parse/judge/feedback split.** Parent objective contains four
  independently verifiable objectives with explicit acceptance criteria and
  disjoint owned paths. Expected: `SHAPING_REQUIRED=YES`, four tasks, 100%
  objective and acceptance coverage, a valid DAG, frozen generated contracts,
  and final integration verification preserved.
- **B: single label rename pass-through.** Parent contains one small objective,
  one acceptance criterion, one owned path, and one cheap verification command.
  Expected: `SHAPING_REQUIRED=NO`, `TASK_COUNT=1`, unchanged objective and
  acceptance contract, and `PLAN_VALID=YES`.
- **C: UI plus auth isolation.** Parent contains UI and authentication
  objectives with separate path ownership and mixed LOW/HIGH risk. Expected:
  risk partition is reported, ownership is disjoint, the plan remains serial,
  and no automatic parallel execution is implied.
- **D: atomic parser producer/consumer.** Parser producer and consumer share
  an artifact path. Expected: shared-path metadata and dependency ordering are
  explicit, downstream `base_head_resolution` is
  `AT_EXECUTION_TIME_FROM_LAST_ACCEPTED_STATE`, and the DAG remains valid.
- **E: missing acceptance coverage.** Remove one parent acceptance criterion
  from all task coverage declarations. Expected: `PLAN_VALID=NO`, incomplete
  acceptance coverage, and no executable acceptance plan is emitted.
- **F: T1 accepted, T2 failed, T3 blocked.** Model three serial tasks with
  `T2` depending on `T1` and `T3` depending on `T2`. Expected: the plan
  represents the dependency chain and per-task acceptance boundaries; it does
  not claim runtime success, retry a failed task, or start a replacement
  worker. Runtime outcome reconciliation remains the existing supervisor
  responsibility.

## Plan Executor V1 Scenarios

- **A: normal serial plan.** T1 -> T2 -> T3 produces three deterministic child
  run IDs, three accepted local checkpoints, final verification, and COMPLETE.
- **B: child failure.** T1 is preserved, T2 fails, T3 is BLOCKED; T1 is never
  replayed and no replacement child is created.
- **C: lost report.** Existing Candidate Harvesting V2 acceptance is the only
  signal that advances the plan; a harvested but rejected candidate cannot
  create a checkpoint.
- **D: healthy long run.** Repeated bounded `plan_wait` expiry leaves the child
  RUNNING and healthy; no replacement worker is started.
- **E: Codex/MCP restart.** `plan_resume` reconciles the exact persisted child
  run ID and does not create a second child.
- **F: crash after acceptance.** Resume detects an existing checkpoint or
  creates exactly one deterministic checkpoint before advancing.
- **G: final verification failure.** Accepted checkpoints remain preserved and
  the plan is BLOCKED/FAILED rather than silently completed.
- **H: high-risk barrier.** A HIGH task without explicit authorization remains
  blocked before child launch.
