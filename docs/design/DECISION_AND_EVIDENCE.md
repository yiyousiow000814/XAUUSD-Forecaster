# Decision and Evidence

## 1. Purpose

This subsystem freezes one point-in-time XAUUSD forecast every eligible
five-minute boundary and appends its fixed 30-minute executable outcome later.
It is research evidence, not an order path.

## 2. Execution boundary

The owner runs on the Collector process main thread. Quote production is a
separate cTrader CLI/robot process. News collection and training are threads in
the Collector process, but their work is not part of the ordinary decision
call path.

| Dimension | Current state |
|---|---|
| Ownership | Collector alone appends decisions and outcomes. |
| Boundary | Collector `PROCESS`; decision/settlement on its main `THREAD`; quote bridge is another `PROCESS`. |
| Critical Path | Quote/session validation, frozen evidence, inference, and append. |
| Bounded Work | Only due live grids; fixed 30-minute horizon; bounded current inputs. |
| Incremental | Last decision clock, append-only IDs, outcome due set, exit checkpoints. |
| Failure Isolation | Dashboard, Cloudflare, training, and optional AI work are not decision dependencies. |

## 3. Owner

`scripts/run_forward_collector.py` owns the runtime loop.
`xauusd_forecaster.forward_engine.ForwardEngine` owns a single frozen decision
and settlement transition. `ForwardLedger` enforces append-only persistence.

## 4. Inputs and outputs

Inputs are broker Bid/Ask receipts, the broker session heartbeat, U5 state, the
latest complete active generation, and point-in-time news coverage/evidence.
Outputs are immutable snapshots, decision events, model predictions, outcomes,
scores, exit predictions, and background-training requests.

## 5. Durable state

- Daily quote JSONL and `market-session.json` are quote/session authority.
- `forward-evidence.sqlite3` is forecast evidence authority.
- `forward-epoch.json` records the immutable collection epoch.
- `u5-state.json` and execution checkpoint/model files are local runtime state.
- Backups are recovery copies; they do not become a second live writer.

## 6. Current data flow

```text
Quote Bridge PROCESS -> JSONL/session file
  -> JsonlMarketProvider -> frozen market snapshot
  + point-in-time news snapshot + active generation
  -> append decision and predictions
  -> receive later quotes -> append outcome and score
  -> request background training
```

The quote bridge writes no order, position, account, or volume operation.

## 7. Critical path

The ordinary path validates a current executable quote and session, freezes
features and evidence, reads the last valid complete generation, and appends
one decision. It must not wait for source polling, annotation, training,
reconciliation, Dashboard history, sync, Cloudflare, or an LLM.

When no compatible active generation exists at startup, synchronous
reconciliation is a deliberate fail-closed bootstrap exception. With a valid
generation, reconciliation is scheduled in the background.

## 8. Bounded-work mechanisms

- The clock loop considers only due five-minute boundaries and never fabricates
  missed historical decisions.
- The outcome contract has one fixed 30-minute horizon and bounded entry expiry.
- Critical status exposes only the recent 90-minute decision window.
- Current U5 context reads a fixed 2,016-sample window at the Dashboard boundary.
- Daily quote archive and SQLite backup happen once per day outside the
  per-decision append.

One appended decision and outcome have fixed work, but restart catch-up loops
over every five-minute candidate since the last persisted decision without an
explicit item cap. It skips ineligible past grids rather than creating them;
the iteration itself is a current bounded-work gap.

## 9. Incremental mechanisms

The loop resumes from the maximum persisted decision time. Append-only IDs and
unique constraints make duplicate transitions fail closed. Outcome and exit
checkpoint work advance from persisted decisions and receipts. The collection
epoch and evaluation epoch are immutable markers.

## 10. Failure behavior

Missing/stale quotes or a closed/crossing session skip an ineligible grid rather
than invent evidence. Missing action-bearing data produces `WAIT` or explicit
unavailability according to its contract. Optional subsystem failure remains
visible but does not erase valid prior state.

## 11. Restart/recovery behavior

Restart reads the last decision clock, existing generation activation, U5
state, and append-only ledger. It skips non-live historical gaps. Quote files
are daily append-only and readable while the bridge writes. Backups are created
for local recovery; recovery must preserve epochs and immutable records.

## 12. Entry points

- `ctrader/XauusdForwardQuoteBridge/run_live_quote_bridge.ps1`
- `ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs`
- `scripts/run_forward_collector.py`
- One-shot: `scripts/initialize_u5_warmup.py` and
  `scripts/run_evidence_repair_v2.py`

## 13. Core modules

- `xauusd_forecaster/forward_engine.py`: one decision/outcome orchestration.
- `xauusd_forecaster/forward_ledger.py`: append-only local persistence surface.
- `xauusd_forecaster/market.py`: JSONL provider and frozen market snapshot.
- `xauusd_forecaster/market_session.py`: broker/weekly-session eligibility.
- `xauusd_forecaster/live_v2.py`: complete V2 decision/outcome append.
- `xauusd_forecaster/inference_v2.py`: complete-generation inference.
- `xauusd_forecaster/evidence_v2.py`: repaired/live evidence contracts.
- `xauusd_forecaster/execution_learning.py`: exit checkpoints and execution
  learning evidence.

## 14. Relevant tests

`tests/integration/test_forward_only.py`, `tests/decision/test_selection.py`,
`tests/test_quotes_and_labeling.py`, `tests/test_market_session.py`,
`tests/test_ledger.py`, `tests/evidence/test_integrity_v2.py`, and
`tests/test_execution_costs.py` cover point-in-time, append-only, executable
quote, label, and failure semantics.

## 15. Authoritative contracts/specs

- [System Boundaries](../contracts/SYSTEM_BOUNDARIES.md)
- [Forward-only Evidence](../contracts/FORWARD_ONLY.md)
- [Evidence Lanes](../contracts/EVIDENCE_LANES.md)
- [Product](../specs/PRODUCT.md)

## 16. Known current gaps

The runtime entry script still contains decision-loop, settlement, checkpoint,
maintenance, startup reconciliation, and owner lifecycle logic. The shared
`ForwardLedger` module has high fan-in across otherwise distinct owners. These
are structural gaps, not permission to split them in this PR. Restart catch-up
work also grows with downtime because its candidate scan has no explicit page
or item cap.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
