# Architecture Mutation Pilot

## Scope

The pilot contains twelve explicit, symbol-validated mutations across Python,
Web, and Windows. Six form the cross-platform smoke profile. The full Windows
workflow adds Stable promotion, structured result, and exact Control Plane
identity checks. Every focused baseline runs before its mutation. No provider,
deployment, database, runtime owner, Stable state, or production state is
called or changed.

## Interpretation

`KILLED` means the syntactically valid targeted break made its focused contract
test fail with the expected signature. `SURVIVED` is a visible blocker for a
critical mutation-protected contract. `INVALID`, `TIMEOUT`, and `ERROR` are
operational findings and never count as protection. Exact duplicate normalized
Python test bodies are reported as review candidates only; this campaign does
not delete or label tests by count.

## Bounds

The smoke job has a five-minute workflow limit and each mutation has a 90-second
focused limit. The complete manual/nightly pilot has a twenty-minute workflow
limit and Windows mutations have a 120-second focused limit. Each mutation uses
one temporary detached worktree and the runner verifies the source checkout is
unchanged after cleanup.

## Current baseline

The exact current pilot produced 12 valid mutations: 9 `KILLED`, 3
`SURVIVED`, and zero `INVALID`, `TIMEOUT`, or `ERROR`. The bounded smoke set
completed locally in 18.36 seconds with all seven selected mutants killed.
The surviving full-pilot mutations are `MUT-SYNC-HEARTBEAT-FIRST`,
`MUT-EVIDENCE-APPEND-ONLY`, and `MUT-RELEASE-PREVIEW-PROMOTION`. They remain
explicit blockers for mutation-protected status; the audit does not reinterpret
their existing passing tests as effective protection.
