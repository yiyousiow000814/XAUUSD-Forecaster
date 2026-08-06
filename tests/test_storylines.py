from xauusd_forecaster.storylines import storyline_rows


def event(key, time, headline, risk, grade="SINGLE_RELIABLE", sources=("gdelt_gold_geopolitics",), domains=("reuters.com",)):
    return {
        "event_cluster_id": key, "collector_first_seen_time": time,
        "canonical_headline": headline, "geopolitical_risk": risk,
        "evidence_grade": grade, "independent_publishers": len(domains),
        "topics": ("war_geopolitics",),
        "entities": ("Iran", "Hormuz"), "source_names": sources,
        "publisher_domains": domains,
    }


def test_hormuz_events_form_display_only_temporal_story():
    rows = storyline_rows([
        event("a", "2026-08-06T01:00:00+00:00", "Iran threatens Strait of Hormuz shipping", 0.3),
        event("b", "2026-08-06T02:00:00+00:00", "Hormuz shipping risk rises after Iran warning", 0.6, "CORROBORATED"),
    ])
    assert len(rows) == 1
    assert rows[0]["storyline_id"].startswith("story-")
    assert rows[0]["title"] == "伊朗—霍尔木兹海峡"
    assert rows[0]["model_permission"] == "DISPLAY_ONLY"
    assert rows[0]["timeline"][1]["relation"] == "ESCALATES"
    assert rows[0]["family"] == "geopolitics"
    assert rows[0]["coverage_total"] == 4
    assert any(item["status"] == "NEEDS_DISCOVERY" for item in rows[0]["candidate_sources"])


def test_single_event_is_not_presented_as_a_story():
    assert storyline_rows([event("a", "2026-08-06T01:00:00+00:00", "Iran update", 0.1)]) == []


def test_broad_topic_is_not_mistaken_for_one_story():
    korea = event("a", "2026-08-06T01:00:00+00:00", "Korea central bank buys gold", 0.1)
    ghana = event("b", "2026-08-06T02:00:00+00:00", "Ghana gold purchase loss", 0.2)
    korea["entities"] = ("Korea",)
    ghana["entities"] = ("Ghana",)
    assert storyline_rows([korea, ghana]) == []


def test_source_roles_drive_coverage_not_source_name_whitelist_permission():
    rows = storyline_rows([
        event("a", "2026-08-06T01:00:00+00:00", "Iran warns on Hormuz", 0.2,
              sources=("us_treasury_press_releases",), domains=()),
        event("b", "2026-08-06T02:00:00+00:00", "Iran Hormuz risk update", 0.2,
              grade="PRIMARY", sources=("eia_press_releases",), domains=()),
    ])
    assert {role["key"] for role in rows[0]["covered_roles"]} == {"OFFICIAL_PRIMARY"}
    assert "PHYSICAL_MONITOR" in {role["key"] for role in rows[0]["missing_roles"]}


def test_shared_gemini_entities_do_not_join_unrelated_headlines():
    first = event("a", "2026-08-06T01:00:00+00:00", "韩国央行恢复购买黄金", 0.1)
    second = event("b", "2026-08-06T02:00:00+00:00", "现货黄金突破4200美元", 0.2)
    first["entities"] = second["entities"] = ("World Gold Council", "Deutsche Bank")
    assert storyline_rows([first, second]) == []


def test_eia_aliases_merge_into_one_energy_story():
    first = event("a", "2026-08-06T01:00:00+00:00", "EIA updates oil outlook", 0.1)
    second = event(
        "b", "2026-08-06T02:00:00+00:00",
        "U.S. Department of Energy updates oil outlook", 0.1,
    )
    for row in (first, second):
        row["primary_category"] = "oil_energy"
        row["topics"] = ("oil_energy",)
        row["entities"] = ()
        row["source_names"] = ("eia_today_in_energy",)
    rows = storyline_rows([first, second])
    assert len(rows) == 1
    assert rows[0]["title"] == "U.S. EIA"


def test_hormuz_energy_story_is_not_mislabeled_as_second_eia_story():
    rows = [
        event("a", "2026-08-06T01:00:00+00:00", "EIA updates oil outlook", 0.1),
        event("b", "2026-08-06T02:00:00+00:00", "Department of Energy updates oil outlook", 0.1),
        event("c", "2026-08-06T03:00:00+00:00", "EIA assesses Strait of Hormuz disruption", 0.2),
        event("d", "2026-08-06T04:00:00+00:00", "Department of Energy sees Hormuz reopening", 0.1),
    ]
    for row in rows:
        row["primary_category"] = "oil_energy"
        row["topics"] = ("oil_energy",)
        row["entities"] = ()
        row["source_names"] = ("eia_today_in_energy",)
    stories = storyline_rows(rows)
    assert {story["title"] for story in stories} == {"U.S. EIA", "霍尔木兹海峡"}
