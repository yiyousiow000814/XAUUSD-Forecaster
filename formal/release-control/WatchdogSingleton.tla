------------------------ MODULE WatchdogSingleton ------------------------
EXTENDS Naturals

Modes == {"ACTIVE", "QUIESCED_INSTALL", "ABSENT"}
GuardStates == {"IDLE", "HEALTHY", "TERMINATING", "TERMINATED", "UNRESOLVED"}
Receipts == {"VALID", "STALE", "ABSENT"}

VARIABLES mutexOwners, activeWriters, mode, receipt, heartbeatFresh,
          guardState, instanceEpoch
vars == <<mutexOwners, activeWriters, mode, receipt, heartbeatFresh,
          guardState, instanceEpoch>>

Init == /\ mutexOwners = 1 /\ activeWriters = 1 /\ mode = "ACTIVE"
        /\ receipt = "VALID" /\ heartbeatFresh = TRUE
        /\ guardState = "IDLE" /\ instanceEpoch = 0

DuplicateLaunch == /\ mutexOwners = 1 /\ UNCHANGED vars
HealthyGuardCheck ==
    /\ mutexOwners = 1 /\ receipt = "VALID" /\ heartbeatFresh
    /\ guardState' = "HEALTHY"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch>>
HeartbeatBecomesStale ==
    /\ mutexOwners = 1 /\ heartbeatFresh
    /\ heartbeatFresh' = FALSE
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    guardState, instanceEpoch>>
GuardBeginsTermination ==
    /\ mutexOwners = 1 /\ ~heartbeatFresh /\ receipt = "VALID"
    /\ guardState' = "TERMINATING"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch>>
TerminationProved ==
    /\ guardState = "TERMINATING"
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "STALE" /\ guardState' = "TERMINATED"
    /\ UNCHANGED <<heartbeatFresh, instanceEpoch>>
TerminationUnresolved ==
    /\ guardState = "TERMINATING"
    /\ guardState' = "UNRESOLVED"
    /\ UNCHANGED <<mutexOwners, activeWriters, mode, receipt,
                    heartbeatFresh, instanceEpoch>>
OwnerDies ==
    /\ mutexOwners = 1
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "STALE" /\ heartbeatFresh' = FALSE
    /\ guardState' = "TERMINATED"
    /\ UNCHANGED instanceEpoch
StartReplacement ==
    /\ mutexOwners = 0 /\ guardState = "TERMINATED"
    /\ mutexOwners' = 1 /\ activeWriters' = 1 /\ mode' = "ACTIVE"
    /\ receipt' = "VALID" /\ heartbeatFresh' = TRUE
    /\ guardState' = "IDLE" /\ instanceEpoch' = 1 - instanceEpoch
ReleaseForInstall ==
    /\ mutexOwners = 1 /\ mode = "ACTIVE"
    /\ mutexOwners' = 0 /\ activeWriters' = 0 /\ mode' = "ABSENT"
    /\ receipt' = "ABSENT" /\ heartbeatFresh' = FALSE
    /\ guardState' = "TERMINATED"
    /\ UNCHANGED instanceEpoch
StartInstallQuiesced ==
    /\ mutexOwners = 0 /\ guardState = "TERMINATED"
    /\ mutexOwners' = 1 /\ activeWriters' = 0
    /\ mode' = "QUIESCED_INSTALL" /\ receipt' = "VALID"
    /\ heartbeatFresh' = TRUE /\ guardState' = "HEALTHY"
    /\ instanceEpoch' = 1 - instanceEpoch
ActivateInstalled ==
    /\ mutexOwners = 1 /\ mode = "QUIESCED_INSTALL" /\ receipt = "VALID"
    /\ mode' = "ACTIVE" /\ activeWriters' = 1
    /\ UNCHANGED <<mutexOwners, receipt, heartbeatFresh,
                    guardState, instanceEpoch>>

Next == DuplicateLaunch \/ HealthyGuardCheck \/ HeartbeatBecomesStale \/
        GuardBeginsTermination \/ TerminationProved \/ TerminationUnresolved \/
        OwnerDies \/ StartReplacement \/ ReleaseForInstall \/
        StartInstallQuiesced \/ ActivateInstalled
SafetySpec == Init /\ [][Next]_vars
LivenessSpec == /\ SafetySpec /\ WF_vars(StartReplacement)

TypeOK == /\ mutexOwners \in 0..1 /\ activeWriters \in 0..1
          /\ mode \in Modes /\ receipt \in Receipts
          /\ heartbeatFresh \in BOOLEAN /\ guardState \in GuardStates
          /\ instanceEpoch \in 0..1
AtMostOneMachineOwner == mutexOwners <= 1
AtMostOneActiveWriter == activeWriters <= 1
ActiveWriterRequiresVerifiedOwnership ==
    activeWriters = 1 => mutexOwners = 1 /\ receipt = "VALID" /\ mode = "ACTIVE"
QuiescedInstallCannotWrite == mode = "QUIESCED_INSTALL" => activeWriters = 0
UnresolvedTerminationCannotStartReplacement ==
    guardState = "UNRESOLVED" => mutexOwners = 1 /\ activeWriters <= 1
DeadOrTerminatedOwnerEventuallyReplaced ==
    [](mutexOwners = 0 /\ guardState = "TERMINATED" => <> (mutexOwners = 1))
=============================================================================
