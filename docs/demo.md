# Collaboration Demo

This demo is intentionally honest about its prerequisites. A real run needs
Codex, the `agy` CLI, a completed `agy` login, Git, and a separate example
repository. The dry-run does not need an `agy` binary or an active login.

## 1. Verify the MCP bridge

First verify the local CLI and MCP registration:

```powershell
agy --version
agy -p "Reply exactly DIRECT_AGY_OK"
codex mcp list
```

Then call `agy_ask` from Codex with a small reversible prompt, for example
`Reply exactly MCP_AGY_OK`. Keep `dangerously_skip_permissions=false` unless
the user has explicitly authorized a trusted worktree and task. The repository
tests mock this process boundary; this step is the real-machine smoke test.

## 2. Validate the plan without side effects

From a Codex session with this MCP server installed, call `agy_collab_start`
with the following request:

```json
{
  "project_dir": "C:/work/demo-app",
  "base_ref": "HEAD",
  "dry_run": true,
  "shared_contract": "GET /api/items returns [{id, name}].",
  "tasks": [
    {
      "id": "backend",
      "role": "Backend",
      "prompt": "Add GET /api/items and backend tests.",
      "owned_paths": ["backend/"],
      "acceptance": ["The API test passes"],
      "verification": ["python -m pytest backend"]
    },
    {
      "id": "frontend",
      "role": "Frontend",
      "prompt": "Add an items page using the shared API contract.",
      "owned_paths": ["frontend/"],
      "acceptance": ["The frontend test passes"],
      "verification": ["python -m pytest frontend"]
    }
  ]
}
```

The response must have `state: "dry-run"`, two planned branches, two planned
worktree paths, and no created directory or job. Invalid refs, overlapping
owned paths, invalid task objects, and a non-Git project must fail before any
side effect.

## 3. Run the real collaboration

After reviewing the dry-run response, repeat the request with `dry_run: false`.
The bridge creates one branch and worktree per task and starts one bounded
`agy` process per task. It does not merge or delete either worktree.

Poll the returned session:

```text
agy_collab_status(session_id="<returned session id>")
```

For each task, review:

- `state`, `branch`, and `workdir`;
- `worktree.committed`, `uncommitted`, `untracked`, and `deleted`;
- `scope_status` and `scope_violations`;
- the task acceptance and verification output.

`scope_status` is `passed` only when every reported path is inside that task's
`owned_paths`. It is `violated` when an outside path is present and `unknown`
when Git inspection is unavailable. A violation is reported for human review;
the bridge does not delete or revert the file.

## 4. Human review

Run the task-specific verification commands in each worktree. Inspect the
diffs and decide whether to merge the task branches manually. A
`ready_for_review` session is only a process handoff, not proof that the
acceptance criteria passed.

The example task contract is in
[`examples/collaboration-demo/shared-contract.md`](../examples/collaboration-demo/shared-contract.md).
