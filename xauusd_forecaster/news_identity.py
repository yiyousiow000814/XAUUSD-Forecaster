"""Compatibility shim for xauusd_forecaster.news.retrieval.identity."""

from xauusd_forecaster.news.retrieval.identity import (
    NEWS_CURRENT_REPRESENTATIVE_CONTRACT_VERSION,
    RESOLVED_IDENTITY_RELATIONS,
    SOURCE_ORGANIZATION_ALIASES,
    canonical_id,
    canonical_material_event_anchor,
    canonical_source_organization,
    canonical_story_episode,
    identity_resolution_status,
    news_representative_key,
    preferred_cluster_peer_predicate,
    resolved_identity_ids,
)

__all__ = [
    "NEWS_CURRENT_REPRESENTATIVE_CONTRACT_VERSION",
    "RESOLVED_IDENTITY_RELATIONS",
    "SOURCE_ORGANIZATION_ALIASES",
    "canonical_id",
    "canonical_material_event_anchor",
    "canonical_source_organization",
    "canonical_story_episode",
    "identity_resolution_status",
    "news_representative_key",
    "preferred_cluster_peer_predicate",
    "resolved_identity_ids",
]
