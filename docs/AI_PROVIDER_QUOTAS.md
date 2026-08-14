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
