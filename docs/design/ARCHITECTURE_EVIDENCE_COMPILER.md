# Architecture Evidence Compiler Design

## Decision

Architecture structure is compiled offline from current repository source and
small explicit declarations. The compiler is deterministic, local, bounded,
and fail closed. It never calls an AI provider or reads production stores.

## Inputs and owners

- Python uses the standard-library AST for modules, imports, exports, symbols,
  entry guards, execution constructions, SQLite connections, and literal SQL.
- Web uses the lockfile-owned TypeScript compiler API for imports and source
  spans, plus filesystem route conventions for page/API routes.
- PowerShell has a platform-neutral inventory whose `FALLBACK` certainty is
  distinct from exact Windows parser evidence.
- cTrader C# has bounded file, namespace, and class evidence; unresolved
  semantic relationships are not invented.
- Declarations own purpose, owner, authority, criticality, failure semantics,
  view taxonomy, scenarios, and orientation-independent layout hints.

Imports prove observed static dependency only. They cannot prove ownership,
authority, runtime execution, or failure isolation. Allowed dependency policy
is emitted separately from observed imports, including unused permissions,
undeclared observations, and prohibited observations.

## Outputs and failure domain

The compiler emits a bounded high-level manifest and separate lazy code and
evidence indexes. Every artifact is repository-relative, sorted, source-bound,
and free of timestamps. A stale output, missing required CURRENT binding,
selector cardinality error, prohibited import, second undeclared writer,
absolute path, or secret pattern fails the local/CI gate. Runtime, API,
database, scheduling, and deployment behavior are unchanged.

