# AI Provider Quota Reference

`GEMINI_API_ACCOUNTS` is the source of truth for quota ownership. Each entry
identifies one provider account or project and may contain one or more API keys.
Keys inside one entry share that entry's daily, RPM, and TPM limits. Separate
entries are metered independently.

A deployment may configure one or more independently metered provider
accounts. Secret key values and installation-specific account counts must never
be written to this repository. Legacy `GEMINI_API_KEYS` configuration treats
each distinct key as an independent account when no explicit grouping exists.

Displayed totals aggregate usage across configured accounts, while admission
control remains per account. Provider limits shown by AI Studio remain the
authority; repository constants are conservative local safety limits.

Gemini Embedding 2 is admitted independently at 100 RPM, 30,000 input TPM,
and 1,000 RPD per configured independent account. Google counts each embedded
content item in `batchEmbedContents` as one request; the scheduler therefore
reserves the batch item count rather than the HTTP envelope count. Exhausted
minute capacity defers work instead of sending an over-limit request.

The runtime reloads this account registry for every scheduler batch and ranks
independent accounts by current daily, RPM, and TPM headroom. On Windows it
reads the current user-scoped environment registry directly, so adding or
removing a credential does not require a service restart. A newly visible
independent account joins routing on the next cycle. An extra key inside an
existing account adds transport redundancy but does not increase that account's
quota or the scheduler's automatic batch size.

## Optional news backup

`OPENROUTER_API_KEY` enables only `google/gemma-4-31b-it:free`.
`GROQ_API_KEY` enables `qwen/qwen3.8-27b`, then `qwen/qwen3.6-27b`.
Both Google Gemma and Gemini recovery try OpenRouter Gemma first, followed by
Groq Qwen 3.8 and then 3.6. Each route is tried once per
failed generation, with the complete source, prompt schema and original decoder.
HTTP 500/502/503 and transport failure trigger recovery; Gemma capacity denial
and HTTP 429 also qualify. Successful generation and invalid model output do
not cause extra requests. Assistant and historical backfill never use backups.

All lanes share 50 requests per UTC day and 20 per trailing 60 seconds through
the existing transactional request ledger. Failed and interrupted attempts count;
changing keys or restarting does not reset the budget. OpenRouter Retry-After
affects only its own provider scope. Paid models and paid routing are excluded by
the fixed free model and zero provider maximum prices. The Windows main launcher
loads the optional key for the annotator; restart is needed after configuration
changes. Request/model outcome evidence contains no secrets.

Groq shares each model's budget across all scheduler lanes: 30 RPM, 1,000 RPD,
8,000 combined tokens/minute and 200,000 tokens/trailing 24 hours. Reservations
count the complete converted prompt conservatively using UTF-8 bytes plus up to
2,048 output tokens. This can skip large articles; input is never truncated to
fit. Failed attempts retain their reservation. Actual provider token usage is
also recorded. Per-model Retry-After survives restart independently of Google
and OpenRouter. Backup network inactivity timeouts are 15 seconds per route.
These limits follow the configured free account; provider limits can be lower.
See [Groq limits](https://console.groq.com/docs/rate-limits).

See [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits).
The account's actual free allowance may be lower or unavailable; provider
availability is not guaranteed and backup failure returns to the existing queue.

## Assistant capacity policy

Assistant generation is currently paused while a suitable API model is being
selected. The provider-neutral chat, capacity, and conversation interfaces are
retained, but the production write route rejects new turns before creating any
conversation state. No Gemini or Gemma route is an implicit Assistant fallback.

`ASSISTANT_CAPACITY_POLICIES` may declare exact or `*` pool templates for each
enabled Assistant model. Each entry declares `credential_pool_id`, `provider`,
`model_id`, optional shared model IDs, RPD/RPM/TPM limits, `soft_cap_ratio`,
`max_in_flight`, reservation TTL, cooldown duration, failure threshold, and
enabled state. Exact pool/model entries override wildcard templates. Unknown
pools, models, providers, fields, duplicate pairs, malformed ratios, and
unbounded values fail closed.

When this variable is absent, models already present in the canonical AI quota
registry inherit its conservative limits with the Assistant default headroom.
An operational model that is not in that registry requires an explicit
Assistant policy; the runtime does not guess provider limits. Limits remain
deployment configuration and are not conversation data.

Assistant reservations reuse the scheduler's durable account/model daily and
minute ledgers, then add finite in-flight reservations and pair health. A
versioned completion receipt stores only an anonymous pool fingerprint and
bounded policy facts. It never stores API keys or raw account IDs in Assistant
conversation provenance.
