# Finite market-method batch 1

This plan is frozen before new scoring under Master section 10. It does not
invalidate or relabel the b501/ca9 runs. The adjacent JSON is the sole numerical
configuration. Twenty new fits maximum, one numerical thread, four unchanged
retrospective blocks, no database queries and no production imports/activation.

## Hypotheses and timing

Admission inputs are the eight retained market features. Their historical owner
`market.build_market_snapshot` filters event and receipt times at decision time
before computing returns, imbalance, speed and volatility (`market.py:219-270`).
The panel's copied snapshot/hash binds those original values. The nine extra
features are deterministic transforms: each of five returns / contemporaneous
U5, realized volatility / U5, sign(return5) * sign(return30), and sine/cosine of
UTC decision hour (including minutes). No label, post-entry quantity, event
consensus, future market regime or full-sample normalizer enters these features.
Ridge normalization and boosting split quantiles are trained on past rows only.

Pre-score input check: all first-fold eligible rows have missing raw source
receipt timestamps in the panel. The original snapshot hash is retained but its
receipt row is absent from the cached source-market join. Therefore original
feature values are byte-bound; raw receipt causality is writer-contract-
conditional, not independently verified for every row. No timestamp is filled
in, no old panel is changed, and strict evidence-only replay stays UNKNOWN.
The initial development attempt rejected this missing field before any fit.
A separately recorded baseline lookup was initially held because SHM mtime
changed while database identity and empty WAL remained unchanged. The
coordinator reconciled the SHM change as read-only reader-index activity. The
authorized indexed lookup completed in 0.117 seconds: 5,813 same-clock ID
matches but zero exact source-hash matches. A hash-predicate full scan was
rejected by EXPLAIN and never executed. Same-clock IDs are not substituted for
exact hash authority; all original panel values remain unchanged.
These facts do not change any feature, fold, parameter, target or action rule.

Hypotheses: state normalization/nonlinearity can expose conditional predictive
structure absent from the original linear representation; temporal calibration
can correct out-of-time scale; signed 30-minute continuation and 5-minute
reversion are fixed mechanistic controls, not hindsight regime selectors.
All use the existing post-cost action owner, without adding a duplicate cost
gate. Kill conditions are the JSON's coverage, cost, concentration and timing
requirements. No cTrader/live alignment is claimed.

Mathematical decision-time feature causality is distinct from historical
publication: strict visibility remains UNKNOWN. The existing conditional
collector-completion bound plus 100 ms for hypothetical methods is used, with
0/1/5 second fixed delays and the same archived Bid/Ask extract. Label marker
maturity also remains historical-writer-conditional, never upgraded to commit
visibility. New timing instrumentation belongs to the Collector transaction
owner and its consumer, separately reviewed by the coordinator, not this PR.

## Change and failure contract

The optional offline boundary is frozen panel + prior artifacts + quote extract
-> bounded fit -> pure existing causal replay -> new ledgers/daily report.
The new CLI is the sole research-output writer; there is no durable runtime
state, provider call, service owner or secret. Old artifacts are read-only.
Existing research-path containment applies to inputs and outputs. A new output
directory is required; partial output after failure is never PASS. Retry uses
a new directory and preserved exact inputs; no old report is overwritten.
Old/new production combinations are unaffected because production imports none
of these opt-in modules. Rollback means retain old research outputs, not change
production. Only the coordinator may merge; no install or broker actions.

One serial bounded pass over already copied closed quote archives adds UTC
boundary marks, without copying archives or reading production. It verifies
their saved bytes, groups same-receipt conflicts before quality, and records
missing marks rather than interpolating. All scheduled calendar days appear;
unknown broker sessions and overnight marks are distinct from zero-exposure
days. Commission is booked once at entry; MTM increments telescope to the
realized Bid/Ask result. No dollar equity or intraday tick drawdown is invented.

Tests cover past-only fit/quantiles, deterministic serialized inference, feature
independence from future labels, strict costs, cross-midnight MTM reconciliation,
missing marks/unresolved exposure and the actual Python CLI filesystem boundary.
The rehearsal executes the entire frozen opportunity set against the retained
quote extract. Numerical results, source/input hashes and all failure states are
stored outside source. Old source-bound reports keep their old identities.

For reruns reuse the actual derived marks, not another archive pass:
`--marks <method-batch1-marks-01/daily_marks.json> --marks-sha256
8eae04b149533fbc67ea831c0e029b0e68b7b8393328f49970c04f1c68df6d42`.
The explicit expected digest is the prior pass's recorded output identity,
never calculated from the current candidate input as its own trust anchor.
