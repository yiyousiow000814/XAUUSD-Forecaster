----------------------------- MODULE ReleaseControl -----------------------------
EXTENDS Naturals, TLC

(***************************************************************************
The operator lifecycle is deliberately small: PREPARE, VERIFY, SWITCH,
OBSERVE. Mutable environment health, News generation compatibility, and
installer-death recovery are composed in the same execution.
***************************************************************************)

CONSTANT AllowControlInstall, AllowMainMove, AllowIdentityDrift

None == "NONE"
StableId == "STABLE"
CandidateId == "CANDIDATE"
NextId == "NEXT"
Identities == {None, StableId, CandidateId, NextId}

OldNews == "OLD"
SharedNews == "SHARED"
NewNews == "NEW"
ExtraNews == "EXTRA"
NewsIdentities == {OldNews, SharedNews, NewNews, ExtraNews}
Generations == 0..2
GenerationIds(g) ==
    IF g = 0 THEN {OldNews, SharedNews}
    ELSE IF g = 1 THEN {SharedNews, NewNews}
    ELSE {OldNews, NewNews}

InstallSteps == {
    "IDLE", "FENCED", "BASELINED", "BUNDLE_SWAPPING",
    "BUNDLE_INSTALLED", "NEW_QUIESCED", "ISOLATION_VERIFIED",
    "ABANDONED_VERIFIED", "ROLLING_BACK"
}
DeathCheckpoints == {
    "BASELINED", "BUNDLE_SWAPPING", "BUNDLE_INSTALLED",
    "NEW_QUIESCED", "ISOLATION_VERIFIED"
}
RecoveryFacts == {
    "TXN", "BUNDLE", "OLD_FENCED", "ONE_OWNER",
    "ISOLATION", "CONTEXT", "NO_RELEASE", "STALE_FENCED"
}
AccessReceiptStates == {"NONE", "VALID", "WRONG_KEY", "TAMPERED", "STALE"}

VARIABLES release, health, install, news, syncOwners, path

vars == <<release, health, install, news, syncOwners, path>>

ExactCandidate ==
    /\ release.candidate # None
    /\ release.candidateExact

CurrentSupervisorCanMutate ==
    /\ install.mode = "ACTIVE"
    /\ install.actorEpoch = install.epoch

ReverseStableCompatible ==
    /\ news.activeLegacyGeneration = news.currentGeneration
    /\ news.activeLegacyIds = news.currentIds

ApplicableAccessEvidence ==
    /\ release.accessAccepted
    /\ release.accessReceiptState = "VALID"
    /\ release.main = release.candidate
    /\ ExactCandidate

FreshStagingCompatible ==
    /\ news.stagingPresent
    /\ news.stagedLegacyPresent
    /\ news.stagedLegacyGeneration = news.stagingGeneration
    /\ news.stagedLegacyIds = news.stagingIds

AbandonedActivationFacts ==
    /\ install.transactionMatches
    /\ install.bundleValid
    /\ install.oldFenced
    /\ install.replacementOwners = 1
    /\ install.isolationMatches
    /\ install.releaseContextCompatible
    /\ ~install.concurrentRelease
    /\ install.staleActorFenced

Init ==
    /\ release = [
        stable |-> StableId,
        prepareStable |-> StableId,
        previous |-> None,
        candidate |-> None,
        candidateExact |-> TRUE,
        main |-> CandidateId,
        phase |-> "STABLE",
        gate |-> "UNTESTED",
        hardSafe |-> TRUE,
        changedSafe |-> TRUE,
        stableDebt |-> FALSE,
        candidateRegression |-> FALSE,
        accessRequired |-> FALSE,
        accessReview |-> FALSE,
        accessReceiptState |-> "NONE",
        accessAccepted |-> FALSE,
        accessApprovalCount |-> 0,
        accepted |-> FALSE,
        migrationReady |-> FALSE,
        transaction |-> FALSE,
        target |-> None,
        origin |-> None,
        kind |-> "NONE",
        applied |-> FALSE,
        verifiedGeneration |-> 0,
        verifiedWatermark |-> 0
        ]
    /\ health = "GOOD"
    /\ install = [
        step |-> "IDLE",
        installerAlive |-> FALSE,
        transactionMatches |-> TRUE,
        bundleValid |-> TRUE,
        oldFenced |-> TRUE,
        replacementOwners |-> 1,
        isolationMatches |-> TRUE,
        releaseContextCompatible |-> TRUE,
        concurrentRelease |-> FALSE,
        staleActorFenced |-> TRUE,
        checksPassed |-> FALSE,
        mode |-> "ACTIVE",
        epoch |-> 0,
        actorEpoch |-> 0,
        deathCheckpoint |-> None
        ]
    /\ news = [
        currentGeneration |-> 0,
        activationWatermark |-> 0,
        currentIds |-> GenerationIds(0),
        activeLegacyGeneration |-> 0,
        activeLegacyIds |-> GenerationIds(0),
        stagingPresent |-> FALSE,
        stagingGeneration |-> 0,
        stagingIds |-> {},
        stagedLegacyPresent |-> FALSE,
        stagedLegacyGeneration |-> 0,
        stagedLegacyIds |-> {},
        legacyWriteObserved |-> FALSE,
        storedGenerations |-> {0}
        ]
    /\ syncOwners = 1
    /\ path = [
        verifyPassed |-> FALSE,
        forwardObserve |-> FALSE,
        observeFailed |-> FALSE,
        recoverySwitched |-> FALSE,
        recoveryCompleted |-> FALSE,
        switchFailed |-> FALSE,
        accessRepeatObserved |-> FALSE
        ]

DiscoverCandidate(accessRequired) ==
    /\ release.phase = "STABLE"
    /\ ~release.transaction
    /\ release.candidate = None
    /\ release' = [release EXCEPT
        !.candidate = CandidateId,
        !.prepareStable = release.stable,
        !.candidateExact = TRUE,
        !.phase = "PREPARE",
        !.gate = "UNTESTED",
        !.hardSafe = TRUE,
        !.changedSafe = TRUE,
        !.stableDebt = FALSE,
        !.candidateRegression = FALSE,
        !.accessRequired = accessRequired,
        !.accessReview = FALSE,
        !.accessReceiptState = "NONE",
        !.accessAccepted = FALSE,
        !.accessApprovalCount = 0,
        !.accepted = FALSE,
        !.migrationReady = FALSE]
    /\ path' = [path EXCEPT !.accessRepeatObserved = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners>>

MainMoves ==
    /\ AllowMainMove
    /\ release.main # NextId
    /\ release' = [release EXCEPT
        !.main = NextId,
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

CorruptCandidateIdentity ==
    /\ AllowIdentityDrift
    /\ release.phase \in {"PREPARE", "VERIFY"}
    /\ release.candidate # None
    /\ release' = [release EXCEPT
        !.candidateExact = FALSE,
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

DegradeHealth ==
    /\ health = "GOOD"
    /\ release.transaction
    /\ release.phase \in {"SWITCH", "OBSERVE"}
    /\ health' = "BAD"
    /\ UNCHANGED <<release, install, news, syncOwners, path>>

RestoreHealth ==
    /\ health = "BAD"
    /\ health' = "GOOD"
    /\ UNCHANGED <<release, install, news, syncOwners, path>>

VerifyMigration ==
    /\ release.phase = "PREPARE"
    /\ ExactCandidate
    /\ syncOwners = 1
    /\ ReverseStableCompatible
    /\ release' = [release EXCEPT
        !.migrationReady = TRUE,
        !.verifiedGeneration = news.currentGeneration,
        !.verifiedWatermark = news.activationWatermark]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

RecordStableDebt ==
    /\ release.phase \in {"PREPARE", "VERIFY"}
    /\ ~release.stableDebt
    /\ release' = [release EXCEPT !.stableDebt = TRUE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

IntroduceCandidateRegression ==
    /\ release.phase \in {"PREPARE", "VERIFY"}
    /\ release.gate = "UNTESTED"
    /\ ~release.candidateRegression
    /\ release' = [release EXCEPT
        !.candidateRegression = TRUE,
        !.changedSafe = FALSE,
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

HardSafetyFails ==
    /\ release.phase \in {"PREPARE", "VERIFY"}
    /\ release.gate = "UNTESTED"
    /\ release.hardSafe
    /\ release' = [release EXCEPT
        !.hardSafe = FALSE,
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

RequireAccessReview ==
    /\ release.phase = "VERIFY"
    /\ release.accessRequired
    /\ ~release.accessReview
    /\ release.gate = "UNTESTED"
    /\ release' = [release EXCEPT !.accessReview = TRUE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

RecordAccessReceipt(kind) ==
    /\ release.phase = "VERIFY"
    /\ release.accessReview
    /\ release.gate = "UNTESTED"
    /\ kind \in AccessReceiptStates \ {"NONE"}
    /\ release' = [release EXCEPT
        !.accessReceiptState = kind,
        !.accessAccepted = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

ApproveAccessReceipt ==
    /\ release.phase = "VERIFY"
    /\ release.accessReview
    /\ release.accessReceiptState = "VALID"
    /\ release.main = release.candidate
    /\ ExactCandidate
    /\ ~release.accessAccepted
    /\ release' = [release EXCEPT
        !.accessAccepted = TRUE,
        !.accessApprovalCount = 1]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

RepeatAccessApproval ==
    /\ release.phase = "VERIFY"
    /\ ApplicableAccessEvidence
    /\ release.accessApprovalCount = 1
    /\ path' = [path EXCEPT !.accessRepeatObserved = TRUE]
    /\ UNCHANGED <<release, health, install, news, syncOwners>>

BeginInstall ==
    /\ AllowControlInstall
    /\ release.phase = "PREPARE"
    /\ ~release.transaction
    /\ install.step = "IDLE"
    /\ CurrentSupervisorCanMutate
    /\ syncOwners = 1
    /\ install' = [install EXCEPT
        !.step = "FENCED",
        !.installerAlive = TRUE,
        !.oldFenced = TRUE,
        !.replacementOwners = 0,
        !.checksPassed = FALSE,
        !.mode = "QUIESCED",
        !.epoch = 1 - @,
        !.deathCheckpoint = None]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

CaptureBaseline ==
    /\ install.step = "FENCED"
    /\ install.installerAlive
    /\ install' = [install EXCEPT !.step = "BASELINED"]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

BeginBundleSwap ==
    /\ install.step = "BASELINED"
    /\ install.installerAlive
    /\ install' = [install EXCEPT
        !.step = "BUNDLE_SWAPPING",
        !.bundleValid = FALSE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

CompleteBundleSwap ==
    /\ install.step = "BUNDLE_SWAPPING"
    /\ install.installerAlive
    /\ install' = [install EXCEPT
        !.step = "BUNDLE_INSTALLED",
        !.bundleValid = TRUE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

StartQuiescedSupervisor ==
    /\ install.step = "BUNDLE_INSTALLED"
    /\ install.bundleValid
    /\ install' = [install EXCEPT
        !.step = "NEW_QUIESCED",
        !.replacementOwners = 1,
        !.actorEpoch = install.epoch]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

VerifyNormalInstall ==
    /\ install.step = "NEW_QUIESCED"
    /\ install.installerAlive
    /\ AbandonedActivationFacts
    /\ install' = [install EXCEPT
        !.step = "ISOLATION_VERIFIED",
        !.checksPassed = TRUE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

ActivateNormalInstall ==
    /\ install.step = "ISOLATION_VERIFIED"
    /\ install.installerAlive
    /\ install.checksPassed
    /\ AbandonedActivationFacts
    /\ install' = [install EXCEPT
        !.step = "IDLE",
        !.installerAlive = FALSE,
        !.mode = "ACTIVE"]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

InstallerDiesAt(checkpoint) ==
    /\ checkpoint \in DeathCheckpoints
    /\ install.installerAlive
    /\ install.step = checkpoint
    /\ install' = [install EXCEPT
        !.installerAlive = FALSE,
        !.deathCheckpoint = checkpoint]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

InvalidateRecoveryFact(fact) ==
    /\ fact \in RecoveryFacts
    /\ ~install.installerAlive
    /\ install.step \in {"BUNDLE_SWAPPING", "BUNDLE_INSTALLED",
                          "NEW_QUIESCED", "ISOLATION_VERIFIED"}
    /\ AbandonedActivationFacts
    /\ install' =
        CASE fact = "TXN" -> [install EXCEPT !.transactionMatches = FALSE]
          [] fact = "BUNDLE" -> [install EXCEPT !.bundleValid = FALSE]
          [] fact = "OLD_FENCED" -> [install EXCEPT !.oldFenced = FALSE]
          [] fact = "ONE_OWNER" -> [install EXCEPT !.replacementOwners = 2]
          [] fact = "ISOLATION" -> [install EXCEPT !.isolationMatches = FALSE]
          [] fact = "CONTEXT" -> [install EXCEPT !.releaseContextCompatible = FALSE]
          [] fact = "NO_RELEASE" -> [install EXCEPT !.concurrentRelease = TRUE]
          [] OTHER -> [install EXCEPT !.staleActorFenced = FALSE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

RepairInterruptedBundle ==
    /\ ~install.installerAlive
    /\ install.step = "BUNDLE_SWAPPING"
    /\ install.transactionMatches
    /\ install' = [install EXCEPT
        !.step = "BUNDLE_INSTALLED",
        !.bundleValid = TRUE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

StartRecoveredQuiesced ==
    /\ ~install.installerAlive
    /\ install.step = "BUNDLE_INSTALLED"
    /\ install.bundleValid
    /\ install' = [install EXCEPT
        !.step = "NEW_QUIESCED",
        !.replacementOwners = 1,
        !.actorEpoch = install.epoch]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

VerifyAbandonedInstall ==
    /\ ~install.installerAlive
    /\ install.step \in {"NEW_QUIESCED", "ISOLATION_VERIFIED"}
    /\ AbandonedActivationFacts
    /\ install' = [install EXCEPT
        !.step = "ABANDONED_VERIFIED",
        !.checksPassed = TRUE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

ActivateRecoveredInstall ==
    /\ ~install.installerAlive
    /\ install.step = "ABANDONED_VERIFIED"
    /\ install.checksPassed
    /\ AbandonedActivationFacts
    /\ install' = [install EXCEPT
        !.step = "IDLE",
        !.mode = "ACTIVE"]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

RejectAbandonedInstall ==
    /\ ~install.installerAlive
    /\ install.step \in DeathCheckpoints
    /\ \/ install.step = "BASELINED"
       \/ ~AbandonedActivationFacts
    /\ install' = [install EXCEPT
        !.step = "ROLLING_BACK",
        !.mode = "NONE",
        !.replacementOwners = 0,
        !.checksPassed = FALSE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

RestorePreviousInstall ==
    /\ install.step = "ROLLING_BACK"
    /\ install' = [install EXCEPT
        !.step = "IDLE",
        !.bundleValid = TRUE,
        !.replacementOwners = 1,
        !.mode = "ACTIVE",
        !.actorEpoch = install.epoch,
        !.concurrentRelease = FALSE]
    /\ UNCHANGED <<release, health, news, syncOwners, path>>

CompletePrepare ==
    /\ release.phase = "PREPARE"
    /\ release.migrationReady
    /\ install.step = "IDLE"
    /\ CurrentSupervisorCanMutate
    /\ release' = [release EXCEPT !.phase = "VERIFY"]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

PassEvidence ==
    /\ health = "GOOD"
    /\ release.phase = "VERIFY"
    /\ release.gate = "UNTESTED"
    /\ ExactCandidate
    /\ release.migrationReady
    /\ release.hardSafe
    /\ release.changedSafe
    /\ ~release.candidateRegression
    /\ ReverseStableCompatible
    /\ news.activationWatermark >= release.verifiedWatermark
    /\ (~release.accessRequired \/
        (release.accessReview /\ ApplicableAccessEvidence))
    /\ release' = [release EXCEPT
        !.gate = "PASSED",
        !.accepted = TRUE]
    /\ path' = [path EXCEPT !.verifyPassed = TRUE]
    /\ UNCHANGED <<health, install, news, syncOwners>>

BeginForwardSwitch ==
    /\ release.phase = "VERIFY"
    /\ ~release.transaction
    /\ release.gate = "PASSED"
    /\ release.accepted
    /\ ExactCandidate
    /\ ReverseStableCompatible
    /\ CurrentSupervisorCanMutate
    /\ release' = [release EXCEPT
        !.phase = "SWITCH",
        !.transaction = TRUE,
        !.target = release.candidate,
        !.origin = release.stable,
        !.kind = "FORWARD",
        !.applied = FALSE]
    /\ syncOwners' = 0
    /\ UNCHANGED <<health, install, news, path>>

ApplySwitch ==
    /\ health = "GOOD"
    /\ release.phase = "SWITCH"
    /\ release.transaction
    /\ release.kind = "FORWARD"
    /\ release.target # None
    /\ release' = [release EXCEPT
        !.phase = "OBSERVE",
        !.applied = TRUE]
    /\ syncOwners' = 1
    /\ path' = [path EXCEPT !.forwardObserve = TRUE]
    /\ UNCHANGED <<health, install, news>>

FailSwitch ==
    /\ health = "BAD"
    /\ release.phase = "SWITCH"
    /\ release.transaction
    /\ release.kind = "FORWARD"
    /\ ~release.applied
    /\ release' = [release EXCEPT
        !.target = release.origin,
        !.kind = "RECOVER",
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ path' = [path EXCEPT !.switchFailed = TRUE]
    /\ UNCHANGED <<health, install, news, syncOwners>>

ObserveSuccess ==
    /\ health = "GOOD"
    /\ release.phase = "OBSERVE"
    /\ release.transaction
    /\ release.applied
    /\ release.kind = "FORWARD"
    /\ release' = [release EXCEPT
        !.stable = release.target,
        !.previous = release.origin,
        !.candidate = None,
        !.phase = "STABLE",
        !.transaction = FALSE,
        !.target = None,
        !.origin = None,
        !.kind = "NONE",
        !.applied = FALSE,
        !.gate = "UNTESTED",
        !.accepted = FALSE,
        !.migrationReady = FALSE,
        !.hardSafe = TRUE,
        !.changedSafe = TRUE,
        !.stableDebt = FALSE,
        !.candidateRegression = FALSE]
    /\ UNCHANGED <<health, install, news, syncOwners, path>>

ObserveFailure ==
    /\ health = "BAD"
    /\ release.phase = "OBSERVE"
    /\ release.transaction
    /\ release.applied
    /\ release.kind = "FORWARD"
    /\ release' = [release EXCEPT
        !.phase = "SWITCH",
        !.target = release.origin,
        !.kind = "RECOVER",
        !.applied = FALSE,
        !.gate = "FAILED",
        !.accepted = FALSE]
    /\ syncOwners' = 0
    /\ path' = [path EXCEPT !.observeFailed = TRUE]
    /\ UNCHANGED <<health, install, news>>

ApplyRecoverySwitch ==
    /\ health = "GOOD"
    /\ release.phase = "SWITCH"
    /\ release.transaction
    /\ release.kind = "RECOVER"
    /\ release.target = release.origin
    /\ release' = [release EXCEPT
        !.phase = "OBSERVE",
        !.applied = TRUE]
    /\ syncOwners' = 1
    /\ path' = [path EXCEPT !.recoverySwitched = TRUE]
    /\ UNCHANGED <<health, install, news>>

ObserveRecovery ==
    /\ health = "GOOD"
    /\ release.phase = "OBSERVE"
    /\ release.transaction
    /\ release.kind = "RECOVER"
    /\ release.applied
    /\ release.stable = release.origin
    /\ release' = [release EXCEPT
        !.phase = "STABLE",
        !.transaction = FALSE,
        !.target = None,
        !.origin = None,
        !.kind = "NONE",
        !.applied = FALSE]
    /\ path' = [path EXCEPT !.recoveryCompleted = TRUE]
    /\ UNCHANGED <<health, install, news, syncOwners>>

PrepareGeneration ==
    /\ ~news.stagingPresent
    /\ news.currentGeneration < 2
    /\ news' = [news EXCEPT
        !.stagingPresent = TRUE,
        !.stagingGeneration = news.currentGeneration + 1,
        !.stagingIds = GenerationIds(news.currentGeneration + 1),
        !.stagedLegacyPresent = FALSE,
        !.stagedLegacyIds = {},
        !.storedGenerations = @ \cup {news.currentGeneration + 1}]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

StageLegacyCorrect ==
    /\ news.stagingPresent
    /\ ~news.stagedLegacyPresent
    /\ news' = [news EXCEPT
        !.stagedLegacyPresent = TRUE,
        !.stagedLegacyGeneration = news.stagingGeneration,
        !.stagedLegacyIds = news.stagingIds]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

StageLegacyInvalid(kind) ==
    /\ kind \in {"MISSING", "EXTRA"}
    /\ news.stagingPresent
    /\ ~news.stagedLegacyPresent
    /\ news' = [news EXCEPT
        !.stagedLegacyPresent = TRUE,
        !.stagedLegacyGeneration = news.stagingGeneration,
        !.stagedLegacyIds = IF kind = "MISSING" THEN {} ELSE news.stagingIds \cup {ExtraNews}]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

RepairStagedLegacy ==
    /\ news.stagingPresent
    /\ news.stagedLegacyPresent
    /\ ~FreshStagingCompatible
    /\ news' = [news EXCEPT
        !.stagedLegacyGeneration = news.stagingGeneration,
        !.stagedLegacyIds = news.stagingIds]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

ActivateGeneration ==
    /\ FreshStagingCompatible
    /\ release.phase \in {"PREPARE", "VERIFY"}
    /\ ~release.transaction
    /\ syncOwners = 1
    /\ news' = [news EXCEPT
        !.currentGeneration = news.stagingGeneration,
        !.activationWatermark = news.stagingGeneration,
        !.currentIds = news.stagingIds,
        !.activeLegacyGeneration = news.stagedLegacyGeneration,
        !.activeLegacyIds = news.stagedLegacyIds,
        !.legacyWriteObserved = FALSE,
        !.stagingPresent = FALSE,
        !.stagingIds = {},
        !.stagedLegacyPresent = FALSE,
        !.stagedLegacyIds = {}]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

LegacyStableWriteAttempt ==
    /\ news.currentGeneration > 0
    /\ ~news.legacyWriteObserved
    /\ news' = [news EXCEPT !.legacyWriteObserved = TRUE]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

CleanupObsolete(generation) ==
    /\ generation \in news.storedGenerations
    /\ generation # news.currentGeneration
    /\ (~news.stagingPresent \/ generation # news.stagingGeneration)
    /\ news' = [news EXCEPT !.storedGenerations = @ \ {generation}]
    /\ UNCHANGED <<release, health, install, syncOwners, path>>

Next ==
    \/ \E accessRequired \in BOOLEAN: DiscoverCandidate(accessRequired)
    \/ MainMoves \/ CorruptCandidateIdentity
    \/ DegradeHealth \/ RestoreHealth
    \/ VerifyMigration \/ RecordStableDebt
    \/ IntroduceCandidateRegression \/ HardSafetyFails
    \/ RequireAccessReview
    \/ \E kind \in AccessReceiptStates \ {"NONE"}: RecordAccessReceipt(kind)
    \/ ApproveAccessReceipt \/ RepeatAccessApproval
    \/ BeginInstall \/ CaptureBaseline \/ BeginBundleSwap
    \/ CompleteBundleSwap \/ StartQuiescedSupervisor
    \/ VerifyNormalInstall \/ ActivateNormalInstall
    \/ \E checkpoint \in DeathCheckpoints: InstallerDiesAt(checkpoint)
    \/ \E fact \in RecoveryFacts: InvalidateRecoveryFact(fact)
    \/ RepairInterruptedBundle \/ StartRecoveredQuiesced
    \/ VerifyAbandonedInstall \/ ActivateRecoveredInstall
    \/ RejectAbandonedInstall \/ RestorePreviousInstall
    \/ CompletePrepare \/ PassEvidence \/ BeginForwardSwitch
    \/ ApplySwitch \/ FailSwitch \/ ObserveSuccess \/ ObserveFailure
    \/ ApplyRecoverySwitch \/ ObserveRecovery
    \/ PrepareGeneration \/ StageLegacyCorrect
    \/ \E kind \in {"MISSING", "EXTRA"}: StageLegacyInvalid(kind)
    \/ RepairStagedLegacy \/ ActivateGeneration
    \/ LegacyStableWriteAttempt
    \/ \E generation \in Generations: CleanupObsolete(generation)

Spec ==
    /\ Init
    /\ [][Next]_vars
    /\ WF_vars(RestoreHealth)
    /\ WF_vars(VerifyMigration)
    /\ WF_vars(CaptureBaseline)
    /\ WF_vars(BeginBundleSwap)
    /\ WF_vars(CompleteBundleSwap)
    /\ WF_vars(StartQuiescedSupervisor)
    /\ WF_vars(VerifyNormalInstall)
    /\ WF_vars(ActivateNormalInstall)
    /\ WF_vars(RepairInterruptedBundle)
    /\ WF_vars(StartRecoveredQuiesced)
    /\ WF_vars(VerifyAbandonedInstall)
    /\ WF_vars(ActivateRecoveredInstall)
    /\ WF_vars(RejectAbandonedInstall)
    /\ WF_vars(RestorePreviousInstall)
    /\ WF_vars(CompletePrepare)
    /\ SF_vars(PassEvidence)
    /\ SF_vars(BeginForwardSwitch)
    /\ SF_vars(ApplySwitch)
    /\ SF_vars(FailSwitch)
    /\ SF_vars(ObserveSuccess)
    /\ SF_vars(ObserveFailure)
    /\ SF_vars(ApplyRecoverySwitch)
    /\ SF_vars(ObserveRecovery)

TypeOK ==
    /\ release.stable \in {StableId, CandidateId, NextId}
    /\ release.prepareStable \in {StableId, CandidateId, NextId}
    /\ release.previous \in Identities
    /\ release.candidate \in Identities
    /\ release.main \in {CandidateId, NextId}
    /\ release.phase \in {"STABLE", "PREPARE", "VERIFY", "SWITCH", "OBSERVE"}
    /\ release.gate \in {"UNTESTED", "PASSED", "FAILED"}
    /\ release.target \in Identities
    /\ release.origin \in Identities
    /\ release.kind \in {"NONE", "FORWARD", "RECOVER"}
    /\ release.verifiedGeneration \in Generations
    /\ release.verifiedWatermark \in Generations
    /\ release.accessRequired \in BOOLEAN
    /\ release.accessReview \in BOOLEAN
    /\ release.accessReceiptState \in AccessReceiptStates
    /\ release.accessAccepted \in BOOLEAN
    /\ release.accessApprovalCount \in 0..1
    /\ health \in {"GOOD", "BAD"}
    /\ install.step \in InstallSteps
    /\ install.mode \in {"ACTIVE", "QUIESCED", "NONE"}
    /\ install.epoch \in 0..1
    /\ install.actorEpoch \in 0..1
    /\ install.replacementOwners \in 0..2
    /\ install.deathCheckpoint \in DeathCheckpoints \cup {None}
    /\ syncOwners \in 0..1
    /\ news.currentGeneration \in Generations
    /\ news.activationWatermark \in Generations
    /\ news.activeLegacyGeneration \in Generations
    /\ news.stagingGeneration \in Generations
    /\ news.stagedLegacyGeneration \in Generations
    /\ news.currentIds \subseteq NewsIdentities
    /\ news.activeLegacyIds \subseteq NewsIdentities
    /\ news.stagingIds \subseteq NewsIdentities
    /\ news.stagedLegacyIds \subseteq NewsIdentities
    /\ news.legacyWriteObserved \in BOOLEAN
    /\ news.storedGenerations \subseteq Generations
    /\ path.accessRepeatObserved \in BOOLEAN

AtMostOneProductionWriter == syncOwners <= 1
PrepareVerifyKeepsStableSync ==
    release.phase \in {"PREPARE", "VERIFY"} => syncOwners = 1
CandidatePreparationPreservesStable ==
    release.phase \in {"PREPARE", "VERIFY"} => release.stable = release.prepareStable
VerificationWatermarkDoesNotRegress ==
    release.migrationReady => news.activationWatermark >= release.verifiedWatermark
StaleSupervisorIsFenced == install.actorEpoch # install.epoch => ~CurrentSupervisorCanMutate
PassedIdentityIsExact == release.gate = "PASSED" => ExactCandidate
AcceptedEvidenceIsRequired == release.gate = "PASSED" => release.accepted
AccessEvidenceIsRequired == release.gate = "PASSED" /\ release.accessRequired =>
    release.accessReview /\ ApplicableAccessEvidence
InvalidAccessReceiptCannotPass ==
    release.accessRequired /\ release.accessReceiptState \in
        {"NONE", "WRONG_KEY", "TAMPERED", "STALE"} =>
        release.gate # "PASSED" /\ ~release.accepted
AccessApprovalIsIdempotent ==
    release.accessApprovalCount <= 1 /\
    (path.accessRepeatObserved =>
        release.accessAccepted /\ release.accessApprovalCount = 1)
PassedGatesAreSafe == release.gate = "PASSED" =>
    release.hardSafe /\ release.changedSafe /\ ~release.candidateRegression
HardFailuresBlock ==
    (~release.hardSafe \/ ~release.changedSafe \/ release.candidateRegression) =>
        release.gate # "PASSED" /\ ~release.accepted
UnrelatedDebtIsNotFailure ==
    release.phase \in {"PREPARE", "VERIFY"} /\
    release.stableDebt /\ release.hardSafe /\ release.changedSafe /\
    ~release.candidateRegression /\ release.candidateExact /\
    release.main = release.candidate =>
        release.gate # "FAILED"
SwitchRequiresAcceptance == release.kind = "FORWARD" /\ release.transaction =>
    release.gate = "PASSED" /\ release.accepted
StableUnchangedDuringSwitchAndObserve == release.transaction => release.stable = release.origin
SingleTransaction == release.transaction <=> release.phase \in {"SWITCH", "OBSERVE"}
ActiveLegacyEqualsCurrent == ReverseStableCompatible
LegacyStableWritesRemainFenced ==
    news.legacyWriteObserved => ReverseStableCompatible
CurrentIdentitySetMatchesGeneration ==
    news.currentIds = GenerationIds(news.currentGeneration)
FreshStagingIdentitySetMatchesGeneration ==
    news.stagingPresent => news.stagingIds = GenerationIds(news.stagingGeneration)
CurrentGenerationCannotBeCleaned == news.currentGeneration \in news.storedGenerations
FreshStagingCannotBeCleaned == news.stagingPresent =>
    news.stagingGeneration \in news.storedGenerations
InvalidStagedLegacyCannotActivate ==
    news.stagingPresent /\ news.stagedLegacyPresent /\ ~FreshStagingCompatible =>
        news.currentGeneration # news.stagingGeneration
RecoveredActivationRequiresIndependentChecks ==
    install.step = "ABANDONED_VERIFIED" =>
        install.checksPassed /\ AbandonedActivationFacts
ActiveRecoveredSupervisorIsSafe ==
    ~install.installerAlive /\ install.step = "IDLE" /\ install.mode = "ACTIVE" =>
        install.bundleValid /\ install.replacementOwners = 1

StableChangesOnlyAfterObservation ==
    [][(release.stable' # release.stable) => ObserveSuccess]_vars

ObservedFailureEventuallyRestoresPrevious ==
    [](path.observeFailed /\ ~path.recoveryCompleted =>
       <> (path.recoveryCompleted /\ release.phase = "STABLE" /\ ~release.transaction))

SwitchFailureEventuallyTerminates ==
    [](path.switchFailed /\ ~path.recoveryCompleted =>
       <> (path.recoveryCompleted /\ release.phase = "STABLE" /\ ~release.transaction))

TransactionEventuallyTerminates ==
    [](release.transaction => <> (~release.transaction /\ release.phase = "STABLE"))

AbandonedInstallEventuallySafe ==
    [](~install.installerAlive /\ install.step # "IDLE" =>
       <> (install.step = "IDLE" /\ install.mode = "ACTIVE"))

=============================================================================
