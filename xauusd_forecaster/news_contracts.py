"""Compatibility shim for xauusd_forecaster.news.semantics.model_contracts."""

from xauusd_forecaster.news.semantics.model_contracts import (
    CANONICAL_IDENTITY_NEWS_V17,
    CORE_BROAD_NEWS_V16,
    CORE_EVIDENCE_STORAGE_LANE,
    CORE_MODEL_STORAGE_PERMISSION,
    CURRENT_NEWS_CONTRACT,
    NEWS_CONTRACT_BY_ELIGIBILITY,
    NewsContract,
    SUPPORTED_NEWS_CONTRACTS,
    generation_matches_contract,
)

__all__ = [
    "CANONICAL_IDENTITY_NEWS_V17",
    "CORE_BROAD_NEWS_V16",
    "CORE_EVIDENCE_STORAGE_LANE",
    "CORE_MODEL_STORAGE_PERMISSION",
    "CURRENT_NEWS_CONTRACT",
    "NEWS_CONTRACT_BY_ELIGIBILITY",
    "NewsContract",
    "SUPPORTED_NEWS_CONTRACTS",
    "generation_matches_contract",
]
