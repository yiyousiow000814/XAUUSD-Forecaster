# AI Provider Quota Reference

`GEMINI_API_ACCOUNTS` is the source of truth for quota ownership. Each entry
identifies one provider account or project and may contain one or more API keys.
Keys inside one entry share that entry's daily, RPM, and TPM limits. Separate
entries are metered independently.

The current local installation uses four API keys owned by four independent
accounts. Secret key values must never be written to this repository. Legacy
`GEMINI_API_KEYS` configuration treats each distinct key as an independent
account for compatibility with this installation.

Displayed totals aggregate usage across configured accounts, while admission
control remains per account. Provider limits shown by AI Studio remain the
authority; repository constants are conservative local safety limits.

The runtime reloads this account registry for every scheduler batch and ranks
independent accounts by current daily, RPM, and TPM headroom. A newly visible
independent account joins routing on the next cycle. An extra key inside an
existing account adds transport redundancy but does not increase that account's
quota or the scheduler's automatic batch size.
