# AGY Development Plan: {{title}}

Status: READY_FOR_AGY
Created: {{date}}
Project: {{project_root}}
Task risk class: {{read_only_or_code_changes}}
Authorization: {{who_authorized_the_exact_worktree_and_task}}

## Baseline Audit

Record the current state before delegation. Preserve existing user changes.

```text
git status --short
git branch --show-current
git worktree list
```

Baseline status: {{baseline_status}}
AGY worktree: {{agy_worktree}}
AGY branch: {{agy_branch}}

## Goal

{{goal}}

## Shared Contracts

{{routes_components_state_and_data_contracts}}

## Codex Track

Worktree: {{codex_worktree}}
Owned files:

- {{codex_owned_file_or_directory}}

Tasks:

1. {{codex_task}}

## AGY Track

Worktree: {{agy_worktree}}
Branch: {{agy_branch}}
Task scope: {{agy_task_scope}}
Delegation tool: {{agy_ask_or_ask_json_or_start}}
Owned files:

- {{agy_owned_file_or_directory}}

Tasks:

1. {{agy_task}}

Forbidden files and operations:

- {{forbidden_file_or_operation}}

## Acceptance Criteria

- {{acceptance_criterion}}

## Verification Commands

```text
{{verification_commands}}
```

Output contract: {{text_report_or_json_schema}}

## Permissions

`dangerously_skip_permissions=false`

Permission mode: dangerously_skip_permissions={{false_or_true}}
Authorization record: {{authorization_record}}

Enable permission bypass only when the user explicitly authorizes this exact
trusted worktree and task. Keep the same permission mode and file boundary for
every corrective call.

## Call Budget and Result State

There are three total AGY calls: one initial call and at most two corrective
calls. Corrections require reproducible evidence and a clear acceptance check.

Result state: {{succeeded_or_permission_blocked_or_authentication_blocked_or_empty_output_or_invalid_output_or_timed_out_or_failed_or_unknown}}

Use `permission_blocked` for a permission denial, including a zero exit code
with `no output produced` caused by a permission request. Do not report
`succeeded` unless output is usable, the exit code is acceptable, and the
acceptance criteria pass.

## Post-Delegation Audit

Run these checks before accepting, correcting, or merging the result:

```text
git status --short
git diff --name-only
git diff --check
git worktree list
```

Changed paths: {{changed_paths}}
Verification result: {{verification_result}}
Blocker or permission error: {{blocker_or_none}}

## Cleanup and Retention

After a verified merge, record the merge result and confirm no uncommitted AGY
changes remain before removing the temporary worktree and deleting its fully
merged temporary branch. After hard timeout, `unknown`, permission/authentication
blockage, or final stop, preserve the AGY worktree and branch and record their
absolute paths for inspection or resume. Never clean unverified work or erase
the user's baseline.

## Stop Conditions

- Tests and acceptance criteria pass.
- A task changes a forbidden file or leaves its worktree.
- The worker asks for a user decision, authentication, or broader permission.
- The worker reaches a hard task timeout or makes no meaningful progress after two corrections.
- A permission blocker persists or the task scope becomes unclear.
- Note: Normal bounded wait-window expiry while the worker is running/queued is NOT a stop condition (`RUNNING_IS_STOP_CONDITION = NO`, `BOUNDED_WAIT_WINDOW_EXPIRED != TASK_TIMEOUT`).

## Merge Checklist

- [ ] AGY changed only its owned files.
- [ ] AGY verification commands pass.
- [ ] Codex reviewed the diff.
- [ ] Codex resolved or rejected conflicts before merge.
- [ ] The result state is not `permission_blocked`, `authentication_blocked`,
      `empty_output`, `invalid_output`, `timed_out`, or `unknown`.
- [ ] Cleanup was completed only after verified merge, or retention was recorded
      for a blocked/unverified result.
