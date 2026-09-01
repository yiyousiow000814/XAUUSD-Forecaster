---------------------- MODULE ReleaseRuntimeReadModel ----------------------
EXTENDS Naturals, TLC

Ids == {"OLD", "NEW"}
ArtifactStates == {"UNKNOWN", "AVAILABLE", "UNAVAILABLE", "MISMATCH"}
LookupStates == {"PENDING", "EXISTS", "FAILED"}
ObservationStates == {"AVAILABLE", "UNKNOWN"}
MembershipStates == {"ASSIGNED", "NOT_ASSIGNED", "UNKNOWN", "MISMATCH", "NOT_APPLICABLE"}
AuthorityStates == {"READY", "BLOCKED"}
HealthStates == {"HEALTHY", "DEGRADED", "UNKNOWN"}
OwnershipStates == {"SINGLE_OWNER", "INVALID", "UNKNOWN"}

VARIABLES committed, active, lkg, artifactStatus, trafficMembership, exactLookup,
          activeObservationStatus, controlAuthority,
          businessHealth, ownershipStatus, committedIdentityValid,
          previousIdentityValid, previousIsLegacy, exactNarrowLegacyPair,
          explicitObservationStatus, transaction, observationEpoch
vars == <<committed, active, lkg, artifactStatus, trafficMembership, exactLookup,
          activeObservationStatus, controlAuthority,
          businessHealth, ownershipStatus, committedIdentityValid,
          previousIdentityValid, previousIsLegacy, exactNarrowLegacyPair,
          explicitObservationStatus, transaction, observationEpoch>>

ActiveMatchesCommitted ==
    /\ activeObservationStatus = "AVAILABLE"
    /\ explicitObservationStatus
    /\ active = committed

SafeReverseAuthority ==
    /\ exactLookup = "EXISTS"
    /\ artifactStatus = "AVAILABLE"
    /\ activeObservationStatus = "AVAILABLE"
    /\ ActiveMatchesCommitted
    /\ controlAuthority = "READY"
    /\ committedIdentityValid
    /\ previousIdentityValid
    /\ (~previousIsLegacy \/ exactNarrowLegacyPair)
    /\ ownershipStatus = "SINGLE_OWNER"
    /\ explicitObservationStatus

Init ==
    /\ committed = "OLD" /\ active = "OLD" /\ lkg = "OLD"
    /\ artifactStatus = "UNKNOWN" /\ trafficMembership = "UNKNOWN"
    /\ exactLookup = "PENDING" /\ activeObservationStatus = "UNKNOWN"
    /\ controlAuthority = "BLOCKED"
    /\ businessHealth = "UNKNOWN" /\ ownershipStatus = "UNKNOWN"
    /\ committedIdentityValid = FALSE /\ previousIdentityValid = FALSE
    /\ previousIsLegacy = FALSE /\ exactNarrowLegacyPair = FALSE
    /\ explicitObservationStatus = FALSE /\ transaction = FALSE
    /\ observationEpoch = FALSE

ObserveActive ==
    /\ ~transaction
    /\ active' \in Ids
    /\ \/ /\ explicitObservationStatus' = TRUE
            /\ activeObservationStatus' \in ObservationStates
       \/ /\ explicitObservationStatus' = FALSE
            /\ activeObservationStatus' = "UNKNOWN"
    /\ controlAuthority' \in AuthorityStates
    /\ businessHealth' \in HealthStates
    /\ ownershipStatus' \in OwnershipStates
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, lkg, artifactStatus, trafficMembership,
                    exactLookup, committedIdentityValid, previousIdentityValid,
                    previousIsLegacy, exactNarrowLegacyPair, transaction>>

ObserveIdentity ==
    /\ ~transaction
    /\ committedIdentityValid' \in BOOLEAN
    /\ previousIdentityValid' \in BOOLEAN
    /\ previousIsLegacy' \in BOOLEAN
    /\ exactNarrowLegacyPair' \in BOOLEAN
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMembership,
                    exactLookup, activeObservationStatus,
                    controlAuthority, businessHealth,
                    ownershipStatus, explicitObservationStatus, transaction>>

ExactArtifactExists ==
    /\ ~transaction
    /\ previousIdentityValid
    /\ (~previousIsLegacy \/ exactNarrowLegacyPair)
    /\ exactLookup' = "EXISTS" /\ artifactStatus' = "AVAILABLE"
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMembership,
                    activeObservationStatus,
                    controlAuthority, businessHealth, ownershipStatus,
                    committedIdentityValid, previousIdentityValid,
                    previousIsLegacy, exactNarrowLegacyPair,
                    explicitObservationStatus, transaction>>

ExactArtifactFails ==
    /\ ~transaction /\ exactLookup' = "FAILED"
    /\ artifactStatus' \in {"UNAVAILABLE", "MISMATCH", "UNKNOWN"}
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, trafficMembership,
                    activeObservationStatus,
                    controlAuthority, businessHealth, ownershipStatus,
                    committedIdentityValid, previousIdentityValid,
                    previousIsLegacy, exactNarrowLegacyPair,
                    explicitObservationStatus, transaction>>

ObservePlacement ==
    /\ ~transaction /\ trafficMembership' \in MembershipStates
    /\ observationEpoch' = ~observationEpoch
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, exactLookup,
                    activeObservationStatus,
                    controlAuthority, businessHealth, ownershipStatus,
                    committedIdentityValid, previousIdentityValid,
                    previousIsLegacy, exactNarrowLegacyPair,
                    explicitObservationStatus, transaction>>

BeginReverse ==
    /\ ~transaction /\ SafeReverseAuthority
    /\ transaction' = TRUE
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMembership,
                    exactLookup, activeObservationStatus,
                    controlAuthority, businessHealth,
                    ownershipStatus, committedIdentityValid,
                    previousIdentityValid, previousIsLegacy,
                    exactNarrowLegacyPair, explicitObservationStatus,
                    observationEpoch>>

FinishReverse ==
    /\ transaction /\ transaction' = FALSE
    /\ UNCHANGED <<committed, active, lkg, artifactStatus, trafficMembership,
                    exactLookup, activeObservationStatus,
                    controlAuthority, businessHealth,
                    ownershipStatus, committedIdentityValid,
                    previousIdentityValid, previousIsLegacy,
                    exactNarrowLegacyPair, explicitObservationStatus,
                    observationEpoch>>

Next == ObserveActive \/ ObserveIdentity \/ ExactArtifactExists \/
        ExactArtifactFails \/ ObservePlacement \/ BeginReverse \/ FinishReverse

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ committed \in Ids /\ active \in Ids /\ lkg \in Ids
    /\ artifactStatus \in ArtifactStates
    /\ trafficMembership \in MembershipStates
    /\ exactLookup \in LookupStates
    /\ activeObservationStatus \in ObservationStates
    /\ controlAuthority \in AuthorityStates
    /\ businessHealth \in HealthStates
    /\ ownershipStatus \in OwnershipStates
    /\ committedIdentityValid \in BOOLEAN
    /\ previousIdentityValid \in BOOLEAN
    /\ previousIsLegacy \in BOOLEAN
    /\ exactNarrowLegacyPair \in BOOLEAN
    /\ explicitObservationStatus \in BOOLEAN
    /\ transaction \in BOOLEAN /\ observationEpoch \in BOOLEAN

ActiveMismatchDoesNotMoveCommittedOrLkg == active # committed => lkg = committed
ArtifactExistenceIndependentFromPlacement ==
    exactLookup = "EXISTS" /\ trafficMembership # "ASSIGNED" => artifactStatus = "AVAILABLE"
NotAssignedAloneDoesNotMeanArtifactMissing ==
    exactLookup = "PENDING" /\ trafficMembership = "NOT_ASSIGNED" => artifactStatus = "UNKNOWN"
ReverseAttemptRequiresSafeAuthority == transaction => SafeReverseAuthority
FailedOrUnknownLookupFailsClosed == exactLookup # "EXISTS" => ~transaction
UnknownActiveObservationFailsClosed == activeObservationStatus # "AVAILABLE" => ~transaction
ActiveDriftFailsClosed == active # committed => ~transaction
ReverseTransactionRequiresActualActiveCommittedEquality ==
    transaction => active = committed
SingleTransaction == transaction \in BOOLEAN
DegradedAuthorityAllowsReverse ==
    businessHealth = "DEGRADED" /\ SafeReverseAuthority /\ ~transaction => ENABLED BeginReverse
InvalidCommittedIdentityFailsClosed == ~committedIdentityValid => ~transaction
InvalidPreviousIdentityFailsClosed == ~previousIdentityValid => ~transaction
ArbitraryLegacyLabelFailsClosed ==
    previousIsLegacy /\ ~exactNarrowLegacyPair => ~transaction
ExactNarrowLegacyReachesArtifactEvaluation ==
    previousIdentityValid /\ previousIsLegacy /\ exactNarrowLegacyPair /\
    ~transaction => ENABLED ExactArtifactExists
InvalidOwnershipFailsClosed == ownershipStatus # "SINGLE_OWNER" => ~transaction
MissingObservationStatusIsNotAvailable ==
    ~explicitObservationStatus => activeObservationStatus # "AVAILABLE"
ReadObservationDoesNotMutateRelease ==
    [][observationEpoch' # observationEpoch =>
        UNCHANGED <<committed, lkg, transaction>>]_vars
ReadModelNeverChangesCommittedOrLkg ==
    [][UNCHANGED <<committed, lkg>>]_vars

=============================================================================
