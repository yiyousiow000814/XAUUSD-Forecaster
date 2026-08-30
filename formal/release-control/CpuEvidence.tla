------------------------------ MODULE CpuEvidence ------------------------------
EXTENDS Naturals, FiniteSets, TLC

Families == {"STATUS", "NEWS", "MARKET", "LEARNING", "AUDIT"}
MaxRepairFamilies == 4
RequestsPerFamily == 4
MaxRepairRequests == 16
Keys == {"A", "B"}
IndependentStages == {"MIGRATION", "DIRECTED", "SEMANTIC"}
EvidenceStates == {"NONE", "PENDING", "INSUFFICIENT", "OUTLIER_REVIEW",
                   "CONFIRMING", "QUALIFIED", "HARD_FAILURE"}
QualificationKinds == {"NONE", "FRESH", "REUSED", "ISOLATED_OUTLIER"}
ConfirmationStates == {"NONE", "CLEAN", "REPRODUCED"}

VARIABLES controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
          qualification, artifactKey, receiptKey, receiptValid,
          receiptQuotasSatisfied, independentStages, repairSet, repairRequests,
          acceptedBeforeRepair, outlierObserved, outlierFamily,
          confirmationFamily, confirmationUses, confirmationState,
          originalOutlierRetained

vars == <<controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
          qualification, artifactKey, receiptKey, receiptValid,
          receiptQuotasSatisfied, independentStages, repairSet, repairRequests,
          acceptedBeforeRepair, outlierObserved, outlierFamily,
          confirmationFamily, confirmationUses, confirmationState,
          originalOutlierRetained>>

CpuQualified == state = "QUALIFIED" /\ qualification # "NONE"
RequiredQuotasSatisfied == evidence = Families
ApplicableQualification ==
    \/ qualification = "FRESH"
    \/ qualification = "ISOLATED_OUTLIER" /\ confirmationState = "CLEAN" /\
       confirmationUses = 1 /\ originalOutlierRetained /\
       confirmationFamily = outlierFamily
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
    /\ outlierObserved = FALSE
    /\ outlierFamily = "STATUS"
    /\ confirmationFamily = "STATUS"
    /\ confirmationUses = 0
    /\ confirmationState = "NONE"
    /\ originalOutlierRetained = FALSE

AcceptIndependentStages ==
    /\ independentStages # IndependentStages
    /\ independentStages' = IndependentStages
    /\ UNCHANGED <<controlledComplete, evidence, state, reserveUses, topUps, hardFailure,
                    qualification, artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, repairSet, repairRequests,
                    acceptedBeforeRepair, outlierObserved, outlierFamily,
                    confirmationFamily, confirmationUses, confirmationState,
                    originalOutlierRetained>>

CompleteControlledRequests ==
    /\ independentStages = IndependentStages
    /\ ~controlledComplete
    /\ controlledComplete' = TRUE
    /\ state' = "PENDING"
    /\ UNCHANGED <<evidence, reserveUses, topUps, hardFailure, qualification, artifactKey,
                    receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair, outlierObserved, outlierFamily,
                    confirmationFamily, confirmationUses, confirmationState,
                    originalOutlierRetained>>

ProviderEvidenceArrives(family) ==
    /\ controlledComplete
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ family \in Families \ evidence
    /\ evidence' = evidence \cup {family}
    /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, reserveUses, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

ReceiveAllEvidence ==
    /\ controlledComplete
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ evidence # Families
    /\ evidence' = Families
    /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, reserveUses, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

MarkInsufficient ==
    /\ state = "PENDING"
    /\ evidence # Families
    /\ state' = "INSUFFICIENT"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, hardFailure,
                    qualification, artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

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
                    receiptQuotasSatisfied, independentStages, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

RecordSuccessfulOutlier(family) ==
    /\ controlledComplete /\ RequiredQuotasSatisfied
    /\ state = "PENDING" /\ ~outlierObserved
    /\ family \in Families
    /\ state' = "OUTLIER_REVIEW"
    /\ outlierObserved' = TRUE
    /\ outlierFamily' = family
    /\ originalOutlierRetained' = TRUE
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps,
                    hardFailure, qualification, artifactKey, receiptKey,
                    receiptValid, receiptQuotasSatisfied, independentStages,
                    repairSet, repairRequests, acceptedBeforeRepair,
                    confirmationFamily, confirmationUses, confirmationState>>

StartOutlierConfirmation ==
    /\ state = "OUTLIER_REVIEW" /\ outlierObserved
    /\ confirmationUses = 0
    /\ state' = "CONFIRMING"
    /\ confirmationUses' = 1
    /\ confirmationFamily' = outlierFamily
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps,
                    hardFailure, qualification, artifactKey, receiptKey,
                    receiptValid, receiptQuotasSatisfied, independentStages,
                    repairSet, repairRequests, acceptedBeforeRepair,
                    outlierObserved, outlierFamily, confirmationState,
                    originalOutlierRetained>>

ConfirmIsolatedOutlier ==
    /\ state = "CONFIRMING" /\ confirmationUses = 1
    /\ confirmationFamily = outlierFamily /\ originalOutlierRetained
    /\ state' = "PENDING"
    /\ confirmationState' = "CLEAN"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps,
                    hardFailure, qualification, artifactKey, receiptKey,
                    receiptValid, receiptQuotasSatisfied, independentStages,
                    repairSet, repairRequests, acceptedBeforeRepair,
                    outlierObserved, outlierFamily, confirmationFamily,
                    confirmationUses, originalOutlierRetained>>

ConfirmRepeatedPressure ==
    /\ state = "CONFIRMING" /\ confirmationUses = 1
    /\ state' = "HARD_FAILURE"
    /\ hardFailure' = TRUE
    /\ qualification' = "NONE"
    /\ confirmationState' = "REPRODUCED"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    originalOutlierRetained>>

UseReserveEvidence ==
    /\ state \in {"PENDING", "INSUFFICIENT"}
    /\ evidence # Families /\ reserveUses = 0
    /\ evidence' = Families /\ reserveUses' = 1 /\ state' = "PENDING"
    /\ UNCHANGED <<controlledComplete, topUps, hardFailure, qualification,
                    artifactKey, receiptKey, receiptValid,
                    receiptQuotasSatisfied, independentStages, repairSet,
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

RecordHardFailure ==
    /\ controlledComplete
    /\ ~hardFailure
    /\ hardFailure' = TRUE
    /\ state' = "HARD_FAILURE"
    /\ qualification' = "NONE"
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, artifactKey,
                    receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair, outlierObserved, outlierFamily,
                    confirmationFamily, confirmationUses, confirmationState,
                    originalOutlierRetained>>

QualifyFresh ==
    /\ controlledComplete
    /\ RequiredQuotasSatisfied
    /\ ~hardFailure
    /\ state # "QUALIFIED"
    /\ state' = "QUALIFIED"
    /\ (~outlierObserved \/
        (confirmationUses = 1 /\ confirmationState = "CLEAN" /\
         confirmationFamily = outlierFamily /\ originalOutlierRetained))
    /\ qualification' = IF outlierObserved THEN "ISOLATED_OUTLIER" ELSE "FRESH"
    /\ receiptKey' = artifactKey
    /\ receiptValid' = TRUE
    /\ receiptQuotasSatisfied' = TRUE
    /\ UNCHANGED <<controlledComplete, evidence, reserveUses, topUps, hardFailure,
                    artifactKey, independentStages, repairSet, repairRequests,
                    acceptedBeforeRepair, outlierObserved, outlierFamily,
                    confirmationFamily, confirmationUses, confirmationState,
                    originalOutlierRetained>>

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
                    repairRequests, acceptedBeforeRepair, outlierObserved,
                    outlierFamily, confirmationFamily, confirmationUses,
                    confirmationState, originalOutlierRetained>>

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
    /\ outlierObserved' = FALSE
    /\ confirmationUses' = 0
    /\ confirmationState' = "NONE"
    /\ originalOutlierRetained' = FALSE
    /\ UNCHANGED <<receiptKey, receiptValid, receiptQuotasSatisfied,
                    independentStages, outlierFamily, confirmationFamily>>

Next ==
    \/ AcceptIndependentStages
    \/ CompleteControlledRequests
    \/ \E family \in Families: ProviderEvidenceArrives(family)
    \/ ReceiveAllEvidence
    \/ MarkInsufficient
    \/ TargetedTopUp
    \/ UseReserveEvidence
    \/ \E family \in Families: RecordSuccessfulOutlier(family)
    \/ StartOutlierConfirmation
    \/ ConfirmIsolatedOutlier
    \/ ConfirmRepeatedPressure
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
    /\ WF_vars(StartOutlierConfirmation)
    /\ WF_vars(ConfirmIsolatedOutlier)
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
    /\ outlierObserved \in BOOLEAN
    /\ outlierFamily \in Families /\ confirmationFamily \in Families
    /\ confirmationUses \in 0..1
    /\ confirmationState \in ConfirmationStates
    /\ originalOutlierRetained \in BOOLEAN

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
OutlierRequiresBoundedConfirmation ==
    qualification = "ISOLATED_OUTLIER" =>
        outlierObserved /\ confirmationUses = 1 /\
        confirmationState = "CLEAN" /\ originalOutlierRetained
NoSecondOutlierConfirmation == confirmationUses <= 1
OutlierConfirmationMatchesRequestShape ==
    confirmationUses = 1 => confirmationFamily = outlierFamily
RepeatedCpuPressureCannotQualify ==
    confirmationState = "REPRODUCED" => hardFailure /\ ~CpuQualified
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
OriginalOutlierCannotBeErased ==
    [][(artifactKey' = artifactKey /\ outlierObserved) =>
        outlierObserved' /\ originalOutlierRetained']_vars
PendingEventuallyResolves ==
    [](state = "PENDING" => <> (state # "PENDING"))

=============================================================================
