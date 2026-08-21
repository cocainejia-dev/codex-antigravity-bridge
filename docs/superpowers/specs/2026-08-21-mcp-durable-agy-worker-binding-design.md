# MCP Durable AGY Worker Binding

## Goal

Make the public VNext `run_start` contract dispatch one durable run to one production worker callback, which invokes the existing `agy_runner.run_agy` primitive exactly once through `DurableRunManager`.

## Design

`server.run_start` parses and validates the existing `TaskContract`, then builds a callback with a small binding module. The callback receives `WorkerContext`, serializes the existing contract fields into the AGY prompt, passes the contract workdir and max runtime to `run_agy`, and maps `AgyResult` to `WorkerResult`. No second async registry is introduced.

The callback is built before the durable row is inserted. A factory failure therefore fails closed without claiming worker ownership. Once construction succeeds, `DurableRunManager.run_start(worker=callback, auto_spawn=True)` owns the single worker thread and its lifecycle. The `queued` identity remains reserved for the no-callback path and is never used by this dispatch path.

## Mapping

- `TaskContract.objective` and the complete contract dictionary become the AGY prompt context.
- `TaskContract.workdir` becomes the AGY working directory.
- `TaskContract.max_runtime` becomes the AGY timeout.
- AGY exit code `0` becomes successful `WorkerResult`; non-zero becomes failed `WorkerResult` with diagnostic text.
- Existing contract fields such as allowed/forbidden paths, risk class, verification commands, and commit policy are preserved as prompt metadata; no new contract fields are added.

## Safety and tests

The implementation preserves persist-before-spawn, duplicate protection, callback ownership detection, manager recreation recovery, and read-only/mutation metadata. Tests use a fake runner and never invoke real AGY. They cover binding, construction failure, mapping, success/failure, exactly-once submission, and the 2091093 false-recovery regression.
