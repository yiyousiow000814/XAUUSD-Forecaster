------------------------------ MODULE CoreRelease ------------------------------
EXTENDS Naturals, TLC

Ids == {"OLD", "NEW"}
CpuStates == {"NOT_REQUIRED", "PENDING", "QUALIFIED", "HARD_FAILURE"}
VARIABLES phase, stable, previous, candidate, productionOwner, releaseAccepted,
          cpuRequired, cpuState, health, transaction, kind, applied,
          observeFailed, switchFailed, recoveryCompleted, syncOwners
vars == <<phase, stable, previous, candidate, productionOwner, releaseAccepted,
          cpuRequired, cpuState, health, transaction, kind, applied,
          observeFailed, switchFailed, recoveryCompleted, syncOwners>>

Init ==
    /\ phase = "STABLE" /\ stable = "OLD" /\ previous = "OLD"
    /\ candidate = "NEW" /\ productionOwner = "OLD" /\ releaseAccepted = FALSE
    /\ cpuRequired = TRUE /\ cpuState = "PENDING" /\ health = "GOOD"
    /\ transaction = FALSE /\ kind = "NONE" /\ applied = FALSE
    /\ observeFailed = FALSE /\ switchFailed = FALSE
    /\ recoveryCompleted = FALSE /\ syncOwners = 1
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
    /\ stable' = candidate /\ productionOwner' = candidate /\ phase' = "STABLE"
    /\ transaction' = FALSE /\ kind' = "NONE" /\ recoveryCompleted' = TRUE
    /\ UNCHANGED <<previous, candidate, releaseAccepted, cpuRequired, cpuState, health,
                    applied, observeFailed, switchFailed, syncOwners>>
ObserveFailure ==
    /\ phase = "OBSERVE" /\ kind = "FORWARD" /\ health = "BAD"
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

Next == Prepare \/ Verify \/ AbstractCpuQualifies \/ AcceptRelease \/ BeginSwitch \/
        DegradeHealth \/ RestoreHealth \/ ApplySwitch \/ FailSwitch \/
        ObserveSuccess \/ ObserveFailure \/ ApplyRecoverySwitch \/ ObserveRecovery
SafetySpec == Init /\ [][Next]_vars
LivenessSpec ==
    /\ SafetySpec
    /\ WF_vars(RestoreHealth)
    /\ SF_vars(ApplySwitch)
    /\ SF_vars(FailSwitch)
    /\ SF_vars(ObserveSuccess)
    /\ SF_vars(ObserveFailure)
    /\ SF_vars(ApplyRecoverySwitch)
    /\ SF_vars(ObserveRecovery)

TypeOK ==
    /\ phase \in {"STABLE", "PREPARE", "VERIFY", "SWITCH", "OBSERVE"}
    /\ stable \in Ids /\ previous \in Ids /\ candidate \in Ids /\ productionOwner \in Ids
    /\ releaseAccepted \in BOOLEAN /\ cpuRequired \in BOOLEAN /\ cpuState \in CpuStates
    /\ health \in {"GOOD", "BAD"} /\ transaction \in BOOLEAN
    /\ kind \in {"NONE", "FORWARD", "RECOVER"} /\ applied \in BOOLEAN
    /\ observeFailed \in BOOLEAN /\ switchFailed \in BOOLEAN
    /\ recoveryCompleted \in BOOLEAN /\ syncOwners \in 0..1
AtMostOneProductionWriter == syncOwners <= 1
PrepareVerifyKeepsStableSync == phase \in {"PREPARE", "VERIFY"} => syncOwners = 1
CandidatePreparationPreservesStable ==
    phase \in {"PREPARE", "VERIFY"} => stable = productionOwner
SwitchRequiresAcceptance == kind = "FORWARD" /\ transaction => releaseAccepted /\ cpuState = "QUALIFIED"
StableUnchangedDuringSwitchAndObserve == transaction => stable = previous
SingleTransaction == transaction <=> phase \in {"SWITCH", "OBSERVE"}
NoPromoteFromPendingCpu == phase \in {"SWITCH", "OBSERVE"} /\ kind = "FORWARD" => cpuState = "QUALIFIED"
StableChangesOnlyAfterObservation == [][stable' # stable => ObserveSuccess]_vars
ObservedFailureEventuallyRestoresPrevious ==
    [](observeFailed /\ ~recoveryCompleted =>
       <> (recoveryCompleted /\ phase = "STABLE" /\ productionOwner = previous /\ ~transaction))
SwitchFailureEventuallyTerminates ==
    [](switchFailed /\ ~recoveryCompleted =>
       <> (recoveryCompleted /\ phase = "STABLE" /\ productionOwner = previous /\ ~transaction))
TransactionEventuallyTerminates == [](transaction => <> (~transaction /\ phase = "STABLE"))

=============================================================================
