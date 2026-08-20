# Legacy Inventory

`D:\软件开发\codex-antigravity-vnext` is the authoritative source repository.

`D:\软件开发\codex-antigravity-bridge` is a historical/production reference
clone. It is not a source dependency for VNext and must not be copied wholesale
or used as the verification source. Its editable installation may remain
machine-local, but VNext verification must override it and attest resolved
paths.

AppData durable databases, old worktrees, reviewer relays, chat transcripts,
Antigravity caches, and user Codex configuration are runtime or private state;
they are not portable project inputs.
