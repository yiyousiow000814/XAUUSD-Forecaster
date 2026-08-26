# Architecture Evidence Contract

## Evidence categories

- `DECLARED` is a human-owned semantic statement. It proves no source or test
  behavior by itself.
- `STATIC_MATCH` is a deterministic match against the current source digest.
- `TEST_BOUND` means an explicit registry row names a test as protection for a
  durable contract. A static import is only `TOUCHES`.
- `TEST_EXECUTED` means the bound test passed at the exact current source
  digest. A failed, absent, or old-digest result cannot satisfy it.
- `RUNTIME_OBSERVED` means a bounded production-shaped fixture executed and
  emitted a normalized asserted event sequence at the current digest.
- `MUTATION_KILLED` requires a valid targeted mutation whose designated tests
  failed for the intended contract reason. A compile-only failure is
  `INVALID`; timeout and infrastructure failure cannot promote the contract.
- `STALE`, `CONTRADICTED`, and `UNRESOLVED` remain visible and never satisfy a
  current requirement.

`VERIFIED`, `PARTIAL`, and `DECLARED_ONLY` are derived presentation statuses;
the underlying categories remain available. Runtime states such as `CURRENT`,
`PAUSED`, and `PENDING` are not trust levels.

## Strict rollout

Every changed CURRENT structural claim requires current static evidence. Every
CRITICAL pilot contract requires `TEST_BOUND` and `TEST_EXECUTED`. A pilot may
also require `RUNTIME_OBSERVED` when a bounded fixture exists. Historical
semantic claims outside the pilot may remain visibly partial; they must not be
silently promoted.

A current surviving CRITICAL mutation blocks mutation-protected status. The
report must expose every outcome and bind it to the current source digest.

Test count is not a safety score. Unclassified tests remain valid regression or
reference evidence until audited. A test that imports an owner is not a
contract test unless the registry binds its exact normalized ID.

## Privacy

Runtime evidence stores only contract/test IDs, repository-relative source
spans, event types, sequence, platform category, digests, and durations. It
must never contain credentials, prompts, user messages, news bodies, payloads,
database values, absolute paths, usernames, or machine identifiers.

