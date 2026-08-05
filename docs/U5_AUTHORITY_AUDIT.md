# U5 Authority Audit

## Authority

The repository authority is `finite-memory-u5-v5` (`SCALE-022`) in the RMG
formula registry and `alpha2_universal_risk_unit_finite_memory_v5.json`.
`SYSTEM_CONTRACT.md`, `REPLAY_CONTRACT.md`, and `u5_state.py` use the same
formula:

```text
A_t = max(abs(log(mid_(t-j) / mid_(t-30)))) for j=0..30
Q_t = rolling 0.99 quantile of the latest 10,000 completed A observations
K_t = 2 * (ask_close_t - bid_close_t) / mid_close_t
U5_t = max(Q_t, A_t, K_t)
```

The Forward implementation version is
`finite-memory-u5-v5-contiguous-m1`. The suffix records an enforced property
of the authority, not a different formula: `A_t` exists only when all 31 M1
closes are consecutive.

## Findings

1. The historical warm-up receipt identifies 12 repository-local XAUTK002
   files and 13,766 M1 rows. They initialize the rolling authority only and
   remain `WARMUP_ONLY`.
2. The Forward state used the correct quantile, excursion, and quote-cost
   floor.
3. The Forward state did not clear its current 31-minute path after a missing
   minute. This could compress separated observations into one apparent
   30-minute path.
4. V2 clears only the current path after a gap. It preserves the last 10,000
   completed, valid excursions and requires another 31 consecutive minutes
   before adding a new excursion.

## Repair rule

Repaired U5 is recomputed from the frozen XAUTK002 warm-up files and retained
raw Forward quotes. An old stored U5 value is never used to infer a V2 value.
Each derived snapshot stores `u5_version`, source evidence hash, repair batch,
and deterministic output hash. U5 remains a scale and report unit; it cannot
vote for `LONG` or `SHORT`.
