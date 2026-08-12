"""Version registry for news feature, eligibility, and event-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsContract:
    name: str
    feature_version: str
    eligibility_version: str
    policy_version: str


AI_SEMANTIC_REVIEW_V15 = NewsContract(
    name="ai-semantic-review-v15",
    feature_version="eligible-news-event-evidence-v14-release-packets",
    eligibility_version="news-source-eligibility-v12-permission-neutral",
    policy_version="news-event-evidence-v12-source-attributes",
)

SUPPORTED_NEWS_CONTRACTS = (
    AI_SEMANTIC_REVIEW_V15,
)
CURRENT_NEWS_CONTRACT = SUPPORTED_NEWS_CONTRACTS[-1]
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
