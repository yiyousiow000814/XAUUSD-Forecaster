----------------------------- MODULE AccessEvidence -----------------------------
EXTENDS Naturals, TLC

Keys == {"A", "B"}
ReceiptStates == {"NONE", "VALID", "WRONG_KEY", "TAMPERED", "STALE"}
ProviderStates == {"UNREADABLE", "UNCHANGED", "CHANGED"}
VARIABLES accessRequired, review, humanReceiptState, releaseKey,
          humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
          priorHumanValid, accessFailure, accessAccepted, accessReused,
          approvalCount, repeated, gate
vars == <<accessRequired, review, humanReceiptState, releaseKey,
          humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
          priorHumanValid, accessFailure, accessAccepted, accessReused,
          approvalCount, repeated, gate>>

Init ==
    /\ accessRequired = TRUE /\ review = FALSE
    /\ humanReceiptState = "NONE"
    /\ releaseKey = "A" /\ humanReceiptReleaseKey = "A"
    /\ accessKey = "A" /\ priorAccessKey = "A"
    /\ providerState = "UNREADABLE"
    /\ priorHumanValid = TRUE /\ accessFailure = FALSE
    /\ accessAccepted = FALSE /\ accessReused = FALSE
    /\ approvalCount = 0 /\ repeated = FALSE /\ gate = "UNTESTED"

RequireReview ==
    /\ accessRequired /\ ~review /\ review' = TRUE
    /\ UNCHANGED <<accessRequired, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, accessAccepted, accessReused,
         approvalCount, repeated, gate>>

RecordReceipt(kind, key) ==
    /\ review /\ kind \in ReceiptStates \ {"NONE"} /\ key \in Keys
    /\ humanReceiptState' = kind /\ humanReceiptReleaseKey' = key
    /\ accessAccepted' = FALSE /\ accessReused' = FALSE
    /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, releaseKey, accessKey,
         priorAccessKey, providerState, priorHumanValid, accessFailure,
         approvalCount>>

Approve ==
    /\ review /\ humanReceiptState = "VALID"
    /\ humanReceiptReleaseKey = releaseKey /\ ~accessAccepted
    /\ accessAccepted' = TRUE /\ accessReused' = FALSE
    /\ approvalCount' = 1
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, repeated, gate>>

InspectProvider(kind) ==
    /\ review /\ ~accessAccepted /\ ~accessReused
    /\ kind \in ProviderStates /\ providerState' = kind
    /\ accessReused' = FALSE /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, priorHumanValid,
         accessFailure, accessAccepted, approvalCount>>

ReuseQualification ==
    /\ review /\ priorHumanValid /\ ~accessFailure
    /\ providerState = "UNCHANGED" /\ priorAccessKey = accessKey
    /\ ~accessAccepted /\ ~accessReused
    /\ accessReused' = TRUE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, accessAccepted, approvalCount,
         repeated, gate>>

RepeatApproval ==
    /\ (accessAccepted \/ accessReused) /\ ~repeated /\ repeated' = TRUE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, accessAccepted, accessReused,
         approvalCount, gate>>

ApplicableHumanEvidence ==
    review /\ accessAccepted /\ humanReceiptState = "VALID" /\
    humanReceiptReleaseKey = releaseKey
ApplicableReusedEvidence ==
    review /\ accessReused /\ priorHumanValid /\ ~accessFailure /\
    providerState = "UNCHANGED" /\ priorAccessKey = accessKey

Pass ==
    /\ (~accessRequired \/ ApplicableHumanEvidence \/ ApplicableReusedEvidence)
    /\ gate' = "PASSED"
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, accessAccepted, accessReused,
         approvalCount, repeated>>

ChangeReleaseKey ==
    /\ releaseKey' \in Keys \ {releaseKey}
    /\ accessAccepted' = FALSE /\ accessReused' = FALSE
    /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, approvalCount>>
ChangeAccessKey ==
    /\ accessKey' \in Keys \ {accessKey}
    /\ accessAccepted' = FALSE /\ accessReused' = FALSE
    /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, priorAccessKey, providerState,
         priorHumanValid, accessFailure, approvalCount>>
RecordAccessFailure ==
    /\ ~accessFailure /\ accessFailure' = TRUE
    /\ accessReused' = FALSE /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         priorHumanValid, accessAccepted, approvalCount>>
InvalidatePriorHuman ==
    /\ priorHumanValid /\ priorHumanValid' = FALSE
    /\ accessReused' = FALSE /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, humanReceiptState, releaseKey,
         humanReceiptReleaseKey, accessKey, priorAccessKey, providerState,
         accessFailure, accessAccepted, approvalCount>>

Next == RequireReview \/
    (\E kind \in ReceiptStates \ {"NONE"}, key \in Keys: RecordReceipt(kind, key)) \/
    (\E kind \in ProviderStates: InspectProvider(kind)) \/
    Approve \/ ReuseQualification \/ RepeatApproval \/ Pass \/
    ChangeReleaseKey \/ ChangeAccessKey \/ RecordAccessFailure \/ InvalidatePriorHuman
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ accessRequired \in BOOLEAN /\ review \in BOOLEAN
    /\ humanReceiptState \in ReceiptStates
    /\ releaseKey \in Keys /\ humanReceiptReleaseKey \in Keys
    /\ accessKey \in Keys /\ priorAccessKey \in Keys
    /\ providerState \in ProviderStates
    /\ priorHumanValid \in BOOLEAN /\ accessFailure \in BOOLEAN
    /\ accessAccepted \in BOOLEAN /\ accessReused \in BOOLEAN
    /\ approvalCount \in 0..1 /\ repeated \in BOOLEAN
    /\ gate \in {"UNTESTED", "PASSED"}
ApplicableAccessEvidence == ApplicableHumanEvidence \/ ApplicableReusedEvidence
AccessEvidenceIsRequired == gate = "PASSED" /\ accessRequired => ApplicableAccessEvidence
InvalidAccessReceiptCannotPass ==
    accessRequired /\ humanReceiptState \in {"NONE", "WRONG_KEY", "TAMPERED", "STALE"} /\
    ~ApplicableReusedEvidence => gate # "PASSED" /\ ~accessAccepted
AccessApprovalIsIdempotent == approvalCount <= 1 /\ (repeated => (accessAccepted \/ accessReused))
ReuseRequiresExactAccessKey == accessReused => priorAccessKey = accessKey
UnreadableOrChangedProviderCannotReuse == providerState \in {"UNREADABLE", "CHANGED"} => ~accessReused
AccessFailureCannotReuse == accessFailure => ~accessReused
ReuseDoesNotFabricateHumanApproval == accessReused => ~accessAccepted
=============================================================================
