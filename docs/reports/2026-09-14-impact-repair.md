# Impact repair correction

## Boundary and invariants

The affected path is persisted retrieval candidates -> impact request -> local
identity validation -> one repair request -> validation -> scheduler attempt
receipt. The annotator owns requests and the scheduler owns append-only attempt
records. Candidate facts live in event_claim; IDs and eligibility stay on the
candidate. Repair must preserve both without inventing facts or weakening
SAME_EVENT eligibility. No schema, credential, service owner, deployment or
queue transition changes are needed.

Existing data remains readable. New structured diagnostics stay JSON in the
existing TEXT column, capped at 8192 characters; overflow retains a bounded
summary and content hash. Plain text stays capped at 500. Existing json_valid /
json_extract consumers remain compatible. Historical attempts are not rewritten.
A failed provider request stays retryable under existing policy; an invalid
repair remains rejected. Restart and forward repair use the current main owner.
No retained code slot or migration is introduced.

## Evidence and verification

The incident's saved retrieval receipt contains five candidates. Read-only
replay through the corrected repair serializer preserves all seven claim fields,
IDs and eligibility for all five; previously the claim fields were empty.
The gateway contract test exercises initial rejection followed by valid
SAME_EVENT recovery, and a second invalid result with the repair output's hash
and identifier. Scheduler coverage proves SQL JSON consumption, bounded overflow
and immutable repeated insertion. Provider transport and event-identity sibling
contracts remain covered by the related suite.

Review traced production call_impact through _repair_impact_contract and
_validate_impact_result, then record_job_attempt and the SQL evidence consumer.
No provider calls or production database writes were made for this rehearsal.
Provider 500/503 availability remains separate from the deterministic correction;
real-model success is not implied by a controlled transport fixture.
