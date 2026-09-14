# Runtime Boundaries

Read when selected by the [Change Safety Protocol](../CHANGE_SAFETY.md).

## Runtime bundles, locators, and maintenance

A runtime bundle must be dependency-closed, not merely self-consistent with its
own manifest. Derive direct and transitive runtime dependencies, require each to
be declared, copied, hashed, and verified, and rehearse startup from an isolated
staged root that cannot resolve omitted files from a development checkout.

For every persistent filesystem locator, record:

- its authoritative owner and permitted roots;
- whether it is absolute, relative, or otherwise portable;
- the finite relocation behavior for known old roots;
- old-code/new-state and new-code/old-state compatibility;
- whether its bytes participate in artifact hashes, generation identities,
  receipts, or immutable evidence.

Unknown roots and traversal fail closed. Do not rewrite immutable locator-bearing
content without accounting for every derived identity and receipt.

Heavy maintenance begins only after critical startup viability is established.
It needs an explicit single owner, a bounded completion or failure receipt, and
idempotent restart behavior. A crash or restart must not multiply the same heavy
operation, and recovery may clean only temporary state proven to belong to that
owner.

The implementer's assumptions are not independent evidence. For large changes,
use non-overlapping architecture, recovery, or final-evidence review when
delegation is authorized and useful; do not duplicate broad scans.


## Real composition execution

Static source inspection is not sufficient when correctness depends on language
or runtime composition semantics such as dot-sourcing and scope, environment
inheritance, CLI parsing, working directory, import resolution, subprocess
quoting, environment precedence, or serializer/consumer wiring.

Before completing a material script or orchestration change, record a compact
execution matrix containing the actual runtime, entrypoint, caller, callee or
import, parameter binding, filesystem roots, working directory, success case,
and fail-closed case. At least one automated test must execute every changed
critical composition boundary with the real runtime involved.

Expected lifecycle:
`request -> change contract -> impact graph -> compatibility/failure/recovery
matrix -> test and rehearsal plan -> implementation -> independent verification
-> rollout -> post-release cleanup`.

