--------------------------- MODULE InstallRecovery ---------------------------
EXTENDS Naturals, TLC

Steps == {"IDLE", "FENCED", "BASELINED", "SWAPPING", "INSTALLED",
          "QUIESCED", "VERIFIED", "ABANDONED_VERIFIED", "ROLLING_BACK"}
Checkpoints == {"BASELINED", "SWAPPING", "INSTALLED", "QUIESCED", "VERIFIED"}
Facts == {"TXN", "BUNDLE", "OLD_FENCED", "ONE_OWNER", "ISOLATION",
           "CONTEXT", "NO_RELEASE", "STALE_FENCED"}
VARIABLES step, installerAlive, validFacts, replacementOwners, mode,
          epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved
vars == <<step, installerAlive, validFacts, replacementOwners, mode,
          epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>

Init == /\ step = "IDLE" /\ installerAlive = FALSE /\ validFacts = Facts
        /\ incidentBaseline \in BOOLEAN /\ bootstrapReserved = FALSE
        /\ replacementOwners = IF incidentBaseline THEN 0 ELSE 1
        /\ mode = IF incidentBaseline THEN "DEGRADED" ELSE "ACTIVE"
        /\ epoch = 0 /\ actorEpoch = 0 /\ deathCheckpoint = "NONE"
BeginInstall ==
    /\ step = "IDLE" /\ step' = "FENCED" /\ installerAlive' = TRUE
    /\ mode' = "QUIESCED" /\ epoch' = 1 /\ actorEpoch' = 0
    /\ bootstrapReserved' = incidentBaseline /\ replacementOwners' = 0
    /\ UNCHANGED <<validFacts, deathCheckpoint, incidentBaseline>>
CaptureBaseline ==
    /\ step = "FENCED" /\ step' = "BASELINED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
SwapBundle ==
    /\ step = "BASELINED" /\ step' = "SWAPPING"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
FinishSwap ==
    /\ step = "SWAPPING" /\ step' = "INSTALLED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
ReleaseBootstrapReservation ==
    /\ step = "INSTALLED" /\ bootstrapReserved /\ bootstrapReserved' = FALSE
    /\ UNCHANGED <<step, installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline>>
StartQuiesced ==
    /\ step = "INSTALLED" /\ installerAlive /\ ~bootstrapReserved
    /\ step' = "QUIESCED" /\ actorEpoch' = epoch /\ replacementOwners' = 1
    /\ UNCHANGED <<installerAlive, validFacts, mode, epoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
Verify ==
    /\ step = "QUIESCED" /\ validFacts = Facts /\ step' = "VERIFIED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
Activate ==
    /\ step \in {"VERIFIED", "ABANDONED_VERIFIED"} /\ validFacts = Facts
    /\ replacementOwners = 1 /\ actorEpoch = epoch
    /\ step' = "IDLE" /\ mode' = "ACTIVE" /\ installerAlive' = FALSE
    /\ UNCHANGED <<validFacts, replacementOwners, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
InstallerDiesAt(checkpoint) ==
    /\ checkpoint \in Checkpoints /\ step = checkpoint /\ installerAlive
    /\ installerAlive' = FALSE /\ deathCheckpoint' = checkpoint
    /\ bootstrapReserved' = FALSE
    /\ UNCHANGED <<step, validFacts, replacementOwners, mode, epoch, actorEpoch, incidentBaseline>>
InvalidateFact(fact) ==
    /\ ~installerAlive /\ step # "IDLE" /\ fact \in validFacts
    /\ validFacts' = validFacts \ {fact}
    /\ step' = IF step = "ABANDONED_VERIFIED" THEN "ROLLING_BACK" ELSE step
    /\ UNCHANGED <<installerAlive, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
VerifyAbandoned ==
    /\ ~installerAlive /\ step # "IDLE" /\ validFacts = Facts /\ replacementOwners = 1
    /\ step' = "ABANDONED_VERIFIED" /\ actorEpoch' = epoch
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
RejectAndRestore ==
    /\ ~installerAlive /\ step # "IDLE" /\ (validFacts # Facts \/ replacementOwners = 0)
    /\ step' = "ROLLING_BACK"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint, incidentBaseline, bootstrapReserved>>
RestorePrevious ==
    /\ step = "ROLLING_BACK" /\ step' = "IDLE"
    /\ mode' = IF incidentBaseline THEN "DEGRADED" ELSE "ACTIVE"
    /\ validFacts' = Facts /\ replacementOwners' = IF incidentBaseline THEN 0 ELSE 1
    /\ actorEpoch' = epoch /\ bootstrapReserved' = FALSE
    /\ UNCHANGED <<installerAlive, epoch, deathCheckpoint, incidentBaseline>>
Next == BeginInstall \/ CaptureBaseline \/ SwapBundle \/ FinishSwap \/ StartQuiesced \/
        ReleaseBootstrapReservation \/ Verify \/ Activate \/ (\E c \in Checkpoints: InstallerDiesAt(c)) \/
        (\E f \in Facts: InvalidateFact(f)) \/ VerifyAbandoned \/ RejectAndRestore \/ RestorePrevious
SafetySpec == Init /\ [][Next]_vars
LivenessSpec == /\ SafetySpec /\ WF_vars(VerifyAbandoned) /\ WF_vars(RejectAndRestore)
                /\ WF_vars(RestorePrevious) /\ WF_vars(Activate)
TypeOK == /\ step \in Steps /\ installerAlive \in BOOLEAN /\ validFacts \subseteq Facts
          /\ replacementOwners \in 0..2 /\ mode \in {"ACTIVE", "QUIESCED", "DEGRADED"}
          /\ incidentBaseline \in BOOLEAN /\ bootstrapReserved \in BOOLEAN
          /\ epoch \in 0..1 /\ actorEpoch \in 0..1
          /\ deathCheckpoint \in Checkpoints \cup {"NONE"}
CurrentSupervisorCanMutate == mode = "ACTIVE" /\ actorEpoch = epoch
StaleSupervisorIsFenced == actorEpoch # epoch => ~CurrentSupervisorCanMutate
RecoveredActivationRequiresIndependentChecks ==
    step = "ABANDONED_VERIFIED" => validFacts = Facts /\ replacementOwners = 1
ActiveRecoveredSupervisorIsSafe ==
    ~installerAlive /\ step = "IDLE" /\ mode = "ACTIVE" => validFacts = Facts /\ replacementOwners = 1
AbandonedInstallEventuallySafe ==
    [](~installerAlive /\ step # "IDLE" => <> (step = "IDLE" /\
        (mode = "ACTIVE" \/ (incidentBaseline /\ mode = "DEGRADED" /\ replacementOwners = 0))))
BootstrapReservationExcludesReplacement == bootstrapReserved => replacementOwners = 0
DegradedRollbackNeverClaimsSupervision == mode = "DEGRADED" => incidentBaseline /\ replacementOwners = 0
=============================================================================
