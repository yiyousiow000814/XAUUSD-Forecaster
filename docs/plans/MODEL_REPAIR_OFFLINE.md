# Offline model repair experiment

This opt-in research CLI has no production caller. The JSON beside this plan is
the frozen experiment configuration. Recorded inputs belong to the preserved
SQLite online backup and immutable historical artifacts. Only an explicit new
research output directory may receive panels, calibrators and simulation files.
No schema initialization, activation, broker, network or service operation is
part of the experiment. Recovery PR #456 remains independent.

Flow: bounded read-only panel/artifact replay -> frozen folds -> past-matured
fit -> held-forward predictions -> one-position fixed-exit ledger -> report.
Only model parameters fitted before the test block are used within that block.
The pure Ridge owner is reused for the optional finite market-only retraining
control; it does not call any production training or generation entrypoint.

The simulator keeps ownership of an open position across fold and generation
changes. Missing outcomes remain unresolved, not zero profit. It records
overlap blocks and separately reports overlapping recommendation scores.
All clocks, labels and arithmetic have explicit units and source identities.
The same evidence is not an untouched validation set or broker account P&L.

Failure/recovery: missing artifacts permit only explicitly limited stored-output
research; incorrect artifact identity is rejected. Insufficient matured fitting
data yields unavailable predictions, not invented parameters. An interrupted
output is incomplete and may be rerun to a new directory; it cannot activate a
model. Production rollback compatibility is unchanged because this CLI is not
imported by any runtime owner. Existing analysis reports are never overwritten.

Verification covers real artifact arithmetic, wrong identity/order, chronological
fit/standardization, future-label independence, WAIT/cost accounting, generation
crossing, unresolved exits, deterministic output and the real CLI. A real frozen
panel run supplies the production-shaped research rehearsal, not deployment
evidence. Final exact source and input hashes accompany results. No new registry,
database, receipt authority or online scheduler is introduced.
