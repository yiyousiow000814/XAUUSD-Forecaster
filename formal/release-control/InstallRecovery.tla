--------------------------- MODULE InstallRecovery ---------------------------
EXTENDS Naturals, TLC

Steps == {"IDLE", "FENCED", "BASELINED", "SWAPPING", "INSTALLED",
          "QUIESCED", "VERIFIED", "ABANDONED_VERIFIED", "ROLLING_BACK"}
Checkpoints == {"BASELINED", "SWAPPING", "INSTALLED", "QUIESCED", "VERIFIED"}
Facts == {"TXN", "BUNDLE", "OLD_FENCED", "ONE_OWNER", "ISOLATION",
           "CONTEXT", "NO_RELEASE", "STALE_FENCED"}
VARIABLES step, installerAlive, validFacts, replacementOwners, mode,
          epoch, actorEpoch, deathCheckpoint
vars == <<step, installerAlive, validFacts, replacementOwners, mode,
          epoch, actorEpoch, deathCheckpoint>>

Init == /\ step = "IDLE" /\ installerAlive = FALSE /\ validFacts = Facts
        /\ replacementOwners = 1 /\ mode = "ACTIVE"
        /\ epoch = 0 /\ actorEpoch = 0 /\ deathCheckpoint = "NONE"
BeginInstall ==
    /\ step = "IDLE" /\ step' = "FENCED" /\ installerAlive' = TRUE
    /\ mode' = "QUIESCED" /\ epoch' = 1 /\ actorEpoch' = 0
    /\ UNCHANGED <<validFacts, replacementOwners, deathCheckpoint>>
CaptureBaseline ==
    /\ step = "FENCED" /\ step' = "BASELINED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
SwapBundle ==
    /\ step = "BASELINED" /\ step' = "SWAPPING"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
FinishSwap ==
    /\ step = "SWAPPING" /\ step' = "INSTALLED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
StartQuiesced ==
    /\ step = "INSTALLED" /\ step' = "QUIESCED" /\ actorEpoch' = epoch
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, deathCheckpoint>>
Verify ==
    /\ step = "QUIESCED" /\ validFacts = Facts /\ step' = "VERIFIED"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
Activate ==
    /\ step \in {"VERIFIED", "ABANDONED_VERIFIED"} /\ validFacts = Facts
    /\ replacementOwners = 1 /\ actorEpoch = epoch
    /\ step' = "IDLE" /\ mode' = "ACTIVE" /\ installerAlive' = FALSE
    /\ UNCHANGED <<validFacts, replacementOwners, epoch, actorEpoch, deathCheckpoint>>
InstallerDiesAt(checkpoint) ==
    /\ checkpoint \in Checkpoints /\ step = checkpoint /\ installerAlive
    /\ installerAlive' = FALSE /\ deathCheckpoint' = checkpoint
    /\ UNCHANGED <<step, validFacts, replacementOwners, mode, epoch, actorEpoch>>
InvalidateFact(fact) ==
    /\ ~installerAlive /\ step # "IDLE" /\ fact \in validFacts
    /\ validFacts' = validFacts \ {fact}
    /\ step' = IF step = "ABANDONED_VERIFIED" THEN "ROLLING_BACK" ELSE step
    /\ UNCHANGED <<installerAlive, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
VerifyAbandoned ==
    /\ ~installerAlive /\ step # "IDLE" /\ validFacts = Facts
    /\ step' = "ABANDONED_VERIFIED" /\ actorEpoch' = epoch
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, deathCheckpoint>>
RejectAndRestore ==
    /\ ~installerAlive /\ step # "IDLE" /\ validFacts # Facts
    /\ step' = "ROLLING_BACK"
    /\ UNCHANGED <<installerAlive, validFacts, replacementOwners, mode, epoch, actorEpoch, deathCheckpoint>>
RestorePrevious ==
    /\ step = "ROLLING_BACK" /\ step' = "IDLE" /\ mode' = "ACTIVE"
    /\ validFacts' = Facts /\ replacementOwners' = 1 /\ actorEpoch' = epoch
    /\ UNCHANGED <<installerAlive, epoch, deathCheckpoint>>
Next == BeginInstall \/ CaptureBaseline \/ SwapBundle \/ FinishSwap \/ StartQuiesced \/
        Verify \/ Activate \/ (\E c \in Checkpoints: InstallerDiesAt(c)) \/
        (\E f \in Facts: InvalidateFact(f)) \/ VerifyAbandoned \/ RejectAndRestore \/ RestorePrevious
SafetySpec == Init /\ [][Next]_vars
LivenessSpec == /\ SafetySpec /\ WF_vars(VerifyAbandoned) /\ WF_vars(RejectAndRestore)
                /\ WF_vars(RestorePrevious) /\ WF_vars(Activate)
TypeOK == /\ step \in Steps /\ installerAlive \in BOOLEAN /\ validFacts \subseteq Facts
          /\ replacementOwners \in 0..2 /\ mode \in {"ACTIVE", "QUIESCED"}
          /\ epoch \in 0..1 /\ actorEpoch \in 0..1
          /\ deathCheckpoint \in Checkpoints \cup {"NONE"}
CurrentSupervisorCanMutate == mode = "ACTIVE" /\ actorEpoch = epoch
StaleSupervisorIsFenced == actorEpoch # epoch => ~CurrentSupervisorCanMutate
RecoveredActivationRequiresIndependentChecks ==
    step = "ABANDONED_VERIFIED" => validFacts = Facts /\ replacementOwners = 1
ActiveRecoveredSupervisorIsSafe ==
    ~installerAlive /\ step = "IDLE" /\ mode = "ACTIVE" => validFacts = Facts /\ replacementOwners = 1
AbandonedInstallEventuallySafe ==
    [](~installerAlive /\ step # "IDLE" => <> (step = "IDLE" /\ mode = "ACTIVE"))
=============================================================================
