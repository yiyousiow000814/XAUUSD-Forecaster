---------------------- MODULE ReleaseRuntimeReadModel ----------------------
EXTENDS Naturals, TLC

Ids == {"OLD", "NEW"}
ArtifactStates == {"UNKNOWN", "AVAILABLE", "UNAVAILABLE", "MISMATCH"}
LookupStates == {"PENDING", "EXISTS", "FAILED"}
ObservationStates == {"AVAILABLE", "UNKNOWN"}
MembershipStates == {"ASSIGNED", "NOT_ASSIGNED", "UNKNOWN", "MISMATCH", "NOT_APPLICABLE"}
AuthorityStates == {"READY", "BLOCKED"}
HealthStates == {"HEALTHY", "DEGRADED", "UNKNOWN"}

VARIABLES committed, active, lkg, artifactStatus, trafficMembership, exactLookup,
          activeObservationStatus, activeMatchesCommitted, controlAuthority,
          businessHealth, transaction, observationEpoch
vars == <<committed, active, lkg, artifactStatus, trafficMembership, exactLookup,
          activeObservationStatus, activeMatchesCommitted, controlAuthority,
          businessHealth, transaction, observationEpoch>>

SafeReverseAuthority ==
    /\ exactLookup = "EXISTS"
    /\ artifactStatus = "AVAILABLE"
    /\ activeObservationStatus = "AVAILABLE"
    /\ activeMatchesCommitted
    /\ controlAuthority = "READY"

Init ==
    /\ committed = "OLD" /\ active = "OLD" /\ lkg = "OLD"
    /\ artifactStatus = "UNKNOWN" /\ trafficMembership = "UNKNOWN"
    /\ exactLookup = "PENDING" /\ activeObservationStatus = "UNKNOWN"
    /\ activeMatchesCommitted = FALSE /\ controlAuthority = "BLOCKED"
    /\ businessHealth = "UNKNOWN" /\ transaction = FALSE
    /\ observationEpoch = FALSE

ObserveActive ==
    /\ ~transaction
    /\ active' \in Ids
    /\ activeObservationStatus' \in ObservationStates
    /\ activeMatchesCommitted' \in BOOLEAN
    /\ controlAuthority' \in AuthorityStates
    /\ businessHealth' \in HealthStates
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, lkg, artifactStatus, trafficMembership,
                    exactLookup, transaction>>

ExactArtifactExists ==
    /\ ~transaction
    /\ exactLookup' = "EXISTS" /\ artifactStatus' = "AVAILABLE"
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMembership,
                    activeObservationStatus, activeMatchesCommitted,
                    controlAuthority, businessHealth, transaction>>

ExactArtifactFails ==
    /\ ~transaction /\ exactLookup' = "FAILED"
    /\ artifactStatus' \in {"UNAVAILABLE", "MISMATCH", "UNKNOWN"}
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMembership,
                    activeObservationStatus, activeMatchesCommitted,
                    controlAuthority, businessHealth, transaction>>

ObservePlacement ==
    /\ ~transaction /\ trafficMembership' \in MembershipStates
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, exactLookup,
                    activeObservationStatus, activeMatchesCommitted,
                    controlAuthority, businessHealth, transaction>>

BeginReverse ==
    /\ ~transaction /\ SafeReverseAuthority
    /\ transaction' = TRUE
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMembership,
                    exactLookup, activeObservationStatus,
                    activeMatchesCommitted, controlAuthority, businessHealth,
                    observationEpoch>>

FinishReverse ==
    /\ transaction /\ transaction' = FALSE
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMembership,
                    exactLookup, activeObservationStatus,
                    activeMatchesCommitted, controlAuthority, businessHealth,
                    observationEpoch>>

Next == ObserveActive \/ ExactArtifactExists \/ ExactArtifactFails \/
        ObservePlacement \/ BeginReverse \/ FinishReverse

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ committed \in Ids /\ active \in Ids /\ lkg \in Ids
    /\ artifactStatus \in ArtifactStates
    /\ trafficMembership \in MembershipStates
    /\ exactLookup \in LookupStates
    /\ activeObservationStatus \in ObservationStates
    /\ activeMatchesCommitted \in BOOLEAN
    /\ controlAuthority \in AuthorityStates
    /\ businessHealth \in HealthStates
    /\ transaction \in BOOLEAN /\ observationEpoch \in BOOLEAN

ActiveMismatchDoesNotMoveCommittedOrLkg == active # committed => lkg = committed
ArtifactExistenceIndependentFromPlacement ==
    exactLookup = "EXISTS" /\ trafficMembership # "ASSIGNED" => artifactStatus = "AVAILABLE"
NotAssignedAloneDoesNotMeanArtifactMissing ==
    exactLookup = "PENDING" /\ trafficMembership = "NOT_ASSIGNED" => artifactStatus = "UNKNOWN"
ReverseAttemptRequiresSafeAuthority == transaction => SafeReverseAuthority
FailedOrUnknownLookupFailsClosed == exactLookup # "EXISTS" => ~transaction
UnknownActiveObservationFailsClosed == activeObservationStatus # "AVAILABLE" => ~transaction
ActiveDriftFailsClosed == ~activeMatchesCommitted => ~transaction
SingleTransaction == transaction \in BOOLEAN
DegradedAuthorityAllowsReverse ==
    businessHealth = "DEGRADED" /\ SafeReverseAuthority /\ ~transaction => ENABLED BeginReverse
ReadObservationDoesNotMutateRelease ==
    [][observationEpoch' # observationEpoch =>
        UNCHANGED <<committed, lkg, transaction>>]_vars
ReadModelNeverChangesCommittedOrLkg ==
    [][UNCHANGED <<committed, lkg>>]_vars

=============================================================================
