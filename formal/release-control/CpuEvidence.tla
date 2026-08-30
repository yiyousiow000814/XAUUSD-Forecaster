------------------------------ MODULE CpuEvidence ------------------------------
EXTENDS Naturals, FiniteSets, TLC

Families == {"STATUS", "NEWS", "MARKET", "LEARNING", "AUDIT"}
MaxRepairFamilies == 4
RequestsPerFamily == 4
MaxRepairRequests == 16
Keys == {"A", "B"}
IndependentStages == {"MIGRATION", "DIRECTED", "SEMANTIC"}
EvidenceStates == {"NONE", "PENDING", "INSUFFICIENT", "QUALIFIED", "HARD_FAILURE"}
QualificationKinds == {"NONE", "FRESH", "REUSED"}

VARIABLES controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
          qualification, artifactKey, receiptKey, receiptValid,
          receiptQuotasSatisfied, independentStages, repairSet, repairRequests,
          acceptedBeforeRepair

vars == <<controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
          qualification, artifactKey, receiptKey, receiptValid,
          receiptQuotasSatisfied, independentStages, repairSet, repairRequests,
          acceptedBeforeRepair>>

CpuQualified == state = "QUALIFIED" /\ qualification # "NONE"
RequiredQuotasSatisfied == evidence = Families
ApplicableQualification ==
    \/ qualification = "FRESH"
    \/ qualification = "REUSED" /\ receiptValid /\
       receiptQuotasSatisfied /\ receiptKey = artifactKey

Init ==
    /\ controlledComplete = FALSE
    /\ evidence = {}
    /\ state = "NONE"
    /\ reserveUses = 0
    /\ topUps = 0
    /\ hardFailure = FALSE
    /\ qualification = "NONE"
    /\ artifactKey = "A"
    /\ receiptKey = "A"
    /\ receiptValid = TRUE
    /\ receiptQuotasSatisfied = TRUE
    /\ independentStages = {}
    /\ repairSet = {}
    /\ repairRequests = 0
    /\ acceptedBeforeRepair = {}

AcceptIndependentStages ==
    /\ independentStages # IndependentStages
    /\ independentStages' = IndependentStages
    /\ UNCHANGED <<controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
                    qualification, artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, repairSet, repairRequests,
                    acceptedBeforeRepair>>

CompleteControlledRequests ==
    /\ independentStages = IndependentStages
    /\ ~controlledComplete
    /\ controlledComplete' = TRUE
    /\ state' = "PENDING"
    /\ UNCHANGED <<evidence, reserveUses, topUps, hardFailure, qualification, artifactKey,
                    receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair>>

ProviderEvidenceArrives(family) ==
    /\ controlledComplete
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ family \in Families \ evidence
    /\ evidence' = evidence \cup {family}
    /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, reserveUses, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair>>

ReceiveAllEvidence ==
    /\ controlledComplete
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ evidence # Families
    /\ evidence' = Families
    /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, reserveUses, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair>>

MarkInsufficient ==
    /\ state = "PENDING"
    /\ evidence # Families
    /\ state' = "INSUFFICIENT"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, hardFailure,
                    qualification, artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair>>

TargetedTopUp ==
    /\ state = "INSUFFICIENT"
    /\ topUps = 0
    /\ Cardinality(Families \ evidence) \in 1..MaxRepairFamilies
    /\ topUps' = 1
    /\ repairSet' = Families \ evidence
    /\ acceptedBeforeRepair' = evidence
    /\ repairRequests' = RequestsPerFamily * Cardinality(Families \ evidence)
    /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages>>

UseReserveEvidence ==
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ evidence # Families /\ reserveUses = 0
    /\ evidence' = Families /\ reserveUses' = 1 /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair>>

RecordHardFailure ==
    /\ controlledComplete
    /\ ~hardFailure
    /\ hardFailure' = TRUE
    /\ state' = "HARD_FAILURE"
    /\ qualification' = "NONE"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, artifactKey,
                    receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair>>

QualifyFresh ==
    /\ controlledComplete
    /\ RequiredQuotasSatisfied
    /\ ~hardFailure
    /\ state # "QUALIFIED"
    /\ state' = "QUALIFIED"
    /\ qualification' = "FRESH"
    /\ receiptKey' = artifactKey
    /\ receiptValid' = TRUE
    /\ receiptQuotasSatisfied' = TRUE
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, hardFailure,
                    artifactKey, independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair>>

ReuseExactQualification ==
    /\ state = "NONE"
    /\ independentStages = IndependentStages
    /\ receiptValid /\ receiptQuotasSatisfied
    /\ receiptKey = artifactKey
    /\ state' = "QUALIFIED"
    /\ qualification' = "REUSED"
    /\ evidence' = Families
    /\ controlledComplete' = TRUE
    /\ UNCHANGED <<reserveUses, topUps, hardFailure, artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair>>

ChangeArtifact ==
    /\ artifactKey' \in Keys \ {artifactKey}
    /\ state' = "NONE"
    /\ qualification' = "NONE"
    /\ evidence' = {}
    /\ controlledComplete' = FALSE
    /\ reserveUses' = 0
    /\ topUps' = 0
    /\ hardFailure' = FALSE
    /\ repairSet' = {}
    /\ repairRequests' = 0
    /\ acceptedBeforeRepair' = {}
    /\ UNCHANGED <<receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages>>

Next ==
    \/ AcceptIndependentStages
    \/ CompleteControlledRequests
    \/ \E family \in Families: ProviderEvidenceArrives(family)
    \/ ReceiveAllEvidence
    \/ MarkInsufficient
    \/ TargetedTopUp
    \/ UseReserveEvidence
    \/ RecordHardFailure
    \/ QualifyFresh
    \/ ReuseExactQualification
    \/ ChangeArtifact

Spec == /\ Init /\ [][Next]_vars
SafetySpec == Spec
LivenessSpec ==
    /\ Spec
    /\ WF_vars(AcceptIndependentStages)
    /\ WF_vars(CompleteControlledRequests)
    /\ WF_vars(ReceiveAllEvidence)
    /\ WF_vars(QualifyFresh)

TypeOK ==
    /\ controlledComplete \in BOOLEAN
    /\ evidence \subseteq Families
    /\ state \in EvidenceStates
    /\ reserveUses \in 0..1
    /\ topUps \in 0..1
    /\ hardFailure \in BOOLEAN
    /\ qualification \in QualificationKinds
    /\ artifactKey \in Keys /\ receiptKey \in Keys
    /\ receiptValid \in BOOLEAN /\ receiptQuotasSatisfied \in BOOLEAN
    /\ independentStages \subseteq IndependentStages
    /\ repairSet \subseteq Families
    /\ repairRequests \in 0..MaxRepairRequests
    /\ acceptedBeforeRepair \subseteq Families

CpuQualificationIsValid ==
    CpuQualified => ~hardFailure /\ RequiredQuotasSatisfied /\ ApplicableQualification
CpuRetryBudgetIsBounded == topUps <= 1
DeficitRepairRequestBudgetIsBounded ==
    /\ repairRequests <= MaxRepairRequests
    /\ (topUps = 0 => repairRequests = 0)
DeficitRepairSetIsFrozen ==
    topUps = 1 => repairSet = Families \ acceptedBeforeRepair
QualifiedFamiliesAreNeverReplayed == repairSet \cap acceptedBeforeRepair = {}
DeficitRepairCannotFabricateEvidence ==
    [][topUps' > topUps => evidence' = evidence]_vars
NoSecondDeficitRepairRound == topUps <= 1
ReserveEvidenceUseIsBounded == reserveUses <= 1
CpuHardFailureCannotQualify == hardFailure => ~CpuQualified
ReusedCpuEvidenceMatchesArtifact ==
    qualification = "REUSED" => receiptValid /\ receiptQuotasSatisfied /\
        receiptKey = artifactKey /\ CpuQualified
CpuRecoveryPreservesIndependentStages ==
    controlledComplete => independentStages = IndependentStages
EveryRequiredFamilyQuotaSatisfied == CpuQualified => evidence = Families
ArtifactKeyChangeInvalidatesReuse ==
    qualification = "REUSED" => receiptKey = artifactKey
CpuEvidenceOnlyGrows ==
    [][(artifactKey' = artifactKey /\ state' # "NONE") => evidence \subseteq evidence']_vars
TargetedRetryPreservesAcceptedWork ==
    [][topUps' > topUps =>
        /\ independentStages' = independentStages
        /\ acceptedBeforeRepair' = evidence
        /\ repairSet' = Families \ evidence]_vars
ProviderEvidenceIsMonotonic == CpuEvidenceOnlyGrows
PendingEventuallyResolves ==
    [](state = "PENDING" => <> (state # "PENDING"))

=============================================================================
