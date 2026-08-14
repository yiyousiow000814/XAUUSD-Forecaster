"""Point-in-time structured macro release packets for model features."""

from __future__ import annotations

import json
from datetime import datetime


MACRO_RELEASE_SERIES = {
    "EIA_RWTC": ("eia_wti", "EIA Cushing WTI spot price, USD per barrel"),
    "BEA_REAL_GDP_GROWTH_QOQ_ANNUALIZED": (
        "bea_real_gdp", "BEA real GDP quarter-on-quarter annualized growth",
    ),
    "BEA_GDP_PRICE_INDEX_Q": (
        "bea_gdp_price_index", "BEA GDP price index quarterly change",
    ),
    "BEA_PCE_PRICE_INDEX_Q": (
        "bea_pce_price_index", "BEA PCE price index quarterly change",
    ),
}

MACRO_RELEASE_FEATURES = tuple(
    feature
    for prefix, _definition in MACRO_RELEASE_SERIES.values()
    for feature in (
        f"{prefix}_level",
        f"{prefix}_change",
        f"{prefix}_revision_delta",
        f"{prefix}_age_hours",
    )
)


def macro_release_packets_at(ledger, decision_time: datetime) -> list[dict]:
    """Build the latest visible packet per configured series without revision leakage."""
    rows = ledger.connection.execute(
        """SELECT * FROM macro_observations
        WHERE collector_first_seen_time <= ?
        ORDER BY series_id, observation_period, revision_number""",
        (decision_time.isoformat(),),
    ).fetchall()
    grouped: dict[str, dict[str, list[dict]]] = {}
    for raw in rows:
        row = dict(raw)
        if row["series_id"] not in MACRO_RELEASE_SERIES:
            continue
        grouped.setdefault(row["series_id"], {}).setdefault(
            row["observation_period"], []
        ).append(row)

    packets = []
    for series_id, periods in sorted(grouped.items()):
        ordered_periods = sorted(periods)
        current_period = ordered_periods[-1]
        current_revisions = periods[current_period]
        current = current_revisions[-1]
        previous_period = ordered_periods[-2] if len(ordered_periods) > 1 else None
        previous = periods[previous_period][-1] if previous_period else None
        prior_revision = current_revisions[-2] if len(current_revisions) > 1 else None
        payload = json.loads(current["payload_json"] or "{}")
        current_value = float(current["value"])
        previous_value = float(previous["value"]) if previous else None
        prior_revision_value = (
            float(prior_revision["value"]) if prior_revision else None
        )
        first_seen = datetime.fromisoformat(current["collector_first_seen_time"])
        expectation = payload.get("expectation_value", payload.get("consensus"))
        prefix, definition = MACRO_RELEASE_SERIES[series_id]
        packets.append({
            "source": current["source"],
            "series_id": series_id,
            "feature_prefix": prefix,
            "definition": payload.get("title") or definition,
            "observation_period": current_period,
            "current_value": current_value,
            "previous_period_value": previous_value,
            "prior_revision_value": prior_revision_value,
            "revision_delta": (
                current_value - prior_revision_value
                if prior_revision_value is not None else 0.0
            ),
            "expectation_value": (
                float(expectation) if expectation not in {None, ""} else None
            ),
            "release_time": payload.get("release_time"),
            "collector_first_seen_time": current["collector_first_seen_time"],
            "age_hours": max(
                0.0, (decision_time - first_seen).total_seconds() / 3600.0
            ),
            "relation_to_prior": (
                "REVISION" if prior_revision is not None else "NEW_PERIOD"
            ),
            "content_hash": current["content_hash"],
        })
    return packets


def macro_release_features_at(ledger, decision_time: datetime) -> tuple[dict, list[dict]]:
    """Return zero-filled release features and their point-in-time packets."""
    features = {name: 0.0 for name in MACRO_RELEASE_FEATURES}
    packets = macro_release_packets_at(ledger, decision_time)
    for packet in packets:
        prefix = packet["feature_prefix"]
        current = float(packet["current_value"])
        previous = packet["previous_period_value"]
        features[f"{prefix}_level"] = current
        features[f"{prefix}_change"] = (
            current - float(previous) if previous is not None else 0.0
        )
        features[f"{prefix}_revision_delta"] = float(packet["revision_delta"])
        features[f"{prefix}_age_hours"] = float(packet["age_hours"])
    return features, packets
