# Impact repair feedback recovery

## Change contract

Production main 7394784 preserves initial impact output and candidate context but
replays only the initial rejection on every repair. The September 14-15 incident
has 276 identical full-response hashes for one task and 1,675 same-episode
comparison failures across 12 tasks. A non-anchor SAME_EVENT becomes
SAME_EPISODE without the required factual change. This is a repair liveness
defect; the identity validator correctly withholds the assessment.

The existing scheduler remains the sole retry/lease/completion owner. Reuse its
append-only attempt evidence as bounded advisory repair feedback; do not add a
second mutable progress store. Read the most recent contract rejection for the
exact annotation/source revision/prompt job using an indexed lookup. HTTP and
capacity attempts cannot erase that feedback. Carry the original frozen context
and original result plus the later rejection; never treat a diagnostic excerpt
as complete accepted output or infer a model decision from it. Save comparison
fields in bounded failure evidence and bind new feedback to the checkpoint key.
Legacy evidence without that key remains advisory for the exact existing job.

Keep initial checkpoint identity v2: its extraction, source and candidate context
are unchanged. Version the changed repair request as v3 independently. Existing
checkpoints and immutable historical attempts stay readable; no data rewrite or
Cloudflare payload/schema change is required. Add one partial attempt index to
bound lookup as unrelated provider errors accumulate. The scheduler schema owner
creates it idempotently; interruption leaves SQLite responsible for atomic DDL.

Impact graph: scheduler -> assess_pending_news_impacts -> checkpoint + latest
attempt reader -> metered Gemma repair -> unchanged full validator -> identity
resolution -> assessment -> scheduler completion. The same owner records a
failed repair before the next claim. A crash before recording retains the prior
feedback and retries safely; after recording, reopen restores it. Quota/HTTP
failure remains externally retryable, no additional immediate request loop.
Malformed/oversized diagnostic evidence is omitted, never promoted. Source or
checkpoint mismatch cannot inject evidence into another repair. Lease expiry,
timeouts and machine restart retain their existing owner and behavior.

Safety: no fabricated facts, forced identity, relaxed validation, source replay,
manual completion, replaced checkpoint or changed credential authority.
Liveness: a valid model decision remains reachable after every rejected repair;
provider availability is external advisory evidence, not guaranteed by tests.
Prompt rules must distinguish optional contextual updates from the independently
required SAME_EPISODE fact changes. Remove obsolete separate-field instructions.

Verification budget: focused contracts under 2 minutes; affected combined gates
under 5 minutes each. Execute actual gateway serialization, scheduler evidence
recording, SQLite close/reopen, production assessor wiring and full validator.
Parameterize same-event, same-episode and new-episode comparison violations,
identity isolation, interleaved HTTP/capacity failures, malformed legacy evidence
and no fabricated fallback. Rehearse with frozen production checkpoint evidence,
then verify original live task completion after normal protected-main activation.
No branch code activates production. Recovery is a normal forward correction;
main owner 7394784 currently supervises services and evidence remains intact.

Sibling review: initial extraction and evidence-anchor repair do not consume this
impact checkpoint; preserve their accepted-stage restrictions. Review all impact
relation/update combinations against comparison validation, not enum membership
alone. Previous tests covered quota restart and field combinations but did not
execute rejected repair -> persisted rejection -> corrected repair after restart.

## Verification and final review

A read-only production replay reconstructed the original rejected response from
its immutable checkpoint and saved selected output. Its SHA-256 exactly matches
022ce14eea44dfa40a47acdd1fed75cadbb9e37cc0355f5ff27ffd6e6def0eb7;
the unchanged validator reproduces `Same-episode identity requires a core factual
change`. The new reader supplies that later rejection to the repair payload
(11,876 UTF-8 bytes in this rehearsal), while keeping original context unchanged.
No provider request or production evidence mutation was made by the rehearsal.

Final review traced scheduler runtime ACTIVE_IMPACT dispatch through the actual
assessor and `record_job_attempt`, which persists each outcome before retry.
Failure feedback is advisory and bounded; no additional durable progress state,
recurring actor, cloud consumer, public API or success authority was introduced.
The existing partial-evidence truncation path now retains checkpoint identity.
Tests execute the metered gateway, real SQLite persistence/reopen, exact identity
isolation, interleaved provider/capacity outcomes, all relation/update comparison
families and assessor-to-feedback wiring. A query-plan assertion verifies the
partial rejection index. The architecture compiler check passes unchanged.

Local Windows/Python execution uses the worktree root, real package imports,
pytest entrypoint, scheduler attempt writer and temporary SQLite ledgers.
Success preserves the checkpoint and accepted impact fields; invalid comparison,
wrong identity and malformed feedback never bypass the validator. Production
activation and external-provider recovery remain separate post-merge evidence.

Final affected local regression: 503 passed in 51.73 seconds.
