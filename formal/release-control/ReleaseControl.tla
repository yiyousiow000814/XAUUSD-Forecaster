----------------------------- MODULE ReleaseControl -----------------------------
EXTENDS Naturals, TLC

(***************************************************************************
Simplification-first Release/Control Plane model. Only PREPARE, VERIFY,
SWITCH, and OBSERVE are release-attempt lifecycle phases. Holds, reviews,
installation checkpoints, reverse, and recovery are internal operations.
***************************************************************************)

CONSTANT HealthyExternal, AllowControlInstall, AllowMainMove, AllowIdentityDrift

None == "NONE"
StableId == "STABLE"
CandidateId == "CANDIDATE"
NextId == "NEXT"
Identities == {None, StableId, CandidateId, NextId}

VARIABLES
    stable, previous, candidate, candidateGit, candidateWorker,
    candidateWindows, mainGit,
    phase, gate, acceptedEvidence, migrationReady,
    transaction, switchTarget, switchOrigin, switchKind, switchApplied,
    syncState, syncOwners, hold,
    supervisorMode, supervisionEpoch, actorEpoch, installStep,
    currentPresent, stagingFresh, reverseCompatible,
    staleActorRejected, drift

vars == <<stable, previous, candidate, candidateGit, candidateWorker,
    candidateWindows, mainGit, phase, gate, acceptedEvidence, migrationReady,
    transaction, switchTarget, switchOrigin, switchKind, switchApplied,
    syncState, syncOwners, hold, supervisorMode, supervisionEpoch, actorEpoch,
    installStep, currentPresent, stagingFresh, reverseCompatible,
    staleActorRejected, drift>>

ExactCandidate ==
    /\ candidate # None
    /\ candidateGit = candidate
    /\ candidateWorker = candidate
    /\ candidateWindows = candidate

CurrentSupervisorCanMutate ==
    /\ supervisorMode = "ACTIVE"
    /\ actorEpoch = supervisionEpoch

Init ==
    /\ stable = StableId
    /\ previous = None
    /\ candidate = None
    /\ candidateGit = None
    /\ candidateWorker = None
    /\ candidateWindows = None
    /\ mainGit = CandidateId
    /\ phase = "STABLE"
    /\ gate = "UNTESTED"
    /\ acceptedEvidence = FALSE
    /\ migrationReady = FALSE
    /\ transaction = FALSE
    /\ switchTarget = None
    /\ switchOrigin = None
    /\ switchKind = "NONE"
    /\ switchApplied = FALSE
    /\ syncState = "RUNNING"
    /\ syncOwners = 1
    /\ hold = "NONE"
    /\ supervisorMode = "ACTIVE"
    /\ supervisionEpoch = 0
    /\ actorEpoch = 0
    /\ installStep = "IDLE"
    /\ currentPresent = TRUE
    /\ stagingFresh = FALSE
    /\ reverseCompatible = TRUE
    /\ staleActorRejected = TRUE
    /\ drift = FALSE

DiscoverCandidate ==
    /\ phase = "STABLE"
    /\ ~transaction
    /\ candidate = None
    /\ candidate' = CandidateId
    /\ candidateGit' = CandidateId
    /\ candidateWorker' = CandidateId
    /\ candidateWindows' = CandidateId
    /\ phase' = "PREPARE"
    /\ gate' = "UNTESTED"
    /\ acceptedEvidence' = FALSE
    /\ migrationReady' = FALSE
    /\ UNCHANGED <<stable, previous, mainGit, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

MainMoves ==
    /\ AllowMainMove
    /\ mainGit # NextId
    /\ mainGit' = NextId
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, phase, gate, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

BeginHold ==
    /\ phase \in {"PREPARE", "VERIFY"}
    /\ ExactCandidate
    /\ hold = "NONE"
    /\ syncState = "RUNNING"
    /\ syncOwners = 1
    /\ hold' = "ACTIVE"
    /\ syncState' = "STOPPED"
    /\ syncOwners' = 0
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

VerifyMigration ==
    /\ phase \in {"PREPARE", "VERIFY"}
    /\ hold = "ACTIVE"
    /\ migrationReady' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

HoldExpires ==
    /\ hold = "ACTIVE"
    /\ hold' = "EXPIRED"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

HoldMismatches ==
    /\ hold = "ACTIVE"
    /\ ~ExactCandidate
    /\ hold' = "MISMATCHED"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

WatchdogRecover ==
    /\ CurrentSupervisorCanMutate
    /\ hold # "ACTIVE"
    /\ ~transaction
    /\ syncState = "STOPPED"
    /\ syncState' = "RUNNING"
    /\ syncOwners' = 1
    /\ hold' = "NONE"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

WatchdogBlockedByHoldOrSwitch ==
    /\ syncState = "STOPPED"
    /\ (hold = "ACTIVE" \/ transaction)
    /\ UNCHANGED vars

FenceSupervisor ==
    /\ AllowControlInstall
    /\ phase = "PREPARE"
    /\ ~transaction
    /\ installStep = "IDLE"
    /\ CurrentSupervisorCanMutate
    /\ supervisorMode' = "QUIESCED"
    /\ supervisionEpoch' = 1 - supervisionEpoch
    /\ installStep' = "FENCED"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        actorEpoch, currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

CaptureBaseline ==
    /\ installStep = "FENCED"
    /\ supervisorMode = "QUIESCED"
    /\ installStep' = "BASELINED"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, actorEpoch, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

InstallQuiescedSupervisor ==
    /\ installStep = "BASELINED"
    /\ supervisorMode = "QUIESCED"
    /\ installStep' = "NEW_QUIESCED"
    /\ actorEpoch' = supervisionEpoch
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

ActivateSupervisor ==
    /\ HealthyExternal
    /\ installStep = "NEW_QUIESCED"
    /\ supervisorMode = "QUIESCED"
    /\ installStep' = "IDLE"
    /\ supervisorMode' = "ACTIVE"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisionEpoch, actorEpoch, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

FailInstall ==
    /\ ~HealthyExternal
    /\ installStep \in {"FENCED", "BASELINED", "NEW_QUIESCED"}
    /\ installStep' = "FAILED"
    /\ supervisorMode' = "NONE"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisionEpoch, actorEpoch, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

RecoverInstall ==
    /\ installStep = "FAILED"
    /\ installStep' = "IDLE"
    /\ supervisorMode' = "ACTIVE"
    /\ actorEpoch' = supervisionEpoch
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisionEpoch, currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

StaleSupervisorAttempt ==
    /\ actorEpoch # supervisionEpoch
    /\ staleActorRejected' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible, drift>>

CompletePrepare ==
    /\ phase = "PREPARE"
    /\ migrationReady
    /\ installStep = "IDLE"
    /\ CurrentSupervisorCanMutate
    /\ phase' = "VERIFY"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

RequestRetryableEvidence ==
    /\ phase = "VERIFY"
    /\ gate = "UNTESTED"
    /\ gate' = "REVIEW_RETRY"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

RetryEvidence ==
    /\ phase = "VERIFY"
    /\ gate = "REVIEW_RETRY"
    /\ gate' = "UNTESTED"
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

BlockEvidence ==
    /\ phase = "VERIFY"
    /\ gate = "UNTESTED"
    /\ gate' \in {"REVIEW_BLOCKED", "FAILED"}
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        switchApplied, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

PassEvidence ==
    /\ HealthyExternal
    /\ phase = "VERIFY"
    /\ gate = "UNTESTED"
    /\ ExactCandidate
    /\ migrationReady
    /\ reverseCompatible
    /\ gate' = "PASSED"
    /\ acceptedEvidence' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, migrationReady,
        transaction, switchTarget, switchOrigin, switchKind, switchApplied,
        syncState, syncOwners, hold, supervisorMode, supervisionEpoch,
        actorEpoch, installStep, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

CorruptCandidateIdentity ==
    /\ AllowIdentityDrift
    /\ phase \in {"PREPARE", "VERIFY"}
    /\ candidate # None
    /\ candidateWorker = candidate
    /\ candidateWorker' = NextId
    /\ gate' = "FAILED"
    /\ acceptedEvidence' = FALSE
    /\ drift' = TRUE
    /\ hold' = IF hold = "ACTIVE" THEN "MISMATCHED" ELSE hold
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWindows, mainGit, phase, migrationReady, transaction,
        switchTarget, switchOrigin, switchKind, switchApplied, syncState,
        syncOwners, supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected>>

BeginForwardSwitch ==
    /\ phase = "VERIFY"
    /\ ~transaction
    /\ gate = "PASSED"
    /\ acceptedEvidence
    /\ ExactCandidate
    /\ reverseCompatible
    /\ CurrentSupervisorCanMutate
    /\ phase' = "SWITCH"
    /\ transaction' = TRUE
    /\ switchTarget' = candidate
    /\ switchOrigin' = stable
    /\ switchKind' = "FORWARD"
    /\ switchApplied' = FALSE
    /\ hold' = "NONE"
    /\ syncState' = "STOPPED"
    /\ syncOwners' = 0
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, supervisorMode, supervisionEpoch, actorEpoch,
        installStep, currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

BeginReturnToPrevious ==
    /\ phase = "STABLE"
    /\ ~transaction
    /\ previous # None
    /\ CurrentSupervisorCanMutate
    /\ phase' = "SWITCH"
    /\ transaction' = TRUE
    /\ switchTarget' = previous
    /\ switchOrigin' = stable
    /\ switchKind' = "RETURN"
    /\ switchApplied' = FALSE
    /\ syncState' = "STOPPED"
    /\ syncOwners' = 0
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, hold, supervisorMode, supervisionEpoch, actorEpoch,
        installStep, currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

ApplySwitch ==
    /\ HealthyExternal
    /\ phase = "SWITCH"
    /\ transaction
    /\ switchTarget # None
    /\ phase' = "OBSERVE"
    /\ switchApplied' = TRUE
    /\ syncState' = "RUNNING"
    /\ syncOwners' = 1
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        hold, supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

ObserveSuccess ==
    /\ HealthyExternal
    /\ phase = "OBSERVE"
    /\ transaction
    /\ switchApplied
    /\ switchKind \in {"FORWARD", "RETURN"}
    /\ stable' = switchTarget
    /\ previous' = switchOrigin
    /\ phase' = "STABLE"
    /\ transaction' = FALSE
    /\ switchTarget' = None
    /\ switchOrigin' = None
    /\ switchKind' = "NONE"
    /\ switchApplied' = FALSE
    /\ candidate' = IF switchKind = "FORWARD" THEN None ELSE candidate
    /\ candidateGit' = IF switchKind = "FORWARD" THEN None ELSE candidateGit
    /\ candidateWorker' = IF switchKind = "FORWARD" THEN None ELSE candidateWorker
    /\ candidateWindows' = IF switchKind = "FORWARD" THEN None ELSE candidateWindows
    /\ gate' = IF switchKind = "FORWARD" THEN "UNTESTED" ELSE gate
    /\ acceptedEvidence' = IF switchKind = "FORWARD" THEN FALSE ELSE acceptedEvidence
    /\ migrationReady' = IF switchKind = "FORWARD" THEN FALSE ELSE migrationReady
    /\ UNCHANGED <<mainGit,
        syncState, syncOwners, hold, supervisorMode, supervisionEpoch,
        actorEpoch, installStep, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

ObserveFailure ==
    /\ ~HealthyExternal
    /\ phase = "OBSERVE"
    /\ transaction
    /\ switchApplied
    /\ phase' = "SWITCH"
    /\ switchTarget' = switchOrigin
    /\ switchKind' = "RECOVER"
    /\ switchApplied' = FALSE
    /\ syncState' = "STOPPED"
    /\ syncOwners' = 0
    /\ gate' = IF gate = "PASSED" THEN "FAILED" ELSE gate
    /\ acceptedEvidence' = FALSE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, migrationReady,
        transaction, switchOrigin, hold, supervisorMode, supervisionEpoch,
        actorEpoch, installStep, currentPresent, stagingFresh,
        reverseCompatible, staleActorRejected, drift>>

ApplyRecoverySwitch ==
    /\ phase = "SWITCH"
    /\ transaction
    /\ switchKind = "RECOVER"
    /\ switchTarget = switchOrigin
    /\ phase' = "OBSERVE"
    /\ switchApplied' = TRUE
    /\ syncState' = "RUNNING"
    /\ syncOwners' = 1
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, transaction, switchTarget, switchOrigin, switchKind,
        hold, supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

ObserveRecovery ==
    /\ phase = "OBSERVE"
    /\ transaction
    /\ switchKind = "RECOVER"
    /\ switchApplied
    /\ stable = switchOrigin
    /\ phase' = "STABLE"
    /\ transaction' = FALSE
    /\ switchTarget' = None
    /\ switchOrigin' = None
    /\ switchKind' = "NONE"
    /\ switchApplied' = FALSE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, syncState, syncOwners, hold, supervisorMode,
        supervisionEpoch, actorEpoch, installStep, currentPresent,
        stagingFresh, reverseCompatible, staleActorRejected, drift>>

PrepareGeneration ==
    /\ ~stagingFresh
    /\ stagingFresh' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        currentPresent, reverseCompatible, staleActorRejected, drift>>

ActivateGeneration ==
    /\ stagingFresh
    /\ reverseCompatible
    /\ stagingFresh' = FALSE
    /\ currentPresent' = TRUE
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, phase, gate,
        acceptedEvidence, migrationReady, transaction, switchTarget,
        switchOrigin, switchKind, switchApplied, syncState, syncOwners, hold,
        supervisorMode, supervisionEpoch, actorEpoch, installStep,
        reverseCompatible, staleActorRejected, drift>>

CleanupObsolete ==
    /\ currentPresent
    /\ UNCHANGED vars

RestartMachine ==
    /\ installStep \in {"FENCED", "BASELINED", "NEW_QUIESCED"} \/ transaction
    /\ installStep' = IF installStep = "IDLE" THEN installStep ELSE "FAILED"
    /\ supervisorMode' = IF installStep = "IDLE" THEN supervisorMode ELSE "NONE"
    /\ phase' = IF transaction THEN "SWITCH" ELSE phase
    /\ switchTarget' = IF transaction THEN switchOrigin ELSE switchTarget
    /\ switchKind' = IF transaction THEN "RECOVER" ELSE switchKind
    /\ switchApplied' = IF transaction THEN FALSE ELSE switchApplied
    /\ syncState' = IF transaction THEN "STOPPED" ELSE syncState
    /\ syncOwners' = IF transaction THEN 0 ELSE syncOwners
    /\ UNCHANGED <<stable, previous, candidate, candidateGit,
        candidateWorker, candidateWindows, mainGit, gate, acceptedEvidence,
        migrationReady, transaction, switchOrigin, hold, supervisionEpoch,
        actorEpoch, currentPresent, stagingFresh, reverseCompatible,
        staleActorRejected, drift>>

Next ==
    \/ DiscoverCandidate \/ MainMoves \/ BeginHold \/ VerifyMigration
    \/ HoldExpires \/ HoldMismatches \/ WatchdogRecover
    \/ WatchdogBlockedByHoldOrSwitch \/ FenceSupervisor \/ CaptureBaseline
    \/ InstallQuiescedSupervisor \/ ActivateSupervisor \/ FailInstall
    \/ RecoverInstall \/ StaleSupervisorAttempt \/ CompletePrepare
    \/ RequestRetryableEvidence \/ RetryEvidence \/ BlockEvidence
    \/ PassEvidence \/ CorruptCandidateIdentity \/ BeginForwardSwitch
    \/ BeginReturnToPrevious \/ ApplySwitch \/ ObserveSuccess
    \/ ObserveFailure \/ ApplyRecoverySwitch \/ ObserveRecovery
    \/ PrepareGeneration \/ ActivateGeneration \/ CleanupObsolete
    \/ RestartMachine

Spec ==
    /\ Init
    /\ [][Next]_vars
    /\ WF_vars(VerifyMigration)
    /\ WF_vars(WatchdogRecover)
    /\ WF_vars(CaptureBaseline)
    /\ WF_vars(InstallQuiescedSupervisor)
    /\ WF_vars(ActivateSupervisor)
    /\ WF_vars(RecoverInstall)
    /\ WF_vars(CompletePrepare)
    /\ WF_vars(RetryEvidence)
    /\ SF_vars(PassEvidence)
    /\ SF_vars(BeginForwardSwitch)
    /\ WF_vars(ApplySwitch)
    /\ WF_vars(ObserveSuccess)
    /\ SF_vars(ApplyRecoverySwitch)
    /\ SF_vars(ObserveRecovery)

TypeOK ==
    /\ stable \in {StableId, CandidateId, NextId}
    /\ previous \in Identities
    /\ candidate \in Identities
    /\ candidateGit \in Identities
    /\ candidateWorker \in Identities
    /\ candidateWindows \in Identities
    /\ mainGit \in {CandidateId, NextId}
    /\ phase \in {"STABLE", "PREPARE", "VERIFY", "SWITCH", "OBSERVE"}
    /\ gate \in {"UNTESTED", "REVIEW_RETRY", "REVIEW_BLOCKED", "PASSED", "FAILED"}
    /\ switchTarget \in Identities
    /\ switchOrigin \in Identities
    /\ switchKind \in {"NONE", "FORWARD", "RETURN", "RECOVER"}
    /\ syncState \in {"RUNNING", "STOPPED"}
    /\ syncOwners \in 0..1
    /\ hold \in {"NONE", "ACTIVE", "EXPIRED", "MISMATCHED"}
    /\ supervisorMode \in {"ACTIVE", "QUIESCED", "NONE"}
    /\ supervisionEpoch \in 0..1
    /\ actorEpoch \in 0..1
    /\ installStep \in {"IDLE", "FENCED", "BASELINED", "NEW_QUIESCED", "FAILED"}

AtMostOneProductionWriter == syncOwners <= 1
ActiveHoldOwnsStoppedSync == hold = "ACTIVE" => syncState = "STOPPED" /\ syncOwners = 0
ExpiredOrMismatchedHoldHasNoAuthority ==
    hold \in {"EXPIRED", "MISMATCHED"} => (syncOwners = 0 \/ syncState = "RUNNING")
BaselineRequiresFence == installStep \in {"BASELINED", "NEW_QUIESCED"} => supervisorMode = "QUIESCED"
StaleSupervisorIsFenced == actorEpoch # supervisionEpoch => ~CurrentSupervisorCanMutate
PassedIdentityIsExact == gate = "PASSED" => ExactCandidate
ReviewIsNotPassed == gate \in {"REVIEW_RETRY", "REVIEW_BLOCKED"} => gate # "PASSED"
AcceptedEvidenceIsRequired == gate = "PASSED" => acceptedEvidence
SwitchRequiresAcceptance == switchKind = "FORWARD" /\ transaction => gate = "PASSED" /\ acceptedEvidence
StableUnchangedDuringSwitchAndObserve == transaction => stable = switchOrigin
SingleTransaction == transaction <=> phase \in {"SWITCH", "OBSERVE"}
CurrentAndFreshStagingSurviveCleanup == currentPresent /\ (stagingFresh => currentPresent)
CurrentKeepsReverseCompatibility == ~stagingFresh => reverseCompatible
UnknownIdentityFailsClosed == ~ExactCandidate => gate # "PASSED"

StableChangesOnlyAfterObservation ==
    [][(stable' # stable) => ObserveSuccess]_vars

ValidTargetEventuallyStable ==
    [](HealthyExternal /\ phase = "VERIFY" /\ gate = "PASSED"
       /\ acceptedEvidence /\ ExactCandidate => <> (phase = "STABLE" /\ ~transaction))

RetryableReviewEventuallyExits ==
    [](HealthyExternal /\ phase = "VERIFY" /\ gate = "REVIEW_RETRY"
       => <> (gate = "UNTESTED" \/ gate = "PASSED"))

InstallEventuallyRecovers ==
    [](installStep = "FAILED" => <> (installStep = "IDLE" /\ supervisorMode = "ACTIVE"))

StoppedSyncEventuallyResumes ==
    [](HealthyExternal /\ syncState = "STOPPED" /\ hold # "ACTIVE"
       => <> (syncState = "RUNNING" /\ syncOwners = 1))

SwitchEventuallyTerminates ==
    [](HealthyExternal /\ transaction => <> (~transaction /\ phase = "STABLE"))

=============================================================================
