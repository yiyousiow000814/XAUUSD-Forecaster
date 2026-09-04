------------------------ MODULE WatchdogSingleton ------------------------
EXTENDS Naturals

Modes == {"ACTIVE", "QUIESCED_INSTALL", "ABSENT"}
GuardStates == {"IDLE", "HEALTHY", "TERMINATING", "TERMINATED", "UNRESOLVED"}
Receipts == {"VALID", "STALE", "ABSENT"}

VARIABLES mutexOwners, activeWriters, mode, receipt, heartbeatFresh,
          guardState, instanceEpoch, businessOwnerSet, unknownDescendant
vars == <<mutexOwners, activeWriters, mode, receipt, heartbeatFresh,
          guardState, instanceEpoch, businessOwnerSet, unknownDescendant>>

Init == /\ mutexOwners = 1 /\ activeWriters = 1 /\ mode = "ACTIVE"
        /\ receipt = "VALID" /\ heartbeatFresh = TRUE
        /\ guardState = "IDLE" /\ instanceEpoch = 0
        /\ businessOwnerSet = 1 /\ unknownDescendant = FALSE

DuplicateLaunch == /\ mutexOwners = 1 /\ UNCHANGED vars
HealthyGuardCheck ==
    /\ mutexOwners = 1 /\ receipt = "VALID" /\ heartbeatFresh
    /\ guardState' = "HEALTHY"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>
HeartbeatBecomesStale ==
    /\ mutexOwners = 1 /\ heartbeatFresh
    /\ heartbeatFresh' = FALSE
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    guardState, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>
GuardBeginsTermination ==
    /\ mutexOwners = 1 /\ ~heartbeatFresh /\ receipt = "VALID"
    /\ ~unknownDescendant
    /\ guardState' = "TERMINATING"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>
TerminationProved ==
    /\ guardState = "TERMINATING"
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "STALE" /\ guardState' = "TERMINATED"
    /\ UNCHANGED <<heartbeatFresh, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>
TerminationUnresolved ==
    /\ guardState = "TERMINATING"
    /\ guardState' = "UNRESOLVED"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>
OwnerDies ==
    /\ mutexOwners = 1
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "STALE" /\ heartbeatFresh' = FALSE
    /\ guardState' = "TERMINATED"
    /\ UNCHANGED <<instanceEpoch, businessOwnerSet, unknownDescendant>>
StartReplacement ==
    /\ mutexOwners = 0 /\ guardState = "TERMINATED" /\ ~unknownDescendant
    /\ mutexOwners' = 1 /\ activeWriters' = 1 /\ mode' = "ACTIVE"
    /\ receipt' = "VALID" /\ heartbeatFresh' = TRUE
    /\ guardState' = "IDLE" /\ instanceEpoch' = 1 - instanceEpoch
    /\ UNCHANGED <<businessOwnerSet, unknownDescendant>>
ReleaseForInstall ==
    /\ mutexOwners = 1 /\ mode = "ACTIVE" /\ ~unknownDescendant
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "ABSENT" /\ heartbeatFresh' = FALSE
    /\ guardState' = "TERMINATED"
    /\ UNCHANGED <<instanceEpoch, businessOwnerSet, unknownDescendant>>
StartInstallQuiesced ==
    /\ mutexOwners = 0 /\ guardState = "TERMINATED"
    /\ mutexOwners' = 1 /\ activeWriters' = 0
    /\ mode' = "QUIESCED_INSTALL" /\ receipt' = "VALID"
    /\ heartbeatFresh' = TRUE /\ guardState' = "HEALTHY"
    /\ instanceEpoch' = 1 - instanceEpoch
    /\ UNCHANGED <<businessOwnerSet, unknownDescendant>>
ActivateInstalled ==
    /\ mutexOwners = 1 /\ mode = "QUIESCED_INSTALL" /\ receipt = "VALID"
    /\ mode' = "ACTIVE" /\ activeWriters' = 1
    /\ UNCHANGED <<mutexOwners, receipt, heartbeatFresh,
                    guardState, instanceEpoch, businessOwnerSet,
                    unknownDescendant>>

UnknownDescendantAppears ==
    /\ ~unknownDescendant /\ guardState \in {"IDLE", "HEALTHY"}
    /\ unknownDescendant' = TRUE
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, guardState, instanceEpoch,
                    businessOwnerSet>>
UnknownDescendantClears ==
    /\ unknownDescendant
    /\ unknownDescendant' = FALSE
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, guardState, instanceEpoch,
                    businessOwnerSet>>

Next == DuplicateLaunch \/ HealthyGuardCheck \/ HeartbeatBecomesStale \/
        GuardBeginsTermination \/ TerminationProved \/ TerminationUnresolved \/
        OwnerDies \/ StartReplacement \/ ReleaseForInstall \/
        StartInstallQuiesced \/ ActivateInstalled \/
        UnknownDescendantAppears \/ UnknownDescendantClears
SafetySpec == Init /\ [][Next]_vars
LivenessSpec == /\ SafetySpec /\ WF_vars(StartReplacement)

TypeOK == /\ mutexOwners \in 0..1 /\ activeWriters \in 0..1
          /\ mode \in Modes /\ receipt \in Receipts
          /\ heartbeatFresh \in BOOLEAN /\ guardState \in GuardStates
          /\ instanceEpoch \in 0..1 /\ businessOwnerSet \in 0..1
          /\ unknownDescendant \in BOOLEAN
AtMostOneMachineOwner == mutexOwners <= 1
AtMostOneActiveWriter == activeWriters <= 1
ActiveWriterRequiresVerifiedOwnership ==
    activeWriters = 1 => mutexOwners = 1 /\ receipt = "VALID" /\ mode = "ACTIVE"
QuiescedInstallCannotWrite == mode = "QUIESCED_INSTALL" => activeWriters = 0
UnresolvedTerminationCannotStartReplacement ==
    guardState = "UNRESOLVED" => mutexOwners = 1 /\ activeWriters <= 1
ControllerReplacementPreservesBusinessOwnerSet == businessOwnerSet = 1
UnknownDescendantBlocksReplacement ==
    unknownDescendant => guardState # "TERMINATING"
DeadOrTerminatedOwnerEventuallyReplaced ==
    [](mutexOwners = 0 /\ guardState = "TERMINATED" /\ ~unknownDescendant
        => <> (mutexOwners = 1))
=============================================================================
