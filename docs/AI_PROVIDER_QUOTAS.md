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

The runtime reloads this account registry for every scheduler batch and ranks
independent accounts by current daily, RPM, and TPM headroom. On Windows it
reads the current user-scoped environment registry directly, so adding or
removing a credential does not require a service restart. A newly visible
independent account joins routing on the next cycle. An extra key inside an
existing account adds transport redundancy but does not increase that account's
quota or the scheduler's automatic batch size.

## Assistant capacity policy

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
