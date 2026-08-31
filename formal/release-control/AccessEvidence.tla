----------------------------- MODULE AccessEvidence -----------------------------
EXTENDS Naturals, TLC
Keys == {"A", "B"}
ReceiptStates == {"NONE", "VALID", "WRONG_KEY", "TAMPERED", "STALE"}
ProviderStates == {"UNREADABLE", "UNCHANGED", "CHANGED"}
AuditStates == {"UNREADABLE", "INCOMPLETE", "CHANGED", "CLEAN"}
MachineStates == {"NONE", "FRESH", "STALE", "TAMPERED"}
VARIABLES accessRequired, review, humanReceiptState, releaseKey,
          humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
          auditState, priorHumanValid, chainValid, accessFailure,
          accessAccepted, accessReused, accessRenewed, machineReceiptState,
          approvalCount, renewalCount, repeated, gate
vars == <<accessRequired, review, humanReceiptState, releaseKey,
          humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
          auditState, priorHumanValid, chainValid, accessFailure,
          accessAccepted, accessReused, accessRenewed, machineReceiptState,
          approvalCount, renewalCount, repeated, gate>>

Init ==
    /\ accessRequired = TRUE /\ review = FALSE
    /\ humanReceiptState = "NONE"
    /\ releaseKey = "A" /\ humanReceiptReleaseKey = "A"
    /\ accessKey = "A" /\ priorAccessKey = "A"
    /\ providerState = "UNREADABLE" /\ auditState = "UNREADABLE"
    /\ priorHumanValid = TRUE /\ chainValid = TRUE /\ accessFailure = FALSE
    /\ accessAccepted = FALSE /\ accessReused = FALSE /\ accessRenewed = FALSE
    /\ machineReceiptState = "NONE"
    /\ approvalCount = 0 /\ renewalCount = 0
    /\ repeated = FALSE /\ gate = "UNTESTED"

RequireReview ==
    /\ accessRequired /\ ~review /\ review' = TRUE
    /\ UNCHANGED <<accessRequired, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure,
         accessAccepted, accessReused, accessRenewed, machineReceiptState,
         approvalCount, renewalCount, repeated, gate>>

RecordReceipt(kind, key) ==
    /\ review /\ kind \in ReceiptStates \ {"NONE"} /\ key \in Keys
    /\ humanReceiptState' = kind /\ humanReceiptReleaseKey' = key
    /\ accessAccepted' = FALSE /\ accessReused' = FALSE /\ accessRenewed' = FALSE
    /\ machineReceiptState' = "NONE" /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, releaseKey, accessKey,
         priorAccessKey, providerState, auditState, priorHumanValid, chainValid,
         accessFailure, approvalCount, renewalCount>>

Approve ==
    /\ review /\ humanReceiptState = "VALID"
    /\ humanReceiptReleaseKey = releaseKey /\ ~accessAccepted
    /\ accessAccepted' = TRUE /\ accessReused' = FALSE /\ accessRenewed' = FALSE
    /\ approvalCount' = 1
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure,
         machineReceiptState, renewalCount, repeated, gate>>

InspectProvider(providerKind, auditKind) ==
    /\ review /\ providerKind \in ProviderStates /\ auditKind \in AuditStates
    /\ providerState' = providerKind /\ auditState' = auditKind
    /\ accessReused' = IF providerKind = "UNCHANGED" /\ auditKind = "CLEAN"
        THEN accessReused ELSE FALSE
    /\ accessRenewed' = IF providerKind = "UNCHANGED" /\ auditKind = "CLEAN"
        THEN accessRenewed ELSE FALSE
    /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, priorHumanValid,
         chainValid, accessFailure, accessAccepted,
         machineReceiptState, approvalCount, renewalCount>>

ReuseQualification ==
    /\ review /\ priorHumanValid /\ chainValid /\ ~accessFailure
    /\ providerState = "UNCHANGED" /\ auditState = "CLEAN"
    /\ priorAccessKey = accessKey /\ ~accessAccepted /\ ~accessReused /\ ~accessRenewed
    /\ accessReused' = TRUE /\ machineReceiptState' = "FRESH"
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, accessAccepted,
         accessRenewed, approvalCount, renewalCount, repeated, gate>>

ExpireMachineReceipt ==
    /\ machineReceiptState = "FRESH" /\ (accessReused \/ accessRenewed)
    /\ machineReceiptState' = "STALE" /\ gate' = "UNTESTED"
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, accessAccepted,
         accessReused, accessRenewed, approvalCount, renewalCount, repeated>>

RenewQualification ==
    /\ review /\ machineReceiptState = "STALE" /\ renewalCount < 2
    /\ ~accessAccepted
    /\ priorHumanValid /\ chainValid /\ ~accessFailure
    /\ providerState = "UNCHANGED" /\ auditState = "CLEAN"
    /\ priorAccessKey = accessKey
    /\ accessRenewed' = TRUE /\ accessReused' = FALSE
    /\ machineReceiptState' = "FRESH" /\ renewalCount' = renewalCount + 1
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, accessAccepted,
         approvalCount, repeated, gate>>

RepeatApproval ==
    /\ (accessAccepted \/ accessReused \/ accessRenewed) /\ ~repeated
    /\ repeated' = TRUE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, accessAccepted,
         accessReused, accessRenewed, machineReceiptState, approvalCount,
         renewalCount, gate>>

ApplicableHumanEvidence == review /\ accessAccepted /\
    humanReceiptState = "VALID" /\ humanReceiptReleaseKey = releaseKey
ApplicableReusedEvidence == review /\ accessReused /\ machineReceiptState = "FRESH" /\
    priorHumanValid /\ chainValid /\ ~accessFailure /\ providerState = "UNCHANGED" /\
    auditState = "CLEAN" /\ priorAccessKey = accessKey
ApplicableRenewedEvidence == review /\ accessRenewed /\ machineReceiptState = "FRESH" /\
    priorHumanValid /\ chainValid /\ ~accessFailure /\ providerState = "UNCHANGED" /\
    auditState = "CLEAN" /\ priorAccessKey = accessKey

Pass ==
    /\ (~accessRequired \/ ApplicableHumanEvidence \/ ApplicableReusedEvidence \/
        ApplicableRenewedEvidence)
    /\ gate' = "PASSED"
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, accessAccepted,
         accessReused, accessRenewed, machineReceiptState, approvalCount,
         renewalCount, repeated>>

Invalidate ==
    /\ accessAccepted' = FALSE /\ accessReused' = FALSE /\ accessRenewed' = FALSE
    /\ machineReceiptState' = "NONE" /\ gate' = "UNTESTED" /\ repeated' = FALSE

ChangeReleaseKey ==
    /\ releaseKey' \in Keys \ {releaseKey} /\ Invalidate
    /\ UNCHANGED <<accessRequired, review, humanReceiptState,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, accessFailure, approvalCount,
         renewalCount>>
ChangeAccessKey ==
    /\ accessKey' \in Keys \ {accessKey} /\ Invalidate
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, priorAccessKey, providerState, auditState,
         priorHumanValid, chainValid, accessFailure, approvalCount, renewalCount>>
RecordAccessFailure ==
    /\ ~accessFailure /\ accessFailure' = TRUE /\ Invalidate
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, chainValid, approvalCount, renewalCount>>
InvalidatePriorHuman ==
    /\ priorHumanValid /\ priorHumanValid' = FALSE /\ Invalidate
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, chainValid, accessFailure, approvalCount, renewalCount>>
BreakChain ==
    /\ chainValid /\ chainValid' = FALSE /\ Invalidate
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         auditState, priorHumanValid, accessFailure, approvalCount, renewalCount>>

Next == RequireReview \/
    (\E kind \in ReceiptStates \ {"NONE"}, key \in Keys: RecordReceipt(kind, key)) \/
    (\E providerKind \in ProviderStates, auditKind \in AuditStates:
        InspectProvider(providerKind, auditKind)) \/
    Approve \/ ReuseQualification \/ ExpireMachineReceipt \/ RenewQualification \/
    RepeatApproval \/ Pass \/ ChangeReleaseKey \/ ChangeAccessKey \/
    RecordAccessFailure \/ InvalidatePriorHuman \/ BreakChain
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ accessRequired \in BOOLEAN /\ review \in BOOLEAN
    /\ humanReceiptState \in ReceiptStates
    /\ releaseKey \in Keys /\ humanReceiptReleaseKey \in Keys
    /\ accessKey \in Keys /\ priorAccessKey \in Keys
    /\ providerState \in ProviderStates /\ auditState \in AuditStates
    /\ priorHumanValid \in BOOLEAN /\ chainValid \in BOOLEAN
    /\ accessFailure \in BOOLEAN /\ accessAccepted \in BOOLEAN
    /\ accessReused \in BOOLEAN /\ accessRenewed \in BOOLEAN
    /\ machineReceiptState \in MachineStates
    /\ approvalCount \in 0..1 /\ renewalCount \in 0..2
    /\ repeated \in BOOLEAN /\ gate \in {"UNTESTED", "PASSED"}
ApplicableAccessEvidence == ApplicableHumanEvidence \/ ApplicableReusedEvidence \/ ApplicableRenewedEvidence
AccessEvidenceIsRequired == gate = "PASSED" /\ accessRequired => ApplicableAccessEvidence
InvalidAccessReceiptCannotPass == accessRequired /\
    humanReceiptState \in {"NONE", "WRONG_KEY", "TAMPERED", "STALE"} /\
    ~ApplicableReusedEvidence /\ ~ApplicableRenewedEvidence =>
    gate # "PASSED" /\ ~accessAccepted
AccessApprovalIsIdempotent == approvalCount <= 1 /\
    (repeated => (accessAccepted \/ accessReused \/ accessRenewed))
ReuseRequiresExactAccessKey == (accessReused \/ accessRenewed) => priorAccessKey = accessKey
UnreadableOrChangedProviderCannotReuse ==
    providerState \in {"UNREADABLE", "CHANGED"} => ~(accessReused \/ accessRenewed)
AccessFailureCannotReuse == accessFailure => ~(accessReused \/ accessRenewed)
ReuseDoesNotFabricateHumanApproval == (accessReused \/ accessRenewed) => ~accessAccepted
RenewalRequiresContinuousAudit == accessRenewed => auditState = "CLEAN"
StaleMachineEvidenceCannotPass == machineReceiptState = "STALE" =>
    ~(ApplicableReusedEvidence \/ ApplicableRenewedEvidence)
BrokenChainCannotRenew == ~chainValid => ~accessRenewed
RenewalKeepsHumanRoot == accessRenewed => priorHumanValid
RenewalIsBounded == renewalCount <= 2
=============================================================================
