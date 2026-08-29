--------------------------- MODULE ReleaseIntegration ---------------------------
EXTENDS TLC

CpuStates == {"NOT_REQUIRED", "PENDING", "QUALIFIED", "HARD_FAILURE"}
Keys == {"A", "B"}
Stages == {"MIGRATION", "DIRECTED", "SEMANTIC"}

VARIABLES phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
          qualificationKey, stages, candidateExact, hardSafe, changedSafe,
          candidateRegression, stableDebt
vars == <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
          qualificationKey, stages, candidateExact, hardSafe, changedSafe,
          candidateRegression, stableDebt>>

Init ==
    /\ phase = "PREPARE" /\ gate = "UNTESTED" /\ releaseAccepted = FALSE
    /\ cpuRequired = TRUE /\ cpuState = "NOT_REQUIRED"
    /\ artifactKey = "A" /\ qualificationKey = "A"
    /\ stages = {} /\ candidateExact = TRUE /\ hardSafe = TRUE /\ changedSafe = TRUE
    /\ candidateRegression = FALSE /\ stableDebt = FALSE

AcceptIndependentStages ==
    /\ stages # Stages
    /\ stages' = Stages
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, candidateExact, hardSafe, changedSafe,
                    candidateRegression, stableDebt>>
CpuPending ==
    /\ stages = Stages /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ cpuState' = "PENDING"
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt>>
CpuQualifies ==
    /\ stages = Stages
    /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ qualificationKey = artifactKey
    /\ cpuState' = "QUALIFIED"
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt>>
CpuHardFails ==
    /\ cpuState \in {"NOT_REQUIRED", "PENDING"}
    /\ cpuState' = "HARD_FAILURE"
    /\ gate' = "FAILED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, artifactKey, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt>>
ChangeArtifact ==
    /\ phase = "PREPARE"
    /\ artifactKey' \in Keys \ {artifactKey}
    /\ cpuState' = "NOT_REQUIRED" /\ gate' = "UNTESTED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt>>
RecordStableDebt ==
    /\ ~stableDebt /\ stableDebt' = TRUE
    /\ UNCHANGED <<phase, gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression>>
IntroduceRegression ==
    /\ phase = "PREPARE"
    /\ ~candidateRegression /\ candidateRegression' = TRUE
    /\ gate' = "FAILED" /\ releaseAccepted' = FALSE
    /\ UNCHANGED <<phase, cpuRequired, cpuState, artifactKey, qualificationKey,
                    stages, candidateExact, hardSafe, changedSafe, stableDebt>>
Pass ==
    /\ phase = "PREPARE" /\ stages = Stages
    /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ candidateExact /\ hardSafe /\ changedSafe /\ ~candidateRegression
    /\ qualificationKey = artifactKey
    /\ phase' = "PASSED" /\ gate' = "PASSED" /\ releaseAccepted' = TRUE
    /\ UNCHANGED <<cpuRequired, cpuState, artifactKey, qualificationKey, stages,
                    candidateExact, hardSafe, changedSafe, candidateRegression,
                    stableDebt>>
BeginPromote ==
    /\ phase = "PASSED" /\ gate = "PASSED" /\ releaseAccepted
    /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ phase' = "PROMOTING"
    /\ UNCHANGED <<gate, releaseAccepted, cpuRequired, cpuState, artifactKey,
                    qualificationKey, stages, candidateExact, hardSafe,
                    changedSafe, candidateRegression, stableDebt>>

Next == AcceptIndependentStages \/ CpuPending \/ CpuQualifies \/ CpuHardFails \/
        ChangeArtifact \/ RecordStableDebt \/ IntroduceRegression \/ Pass \/ BeginPromote
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ phase \in {"PREPARE", "PASSED", "PROMOTING"}
    /\ gate \in {"UNTESTED", "PASSED", "FAILED"}
    /\ releaseAccepted \in BOOLEAN /\ cpuRequired \in BOOLEAN /\ cpuState \in CpuStates
    /\ artifactKey \in Keys /\ qualificationKey \in Keys /\ stages \subseteq Stages
    /\ candidateExact \in BOOLEAN /\ hardSafe \in BOOLEAN /\ changedSafe \in BOOLEAN
    /\ candidateRegression \in BOOLEAN /\ stableDebt \in BOOLEAN
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

=============================================================================
