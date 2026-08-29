----------------------------- MODULE NewsMigration -----------------------------
EXTENDS Naturals, TLC

Generations == 0..2
Ids == {"OLD", "SHARED", "NEW", "EXTRA"}
GenerationIds(g) == IF g = 0 THEN {"OLD", "SHARED"}
                    ELSE IF g = 1 THEN {"SHARED", "NEW"}
                    ELSE {"OLD", "NEW"}
VARIABLES current, watermark, currentIds, legacyGeneration, legacyIds,
          staging, stagingGeneration, stagingIds, stagedLegacy,
          stagedLegacyGeneration, stagedLegacyIds, stored, legacyWriteObserved,
          verifiedWatermark
vars == <<current, watermark, currentIds, legacyGeneration, legacyIds,
          staging, stagingGeneration, stagingIds, stagedLegacy,
          stagedLegacyGeneration, stagedLegacyIds, stored, legacyWriteObserved,
          verifiedWatermark>>
Compatible == stagedLegacy /\ stagedLegacyGeneration = stagingGeneration /\ stagedLegacyIds = stagingIds
Init == /\ current = 0 /\ watermark = 0 /\ currentIds = GenerationIds(0)
        /\ legacyGeneration = 0 /\ legacyIds = GenerationIds(0)
        /\ staging = FALSE /\ stagingGeneration = 0 /\ stagingIds = {}
        /\ stagedLegacy = FALSE /\ stagedLegacyGeneration = 0 /\ stagedLegacyIds = {}
        /\ stored = {0} /\ legacyWriteObserved = FALSE /\ verifiedWatermark = 0
VerifyWatermark ==
    /\ verifiedWatermark < watermark
    /\ verifiedWatermark' = watermark
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stagedLegacy,
                    stagedLegacyGeneration, stagedLegacyIds, stored, legacyWriteObserved>>
PrepareGeneration(g) ==
    /\ current < 2 /\ g = current + 1 /\ ~staging
    /\ staging' = TRUE /\ stagingGeneration' = g /\ stagingIds' = GenerationIds(g)
    /\ stagedLegacy' = FALSE /\ stagedLegacyGeneration' = g /\ stagedLegacyIds' = {}
    /\ stored' = stored \cup {g}
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    legacyWriteObserved, verifiedWatermark>>
StageLegacyCorrect ==
    /\ staging /\ ~stagedLegacy
    /\ stagedLegacy' = TRUE /\ stagedLegacyGeneration' = stagingGeneration
    /\ stagedLegacyIds' = stagingIds
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stored,
                    legacyWriteObserved, verifiedWatermark>>
StageLegacyInvalid(kind) ==
    /\ staging /\ ~stagedLegacy /\ kind \in {"MISSING", "EXTRA"}
    /\ stagedLegacy' = TRUE /\ stagedLegacyGeneration' = stagingGeneration
    /\ stagedLegacyIds' = IF kind = "MISSING" THEN {} ELSE stagingIds \cup {"EXTRA"}
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stored,
                    legacyWriteObserved, verifiedWatermark>>
RepairLegacy ==
    /\ staging /\ stagedLegacy /\ ~Compatible
    /\ stagedLegacyIds' = stagingIds /\ stagedLegacyGeneration' = stagingGeneration
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stagedLegacy, stored,
                    legacyWriteObserved, verifiedWatermark>>
Activate ==
    /\ staging /\ Compatible
    /\ current' = stagingGeneration /\ currentIds' = stagingIds
    /\ legacyGeneration' = stagedLegacyGeneration /\ legacyIds' = stagedLegacyIds
    /\ watermark' = watermark + 1
    /\ staging' = FALSE /\ stagingIds' = {} /\ stagedLegacy' = FALSE /\ stagedLegacyIds' = {}
    /\ UNCHANGED <<stagingGeneration, stagedLegacyGeneration, stored,
                    legacyWriteObserved, verifiedWatermark>>
LegacyStableWriteAttempt ==
    /\ current > 0 /\ ~legacyWriteObserved /\ legacyWriteObserved' = TRUE
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stagedLegacy,
                    stagedLegacyGeneration, stagedLegacyIds, stored, verifiedWatermark>>
Cleanup(g) ==
    /\ g \in stored /\ g # current /\ (~staging \/ g # stagingGeneration)
    /\ stored' = stored \ {g}
    /\ UNCHANGED <<current, watermark, currentIds, legacyGeneration, legacyIds,
                    staging, stagingGeneration, stagingIds, stagedLegacy,
                    stagedLegacyGeneration, stagedLegacyIds, legacyWriteObserved,
                    verifiedWatermark>>
Next == VerifyWatermark \/ (\E g \in Generations: PrepareGeneration(g)) \/
        StageLegacyCorrect \/ (\E k \in {"MISSING", "EXTRA"}: StageLegacyInvalid(k)) \/
        RepairLegacy \/ Activate \/ LegacyStableWriteAttempt \/ (\E g \in Generations: Cleanup(g))
Spec == Init /\ [][Next]_vars
TypeOK == /\ current \in Generations /\ watermark \in 0..2 /\ currentIds \subseteq Ids
          /\ legacyGeneration \in Generations /\ legacyIds \subseteq Ids
          /\ staging \in BOOLEAN /\ stagingGeneration \in Generations /\ stagingIds \subseteq Ids
          /\ stagedLegacy \in BOOLEAN /\ stagedLegacyGeneration \in Generations /\ stagedLegacyIds \subseteq Ids
          /\ stored \subseteq Generations /\ legacyWriteObserved \in BOOLEAN /\ verifiedWatermark \in 0..2
VerificationWatermarkDoesNotRegress == watermark >= verifiedWatermark
ActiveLegacyEqualsCurrent == legacyGeneration = current /\ legacyIds = currentIds
LegacyStableWritesRemainFenced == legacyWriteObserved => ActiveLegacyEqualsCurrent
CurrentIdentitySetMatchesGeneration == currentIds = GenerationIds(current)
FreshStagingIdentitySetMatchesGeneration == staging => stagingIds = GenerationIds(stagingGeneration)
CurrentGenerationCannotBeCleaned == current \in stored
FreshStagingCannotBeCleaned == staging => stagingGeneration \in stored
InvalidStagedLegacyCannotActivate == staging /\ stagedLegacy /\ ~Compatible => current # stagingGeneration
=============================================================================
