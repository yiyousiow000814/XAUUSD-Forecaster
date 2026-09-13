# News retry capacity and malformed-output fallback

## Change contract

The annotation persistence projection must retain the accountant's capacity reason, evidence and next eligible time. The scheduler remains the sole queue owner. Health headlines count effective processing failures; scheduler claims stay in technical evidence. Malformed JSON from a news provider may use the existing metered Groq chain, in declared Qwen order, once per route per gateway call.

Impact graph: provider/quota accountant -> gateway -> annotation parse -> persistence projection -> scheduler release -> status payload -> health UI. Title and impact projections already preserve capacity metadata and must remain covered by the same contract. No schema, credentials, quota limits, accepted annotations or historical attempts change. No source text is truncated to force backup admission.

## Ownership, states and compatibility

Quota reservation owns permission to send. Provider HTTP and incomplete/malformed responses are externally retryable, never accepted results. Explicit prohibited or safety-blocked content must not be rerouted. Successful parsing remains subject to downstream source/schema validation. Pending capacity remains QUEUED until the known next eligibility; normal failures retain fair queue retry. A missing recovery time retains the existing bounded one-minute retry. All fields are additive or already supported, so new/old consumers and old/new state retain schema compatibility; rollback restores prior behavior without rewriting data.

The same process and supervisor own restart and machine restart; durable quota and queue times survive both. A crash before release is recovered by lease expiry. A failed or unavailable backup does not mark the job complete. Backup attempts remain bounded by the existing route list, atomic minute/day budgets and provider cooldown. Backup failure does not erase primary provider evidence. Optional backup failure must not stop unrelated work. There are no new durable locks, retry modes or owners.

## Evidence and failure/recovery matrix

Controlled exact: reason/time preservation, no-provider-call capacity refusal, metered single-route attempts, secret handling, health count semantics. External uncertain: provider availability and response completeness; mocked HTTP tests establish routing, not provider uptime. A read-only production-shaped payload capture verifies real input size/route eligibility; no prohibited content is replayed. Rehearse an innocuous request through the configured backup when actual capacity permits, otherwise report that limit explicitly.

Test no state, partial response, malformed JSON, valid backup, both backups unavailable, explicit prohibited/safety rejection, quotas exhausted then renewed, deferred time surviving the public annotation entrypoint and scheduler consumer, and health copy with hundreds of capacity deferrals plus a small actual error count. Existing production-shaped scheduler and gateway tests cover real entrypoints. Required focused gates must finish within normal CI budgets; no timeout expansion.

## Rollout and review

Normal main-only PR release. Preserve previous main identity for normal code rollback. Verify current runtime revision, a real deferred task's next eligibility/reason, and provider outcome accounting after deployment. Do not call the production incident resolved merely because warnings change. Review all sibling producer/consumer boundaries on the final head. No temporary bridge needs cleanup.
