# TLC artifact recovery verification

## Scope and identity

Verified the explicitly approved official asset, not the latest release. The
rolling prerelease caused the prior URL/pin mismatch. The sole lock is
`formal/tools/tlc/tool-lock.json`; the reviewed JAR is retained beside it.
The build changed to TLC `2026.09.04.170753 (rev: b123b22)`. No older PASS was reused.
Manifest source fields are self-reported; independent source-build proof and
attestation remain UNKNOWN (the official digest attestation API returned 404).

Windows JDK: Temurin 21.0.12.1+1. Local execution limited visible processors to
two; CI budgets and worker-selection behavior are unchanged. Source HEAD was
`ebb6744ab903bf90cba6b36915fd01072a10cbc5` with `source_dirty=true`. This is WIP
verification, not exact-head merge/release authority. Every JSON result records
the complete tool identity and module/config/dependency hashes.

## Results

Tool and formal contract tests: 23 passed in 1.21 seconds. Cold and hot caches
used identical reviewed bytes; corrupt cache/source, HTML, truncated input,
unavailable source and unpinned adjacent dependencies failed before Java.
No network/latest fallback exists in the runner. Windows and CI use the same
lock/resolver. Linux exact-head CI subsequently verified every required shard
on `a6b8ff68c9f046d8c5f9a361766424238ae3332b` in run `33949141491`:
workflow start 06:11:31 UTC, completed 06:12:02 UTC (31 seconds). All 14 shards
and the required aggregator passed. The same clean local head also passed all
14 shards with unchanged state counts. Later source changes require their own
exact-head gates; this recorded run does not authorize another revision.

| Required shard | Seconds | Generated | Distinct | Result |
|---|---:|---:|---:|---|
| cpu-evidence-safety | 2.344 | 395795 | 98172 | PASS |
| cpu-evidence-liveness | 5.175 | 395795 | 98172 | PASS |
| release-integration-safety | 1.605 | 150665 | 21824 | PASS |
| core-release-safety | 1.3 | 34 | 20 | PASS |
| core-release-liveness | 1.461 | 34 | 20 | PASS |
| recovery-hotfix-safety | 1.289 | 148 | 82 | PASS |
| recovery-hotfix-liveness | 1.312 | 40 | 23 | PASS |
| release-runtime-read-model-safety | 6.403 | 11448211 | 86490 | PASS |
| watchdog-singleton-safety | 1.299 | 171 | 38 | PASS |
| watchdog-singleton-liveness | 1.197 | 171 | 38 | PASS |
| install-recovery-safety | 1.404 | 44697 | 7265 | PASS |
| install-recovery-liveness | 1.578 | 44697 | 7265 | PASS |
| news-migration-safety | 1.246 | 204 | 69 | PASS |
| access-evidence-safety | 1.83 | 682693 | 28268 | PASS |

All 14 manifest entries were executed. Summed shard execution was 29.443 seconds;
this is not GitHub workflow wall-clock. Maximum queue depth was not emitted by
these short runs. State counts are descriptive, never acceptance targets.
InstallRecovery differs from the old model because the incident WIP explicitly
adds the zero-owner degraded baseline and mutex handoff. No property was removed
or changed to accommodate the tool update. Comparisons with old-tool state counts
are unavailable because no trusted old pinned binary was recovered.

## Remaining incident work

The independent production database copy passed five real-process crash/restart
checkpoints: after snapshot, after decision and during v2 left zero clock rows;
after commit/before checkpoint and exact restart retained one complete clock.
The original snapshot-only row stayed unchanged. The separate new-clock replay
performed zero additional writes. These are offline fixture clocks, not production
progress. An initial rehearsal assertion used one timestamp spelling across old
tables with different encodings; corrected evidence uses exact table identities.

An installed staged Watchdog child produced its own QUIESCED heartbeat and exact
owner receipt through the real launcher, then exited after activation withdrawal.
The preserved process stand-ins remained alive. This is not ACTIVE supervision
or a complete production recovery rehearsal. The machine-global scheduler and
provider boundaries must remain isolated before extending that rehearsal.

This tool result is not SOURCE_READY or PRODUCTION_RECOVERED. Complete the real
zero-Watchdog/zero-Collector service rehearsal, source/CI closure, formal production
recovery and separate source-first efficiency acceptance under the existing
incident authorization. Do not infer production progress from simulated clocks.
