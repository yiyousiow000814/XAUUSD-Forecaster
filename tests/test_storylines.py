from xauusd_forecaster.storylines import storyline_rows


def event(key, time, headline, risk, grade="SINGLE_RELIABLE"):
    return {
        "event_cluster_id": key, "collector_first_seen_time": time,
        "canonical_headline": headline, "geopolitical_risk": risk,
        "evidence_grade": grade, "independent_publishers": 1,
        "topics": ("war_geopolitics",),
    }


def test_hormuz_events_form_display_only_temporal_story():
    rows = storyline_rows([
        event("a", "2026-08-06T01:00:00+00:00", "Iran threatens Strait of Hormuz shipping", 0.3),
        event("b", "2026-08-06T02:00:00+00:00", "Hormuz shipping risk rises after Iran warning", 0.6, "CORROBORATED"),
    ])
    assert len(rows) == 1
    assert rows[0]["storyline_id"] == "iran_hormuz"
    assert rows[0]["model_permission"] == "DISPLAY_ONLY"
    assert rows[0]["timeline"][1]["relation"] == "ESCALATES"


def test_single_event_is_not_presented_as_a_story():
    assert storyline_rows([event("a", "2026-08-06T01:00:00+00:00", "Iran update", 0.1)]) == []


def test_broad_topic_is_not_mistaken_for_one_story():
    assert storyline_rows([
        event("a", "2026-08-06T01:00:00+00:00", "Korea central bank buys gold", 0.1),
        event("b", "2026-08-06T02:00:00+00:00", "Ghana gold purchase loss", 0.2),
    ]) == []
