# Current-source architecture tooling

## Change contract

The boundary is tracked source -> read-only parser -> deterministic index ->
Mermaid/check output. It has no runtime, database, network, release, or secret
authority. This first increment covers three declared critical slices, not the
entire application. Reuse #321's Python AST spans and PowerShell AST extraction,
without its stale package map or claims that syntactic facts prove ownership.

Inputs are explicit repository-relative files. Their normalized UTF-8 bytes,
extractors, and declaration form the input digest. Generated outputs, Git SHA,
time, machine roots, logs and runtime evidence are excluded. Source SHA belongs
in CI metadata, not committed generated output. Declarations select roots and
explain intent; they cannot create observed calls. Runtime observations remain
absent/UNKNOWN until separately supplied and verified.

The parser owns symbol and call-site extraction. A uniquely located lexical
call is not proof of runtime binding: dynamic object dispatch stays UNKNOWN.
Transaction statements are syntactic observations, not proof of atomicity.
Unknown edges must survive rendering. Missing sources/roots, parser errors and
generated drift fail the command; none may silently produce a complete graph.

Compatibility: developer-only additive tooling; old/new runtime and state are
unchanged. Build writes only its generated directory. Check never writes it.
An interrupted build is repaired by regeneration; a missing/stale graph blocks
the tooling check, never production recovery. The coordinator alone owns
branch protection and rollout. RULE_ENFORCEMENT_PENDING until verified remotely.

Validation executes Python -> real PowerShell parser -> JSON consumer from the
checkout, with explicit UTF-8 and bounded hidden subprocess. Tests cover parser
failure, unresolved dispatch, source mutation, deterministic regeneration,
input relocation, required-root failure and generated drift. No production
rehearsal is needed because the tool never executes parsed application code.

## Remaining integration

Full-source indexing, TS/C# extraction, SCC analysis, contract/mutation evidence,
the existing Explorer and per-intent merged evidence remain UNRESOLVED. This
increment does not close any old PR or claim architecture acceptance complete.

## Reuse review against the captured old stack

| PR | Exact head | Reusable intent | Current status |
| --- | --- | --- | --- |
| 321 | fd57fe7468bb74a8fc35386ac697d0deac69fba2 | AST facts, deterministic input identity, source spans, generated CI | Critical Python/PowerShell slice implemented locally; TS/C#/whole-source and integration UNRESOLVED |
| 324 | 1c8f1fa2d675f0aac14a71ad09d567f15680c889 | Explicit contract/test identities and source-bound execution evidence | UNRESOLVED; declared `runtime_events` plus a passed test must not manufacture `RUNTIME_OBSERVED` |
| 325 | 056a2bf4421af9a6a9ff46107c3c4ecd0b50dd7b | Isolated, valid, bounded mutation runs with distinct outcomes | UNRESOLVED; all three historical survivors need current-source mutations and assertions |
| 304/328 | 48cb32a60c803e7f7b97290209933b9041d5750a / 64ac434d97d557ec9857bb4a465a4de9c5c3554b | One private lazy Explorer, established camera/mobile semantics and evidence drill-down | UNRESOLVED; no replacement UI or deployment has been validated |

The old evidence compiler synthesizes ordered event rows from declaration
strings after a bound test passes. Those strings remain declared fixture
expectations, not recorded runtime events. Preserve that distinction while
reusing its registry and identity checks. The historical survivors are
`MUT-SYNC-HEARTBEAT-FIRST`, `MUT-EVIDENCE-APPEND-ONLY`, and
`MUT-RELEASE-PREVIEW-PROMOTION`; no current result is inferred from their names.

## Bounded historical-survivor execution

The current mutation runner reuses #325's exact symbol replacement and
baseline-first execution. Isolation is a disposable export of a clean tracked
checkpoint, not an old development tree or production runtime. No database
copy or external provider is used. The source checkout remains unchanged.

The three sentinel tests target actual owners: ForwardLedger (not the separate
PredictionLedger), ordering inside the real Sync entrypoint, and the exact
Preview rejection boundary before unrelated Promote preconditions. PowerShell
parses and executes only that function, with a deliberate throwing sentinel at
the next precondition; no facade or production configuration is loaded. Both
Windows shells must actually execute. Missing shells/cases are not a kill.

Results require a passing baseline and valid mutated syntax. KILLED requires
every selected JUnit case to fail with its exact named AssertionError. Skips,
setup errors, other failures, missing reports and timeouts never count as kills.
The exported source SHA and archive digest, changed-file hashes, logs and XML
are retained outside generated architecture content. This is isolated contract
evidence, never a production runtime trace or full release qualification.
