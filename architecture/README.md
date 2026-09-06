# Current-source architecture

Run `python scripts/compile_architecture.py build`, then commit the generated
files. `python scripts/compile_architecture.py check` fails on drift without
rewriting anything. `python scripts/compile_architecture.py explain append_clock_event`
prints source call sites and side-effect syntax. PowerShell is required for the
real parser; inspected scripts are never dot-sourced or executed.
Obsolete generated files also fail check. Build preserves rather than silently
deleting them; review and remove the retired generated view when renaming it.

The first slice uses the AST extraction approach from PR #321, adapted to current
source rather than the obsolete classification branch. Its shared JSON and three
Mermaid views are generated from the selection, source and tool bytes. Symbols
use path plus qualified name, independent of line movement; spans retain exact
locations. `syntactic_owner` names the source file, not process/data authority.

`allowed` contains view-selection declarations, not approved dependency edges.
`observed` contains source syntax only. Calls retain UNKNOWN runtime binding,
including same-file candidates. Literal SQL is retained without claiming query
plans, transitive writes, successful execution or atomicity. Python context
manager commits, native/PowerShell method invocation, decorators and dynamic
imports require additional analysis/runtime evidence. Absence of an extracted
edge does not prove absence of a side effect. `runtime` is explicitly UNKNOWN.

Input digests exclude outputs, commit SHA, machine paths and time. They normalize
UTF-8 BOM and CRLF/LF for Windows/Linux parity. The CI summary records source SHA
separately. Test sources are inputs but their presence is not a test PASS.

This is DECLARED_CRITICAL_SLICES_ONLY, not the finished whole-system Explorer or
transaction proof. The staged lifecycle remains the recovery evidence authority.
Branch protection status: RULE_ENFORCEMENT_PENDING until coordinator verification.
No old PR is superseded merely by this generated index.

## Retained execution evidence

`python scripts/architecture_evidence.py --evidence <run-directory> --source-sha <exact-sha>`
projects an existing mutation report plus its actual baseline/mutant JUnit files.
It validates the complete declared family set, exact test bindings, case identity,
case counts and the named behavior assertions. A report's `KILLED` label alone is
not sufficient. The selected SHA is explicit: evidence from another revision is
`STALE`, not a current pass. This command does not execute tests or rewrite the
source index. Retained bytes are hashed; this is not a signed CI attestation or
independent proof of execution provenance.

The test-binding concept is reused from PR #324. Its declared `runtime_events`
must not produce `RUNTIME_OBSERVED` merely because a bound test passes. This
projection keeps runtime traces `UNKNOWN`; actual trace capture and full Explorer
composition remain separate unfinished work. A mutation result proves only the
specific tested boundary, not complete production recovery or a release gate.
