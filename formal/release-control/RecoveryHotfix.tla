----------------------------- MODULE RecoveryHotfix -----------------------------
EXTENDS Naturals, TLC

VARIABLES phase, mode, action, transaction, activeKnown, activeHealth,
          active, committed, recoveryBase, target, lkgValid, familyAllowed, evidenceDagValid,
          observeEligible, observeResult, hotfixCommitted

vars == <<phase, mode, action, transaction, activeKnown, activeHealth,
          active, committed, recoveryBase, target, lkgValid, familyAllowed, evidenceDagValid,
          observeEligible, observeResult, hotfixCommitted>>

Init ==
    /\ phase = "STABLE" /\ mode = "NORMAL" /\ action = "NONE"
    /\ transaction = FALSE /\ activeKnown = TRUE /\ activeHealth = "DEGRADED"
    /\ active = "LKG" /\ committed = "LKG" /\ recoveryBase = "LKG"
    /\ target = "HOTFIX"
    /\ lkgValid = TRUE /\ familyAllowed = TRUE /\ evidenceDagValid = TRUE
    /\ observeEligible = TRUE /\ observeResult = "NONE" /\ hotfixCommitted = FALSE

BeginRestoreLkg ==
    /\ ~transaction /\ activeKnown /\ lkgValid /\ evidenceDagValid
    /\ phase' = "SWITCH" /\ mode' = "RECOVERY_HOTFIX"
    /\ action' = "RESTORE_LKG" /\ transaction' = TRUE
    /\ target' = committed /\ recoveryBase' = committed
    /\ observeResult' = "NONE"
    /\ UNCHANGED <<activeKnown, activeHealth, active, committed, lkgValid,
                    familyAllowed, evidenceDagValid, observeEligible,
                    hotfixCommitted>>

BeginRecoveryHotfix ==
    /\ ~transaction /\ activeKnown /\ active = committed /\ lkgValid
    /\ familyAllowed /\ evidenceDagValid
    /\ phase' = "SWITCH" /\ mode' = "RECOVERY_HOTFIX"
    /\ action' = "APPLY_RECOVERY_HOTFIX" /\ transaction' = TRUE
    /\ recoveryBase' = committed
    /\ observeResult' = "NONE"
    /\ UNCHANGED <<activeKnown, activeHealth, active, committed, target, lkgValid,
                    familyAllowed, evidenceDagValid, observeEligible,
                    hotfixCommitted>>

SwitchRecovery ==
    /\ transaction /\ phase = "SWITCH"
    /\ phase' = "OBSERVE" /\ active' = target
    /\ UNCHANGED <<mode, action, transaction, activeKnown, activeHealth, committed, recoveryBase,
                    target, lkgValid, familyAllowed, evidenceDagValid,
                    observeEligible, observeResult, hotfixCommitted>>

ObservePass ==
    /\ transaction /\ phase = "OBSERVE" /\ observeEligible
    /\ phase' = "STABLE" /\ mode' = "NORMAL" /\ action' = "NONE"
    /\ transaction' = FALSE /\ committed' = IF action = "APPLY_RECOVERY_HOTFIX"
                                              THEN target ELSE committed
    /\ observeResult' = "PASSED"
    /\ hotfixCommitted' = IF action = "APPLY_RECOVERY_HOTFIX"
                              THEN TRUE ELSE hotfixCommitted
    /\ UNCHANGED <<activeKnown, activeHealth, active, recoveryBase, target, lkgValid,
                    familyAllowed, evidenceDagValid, observeEligible>>

ObserveFail ==
    /\ transaction /\ phase = "OBSERVE"
    /\ phase' = "STABLE" /\ mode' = "NORMAL" /\ action' = "NONE"
    /\ transaction' = FALSE /\ active' = committed /\ observeResult' = "FAILED"
    /\ UNCHANGED <<activeKnown, activeHealth, committed, recoveryBase, target, lkgValid,
                    familyAllowed, evidenceDagValid, observeEligible, hotfixCommitted>>

MakeUnknown ==
    /\ ~transaction /\ activeKnown
    /\ activeKnown' = FALSE /\ activeHealth' = "UNKNOWN"
    /\ UNCHANGED <<phase, mode, action, transaction, active, committed, recoveryBase, target,
                    lkgValid, familyAllowed, evidenceDagValid, observeEligible,
                    observeResult, hotfixCommitted>>

MakeDrifted ==
    /\ ~transaction /\ activeKnown /\ active = committed
    /\ active' = "DRIFTED" /\ activeHealth' = "DEGRADED"
    /\ observeResult' = "NONE"
    /\ UNCHANGED <<phase, mode, action, transaction, activeKnown, committed, recoveryBase,
                    target, lkgValid, familyAllowed, evidenceDagValid,
                    observeEligible, hotfixCommitted>>

MakeForbidden ==
    /\ ~transaction /\ familyAllowed
    /\ familyAllowed' = FALSE
    /\ UNCHANGED <<phase, mode, action, transaction, activeKnown, activeHealth,
                    active, committed, recoveryBase, target, lkgValid,
                    evidenceDagValid, observeEligible, observeResult, hotfixCommitted>>

SafetyNext == BeginRestoreLkg \/ BeginRecoveryHotfix \/ SwitchRecovery \/
              ObservePass \/ ObserveFail \/ MakeUnknown \/ MakeDrifted \/ MakeForbidden
LivenessNext == BeginRestoreLkg \/ BeginRecoveryHotfix \/ SwitchRecovery \/
                ObservePass \/ ObserveFail

SafetySpec == Init /\ [][SafetyNext]_vars
LivenessSpec == Init /\ [][LivenessNext]_vars /\ WF_vars(SwitchRecovery) /\
                WF_vars(ObservePass)

TypeOK ==
    /\ phase \in {"STABLE", "SWITCH", "OBSERVE"}
    /\ mode \in {"NORMAL", "RECOVERY_HOTFIX"}
    /\ action \in {"NONE", "RESTORE_LKG", "APPLY_RECOVERY_HOTFIX"}
    /\ transaction \in BOOLEAN /\ activeKnown \in BOOLEAN
    /\ activeHealth \in {"HEALTHY", "DEGRADED", "UNKNOWN"}
    /\ active \in {"LKG", "HOTFIX", "DRIFTED"}
    /\ committed \in {"LKG", "HOTFIX"} /\ recoveryBase \in {"LKG", "HOTFIX"}
    /\ target \in {"LKG", "HOTFIX"}
    /\ lkgValid \in BOOLEAN /\ familyAllowed \in BOOLEAN
    /\ evidenceDagValid \in BOOLEAN /\ observeEligible \in BOOLEAN
    /\ observeResult \in {"NONE", "PASSED", "FAILED"}
    /\ hotfixCommitted \in BOOLEAN

ActiveUnknownCannotBegin == ~activeKnown => ~transaction
RestoreLkgDoesNotChangeCommitted == transaction /\ action = "RESTORE_LKG" => committed = recoveryBase
HotfixCommitsOnlyAfterObservation == committed = "HOTFIX" => hotfixCommitted
FailedHotfixRestoresLkg == observeResult = "FAILED" => active = committed
SingleRecoveryTransaction == transaction <=> phase \in {"SWITCH", "OBSERVE"}
ForbiddenFamilyCannotEnterHotfix == ~familyAllowed => action # "APPLY_RECOVERY_HOTFIX"
RecoveryUsesEvidenceDag == transaction => evidenceDagValid
RecoveryModeAddsNoPhase == mode = "RECOVERY_HOTFIX" => phase \in {"SWITCH", "OBSERVE"}
DegradedActiveHasRecoveryPath ==
    activeKnown /\ activeHealth = "DEGRADED" /\ lkgValid /\ evidenceDagValid /\ ~transaction
        => ENABLED BeginRestoreLkg
DriftedActiveCanRestoreLkg ==
    activeKnown /\ active = "DRIFTED" /\ lkgValid /\ evidenceDagValid /\ ~transaction
        => ENABLED BeginRestoreLkg
RecoveryEventuallyTerminates == [](transaction => <> (~transaction /\ phase = "STABLE"))

=============================================================================
