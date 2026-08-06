"""Research-only, auditable temporal storylines derived from news evidence."""

from __future__ import annotations

from collections import defaultdict


STORYLINE_POLICY_VERSION = "display-storyline-v1"

_ANCHORS = (
    ("iran_hormuz", "伊朗—霍尔木兹海峡", ("iran", "伊朗", "hormuz", "霍尔木兹")),
    ("ukraine_russia", "俄乌战争", ("ukraine", "乌克兰")),
)


def _anchor(event: dict) -> tuple[str, str] | None:
    text = str(event.get("canonical_headline") or event.get("headline") or "").casefold()
    for key, title, terms in _ANCHORS:
        if any(term.casefold() in text for term in terms):
            return key, title
    return None


def _relation(previous: dict, current: dict) -> str:
    delta = float(current.get("geopolitical_risk") or 0) - float(previous.get("geopolitical_risk") or 0)
    if delta >= 0.15:
        return "ESCALATES"
    if delta <= -0.15:
        return "DEESCALATES"
    if current.get("evidence_grade") in {"PRIMARY", "CORROBORATED"} and previous.get("evidence_grade") not in {"PRIMARY", "CORROBORATED"}:
        return "CONFIRMS"
    return "FOLLOWED_BY"


def storyline_rows(events: list[dict]) -> list[dict]:
    """Build display-only story state without inventing causal edges."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        anchor = _anchor(event)
        if anchor is None:
            continue
        key, title = anchor
        grouped[(key, title)].append(event)

    stories = []
    for (key, title), members in grouped.items():
        members = sorted(members, key=lambda row: (row["collector_first_seen_time"], row["event_cluster_id"]))
        if len(members) < 2:
            continue
        timeline = []
        previous = None
        for event in members[-12:]:
            timeline.append({
                "event_key": event["event_cluster_id"],
                "first_seen": event["collector_first_seen_time"],
                "headline": event["canonical_headline"],
                "evidence_grade": event["evidence_grade"],
                "independent_publishers": event["independent_publishers"],
                "relation": "STARTS" if previous is None else _relation(previous, event),
                "topics": list(event["topics"]),
            })
            previous = event
        recent = members[-3:]
        risk = sum(float(row.get("geopolitical_risk") or 0) for row in recent) / len(recent)
        state = "升级中" if risk >= 0.25 else "缓和中" if risk <= -0.25 else "发展中"
        reliable = sum(row["evidence_grade"] in {"PRIMARY", "CORROBORATED"} for row in members)
        stories.append({
            "storyline_id": key,
            "title": title,
            "policy_version": STORYLINE_POLICY_VERSION,
            "state": state,
            "event_count": len(members),
            "reliable_event_count": reliable,
            "latest_change": members[-1]["canonical_headline"],
            "last_updated": members[-1]["collector_first_seen_time"],
            "topics": sorted({topic for row in members for topic in row["topics"]}),
            "timeline": timeline,
            "model_permission": "DISPLAY_ONLY",
        })
    return sorted(stories, key=lambda row: row["last_updated"], reverse=True)
