------------------------------ MODULE CoreRelease ------------------------------
EXTENDS Naturals, TLC

Ids == {"OLD", "NEW"}
CpuStates == {"NOT_REQUIRED", "PENDING", "QUALIFIED", "HARD_FAILURE"}
VARIABLES phase, stable, previous, candidate, productionOwner, releaseAccepted,
          cpuRequired, cpuState, health, transaction, kind, applied,
          observeFailed, switchFailed, recoveryCompleted, syncOwners
VARIABLES projectionRequired, projectionState, projectionKey
coreVars == <<phase, stable, previous, candidate, productionOwner, releaseAccepted,
          cpuRequired, cpuState, health, transaction, kind, applied,
          observeFailed, switchFailed, recoveryCompleted, syncOwners>>
projectionVars == <<projectionRequired, projectionState, projectionKey>>
vars == <<coreVars, projectionVars>>

Init ==
    /\ phase = "STABLE" /\ stable = "OLD" /\ previous = "OLD"
    /\ candidate = "NEW" /\ productionOwner = "OLD" /\ releaseAccepted = FALSE
    /\ cpuRequired = TRUE /\ cpuState = "PENDING" /\ health = "GOOD"
    /\ transaction = FALSE /\ kind = "NONE" /\ applied = FALSE
    /\ observeFailed = FALSE /\ switchFailed = FALSE
    /\ recoveryCompleted = FALSE /\ syncOwners = 1
    /\ projectionRequired \in BOOLEAN
    /\ projectionState = "NOT_STARTED" /\ projectionKey = "NONE"
Prepare ==
    /\ phase = "STABLE" /\ ~transaction /\ ~recoveryCompleted
    /\ phase' = "PREPARE"
    /\ UNCHANGED <<stable, previous, candidate, productionOwner, releaseAccepted,
                    cpuRequired, cpuState, health, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
Verify ==
    /\ phase = "PREPARE" /\ phase' = "VERIFY"
    /\ UNCHANGED <<stable, previous, candidate, productionOwner, releaseAccepted,
                    cpuRequired, cpuState, health, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
AbstractCpuQualifies ==
    /\ phase = "VERIFY" /\ cpuState = "PENDING"
    /\ cpuState' = "QUALIFIED"
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner, releaseAccepted,
                    cpuRequired, health, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
AcceptRelease ==
    /\ phase = "VERIFY" /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ releaseAccepted' = TRUE
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner,
                    cpuRequired, cpuState, health, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
BeginSwitch ==
    /\ phase = "VERIFY" /\ releaseAccepted
    /\ (~cpuRequired \/ cpuState = "QUALIFIED")
    /\ phase' = "SWITCH" /\ previous' = stable /\ transaction' = TRUE
    /\ kind' = "FORWARD" /\ applied' = FALSE
    /\ UNCHANGED <<stable, candidate, productionOwner, releaseAccepted, cpuRequired,
                    cpuState, health, observeFailed, switchFailed,
                    recoveryCompleted, syncOwners>>
DegradeHealth ==
    /\ transaction /\ phase \in {"SWITCH", "OBSERVE"} /\ health = "GOOD"
    /\ health' = "BAD"
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner,
                    releaseAccepted, cpuRequired, cpuState, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
RestoreHealth ==
    /\ health = "BAD" /\ health' = "GOOD"
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner,
                    releaseAccepted, cpuRequired, cpuState, transaction, kind, applied,
                    observeFailed, switchFailed, recoveryCompleted, syncOwners>>
ApplySwitch ==
    /\ phase = "SWITCH" /\ kind = "FORWARD" /\ health = "GOOD" /\ ~applied
    /\ productionOwner' = candidate /\ phase' = "OBSERVE" /\ applied' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, releaseAccepted, cpuRequired, cpuState,
                    health, transaction, kind, observeFailed, switchFailed,
                    recoveryCompleted, syncOwners>>
FailSwitch ==
    /\ phase = "SWITCH" /\ kind = "FORWARD" /\ health = "BAD"
    /\ kind' = "RECOVER" /\ switchFailed' = TRUE
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner,
                    releaseAccepted, cpuRequired, cpuState, health, transaction, applied,
                    observeFailed, recoveryCompleted, syncOwners>>
ObserveSuccess ==
    /\ phase = "OBSERVE" /\ kind = "FORWARD" /\ health = "GOOD"
    /\ ~projectionRequired \/ (projectionState = "ACCEPTED" /\ projectionKey = candidate)
    /\ stable' = candidate /\ productionOwner' = candidate /\ phase' = "STABLE"
    /\ transaction' = FALSE /\ kind' = "NONE" /\ recoveryCompleted' = TRUE
    /\ UNCHANGED <<previous, candidate, releaseAccepted, cpuRequired, cpuState, health,
                    applied, observeFailed, switchFailed, syncOwners>>
ObserveFailure ==
    /\ phase = "OBSERVE" /\ kind = "FORWARD"
    /\ health = "BAD" \/ projectionState = "FAILED"
    /\ kind' = "RECOVER" /\ observeFailed' = TRUE
    /\ UNCHANGED <<phase, stable, previous, candidate, productionOwner,
                    releaseAccepted, cpuRequired, cpuState, health, transaction, applied,
                    switchFailed, recoveryCompleted, syncOwners>>
ApplyRecoverySwitch ==
    /\ transaction /\ kind = "RECOVER"
    /\ productionOwner' = previous /\ phase' = "OBSERVE" /\ applied' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, releaseAccepted, cpuRequired, cpuState,
                    health, transaction, kind, observeFailed, switchFailed,
                    recoveryCompleted, syncOwners>>
ObserveRecovery ==
    /\ phase = "OBSERVE" /\ kind = "RECOVER" /\ health = "GOOD"
    /\ productionOwner' = previous /\ phase' = "STABLE" /\ transaction' = FALSE
    /\ kind' = "NONE" /\ recoveryCompleted' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, releaseAccepted, cpuRequired, cpuState,
                    health, applied, observeFailed, switchFailed, syncOwners>>

CoreNext == Prepare \/ Verify \/ AbstractCpuQualifies \/ AcceptRelease \/ BeginSwitch \/
        DegradeHealth \/ RestoreHealth \/ ApplySwitch \/ FailSwitch \/
        ObserveSuccess \/ ObserveFailure \/ ApplyRecoverySwitch \/ ObserveRecovery
\* The producer starts only after the real Switch. No pages, provider samples,
\* or install state are multiplied into this lifecycle. The Python/Worker and
\* deferred-parity consumers own concrete count/digest/transaction validation.
ProjectionOnCoreStep ==
    /\ UNCHANGED projectionRequired
    /\ IF ApplySwitch
          THEN /\ projectionState' = IF projectionRequired THEN "PENDING" ELSE "NOT_REQUIRED"
               /\ projectionKey' = "NONE"
          ELSE UNCHANGED <<projectionState, projectionKey>>
ProjectionPending ==
    phase = "OBSERVE" /\ kind = "FORWARD" /\ projectionState = "PENDING"
AcceptProjection ==
    /\ ProjectionPending
    /\ projectionState' = "ACCEPTED" /\ projectionKey' = candidate
    /\ UNCHANGED <<coreVars, projectionRequired>>
RejectProjection ==
    /\ ProjectionPending
    /\ projectionState' = "FAILED" /\ projectionKey' \in {"OLD", "NONE"}
    /\ UNCHANGED <<coreVars, projectionRequired>>
ExpireProjection ==
    /\ ProjectionPending
    /\ projectionState' = "FAILED" /\ projectionKey' = "NONE"
    /\ UNCHANGED <<coreVars, projectionRequired>>
ProjectionResolves == AcceptProjection \/ RejectProjection \/ ExpireProjection
Next == (CoreNext /\ ProjectionOnCoreStep) \/ ProjectionResolves
SafetySpec == Init /\ [][Next]_vars
LivenessSpec ==
    /\ SafetySpec
    /\ WF_vars(RestoreHealth /\ ProjectionOnCoreStep)
    /\ SF_vars(ApplySwitch /\ ProjectionOnCoreStep)
    /\ SF_vars(FailSwitch /\ ProjectionOnCoreStep)
    /\ SF_vars(ObserveSuccess /\ ProjectionOnCoreStep)
    /\ SF_vars(ObserveFailure /\ ProjectionOnCoreStep)
    /\ SF_vars(ApplyRecoverySwitch /\ ProjectionOnCoreStep)
    /\ SF_vars(ObserveRecovery /\ ProjectionOnCoreStep)
    \* A pending producer either supplies a valid ACK, fails, or reaches the
    \* existing bounded observation deadline. Fairness never assumes ACK success.
    /\ WF_vars(ProjectionResolves)

TypeOK ==
    /\ phase \in {"STABLE", "PREPARE", "VERIFY", "SWITCH", "OBSERVE"}
    /\ stable \in Ids /\ previous \in Ids /\ candidate \in Ids /\ productionOwner \in Ids
    /\ releaseAccepted \in BOOLEAN /\ cpuRequired \in BOOLEAN /\ cpuState \in CpuStates
    /\ health \in {"GOOD", "BAD"} /\ transaction \in BOOLEAN
    /\ kind \in {"NONE", "FORWARD", "RECOVER"} /\ applied \in BOOLEAN
    /\ observeFailed \in BOOLEAN /\ switchFailed \in BOOLEAN
    /\ recoveryCompleted \in BOOLEAN /\ syncOwners \in 0..1
    /\ projectionRequired \in BOOLEAN
    /\ projectionState \in {"NOT_STARTED", "NOT_REQUIRED", "PENDING", "ACCEPTED", "FAILED"}
    /\ projectionKey \in Ids \cup {"NONE"}
AtMostOneProductionWriter == syncOwners <= 1
PrepareVerifyKeepsStableSync == phase \in {"PREPARE", "VERIFY"} => syncOwners = 1
CandidatePreparationPreservesStable ==
    phase \in {"PREPARE", "VERIFY"} => stable = productionOwner
SwitchRequiresAcceptance == kind = "FORWARD" /\ transaction => releaseAccepted /\ cpuState = "QUALIFIED"
StableUnchangedDuringSwitchAndObserve == transaction => stable = previous
SingleTransaction == transaction <=> phase \in {"SWITCH", "OBSERVE"}
NoPromoteFromPendingCpu == phase \in {"SWITCH", "OBSERVE"} /\ kind = "FORWARD" => cpuState = "QUALIFIED"
StableChangesOnlyAfterObservation == [][stable' # stable => ObserveSuccess]_vars
CommitRequiresExactDeferredAck ==
    stable = candidate /\ projectionRequired =>
        projectionState = "ACCEPTED" /\ projectionKey = candidate
PendingOrFailedProjectionKeepsPreviousCommitted ==
    projectionState \in {"PENDING", "FAILED"} => stable = previous
ProjectionFailureEventuallyRestoresPrevious ==
    [](projectionState = "FAILED" /\ ~recoveryCompleted =>
        <> (recoveryCompleted /\ productionOwner = previous /\ ~transaction))
ObservedFailureEventuallyRestoresPrevious ==
    [](observeFailed /\ ~recoveryCompleted =>
       <> (recoveryCompleted /\ phase = "STABLE" /\ productionOwner = previous /\ ~transaction))
SwitchFailureEventuallyTerminates ==
    [](switchFailed /\ ~recoveryCompleted =>
       <> (recoveryCompleted /\ phase = "STABLE" /\ productionOwner = previous /\ ~transaction))
TransactionEventuallyTerminates == [](transaction => <> (~transaction /\ phase = "STABLE"))

=============================================================================
