# Training and Models

## 1. Purpose

This subsystem turns mature, eligible append-only evidence into a complete,
versioned Shadow generation without delaying ordinary five-minute decisions.

## 2. Execution boundary

`BackgroundTrainingOwner` is a background thread inside the Collector process.
It opens its own SQLite connection and persists a single-owner lease. A
temporary lease-keeper thread renews the lease during work. This is scheduling
isolation, not an independent OS process failure domain.

| Dimension | Current state |
|---|---|
| Ownership | One durable BackgroundTrainingOwner publishes materialization and model generations. |
| Boundary | Collector `THREAD`, separate SQLite connection; same Collector `PROCESS`. |
| Critical Path | Decision reads the last complete active generation only. |
| Bounded Work | 200-row materialization page and early `NOT_DUE`; publication is one generation. |
| Incremental | Dirty revisions, materialized cursor/state, coalesced request, versioned generation. |
| Failure Isolation | Training errors retry in background; process crash still affects Decision. |

## 3. Owner

`xauusd_forecaster.training_owner.BackgroundTrainingOwner` owns claim, lease,
retry, and completion. `training_v2.train_due_v2` owns the complete V2 training
and publication transaction. No decision request may publish a partial member.

## 4. Inputs and outputs

Inputs are matured valid outcomes, derived market/news snapshots, training
eligibility, frozen news coverage, event/source budgets, the materialization
contract hash, and retrain cadence. Outputs are durable materialized rows,
training receipts, Ridge artifacts, a manifest, model updates, and one complete
generation activation.

## 5. Durable state

- `background_training_owner_v1`: coalesced request, state, lease identity,
  retry/error, and completion times.
- `training_materialization_state_v1`: contract, cursor, mode, count, receipt,
  generation, and completion time.
- `materialized_training_rows_v1`: durable normalized training rows.
- `training_materialization_dirty_v1`: changed source IDs and revisions.
- Versioned model directories and manifests: immutable artifact payloads.
- SQLite model update and generation activation records: publication identity.

## 6. Current data flow

```text
outcome/reconciliation request -> durable coalesced owner row
  -> claim with process identity + renewable lease
  -> refresh dirty materialization page
  -> return NOT_DUE when cadence is unmet
  -> when due, consume complete materialized dataset
  -> train all required identities
  -> write versioned artifacts/manifest
  -> atomically append complete generation activation
```

The generation contains Market-only, Core/Broad news residual, and matching
Full identities under one contract.

## 7. Critical path

Healthy Decision consumes the last published valid complete generation and
only requests/wakes background training. It does not join the thread or wait
for reconciliation. Startup blocks only when no compatible generation exists,
because publishing decisions from an unknown generation would violate evidence
safety.

## 8. Bounded-work mechanisms

- `MATERIALIZATION_BATCH_ROWS` is 200 for incremental refresh.
- Dirty selection is ordered and page-limited.
- Retrain cadence returns `NOT_DUE` before model fitting.
- Required generation identities are a fixed set and publish as one unit.
- Cross-validation and feature sets are versioned and finite for a due retrain.

A real retrain deliberately consumes the complete materialized dataset. Its
cost grows with eligible history and must remain background work.

## 9. Incremental mechanisms

Database triggers mark affected source decision IDs dirty and increment a
dirty revision. The materializer updates only an observed page and deletes a
dirty item only if its revision still matches. A durable cursor admits new
ordered rows. Contract drift, source deletion, or late insertion marks an
explicit full rebuild, which replaces rows transactionally and records a new
rebuild generation.

## 10. Failure behavior

Training exceptions are truncated into the durable owner error, the request
returns to `PENDING` with a delay, and the loop survives. A failed or incomplete
artifact set cannot activate. Decisions retain the last valid generation.
Lease identity ambiguity fails closed rather than allowing two training owners.

## 11. Restart/recovery behavior

The owner reopens the ledger, examines persisted state, and claims pending work.
A running lease is reclaimed only after expiry and proof that the recorded OS
process identity is dead. Requests received during work coalesce into one
rerun. Dirty revisions and publication records survive restart.

## 12. Entry points

- Owner creation and wake: `scripts/run_forward_collector.py`
- Training owner: `xauusd_forecaster/training_owner.py`
- One-shot repair that may seed eligible evidence:
  `scripts/run_evidence_repair_v2.py`

## 13. Core modules

- `xauusd_forecaster/training_v2.py`: materialization, fit, manifest and
  complete-generation publication.
- `xauusd_forecaster/training_owner.py`: durable background lifecycle.
- `xauusd_forecaster/training.py`: shared market/news training operations.
- `xauusd_forecaster/ridge.py`: bounded Ridge artifact implementation.
- `xauusd_forecaster/news_contracts.py`: current complete-generation contract.
- `xauusd_forecaster/inference_v2.py`: active-generation validation and reads.
- `xauusd_forecaster/execution_learning.py`: separate execution models.

## 14. Relevant tests

`tests/test_training_owner.py`, `tests/test_evidence_integrity_v2.py`,
`tests/test_forward_only.py`, `tests/test_execution_costs.py`, and
`tests/test_production_shape.py` protect owner recovery, dirty materialization,
causality, complete generations, publication, and production shape.

## 15. Authoritative contracts/specs

- [System Boundaries](../contracts/SYSTEM_BOUNDARIES.md)
- [Forward-only Evidence](../contracts/FORWARD_ONLY.md)
- [Evidence Lanes](../contracts/EVIDENCE_LANES.md)
- [Learning Curves](../specs/LEARNING_CURVES.md)

## 16. Known current gaps

Training is not process-isolated from Decision. `training_v2.py` combines
materialization, weighting, cross-fit, artifact creation, and publication, and
the flat import graph couples it to shared ledger/news modules. Full due
retraining remains history-growing by design.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
