"""Version registry for news feature, eligibility, and event-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsContract:
    name: str
    feature_version: str
    eligibility_version: str
    policy_version: str


SEMANTIC_IMPACT_V8 = NewsContract(
    name="semantic-impact-v8",
    feature_version="eligible-news-event-evidence-v10-absolute-trust-weight",
    eligibility_version="news-source-eligibility-v9-independent-origin",
    policy_version="news-event-evidence-v8-versioned-membership",
)

SUPPORTED_NEWS_CONTRACTS = (
    SEMANTIC_IMPACT_V8,
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


def supported_generation_contract(generation) -> NewsContract | None:
    """Resolve an activated generation only when its full contract is supported."""
    if generation is None:
        return None
    for contract in SUPPORTED_NEWS_CONTRACTS:
        if generation_matches_contract(generation, contract):
            return contract
    return None
