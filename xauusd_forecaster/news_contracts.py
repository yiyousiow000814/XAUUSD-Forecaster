"""Version registry for news feature, eligibility, and event-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsContract:
    name: str
    feature_version: str
    eligibility_version: str
    policy_version: str


CORE_BROAD_NEWS_V16 = NewsContract(
    name="core-broad-news-v16",
    feature_version="eligible-news-event-evidence-v15-core-broad",
    eligibility_version="news-source-eligibility-v13-evidence-attributes",
    policy_version="news-event-evidence-v13-core-broad",
)

SUPPORTED_NEWS_CONTRACTS = (
    CORE_BROAD_NEWS_V16,
)
CURRENT_NEWS_CONTRACT = SUPPORTED_NEWS_CONTRACTS[-1]
# These values are immutable database tokens from the original V2 schema.
# Under the current contract they encode the Core lane; they do not grant
# permission based on an "official" source allowlist.
CORE_MODEL_STORAGE_PERMISSION = "OFFICIAL_MODEL"
CORE_EVIDENCE_STORAGE_LANE = "OFFICIAL"
NEWS_CONTRACT_BY_ELIGIBILITY = {
    contract.eligibility_version: contract for contract in SUPPORTED_NEWS_CONTRACTS
}


def generation_matches_contract(generation, contract: NewsContract) -> bool:
    """Return whether one generation is bound to the exact contract triple."""
    if generation is None:
        return False
    return (
        generation["feature_version"] == contract.feature_version
        and generation["eligibility_version"] == contract.eligibility_version
        and generation["policy_version"] == contract.policy_version
    )
