from xauusd_forecaster.storylines import (
    LEGACY_POLICY_STATUS,
    storyline_rows,
    temporal_event_graph,
)


def event(key, time, headline, **overrides):
    row = {
        "event_cluster_id": key,
        "collector_first_seen_time": time,
        "source_published_time": time,
        "canonical_headline": headline,
        "evidence_grade": "SINGLE_RELIABLE",
        "independent_publishers": 1,
        "publisher_domains": ("reuters.com",),
        "source_names": ("gdelt_gold_geopolitics",),
        "primary_category": "war_geopolitics",
        "record_kind": "FACT_EVENT",
        "actor": "伊朗",
        "action": "威胁限制商业航运",
        "object": "商业航运",
        "location": "霍尔木兹海峡",
        "event_time": time,
        "claim_status": "REPORTED",
        "materiality": 0.8,
        "canonical_actor_id": "iran",
        "action_family": "THREAT",
        "canonical_object_id": "commercial_shipping",
        "canonical_location_id": "strait_of_hormuz",
        "episode_key": "iran_hormuz_shipping_2026_08",
        "primary_story_title_zh": "2026年8月伊朗—霍尔木兹航运紧张局势",
        "secondary_contexts_zh": [],
        "relation_to_prior": "NONE",
    }
    row.update(overrides)
    return row


def test_core_fact_and_market_reaction_are_separated():
    core = event("a", "2026-08-06T01:00:00+00:00", "伊朗发表霍尔木兹航运威胁")
    confirmation = event(
        "a2", "2026-08-06T01:05:00+00:00", "第二家媒体报道同一项威胁",
        event_time="2026-08-06T01:00:00+00:00", publisher_domains=("apnews.com",),
        relation_to_prior="CONFIRMS",
    )
    reaction = event(
        "b", "2026-08-06T02:00:00+00:00", "黄金因中东风险上涨",
        record_kind="MARKET_REACTION", relation_to_prior="MARKET_REACTS_TO",
        actor="黄金", action="上涨", object="", canonical_actor_id="gold",
        action_family="OTHER_FACT", canonical_object_id="risk_premium",
    )
    story = storyline_rows([core, confirmation, reaction])[0]
    assert story["event_count"] == 1
    assert story["evidence_document_count"] == 2
    assert story["latest_change"] == core["canonical_headline"]
    assert [row["headline"] for row in story["market_reactions"]] == [reaction["canonical_headline"]]


def test_spacex_amd_and_generic_commentary_cannot_create_hormuz_story():
    unrelated = event(
        "x", "2026-08-06T01:00:00+00:00", "标普创新高，SpaceX与AMD拖累",
        actor="美国股市", action="波动", object="SpaceX与AMD",
        location="", record_kind="COMMENTARY_FORECAST", materiality=0.2,
        canonical_actor_id="us_stocks", canonical_object_id="spacex_amd",
        canonical_location_id="", episode_key="",
    )
    assert storyline_rows([unrelated]) == []
    assert temporal_event_graph([unrelated])["unassigned_events"][0]["reason"] == "INSUFFICIENT_STORY_MATCH"


def test_one_article_has_only_one_primary_story_membership():
    row = event("a", "2026-08-06T01:00:00+00:00", "伊朗发表霍尔木兹航运威胁")
    row["secondary_contexts_zh"] = ["油价", "黄金避险"]
    graph = temporal_event_graph([row])
    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    assert graph["event_candidates"][0]["episode_key"] == "strait_of_hormuz_2026_08"


def test_single_publisher_is_not_independent_confirmation():
    story = storyline_rows([
        event("a", "2026-08-06T01:00:00+00:00", "伊朗发表威胁"),
        event(
            "b", "2026-08-06T02:00:00+00:00", "伊朗随后进入谈判",
            action="进入谈判", action_family="NEGOTIATION", relation_to_prior="FOLLOWED_BY",
        ),
    ])[0]
    assert story["independent_confirmation"] is False
    assert {item["key"] for item in story["covered_roles"]} == {"SINGLE_RELIABLE"}


def test_reported_confirmed_claim_does_not_impersonate_official_source():
    row = event(
        "reported-confirmation", "2026-08-06T01:00:00+00:00",
        "媒体称伊朗行动已获确认", claim_status="CONFIRMED",
        evidence_grade="DISCOVERY_ONLY", publisher_domains=("example.com",),
    )
    graph = temporal_event_graph([row])
    assert graph["stories"] == []
    assert graph["event_candidates"][0]["independent_publishers"] == 1


def test_two_publishers_can_cross_confirm_same_core_claim():
    second = event(
        "b", "2026-08-06T02:00:00+00:00", "第二家媒体确认伊朗威胁",
        publisher_domains=("apnews.com",), relation_to_prior="CONFIRMS",
    )
    graph = temporal_event_graph([event("a", "2026-08-06T01:00:00+00:00", "伊朗发表威胁"), second])
    assert graph["stories"] == []
    assert graph["event_candidates"][0]["independent_publishers"] == 2


def test_multiple_discovery_publishers_are_not_cross_confirmation():
    first = event(
        "a", "2026-08-06T01:00:00+00:00", "第一家线索站报道",
        evidence_grade="DISCOVERY_ONLY", publisher_domains=("one.example",),
    )
    second = event(
        "b", "2026-08-06T02:00:00+00:00", "第二家线索站转载",
        evidence_grade="DISCOVERY_ONLY", publisher_domains=("two.example",),
        relation_to_prior="CONFIRMS",
    )
    graph = temporal_event_graph([first, second])
    assert graph["stories"] == []
    assert graph["event_candidates"][0]["independent_publishers"] == 2


def test_hormuz_episode_aliases_merge_when_structured_anchor_matches():
    first = event(
        "a", "2026-08-06T01:00:00+00:00", "霍尔木兹谈判开始",
        episode_key="hormuz_reopening_2026_08", action_family="OFFICIAL_STATEMENT",
        canonical_object_id="strait_of_hormuz",
    )
    second = event(
        "b", "2026-08-06T02:00:00+00:00", "霍尔木兹谈判取得进展",
        episode_key="strait_of_hormuz_deal_2026_08", action_family="NEGOTIATION",
        canonical_object_id="strait_of_hormuz_deal", relation_to_prior="FOLLOWED_BY",
    )
    stories = storyline_rows([first, second])
    assert len(stories) == 1
    assert stories[0]["episode_key"] == "strait_of_hormuz_2026_08"
    assert stories[0]["event_count"] == 2


def test_mislabeled_gold_price_fact_cannot_update_core_timeline():
    core = event("a", "2026-08-06T01:00:00+00:00", "霍尔木兹谈判开始")
    follow_up = event(
        "a2", "2026-08-06T01:30:00+00:00", "伊朗回应谈判安排",
        action_family="OFFICIAL_STATEMENT", relation_to_prior="RESPONDS_TO",
    )
    reaction = event(
        "b", "2026-08-06T02:00:00+00:00", "霍尔木兹消息推动现货黄金上涨",
        record_kind="FACT_EVENT", actor="现货黄金", canonical_actor_id="spot_gold",
        action="上涨", action_family="OTHER_FACT", object="金价",
        canonical_object_id="gold_price", relation_to_prior="MARKET_REACTS_TO",
    )
    story = storyline_rows([core, follow_up, reaction])[0]
    assert story["event_count"] == 2
    assert [row["headline"] for row in story["market_reactions"]] == [reaction["canonical_headline"]]


def test_relation_never_comes_from_geopolitical_score_delta():
    first = event("a", "2026-08-06T01:00:00+00:00", "伊朗发表威胁", geopolitical_risk=-1)
    second = event(
        "b", "2026-08-06T02:00:00+00:00", "随后发布背景说明",
        geopolitical_risk=1, relation_to_prior="FOLLOWED_BY",
    )
    assert storyline_rows([first, second])[0]["timeline"][1]["relation"] == "FOLLOWED_BY"


def test_legacy_broad_subject_is_quarantined_not_used_as_story():
    legacy = event("a", "2026-08-06T01:00:00+00:00", "普通黄金价格评论")
    for field in (
        "record_kind", "actor", "action", "object", "location", "event_time",
        "claim_status", "materiality", "canonical_actor_id", "action_family",
        "canonical_object_id", "canonical_location_id", "episode_key",
        "primary_story_title_zh", "relation_to_prior",
    ):
        legacy.pop(field, None)
    legacy["story_subjects"] = ["黄金价格"]
    graph = temporal_event_graph([legacy])
    assert graph["stories"] == []
    assert graph["legacy_policy_status"] == LEGACY_POLICY_STATUS
    assert graph["unassigned_events"][0]["reason"] == "LEGACY_ANNOTATION_NO_FACT_STRUCTURE"


def test_rbi_and_bank_of_korea_aliases_are_canonicalized_without_merging_episodes():
    rbi = event(
        "rbi", "2026-08-06T03:00:00+00:00", "RBI公布8月利率决定",
        actor="RBI", canonical_actor_id="RBI", action="公布利率决定",
        action_family="POLICY_DECISION", object="政策利率",
        canonical_object_id="policy_rate", location="印度", canonical_location_id="india",
        episode_key="rbi_rate_meeting_2026_08", primary_story_title_zh="2026年8月印度储备银行利率会议",
    )
    korea = event(
        "bok", "2026-08-06T04:00:00+00:00", "韩国央行恢复购金",
        actor="韩国银行", canonical_actor_id="Bank of Korea", action="恢复购买黄金",
        action_family="GOLD_PURCHASE", object="黄金储备", canonical_object_id="gold_reserves",
        location="韩国", canonical_location_id="south_korea", episode_key="bank_of_korea_gold_purchase_2026_08",
        primary_story_title_zh="韩国央行恢复黄金购买计划",
    )
    graph = temporal_event_graph([rbi, korea])
    assert graph["stories"] == []
    assert {row["episode_key"] for row in graph["event_candidates"]} == {
        "rbi_rate_meeting_2026_08", "bank_of_korea_gold_purchase_2026_08"
    }


def test_gold_aliases_are_one_theme_stream_not_stories():
    rows = []
    for index, headline in enumerate(("黄金上涨", "黄金价格回落", "国际金价震荡")):
        rows.append(event(
            str(index), f"2026-08-06T0{index}:00:00+00:00", headline,
            primary_category="risk_sentiment", record_kind="MARKET_REACTION",
            actor="黄金", canonical_actor_id="gold", action="价格变动",
            object="", location="", canonical_object_id="", canonical_location_id="",
            episode_key="", materiality=0.2,
        ))
    graph = temporal_event_graph(rows)
    assert graph["stories"] == []
    assert [(row["title"], row["item_count"]) for row in graph["theme_streams"]] == [("黄金与风险偏好", 3)]


def test_same_central_bank_documents_are_one_event_and_one_organization():
    statement = event(
        "statement", "2026-08-06T01:00:00+00:00", "欧洲央行维持利率不变",
        source_names=("ecb_press_releases",), publisher_domains=("ecb.europa.eu",),
        evidence_grade="PRIMARY", actor="欧洲央行", canonical_actor_id="ecb",
        action="维持利率不变", action_family="POLICY_DECISION", object="政策利率",
        canonical_object_id="policy_rate", location="欧元区", canonical_location_id="euro_area",
        episode_key="ecb_rate_decision_2026_08", document_kind="OFFICIAL_STATEMENT",
    )
    questions = event(
        "questions", "2026-08-06T01:10:00+00:00", "欧洲央行记者会问答",
        source_names=("ecb_press_releases",), publisher_domains=("ecb.europa.eu",),
        evidence_grade="PRIMARY", actor="欧洲央行", canonical_actor_id="ecb",
        action="维持利率不变", action_family="POLICY_DECISION", object="政策利率",
        canonical_object_id="policy_rate", location="欧元区", canonical_location_id="euro_area",
        episode_key="ecb_rate_decision_2026_08", document_kind="PRESS_CONFERENCE",
        relation_to_prior="CONFIRMS",
    )
    graph = temporal_event_graph([statement, questions])
    assert graph["stories"] == []
    candidate = graph["event_candidates"][0]
    assert candidate["evidence_documents"] == 2
    assert candidate["independent_publishers"] == 1


def test_timeline_uses_event_time_not_collector_arrival_order():
    later_receipt = event(
        "start", "2026-08-06T03:00:00+00:00", "谈判开始",
        event_time="2026-08-06T01:00:00+00:00",
    )
    earlier_receipt = event(
        "response", "2026-08-06T02:00:00+00:00", "官方随后回应",
        event_time="2026-08-06T01:30:00+00:00", action="官方回应",
        action_family="OFFICIAL_STATEMENT", relation_to_prior="RESPONDS_TO",
    )
    story = storyline_rows([later_receipt, earlier_receipt])[0]
    assert [row["headline"] for row in story["timeline"]] == ["谈判开始", "官方随后回应"]
    assert story["timeline"][0]["collector_first_seen_time"] > story["timeline"][1]["collector_first_seen_time"]


def test_date_only_event_time_uses_source_publication_clock_for_ordering():
    date_only = event(
        "late", "2026-08-06T14:00:00+00:00", "当天稍晚发布",
        event_time="2026-08-06", source_published_time="2026-08-06T13:00:00+00:00",
    )
    precise = event(
        "early", "2026-08-06T12:00:00+00:00", "当天较早发生",
        event_time="2026-08-06T11:30:00+00:00", action="官方回应",
        action_family="OFFICIAL_STATEMENT", relation_to_prior="RESPONDS_TO",
    )
    story = storyline_rows([date_only, precise])[0]
    assert [row["headline"] for row in story["timeline"]] == ["当天较早发生", "当天稍晚发布"]


def test_archival_backfill_is_not_an_active_story():
    rows = [
        event("old-1", "2026-08-06T03:00:00+00:00", "历史政策决定", freshness_status="PRE_FORWARD_PUBLICATION"),
        event(
            "old-2", "2026-08-06T03:05:00+00:00", "历史政策后续披露",
            action="后续披露", action_family="OFFICIAL_STATEMENT", relation_to_prior="FOLLOWED_BY",
            freshness_status="PRE_FORWARD_PUBLICATION",
        ),
    ]
    graph = temporal_event_graph(rows)
    assert graph["stories"] == []
    assert graph["archived_storylines"][0]["state"] == "ARCHIVAL_BACKFILL"


def test_commentary_question_cannot_replace_latest_core_change():
    core = event("core", "2026-08-06T01:00:00+00:00", "韩国央行恢复购金")
    update = event(
        "update", "2026-08-06T01:30:00+00:00", "韩国央行确认购金计划",
        action="确认计划", action_family="OFFICIAL_STATEMENT", relation_to_prior="CONFIRMS",
    )
    commentary = event(
        "comment", "2026-08-06T02:00:00+00:00", "黄金牛市迎来新支撑？",
        record_kind="FACT_EVENT", relation_to_prior="FOLLOWED_BY",
    )
    story = storyline_rows([core, update, commentary])[0]
    assert story["latest_change"] == update["canonical_headline"]
    assert [row["headline"] for row in story["commentary"]] == [commentary["canonical_headline"]]


def test_market_reports_are_quarantined_as_narrative_candidate():
    rows = [
        event("market-1", "2026-08-06T01:00:00+00:00", "华尔街因霍尔木兹乐观情绪上涨", document_kind="MARKET_REPORT"),
        event(
            "market-2", "2026-08-06T02:00:00+00:00", "加拿大股市因海峡消息上涨",
            action="市场上涨", action_family="OTHER_FACT", relation_to_prior="FOLLOWED_BY",
            document_kind="MARKET_REPORT",
        ),
    ]
    graph = temporal_event_graph(rows)
    assert graph["stories"] == []
    assert graph["market_narrative_candidates"][0]["story_type"] == "MARKET_NARRATIVE_CANDIDATE"
    assert graph["market_narrative_candidates"][0]["coverage_total"] > 0
