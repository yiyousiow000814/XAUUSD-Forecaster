# Repair and Evidence Lanes

## Frozen epochs

- `COLLECTION_EPOCH` is the original `FORWARD_EPOCH`. It is never reset.
- `EVALUATION_EPOCH_V2` is appended once when the repaired runtime is enabled.
- One repair batch uses one immutable `source_cutoff`.

## Lanes

### LEGACY_ENGINEERING

V1 snapshots, predictions, outcomes, eligibility rows, and model records are
retained byte-for-byte for engineering audit. They do not contribute to V2
Live OOS metrics.

### REPAIRED_SEED

The repair reads retained raw quote receipts, news revisions, first-seen
times, annotations, and frozen U5 warm-up sources. It appends V2 market
features, eligible-news features, received-time executable labels, and
training eligibility. Seed rows may train Preview and Shadow models, but they
are never presented as predictions those new models made in the past.

### LIVE_OOS

A row is Live OOS only when its Decision is at or after
`EVALUATION_EPOCH_V2` and the model artifact already existed before that
Decision. The immutable order is prediction, later outcome, score, then V2
training eligibility.

## Append-only migration

The migration creates new tables and indexes, appends one evaluation epoch,
one repair receipt, derived evidence, and lane assignments. It executes no
`UPDATE` or `DELETE` against legacy evidence. Every unreconstructable row is
retained with an explicit reason. A SQLite online backup, integrity result,
database hash, Git commit, source hash, output hash, and deterministic rerun
receipt are stored locally.

The local append-only SQLite database is the source of truth. Sites D1 stores
a replaceable read-only dashboard snapshot and is not described as the full
immutable history.
