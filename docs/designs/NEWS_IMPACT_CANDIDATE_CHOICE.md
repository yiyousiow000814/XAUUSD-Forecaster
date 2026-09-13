# Impact candidate selection transport

## Change contract

The classifier chooses an offered event identifier or `NO_MATCH` from a
request-local enum. The product normalizes `NO_MATCH` to the existing empty
canonical identifier before semantic validation and persistence. Initial and
contract-repair requests share the schema builder and representation. This
changes provider transport only; event identity, source qualification, lifetime
rules, generation membership and stored historical records remain unchanged.

Boundary: scheduler -> existing candidate retrieval/context fitting -> impact
payload -> metered Google or Qwen gateway -> semantic validator -> identity
resolution -> append-only assessment -> job completion. The same fitted
candidate set owns prompt, response choices and validation. An empty candidate
set permits only `NO_MATCH`. Candidate choice is request-local, with no durable
mapping, queue mode, retry owner or migration. Existing canonical IDs cannot
collide with the reserved sentinel. Schemas must be copied per request so
concurrent tasks and repair requests cannot share a mutable enum.

All old/new runtime and state combinations retain the canonical empty string
and existing UUID fields. Rejected output remains non-promotable; omitted or
unknown choices must still fail validation. Provider choice restrictions are
advisory externally; the local validator is authoritative. A valid ID alone
does not establish same-event equivalence. Refusals, quota limits, fallback
order and all semantic checks are preserved. A crash loses only a request-local
schema; the scheduler retries through its existing owner and durable quota
ledger. Normal main-only release and code rollback apply without state cleanup.

## Evidence and rehearsal

The affected Treasury-yield explainer produced the identical malformed output
hash on 197 recorded decoding failures. A metered 2,600-token rehearsal ended
MAX_TOKENS with 5,040 slash characters inside matched_candidate_id. Its first
1,426 characters reproduce the historical failure hash exactly. More output
budget did not repair it. An enum containing an empty string was rejected by
the actual Google API, so the transport uses the nonempty NO_MATCH sentinel.
With finite choices and the original 700-token cap, the real response ended
STOP at 214 output tokens and passed the unchanged semantic validator.
These are empirical provider observations, not a provider reliability guarantee.

Focused tests exercise both production request entrypoints, candidate and
no-candidate responses, schema isolation, invalid identifiers, truncated context
and canonical normalization. Existing gateway tests cover metered fallback and
content refusal. Rehearse the exact implementation against the saved real
envelope and then observe the affected job through normal runtime execution.
Do not call the live incident resolved before that job completes. Required
focused verification should take minutes; no timeout increases.

The escaped defect was free-form generation of a closed-set reference followed
by unchanged retries. Regression ownership belongs to the provider-to-identity
contract, not to this article's wording or a specific UUID.
