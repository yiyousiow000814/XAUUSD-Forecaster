# Change Safety Protocol

Use this protocol before a material change to architecture, persistence, state,
storage, deployment, process lifecycle, ownership, a public or provider API, a
CLI or interface, authentication, concurrency, cross-version behavior,
background services, rollback, recovery, or irreversible mutation. Tiny local,
behavior-preserving edits do not require it.

## Change contract

Record before editing:

1. **Change:** the exact boundary changing.
2. **Invariants:** everything that must remain true.
3. **Ownership:** code, mutable state, persistent data, config, secrets, cache,
   receipts, and lifecycle authority.
4. **Boundary and impact graph:** callers, callees, and changed interfaces.
5. **Compatibility matrix:** applicable `new -> new`, `new controller -> old
   runtime`, `old controller -> new runtime`, `new code -> old state`, `old code
   -> new state`, rollback-to-old, and partial-migration restart combinations.
6. **State transitions:** before, during, after, retry, and crash/restart.
7. **Failure matrix:** failure at zero progress, partial progress, after an
   irreversible mutation, after the new component starts, and before commit.
8. **Recovery authority:** prove the old known-good system can recover with
   captured old authority, independently of the failing change.
9. **External truth:** verify applicable pagination, truncation, eventual
   consistency, omission, encoding, rate limits, real envelopes, and versions.
10. **Observability:** distinguish delayed, unavailable, corrupt, partial, and
    provider-omitted results.
11. **Rehearsal:** run the production-shaped real interaction unit tests cannot
    prove.
12. **Rollout and rollback:** stage the change and prove rollback before switch.
13. **Cleanup:** state when temporary bridges and adapters are removed.

The implementer's assumptions are not independent evidence. For large changes,
use non-overlapping architecture, recovery, or final-evidence review when
delegation is authorized and useful; do not duplicate broad scans.

## Pre-mortem

- What failure appears only after all earlier gates pass?
- Which old/new combination has never executed?
- Which hidden state, process, provider, filesystem, encoding, or identity
  boundary is assumed?
- What happens at the worst possible failure point?

## Escaped-blocker learning

Do more than add a literal regression. Identify which general Change Safety
question should have caught the blocker, improve the framework at that general
level, and exercise siblings governed by the same rule without expanding the
authorization boundary.

Expected lifecycle:
`request -> change contract -> impact graph -> compatibility/failure/recovery
matrix -> test and rehearsal plan -> implementation -> independent verification
-> rollout -> post-release cleanup`.
