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
- `test-evidence.json`: normalized collected test IDs, explicit contract
  bindings, TOUCHES/PROTECTS relationships, exact-digest execution, durations,
  classifications, and derived contract status.
- `runtime-evidence.json`: privacy-bounded asserted fixture event sequences and
  normalized hashes at the exact source digest.
- `mutation-report.json`: exact-source targeted mutation outcomes, test
  inventory, duration hotspots, and duplicate candidates. It becomes explicit
  `NOT_RUN` when no report matches the current source digest.

The private Web build embeds `explorer-manifest.json` in the already-lazy Admin
Explorer chunk. It exposes the larger code and evidence families as separate,
bounded virtual modules so the public initial bundle does not absorb them.
These modules are build artifacts, not a runtime Architecture API or store.

Generated artifacts must never contain environment values, credentials, raw
prompts, user/news/database content, request bodies, absolute paths, usernames,
or machine identifiers.

