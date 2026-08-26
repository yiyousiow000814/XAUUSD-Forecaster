# Architecture Generated Artifact Protocol

## Contract

All JSON under `architecture/generated/` is derived and carries a schema,
generated-file notice, and stable source digest where applicable. JSON keys and
arrays are stably ordered and serialized as UTF-8 without timestamps.

- `explorer-manifest.json`: bounded high-level semantic graph consumed at build
  time by the private Admin Explorer.
- `code-index.json`: repository hierarchy facts, source spans, extractor rules,
  certainty, observed imports, allowed-unused policy, and violations.
- `evidence-index.json`: stable node/edge claim IDs, evidence categories, and
  repository-relative source bindings.
- `source-digest.json`: per-input SHA-256 and the aggregate source digest.
- `windows-evidence.json`: exact PowerShell parser facts tied to a separate
  PowerShell-source digest. Neutral fallback facts never satisfy this evidence.
- `test-evidence.json` and `mutation-report.json`: explicit `NOT_COLLECTED` and
  `NOT_RUN` placeholders until their owning campaign PRs produce evidence.

Generated artifacts must never contain environment values, credentials, raw
prompts, user/news/database content, request bodies, absolute paths, usernames,
or machine identifiers.

