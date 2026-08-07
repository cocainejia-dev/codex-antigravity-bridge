# AGY Development Plan: {{title}}

Status: READY_FOR_AGY
Created: {{date}}
Project: {{project_root}}

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

## Permissions

`dangerously_skip_permissions=false`

Enable permission bypass only when the user explicitly authorizes this exact
worktree and task.

## Stop Conditions

- Tests and acceptance criteria pass.
- A task changes a forbidden file or leaves its worktree.
- The worker asks for a user decision, authentication, or broader permission.
- The worker times out or makes no meaningful progress after two corrections.

## Merge Checklist

- [ ] AGY changed only its owned files.
- [ ] AGY verification commands pass.
- [ ] Codex reviewed the diff.
- [ ] Codex resolved or rejected conflicts before merge.
