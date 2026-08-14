# Frozen Phase 2A Replay Contract

Status: frozen before the first full-history Phase 2A dataset build.

This contract governs historical Replay only. Phase 2A must not train a model,
add external data, change U5, inspect a candidate policy, or write historical
rows into the Shadow Prediction Ledger.

## Versions

| Surface | Version |
|---|---|
| Replay | `xau-replay-v1` |
| Features | `xau-features-v1` |
| U5 | `finite-memory-u5-v5` |
| Label | `executable-fixed-30m-v2` |
| Schema | `xauusd.phase2a.replay.v1` |

Changing any rule below requires a new version. A result must never redefine a
version in place.

## Clock and event order

- All source and computation timestamps use UTC. Malaysia time is display-only.
- Decision events occur at every UTC timestamp divisible by five minutes,
  beginning at the first such boundary within source coverage and ending at
  the last such boundary within source coverage.
- At one timestamp, an old 30-minute Shadow signal matures first. Completed
  quotes with source time `<= decision_time` then update features. The new
  decision is created next. Its entry must use a quote strictly after the
  decision event.
- Phase 2A labels every clock event independently. The separate 30-minute
  active-signal lock is applied only in later policy replay; it cannot delete
  rows from the common research clock.

## Feature availability

- Every feature source timestamp must be `<= decision_time`.
- A completed M1 observation is the final valid Tick in the UTC minute. At a
  decision boundary `t`, the latest admissible M1 observation belongs to
  minute `t-1`.
- Features use calendar-minute windows with no interpolation and no silent
  forward-fill. Every required minute in the longest 240-minute feature window
  must be present.
- A completed minute whose final quote is more than 60 seconds before the
  minute end is stale and makes the decision invalid.
- Session, weekend, holiday, and maintenance gaps are not special-cased by a
  calendar. They appear as missing UTC minutes and make the affected feature
  window invalid.
- Time-of-day is an admissible live market-time feature. Year, month, date,
  weekday, and day-of-week are report-only fields added after actions and
  labels are frozen.

## Frozen XAU-only features

The feature set contains no macro, news, technical-indicator bundle, learned
feature selection, or future path field:

1. midpoint log returns over 5, 15, 30, and 60 calendar minutes;
2. realized volatility over 15, 30, 60, and 240 calendar minutes;
3. midpoint high-low log range over 15, 30, and 60 minutes;
4. path efficiency over 15, 30, and 60 minutes;
5. current spread/mid, 15-minute median spread/mid, and 60-minute p95;
6. Tick counts over 1, 5, and 30 minutes;
7. signed quote-change imbalance over 1, 5, and 30 minutes;
8. U5-normalized distance to the 60-minute high and low;
9. UTC minute-of-day sine and cosine;
10. frozen U5.

Price movements and ranges are dimensionless log values. Distance features
are divided by decision-frozen U5. Tick count and imbalance retain their
natural units.

## U5

U5 remains `finite-memory-u5-v5`:

```text
A_t = max over j=0..30 of abs(log(mid_(t-30+j) / mid_(t-30)))
Q_t = rolling 0.99 quantile of the latest 10,000 completed A observations
K_t = 2 * spread_close_t / mid_close_t
U5_t = max(Q_t, A_t, K_t)
```

It is computed only from completed observed M1 rows, exactly as frozen. A
decision cannot be valid until 10,000 completed A observations exist and its
240-minute calendar feature window is complete. U5 is a scale and report field;
it does not select Long or Short.

## Executable entry, exit, and path

- Primary entry is the first valid non-crossed Bid/Ask Tick with
  `entry_time > decision_time` and `entry_time <= decision_time + 20s`.
- Target exit time is `entry_time + 30m`.
- Exit is the first valid non-crossed Bid/Ask Tick with
  `exit_time >= target_exit_time`.
- An exit delay greater than 60 seconds makes the primary label invalid.
- A maximum quote-to-quote gap greater than 60 seconds between entry and exit
  makes the primary label invalid.
- No quote is interpolated. Closed-market and maintenance events remain rows
  with an explicit invalid reason.
- Primary MFE/MAE use every observed executable quote from entry through exit.
- Fixed latency stress reruns quote selection at additional 1, 5, and 20
  seconds after both the primary entry eligibility time and target exit time.
  These three delays are diagnostics and cannot be selected from outcomes.

## Quote-return and D/C labels

No commission or synthetic slippage is subtracted in Phase 2A:

```text
L = log(exit_bid / entry_ask)
S = log(entry_bid / exit_ask)
D = (L - S) / 2
C_spread = -(L + S) / 2
```

Every valid row must satisfy, within floating-point tolerance:

```text
L = D - C_spread
S = -D - C_spread
MFE_long >= L >= MAE_long
MFE_short >= S >= MAE_short
```

The dataset stores raw log values and U5-normalized values. It also stores the
raw fields needed to evaluate future fixed commission and slippage scenarios.
Phase 2A makes no true after-cost EV or profitability claim.

## Invalid reason priority

Every row has one primary `invalid_reason` and may have additional reason codes.
Priority is frozen as:

1. `U5_WARMUP`
2. `FEATURE_WINDOW_INCOMPLETE`
3. `FEATURE_SOURCE_STALE`
4. `NO_ENTRY_WITHIN_20S`
5. `NO_EXIT_QUOTE`
6. `EXIT_DELAY_GT_60S`
7. `PATH_GAP_GT_60S`
8. `NONFINITE_VALUE`

No invalid row may be silently removed from counts. Invalid rows retain their
clock, versions, source provenance where available, and reason codes.

## Source provenance and deterministic output

- The source authority is the repository-local XAUTK002 manifest and its daily
  files.
- Every row records feature, entry, and exit source hashes when available, plus
  a combined source hash.
- The manifest records Git HEAD, dirty-worktree state, hashes of active Replay
  source files, source manifest hash, schema/feature/U5/label/Replay versions,
  row counts, invalid counts, partitions, and canonical dataset hash.
- Canonical dataset hash is computed over ordered schema values, independent of
  Parquet metadata. Partition file SHA-256 hashes are also recorded.
- Repeating the same build with the same source and code snapshot must produce
  the same row counts, invalid counts, canonical dataset hash, and row values.

## Storage isolation

- Historical outputs: `src/XAUUSD-Forecaster/.local/replay/`.
- Shadow state: `src/XAUUSD-Forecaster/.local/shadow/`.
- Historical data uses year/month-partitioned Parquet and separate QA artifacts.
- Shadow uses its own database and must never attach or mutate a historical
  Replay dataset.
- Generated data, caches, manifests, and reports are ignored by Git.

## Required QA gates

Before any model work:

- all feature source timestamps are no later than decisions;
- entries and exits satisfy the frozen timing inequalities;
- D/C and MFE/final/MAE identities pass;
- source anomalies and invalid reasons are reported by UTC day and hour;
- two complete builds have identical canonical hashes and counts;
- deterministic sample days match between Tick-streaming and batch aggregation,
  features, U5, and labels;
- existing module tests, repository layout, research policy ratchet, and
  `git diff --check` pass.

Phase 2A stops after these artifacts. It does not train Ridge or any other model.
