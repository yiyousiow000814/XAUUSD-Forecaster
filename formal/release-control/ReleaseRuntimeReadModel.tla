---------------------- MODULE ReleaseRuntimeReadModel ----------------------
EXTENDS Naturals, TLC

Ids == {"OLD", "NEW"}
ArtifactStates == {"UNKNOWN", "AVAILABLE", "UNAVAILABLE", "MISMATCH"}
LookupStates == {"PENDING", "EXISTS", "FAILED"}
PrecheckStates == {"UNKNOWN", "READY", "BLOCKED"}

VARIABLES committed, active, lkg, artifactStatus, trafficMember, exactLookup,
          reversePrecheck, transaction, observationEpoch, releaseMutationEpoch
vars == <<committed, active, lkg, artifactStatus, trafficMember, exactLookup,
          reversePrecheck, transaction, observationEpoch, releaseMutationEpoch>>

Init ==
    /\ committed = "OLD" /\ active = "OLD" /\ lkg = "OLD"
    /\ artifactStatus = "UNKNOWN" /\ trafficMember = FALSE
    /\ exactLookup = "PENDING" /\ reversePrecheck = "UNKNOWN"
    /\ transaction = FALSE /\ observationEpoch = FALSE
    /\ releaseMutationEpoch = FALSE

ObserveActive ==
    /\ ~transaction /\ active' \in Ids
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, lkg, artifactStatus, trafficMember, exactLookup,
                    reversePrecheck, transaction, releaseMutationEpoch>>

ExactArtifactExists ==
    /\ ~transaction
    /\ exactLookup' = "EXISTS" /\ artifactStatus' = "AVAILABLE"
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMember, reversePrecheck,
                    transaction, releaseMutationEpoch>>

ExactArtifactFails ==
    /\ ~transaction /\ exactLookup' = "FAILED"
    /\ artifactStatus' \in {"UNAVAILABLE", "MISMATCH", "UNKNOWN"}
    /\ reversePrecheck' = "BLOCKED"
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMember, transaction,
                    releaseMutationEpoch>>

ObservePlacement ==
    /\ ~transaction /\ trafficMember' \in BOOLEAN
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, exactLookup,
                    reversePrecheck, transaction, releaseMutationEpoch>>

EvaluateReady ==
    /\ ~transaction /\ exactLookup = "EXISTS" /\ artifactStatus = "AVAILABLE"
    /\ reversePrecheck' = "READY"
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMember,
                    exactLookup, transaction, observationEpoch, releaseMutationEpoch>>

EvaluateBlocked ==
    /\ ~transaction
    /\ exactLookup = "FAILED" \/ artifactStatus \in {"UNAVAILABLE", "MISMATCH"}
    /\ reversePrecheck' = "BLOCKED"
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMember,
                    exactLookup, transaction, observationEpoch, releaseMutationEpoch>>

BeginReverse ==
    /\ reversePrecheck = "READY" /\ ~transaction
    /\ transaction' = TRUE /\ releaseMutationEpoch' = ~releaseMutationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMember,
                    exactLookup, reversePrecheck, observationEpoch>>

FinishReverse ==
    /\ transaction /\ transaction' = FALSE
    /\ releaseMutationEpoch' = ~releaseMutationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMember,
                    exactLookup, reversePrecheck, observationEpoch>>

CommitAfterSuccessfulObservation ==
    /\ active = "NEW" /\ committed = "OLD" /\ ~transaction
    /\ committed' = "NEW" /\ lkg' = "NEW"
    /\ releaseMutationEpoch' = ~releaseMutationEpoch
    /\ UNCHANGED <<active, artifactStatus, trafficMember, exactLookup,
                    reversePrecheck, transaction, observationEpoch>>

Next == ObserveActive \/ ExactArtifactExists \/ ExactArtifactFails \/
        ObservePlacement \/ EvaluateReady \/ EvaluateBlocked \/ BeginReverse \/
        FinishReverse \/ CommitAfterSuccessfulObservation

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ committed \in Ids /\ active \in Ids /\ lkg \in Ids
    /\ artifactStatus \in ArtifactStates /\ trafficMember \in BOOLEAN
    /\ exactLookup \in LookupStates /\ reversePrecheck \in PrecheckStates
    /\ transaction \in BOOLEAN /\ observationEpoch \in BOOLEAN
    /\ releaseMutationEpoch \in BOOLEAN
ActiveMismatchDoesNotMoveCommittedOrLkg == active # committed => lkg = committed
ArtifactExistenceIndependentFromPlacement ==
    exactLookup = "EXISTS" /\ ~trafficMember => artifactStatus = "AVAILABLE"
NotAssignedAloneDoesNotMeanArtifactMissing ==
    exactLookup = "PENDING" /\ ~trafficMember => artifactStatus = "UNKNOWN"
ReverseAttemptRequiresReadyPrecheck == transaction => reversePrecheck = "READY"
FailedOrUnknownLookupFailsClosed == exactLookup = "FAILED" => reversePrecheck = "BLOCKED"
ReadObservationDoesNotMutateRelease ==
    [][observationEpoch' # observationEpoch =>
        UNCHANGED <<committed, lkg, transaction, releaseMutationEpoch>>]_vars
CommittedChangesOnlyAfterSuccessfulObservation ==
    [][committed' # committed => CommitAfterSuccessfulObservation]_vars

=============================================================================
