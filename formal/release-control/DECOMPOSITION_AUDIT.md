# Required Formal Verification Decomposition Audit

Date: 2026-08-30  
Affected pull request: #382  
Audited head: `36fc389083adcce144c4bd530217a527b1f529ec`

## Cancelled exploration evidence

The required Formal Verification run `33266814181` (job `99138239424`) was
cancelled instead of extending its budget. The TLC step ran for 17 minutes and
52 seconds on four workers. Its last progress record reported 152,882,397
generated states, 28,611,506 distinct states, 3,662,646 states left on the
queue, search depth 33, 3,554 MB heap, and 64 MB off-heap memory. The run had
not reached the liveness configuration.

Earlier superseded runs confirm that the growth was structural rather than a
temporary slow runner:

| Run | Elapsed | Generated | Distinct | Queued | Result |
|---|---:|---:|---:|---:|---|
| `33261601234` | 20 min | 79,093,880 | 12,769,626 | 4,158,249 | timed out |
| `33265210974` | about 35 min | 284,268,154 | 52,209,654 | 4,127,199 | superseded |
| `33266814181` | 17 min 52 sec | 152,882,397 | 28,611,506 | 3,662,646 | cancelled |

The largest independent dimensions were the release record (approximately
1,579 values), install/recovery record (approximately 178), News generation
record (approximately 33), detailed CPU record (approximately 44), path record
(approximately 13), Sync owner count, and mutable health. The CPU record added
artifact and receipt keys, receipt validity, evidence state and set, top-up
count, hard failure, qualification, reuse, and independent-stage state. CPU
accumulation actions were interleaved with install death/recovery, generation
staging/cleanup, Access receipt, and Switch/Observe actions even though those
families do not inspect individual CPU evidence. This multiplied independent
subsystems rather than proving their interfaces.

## Change contract

- **Change:** replace the required monolithic TLC exploration with bounded,
  compositional proof shards and a required parallel CI aggregator.
- **Invariant:** production CPU thresholds, quotas, qualification keys,
  provider-omission policy, hard-failure behavior, release gates, and every
  existing formal property remain unchanged.
- **Ownership:** each shard owns only its state family; a versioned shard
  manifest owns property assignment, implementation impact, and composition
  interfaces; the workflow owns bounded parallel execution and aggregation.
- **Impact graph:** modeled implementation or formal interface change -> shard
  selector -> selected matrix jobs -> immutable TLC tool -> result reports ->
  `Release Control TLC` aggregator.
- **Compatibility:** the PR check name remains exact; existing branch protection
  sees one required aggregator; the deep monolithic model may remain manual and
  non-blocking, but is not accepted as required evidence.
- **Recovery:** a failed, timed-out, skipped, or cancelled required shard makes
  the aggregator fail. A newer PR head cancels stale work. No partial shard pass
  authorizes release.
- **External truth:** GitHub scheduling latency is external, but each required
  TLC process has a five-minute hard job timeout. Tool versions and the tool
  digest remain pinned.
- **Rehearsal:** run every selected shard locally, run the selector and workflow
  contract tests, then require exact-head GitHub evidence with total workflow
  wall-clock at most five minutes.
- **Rollback:** the old monolithic required job can be restored from Git, but a
  timeout increase is not an authorized rollback or correction.

## Composition contract

1. The CPU evidence shard guarantees that `CpuQualified` implies no hard CPU
   failure, applicable fresh or exact-key reused qualification, and every
   required family quota satisfied.
2. The release-integration shard consumes only that guarantee and an abstract
   CPU state: `NOT_REQUIRED`, `PENDING`, `QUALIFIED`, or `HARD_FAILURE`.
3. The core release shard requires abstract CPU qualification before `PASSED`
   and before Promote; it never owns provider event identities or quotas.
4. An artifact/qualification-key change invalidates CPU applicability.
5. CPU-only recovery preserves independently accepted migration, directed, and
   semantic stages.
6. Provider pending is non-promotable but does not convert an otherwise valid
   Candidate into a deterministic Candidate regression.
7. Installation, News, Access, and Windows ownership shards prove their own
   contracts. Integration models reference only their abstract accepted or
   blocked result, never their internal state.

These are explicit assume/guarantee interfaces. The shard manifest and
implementation contract tests must assign every guarantee to a TLC property
and every consumer assumption to a matching integration invariant.

## Existing-property ownership matrix

| Existing property | Authoritative required shard |
|---|---|
| `TypeOK` | every shard, specialized to owned state |
| `AtMostOneProductionWriter` | core-release-safety |
| `PrepareVerifyKeepsStableSync` | core-release-safety |
| `CandidatePreparationPreservesStable` | core-release-safety |
| `VerificationWatermarkDoesNotRegress` | news-migration-safety |
| `StaleSupervisorIsFenced` | install-recovery-safety |
| `PassedIdentityIsExact` | release-integration-safety |
| `AcceptedEvidenceIsRequired` | release-integration-safety |
| `AccessEvidenceIsRequired` | access-evidence-safety |
| `InvalidAccessReceiptCannotPass` | access-evidence-safety |
| `AccessApprovalIsIdempotent` | access-evidence-safety |
| `RenewalRequiresContinuousAudit` | access-evidence-safety |
| `StaleMachineEvidenceCannotPass` | access-evidence-safety |
| `BrokenChainCannotRenew` | access-evidence-safety |
| `RenewalKeepsHumanRoot` | access-evidence-safety |
| `RenewalIsBounded` | access-evidence-safety |
| `PassedGatesAreSafe` | release-integration-safety |
| `HardFailuresBlock` | release-integration-safety |
| `UnrelatedDebtIsNotFailure` | release-integration-safety |
| `SwitchRequiresAcceptance` | core-release-safety |
| `StableUnchangedDuringSwitchAndObserve` | core-release-safety |
| `SingleTransaction` | core-release-safety |
| `ActiveLegacyEqualsCurrent` | news-migration-safety |
| `LegacyStableWritesRemainFenced` | news-migration-safety |
| `CurrentIdentitySetMatchesGeneration` | news-migration-safety |
| `FreshStagingIdentitySetMatchesGeneration` | news-migration-safety |
| `CurrentGenerationCannotBeCleaned` | news-migration-safety |
| `FreshStagingCannotBeCleaned` | news-migration-safety |
| `InvalidStagedLegacyCannotActivate` | news-migration-safety |
| `RecoveredActivationRequiresIndependentChecks` | install-recovery-safety |
| `ActiveRecoveredSupervisorIsSafe` | install-recovery-safety |
| `CpuQualificationRequiredForPass` | release-integration-safety |
| `ProviderPendingIsNotCandidateFailure` | release-integration-safety |
| `CpuRetryBudgetIsBounded` | cpu-evidence-safety |
| `DeficitRepairRequestBudgetIsBounded` | cpu-evidence-safety and cpu-evidence-liveness |
| `DeficitRepairSetIsFrozen` | cpu-evidence-safety and cpu-evidence-liveness |
| `QualifiedFamiliesAreNeverReplayed` | cpu-evidence-safety and cpu-evidence-liveness |
| `DeficitRepairCannotFabricateEvidence` | cpu-evidence-safety and cpu-evidence-liveness |
| `NoSecondDeficitRepairRound` | cpu-evidence-safety and cpu-evidence-liveness |
| `CpuHardFailureCannotQualify` | cpu-evidence-safety |
| `OutlierRequiresBoundedConfirmation` | cpu-evidence-safety and cpu-evidence-liveness |
| `NoSecondOutlierConfirmation` | cpu-evidence-safety and cpu-evidence-liveness |
| `OutlierConfirmationMatchesRequestShape` | cpu-evidence-safety and cpu-evidence-liveness |
| `RepeatedCpuPressureCannotQualify` | cpu-evidence-safety and cpu-evidence-liveness |
| `OriginalOutlierCannotBeErased` | cpu-evidence-safety and cpu-evidence-liveness |
| `ReusedCpuEvidenceMatchesArtifact` | cpu-evidence-safety |
| `CpuRecoveryPreservesIndependentStages` | cpu-evidence-safety and release-integration-safety |
| `StableChangesOnlyAfterObservation` | core-release-safety |
| `CpuEvidenceOnlyGrows` | cpu-evidence-safety |
| `ObservedFailureEventuallyRestoresPrevious` | core-release-liveness |
| `SwitchFailureEventuallyTerminates` | core-release-liveness |
| `TransactionEventuallyTerminates` | core-release-liveness |
| `AbandonedInstallEventuallySafe` | install-recovery-liveness |

New detailed CPU guarantees for controlled completion, every family quota,
reserve evidence, bounded targeted recovery, fresh/reused applicability, and
provider-pending classification belong to the CPU evidence shards. Their only
release-facing refinement is the abstract CPU state consumed by the integration
shard.

## Failure, compatibility, and test matrix

| Case | Required result |
|---|---|
| CPU provider evidence is partial or delayed | CPU remains pending; Candidate is not falsely failed; Promote remains blocked |
| CPU hard threshold failure | qualification is impossible; integration blocks release |
| exact qualification key matches | reuse may qualify only when the stored receipt is valid and quotas were satisfied |
| artifact key changes | applicability resets; unrelated accepted stages remain accepted |
| Switch or Observe fails | previous Stable is restored and the transaction terminates |
| installer dies at any modeled checkpoint | independent recovery facts are re-proven or recovery fails closed |
| legacy identity set is missing or extra | generation activation remains blocked |
| Access receipt is absent, stale, tampered, or wrong-key | acceptance remains blocked |
| required shard fails, skips, cancels, or times out | aggregator fails |
| newer PR head appears | stale workflow is cancelled |
| change is outside all modeled ownership | selector returns a bounded no-op and aggregator succeeds quickly |

The performance contract tests must reject any required formal timeout over five
minutes, a missing property assignment, a shard that reintroduces detailed CPU
state alongside install/News/Access/core lifecycle state, a non-parallel
required workflow, or a changed aggregator name.

## Local compositional measurement

The complete post-decomposition local run passed on 2026-08-30. TLC did not emit
an intermediate non-zero queue sample for these sub-second graphs, so maximum
queue depth is reported as unavailable rather than treating the final empty
queue as a measured peak.

| Shard | Elapsed (s) | Generated | Distinct | Max queue | Result |
|---|---:|---:|---:|---:|---|
| access-evidence-safety | 0.942 | 399 | 44 | unavailable | PASS |
| core-release-liveness | 1.015 | 34 | 20 | unavailable | PASS |
| core-release-safety | 0.908 | 34 | 20 | unavailable | PASS |
| cpu-evidence-liveness | 1.241 | 13,059 | 3,160 | unavailable | PASS |
| cpu-evidence-safety | 1.090 | 13,059 | 3,160 | unavailable | PASS |
| install-recovery-liveness | 1.229 | 33,077 | 5,928 | unavailable | PASS |
| install-recovery-safety | 1.059 | 33,077 | 5,928 | unavailable | PASS |
| news-migration-safety | 0.957 | 204 | 69 | unavailable | PASS |
| release-integration-safety | 0.971 | 208 | 62 | unavailable | PASS |

Sequential local execution took 10.176 seconds. The measured TLC ceiling under
the CI parallel composition is 1.229 seconds, excluding external GitHub job
scheduling and tool setup. Exact-head CI artifacts are the authoritative
workflow wall-clock evidence and include each shard's JSON report, TLC log, and
property set.
