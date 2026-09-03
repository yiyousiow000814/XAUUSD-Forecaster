--------------------------- MODULE ReleaseIntegration ---------------------------
EXTENDS TLC

CpuStates == {"NOT_REQUIRED", "PENDING", "QUALIFIED", "HARD_FAILURE"}
Keys == {"A", "B"}
Stages == {"MIGRATION", "DIRECTED", "SEMANTIC"}
SupersessionStates == {"UNCHECKED", "FOUND", "NOT_APPLICABLE",
                       "REUSE_UNAVAILABLE", "FAILED"}

VARIABLES phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
          qualificationKey, stages, candidateExact, hardSafe, changedSafe,
          candidateRegression, stableDebt, evidenceState, behaviorKeyMatches,
          leaseFresh, dependencyDigest, frozenDigest, transaction,
          immutableIdentityPreserved, productionMutation, supersessionState,
          supersessionReuseApplied, freshValidationStarted
vars == <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
          qualificationKey, stages, candidateExact, hardSafe, changedSafe,
          candidateRegression, stableDebt, evidenceState, behaviorKeyMatches,
          leaseFresh, dependencyDigest, frozenDigest, transaction,
          immutableIdentityPreserved, productionMutation,
          supersessionState, supersessionReuseApplied, freshValidationStarted>>

Init ==
    /\ phase = "PREPARE" /\ gate = "UNTESTED" /\ releaseAccepted = FALSE
    /\ cpuRequired = TRUE /\ cpuState = "NOT_REQUIRED"
    /\ artifactKey = "A" /\ qualificationKey = "A"
    /\ stages = {} /\ candidateExact = TRUE /\ hardSafe = TRUE /\ changedSafe = TRUE
    /\ candidateRegression = FALSE /\ stableDebt = FALSE
    /\ evidenceState = "MISSING" /\ behaviorKeyMatches = FALSE /\ leaseFresh = FALSE
    /\ dependencyDigest = "A" /\ frozenDigest = "NONE" /\ transaction = FALSE
    /\ immutableIdentityPreserved = TRUE /\ productionMutation = FALSE
    /\ supersessionState = "UNCHECKED" /\ supersessionReuseApplied = FALSE
    /\ freshValidationStarted = FALSE

AcceptIndependentStages ==
    /\ stages # Stages
    /\ stages' = Stages
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, candidateExact, hardSafe, changedSafe,
                    candidateRegression, stableDebt, evidenceState, behaviorKeyMatches,
                    leaseFresh, dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
CpuPending ==
    /\ stages = Stages /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ cpuState' = "PENDING"
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt, evidenceState,
                    behaviorKeyMatches, leaseFresh, dependencyDigest, frozenDigest,
                    transaction, immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
CpuQualifies ==
    /\ stages = Stages
    /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ qualificationKey = artifactKey
    /\ cpuState' = "QUALIFIED"
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt, evidenceState,
                    behaviorKeyMatches, leaseFresh, dependencyDigest, frozenDigest,
                    transaction, immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
CpuHardFails ==
    /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ cpuState' = "HARD_FAILURE"
    /\ gate' = "FAILED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, artifactKey, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt, evidenceState, behaviorKeyMatches, leaseFresh,
                    dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
ChangeArtifact ==
    /\ phase = "PREPARE"
    /\ artifactKey' \in Keys \ {artifactKey}
    /\ cpuState' = "NOT_REQUIRED" /\ gate' = "UNTESTED" /\ releaseAccepted' = FALSE
    /\ evidenceState' = "MISSING" /\ behaviorKeyMatches' = FALSE
    /\ leaseFresh' = FALSE /\ frozenDigest' = "NONE"
    /\ UNCHANGED <<phase, cpuRequired, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt, dependencyDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
RecordStableDebt ==
    /\ ~stableDebt /\ stableDebt' = TRUE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, evidenceState,
                    behaviorKeyMatches, leaseFresh, dependencyDigest, frozenDigest,
                    transaction, immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
IntroduceRegression ==
    /\ phase = "PREPARE"
    /\ ~candidateRegression /\ candidateRegression' = TRUE
    /\ gate' = "FAILED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, cpuState, artifactKey, qualificationKey,
                    stages, candidateExact, hardSafe, changedSafe, stableDebt,
                    evidenceState, behaviorKeyMatches, leaseFresh, dependencyDigest,
                    frozenDigest, transaction, immutableIdentityPreserved,
                    productionMutation, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>

BuildCompleteEvidence ==
    /\ phase = "PREPARE" /\ evidenceState = "MISSING"
    /\ evidenceState' = "VALID" /\ behaviorKeyMatches' = TRUE /\ leaseFresh' = TRUE
    /\ dependencyDigest' = artifactKey /\ immutableIdentityPreserved' = TRUE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe, changedSafe,
                    candidateRegression, stableDebt, frozenDigest, transaction,
                    productionMutation, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>
ExpireLease ==
    /\ phase = "PREPARE" /\ leaseFresh /\ leaseFresh' = FALSE
    /\ gate' = "UNTESTED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, cpuState, artifactKey, qualificationKey,
                    stages, candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt, evidenceState, behaviorKeyMatches, dependencyDigest,
                    frozenDigest, transaction, immutableIdentityPreserved,
                    productionMutation, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>
TamperEvidence ==
    /\ phase = "PREPARE" /\ evidenceState = "VALID"
    /\ evidenceState' = "TAMPERED" /\ hardSafe' = FALSE
    /\ gate' = "FAILED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, cpuState, artifactKey, qualificationKey,
                    stages, candidateExact, changedSafe, candidateRegression,
                    stableDebt, behaviorKeyMatches, leaseFresh, dependencyDigest,
                    frozenDigest, transaction, immutableIdentityPreserved,
                    productionMutation, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>
ChangeBehaviorKey ==
    /\ phase = "PREPARE" /\ behaviorKeyMatches
    /\ behaviorKeyMatches' = FALSE /\ gate' = "UNTESTED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, cpuState, artifactKey, qualificationKey,
                    stages, candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt, evidenceState, leaseFresh, dependencyDigest,
                    frozenDigest, transaction, immutableIdentityPreserved,
                    productionMutation, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>
ReadAndPlan ==
    /\ productionMutation' = FALSE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe, changedSafe,
                    candidateRegression, stableDebt, evidenceState, behaviorKeyMatches,
                    leaseFresh, dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, supersessionState,
                    supersessionReuseApplied, freshValidationStarted>>

ClassifySupersession ==
    /\ supersessionState = "UNCHECKED"
    /\ supersessionState' \in SupersessionStates \ {"UNCHECKED"}
    /\ supersessionReuseApplied' = FALSE /\ freshValidationStarted' = FALSE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState,
                    artifactKey, qualificationKey, stages, candidateExact,
                    hardSafe, changedSafe, candidateRegression, stableDebt,
                    evidenceState, behaviorKeyMatches, leaseFresh,
                    dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation>>
ContinueFreshValidation ==
    /\ supersessionState \in {"NOT_APPLICABLE", "REUSE_UNAVAILABLE"}
    /\ ~freshValidationStarted
    /\ freshValidationStarted' = TRUE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState,
                    artifactKey, qualificationKey, stages, candidateExact,
                    hardSafe, changedSafe, candidateRegression, stableDebt,
                    evidenceState, behaviorKeyMatches, leaseFresh,
                    dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied>>
ApplySupersessionReuse ==
    /\ supersessionState = "FOUND" /\ ~supersessionReuseApplied
    /\ supersessionReuseApplied' = TRUE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState,
                    artifactKey, qualificationKey, stages, candidateExact,
                    hardSafe, changedSafe, candidateRegression, stableDebt,
                    evidenceState, behaviorKeyMatches, leaseFresh,
                    dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, freshValidationStarted>>
Pass ==
    /\ phase = "PREPARE" /\ stages = Stages
    /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ candidateExact /\ hardSafe /\ changedSafe /\ ~candidateRegression
    /\ qualificationKey = artifactKey
    /\ evidenceState = "VALID" /\ behaviorKeyMatches /\ leaseFresh
    /\ immutableIdentityPreserved
    /\ phase' = "PASSED" /\ gate' = "PASSED" /\ releaseAccepted' = TRUE
    /\ UNCHANGED <<cpuRequired, cpuState, artifactKey, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt, evidenceState, behaviorKeyMatches, leaseFresh,
                    dependencyDigest, frozenDigest, transaction,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>
BeginPromote ==
    /\ phase = "PASSED" /\ gate = "PASSED" /\ releaseAccepted
    /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ evidenceState = "VALID" /\ behaviorKeyMatches /\ leaseFresh
    /\ phase' = "PROMOTING" /\ transaction' = TRUE
    /\ frozenDigest' = dependencyDigest
    /\ UNCHANGED <<gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt, evidenceState,
                    behaviorKeyMatches, leaseFresh, dependencyDigest,
                    immutableIdentityPreserved, productionMutation,
                    supersessionState, supersessionReuseApplied,
                    freshValidationStarted>>

Next == AcceptIndependentStages \/ CpuPending \/ CpuQualifies \/ CpuHardFails \/
        ChangeArtifact \/ RecordStableDebt \/ IntroduceRegression \/
        BuildCompleteEvidence \/ ExpireLease \/ TamperEvidence \/ ChangeBehaviorKey \/
        ReadAndPlan \/ ClassifySupersession \/ ContinueFreshValidation \/
        ApplySupersessionReuse \/ Pass \/ BeginPromote
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ phase \in {"PREPARE", "PASSED", "PROMOTING"}
    /\ gate \in {"UNTESTED", "PASSED", "FAILED"}
    /\ releaseAccepted \in BOOLEAN /\ cpuRequired \in BOOLEAN /\ cpuState \in CpuStates
    /\ artifactKey \in Keys /\ qualificationKey \in Keys /\ stages \subseteq Stages
    /\ candidateExact \in BOOLEAN /\ hardSafe \in BOOLEAN /\ changedSafe \in BOOLEAN
    /\ candidateRegression \in BOOLEAN /\ stableDebt \in BOOLEAN
    /\ evidenceState \in {"MISSING", "VALID", "TAMPERED"}
    /\ behaviorKeyMatches \in BOOLEAN /\ leaseFresh \in BOOLEAN
    /\ dependencyDigest \in Keys /\ frozenDigest \in Keys \cup {"NONE"}
    /\ transaction \in BOOLEAN /\ immutableIdentityPreserved \in BOOLEAN
    /\ productionMutation \in BOOLEAN
    /\ supersessionState \in SupersessionStates
    /\ supersessionReuseApplied \in BOOLEAN /\ freshValidationStarted \in BOOLEAN
CpuQualificationRequiredForPass == gate = "PASSED" /\ cpuRequired => cpuState = "QUALIFIED"
ProviderPendingIsNotCandidateFailure ==
    cpuState = "PENDING" /\ candidateExact /\ hardSafe /\ changedSafe /\
    ~candidateRegression => gate # "FAILED"
CpuRecoveryPreservesIndependentStages == cpuState \in {"PENDING", "QUALIFIED"} => stages = Stages
ArtifactMovementInvalidatesCpu == artifactKey # qualificationKey => cpuState # "QUALIFIED" /\ gate # "PASSED"
PassedIdentityIsExact == gate = "PASSED" => candidateExact
AcceptedEvidenceIsRequired == gate = "PASSED" => releaseAccepted
PassedGatesAreSafe == gate = "PASSED" => hardSafe /\ changedSafe /\ ~candidateRegression
HardFailuresBlock == cpuState = "HARD_FAILURE" => gate # "PASSED" /\ ~releaseAccepted
UnrelatedDebtIsNotFailure == stableDebt /\ candidateExact /\ hardSafe /\ changedSafe /\
    ~candidateRegression /\ cpuState # "HARD_FAILURE" => gate # "FAILED"
PromoteRequiresPassed == phase = "PROMOTING" => gate = "PASSED" /\ releaseAccepted
CompleteEvidenceRequiredForPass == gate = "PASSED" => evidenceState = "VALID"
BehaviorKeyChangeInvalidatesReuse == ~behaviorKeyMatches => gate # "PASSED"
StaleLeaseCannotAuthorize == ~leaseFresh => phase # "PROMOTING"
TamperedReceiptCannotPromote == evidenceState = "TAMPERED" => phase # "PROMOTING"
DependencyDigestCannotBeReplaced == transaction => frozenDigest = dependencyDigest
ImmutableReusePreservesIdentity == gate = "PASSED" => immutableIdentityPreserved
ReadPlanningDoesNotMutateProduction == ~productionMutation
EvidenceTransactionIsSingle == transaction => phase = "PROMOTING"
SupersessionReuseRequiresFound == supersessionReuseApplied => supersessionState = "FOUND"
FreshFallbackCannotReuseSupersededEvidence ==
    freshValidationStarted => ~supersessionReuseApplied /\
        supersessionState \in {"NOT_APPLICABLE", "REUSE_UNAVAILABLE"}
UnsafeSupersessionBlocksFreshValidation ==
    supersessionState = "FAILED" => ~freshValidationStarted
SupersessionFallbackPreservesCandidateIdentity ==
    supersessionState = "REUSE_UNAVAILABLE" =>
        candidateExact /\ immutableIdentityPreserved /\ ~productionMutation

=============================================================================
