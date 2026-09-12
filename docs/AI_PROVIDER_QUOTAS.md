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

`GROQ_API_KEY` enables `qwen/qwen3.8-27b`, then `qwen/qwen3.6-27b` after
Google generation HTTP 500/502/503 or transport failure. Google Gemma capacity
denial and 429 also qualify. Each route is tried once per failed generation,
with the complete source, schema and original decoder. Successful generation,
authentication failures and invalid output do not cause extra requests.
Assistant and historical backfill never use backups.

All lanes share each Groq model's budget through the existing SQLite owner:
30 RPM, 1,000 RPD, 8,000 combined tokens/minute and 200,000 tokens/trailing
24 hours. Complete converted prompt UTF-8 bytes plus up to 2,048 output tokens
form a conservative reservation. Large requests skip the route; source input
is never truncated. Failed attempts retain usage and successful responses also
record actual tokens and model identity. Per-model Retry-After is durable and
independent of Google. Network inactivity timeout is 15 seconds per route.
Both Qwen models use `reasoning_effort=none` for bounded JSON news tasks.

Only the annotator receives the optional Windows user setting. Restart is
needed after a credential change. Credentials never enter logs or Git. Account
limits and external availability remain authoritative; unavailable backup work
returns to the existing queue. No paid account or model is enabled.
See [Groq limits](https://console.groq.com/docs/rate-limits) and
[reasoning parameters](https://console.groq.com/docs/reasoning).

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
