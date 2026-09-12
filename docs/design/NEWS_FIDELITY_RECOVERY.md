# News fidelity and provider-failure recovery

## Scope and evidence

The September 12 source audit found numeric scale, modality and historical-time
ambiguity in accepted prose. Display review already exists, but its instruction
does not explicitly compare these dimensions against the source. Strengthen that
same request, without adding a reviewer or a local language/numeric veto.

A read-only runtime query of current-generation dead-letter jobs updated since
September 1 found 341 jobs excluding retired eligibility records: 271 ended with
HTTP 500/503 and 45 with absent source anchors. These are task counts, not unique
articles or an estimated corpus error rate. Page-selection requests explicitly
skip the existing exact-source anchor recovery; this is an inconsistent boundary.

## Ownership and transitions

The annotation worker owns source selection, semantic validation and the single
Gemma display review. The selected immutable article is the source for both
review and bounded anchor recovery. Semantic values and selection IDs stay frozen
during anchor repair; invalid selection still fails before another request.

The scheduler owns leases, quota admission and retry eligibility. Provider
unavailability remains BACKING_OFF after the fifth failure, with the existing
backoff capped at twelve hours per record. It never grants completed/model
permission. Existing current-source eligibility and quota controls still apply;
there is no immediate retry loop, fallback provider or new recurring owner.
Deterministic content/JSON failures retain their existing bounded stopping rule.

Existing versioned recovery receipts reopen only matching old terminal failures
once. Receipt and job updates remain one transaction. Repeated scheduler calls
or restart cannot issue another recovery grant for the same version and identity.
Latest failure selection prevents an older provider error from authorizing a
later deterministic failure. Original failure rows and annotations are immutable.

## Boundaries, compatibility and failure handling

Worker -> append-only evidence -> scheduler -> archive/mirror -> reader retains
the current schema and state enum. Recovery clocks already participate in the
incremental archive clock. Existing accepted annotations are not mass-rewritten;
new interpretation is visible only at its actual completion time. This patch
does not assert that historical research inputs or already accepted prose have
been repaired. Source clocks must not be invented from receipt time.

Old code can read new rows and receipts; reverting code preserves data but
restores the old terminal retry policy. Before receipt commit a crash changes
nothing; after commit the normal scheduler claims the queued job. Provider
failure thereafter records a new backed-off attempt. Invalid model output never
becomes a successful receipt. Optional news processing cannot stop collection.

## Verification

Exercise both annotation and impact failure persistence beyond five attempts,
bounded retry timing, exact-stage recovery and repeated scheduler reconciliation.
Exercise page selection -> exact anchor repair -> Gemma review -> ledger using
the real Python pipeline with metered provider fixtures. Check source fidelity
instructions in the real outgoing request and preserve non-display semantics.
Fixtures establish wiring and recovery, not actual model accuracy: real-provider
evaluation and historical correction remain separate acceptance evidence.

## Verification record

- Related Python contracts: 396 passed (failure persistence, scheduler,
  annotation, source selection, ledger and dashboard resource consumers).
- Architecture tool contracts: 177 passed, 4 skipped on this Windows host.
  Generated architecture identity: `623c0d5d298c4838043c284a929908c064074d1f2c05568e26dfb4ce70124e14`.
- Import and repository policy checks passed. No web component or route changed.
- A real single-review rehearsal used immutable annotation
  `afd8a749-bae8-5ee2-b05f-67d7d26b2b37` and source hash
  `2dcc83ed0062050560acf03ad18b347d0582584f41c6d3c29650756c80bc74ee`.
  The first attempt returned an HTTP error; one diagnostic confirmation at
  September 12, 15:27 MYT returned HTTP 500. No accuracy pass is claimed.
  Both attempts used the existing scheduler quota accountant; quota/request
  receipts were written, but no annotation, raw source, model or job was changed.
- A subsequent read-only indexed snapshot at September 12, 15:29 MYT found
  342 non-retired terminal jobs since September 1. The initial 341-job count
  is a point-in-time observation, not a fixed acceptance total.
- Exact production-path review found the impact candidate reader ignored recovery
  receipts even after its scheduler job was reopened. It now consumes the same
  recovery identity. A regression executes lease -> recovery -> lease -> exact
  source resolution, and confirms a later deterministic failure remains blocked.

The PR fixes prospective behavior and recovery eligibility. Existing incorrect
accepted prose still requires a reviewed append-only correction; this change
must not be described as a completed historical cleanup or a production recovery.
Provider availability and independent PR review remain separate acceptance gates.
