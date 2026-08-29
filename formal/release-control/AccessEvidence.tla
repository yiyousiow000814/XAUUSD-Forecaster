----------------------------- MODULE AccessEvidence -----------------------------
EXTENDS Naturals, TLC

Keys == {"A", "B"}
ReceiptStates == {"NONE", "VALID", "WRONG_KEY", "TAMPERED", "STALE"}
VARIABLES accessRequired, review, receiptState, releaseKey, receiptKey,
          accessAccepted, approvalCount, repeated, gate
vars == <<accessRequired, review, receiptState, releaseKey, receiptKey,
          accessAccepted, approvalCount, repeated, gate>>
Init == /\ accessRequired = TRUE /\ review = FALSE /\ receiptState = "NONE"
        /\ releaseKey = "A" /\ receiptKey = "A" /\ accessAccepted = FALSE
        /\ approvalCount = 0 /\ repeated = FALSE /\ gate = "UNTESTED"
RequireReview ==
    /\ accessRequired /\ ~review /\ review' = TRUE
    /\ UNCHANGED <<accessRequired, receiptState, releaseKey, receiptKey, accessAccepted, approvalCount, repeated, gate>>
RecordReceipt(kind, key) ==
    /\ review /\ kind \in ReceiptStates \ {"NONE"} /\ key \in Keys
    /\ receiptState' = kind /\ receiptKey' = key
    /\ accessAccepted' = FALSE /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, releaseKey, approvalCount>>
Approve ==
    /\ review /\ receiptState = "VALID" /\ receiptKey = releaseKey /\ ~accessAccepted
    /\ accessAccepted' = TRUE /\ approvalCount' = 1
    /\ UNCHANGED <<accessRequired, review, receiptState, releaseKey, receiptKey, repeated, gate>>
RepeatApproval ==
    /\ accessAccepted /\ ~repeated /\ repeated' = TRUE
    /\ UNCHANGED <<accessRequired, review, receiptState, releaseKey, receiptKey, accessAccepted, approvalCount, gate>>
Pass ==
    /\ (~accessRequired \/ (review /\ accessAccepted /\ receiptState = "VALID" /\ receiptKey = releaseKey))
    /\ gate' = "PASSED"
    /\ UNCHANGED <<accessRequired, review, receiptState, releaseKey, receiptKey, accessAccepted, approvalCount, repeated>>
ChangeReleaseKey ==
    /\ releaseKey' \in Keys \ {releaseKey} /\ accessAccepted' = FALSE
    /\ gate' = "UNTESTED" /\ repeated' = FALSE
    /\ UNCHANGED <<accessRequired, review, receiptState, receiptKey, approvalCount>>
Next == RequireReview \/ (\E kind \in ReceiptStates \ {"NONE"}, key \in Keys: RecordReceipt(kind, key)) \/
        Approve \/ RepeatApproval \/ Pass \/ ChangeReleaseKey
Spec == Init /\ [][Next]_vars
TypeOK == /\ accessRequired \in BOOLEAN /\ review \in BOOLEAN /\ receiptState \in ReceiptStates
          /\ releaseKey \in Keys /\ receiptKey \in Keys /\ accessAccepted \in BOOLEAN
          /\ approvalCount \in 0..1 /\ repeated \in BOOLEAN /\ gate \in {"UNTESTED", "PASSED"}
ApplicableAccessEvidence == review /\ accessAccepted /\ receiptState = "VALID" /\ receiptKey = releaseKey
AccessEvidenceIsRequired == gate = "PASSED" /\ accessRequired => ApplicableAccessEvidence
InvalidAccessReceiptCannotPass ==
    accessRequired /\ receiptState \in {"NONE", "WRONG_KEY", "TAMPERED", "STALE"} => gate # "PASSED" /\ ~accessAccepted
AccessApprovalIsIdempotent == approvalCount <= 1 /\ (repeated => accessAccepted /\ approvalCount = 1)
=============================================================================
