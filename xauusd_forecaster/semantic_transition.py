"""Declared semantic-contract transition and demand policy.

Historical evidence is immutable. This policy decides whether a new active
contract may reuse it, transform it deterministically, or must schedule a
bounded model review. It never performs provider work itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
)

REUSE_COMPATIBLE = "REUSE_COMPATIBLE"
DETERMINISTIC_MIGRATION = "DETERMINISTIC_MIGRATION"
MODEL_REVIEW_REQUIRED = "MODEL_REVIEW_REQUIRED"
TRANSITION_KINDS = frozenset({
    REUSE_COMPATIBLE, DETERMINISTIC_MIGRATION, MODEL_REVIEW_REQUIRED,
})

CURRENT_OPERATIONAL = "CURRENT_OPERATIONAL"
TRAINING_REQUIRED = "TRAINING_REQUIRED"
ARCHIVAL_ONLY = "ARCHIVAL_ONLY"
DEMAND_CLASSES = frozenset({CURRENT_OPERATIONAL, TRAINING_REQUIRED, ARCHIVAL_ONLY})


@dataclass(frozen=True)
class SemanticTransition:
    from_version: str
    to_version: str
    kind: str
    rationale: str


DECLARED_TRANSITIONS = {
    (PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION): SemanticTransition(
        PREVIOUS_NEWS_PROMPT_VERSION,
        CURRENT_NEWS_PROMPT_VERSION,
        MODEL_REVIEW_REQUIRED,
        "V17 changes source-grounded visible-Latin validation semantics.",
    ),
}


def transition_for(from_version: str, to_version: str) -> SemanticTransition:
    if from_version == to_version:
        return SemanticTransition(
            from_version, to_version, REUSE_COMPATIBLE,
            "The semantic contract version is unchanged.",
        )
    try:
        return DECLARED_TRANSITIONS[(from_version, to_version)]
    except KeyError:
        raise ValueError("semantic contract transition is not declared") from None


def requires_model_review(from_version: str, to_version: str) -> bool:
    return transition_for(from_version, to_version).kind == MODEL_REVIEW_REQUIRED


def provider_dispatches_for_transition(transition: SemanticTransition) -> int:
    """Return the maximum provider work admitted per demanded record.

    Reuse and deterministic migration preserve prior model provenance. They
    cannot be represented as a new model invocation.
    """
    if transition.kind not in TRANSITION_KINDS:
        raise ValueError("semantic transition kind is not controlled")
    return 1 if transition.kind == MODEL_REVIEW_REQUIRED else 0


def demand_allows_scheduling(
    demand_class: str, *, training_generation_requested: bool = False,
) -> bool:
    if demand_class not in DEMAND_CLASSES:
        raise ValueError("semantic demand class is not controlled")
    if demand_class == CURRENT_OPERATIONAL:
        return True
    if demand_class == TRAINING_REQUIRED:
        return training_generation_requested
    return False
