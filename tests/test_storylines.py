import pytest

from xauusd_forecaster.news_semantics import CURRENT_NEWS_PROMPT_VERSION
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
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
        "parsed_at": time,
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
        "document_kind": "NEWS_ARTICLE",
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
    graph = temporal_event_graph([core, confirmation, reaction])
    assert graph["stories"] == []
    assert graph["event_candidates"][0]["evidence_documents"] == 2
    assert graph["market_reaction_streams"][0]["latest_headline"] == reaction["canonical_headline"]


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
    assert graph["event_candidates"] == []


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
    assert graph["event_candidates"] == []


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


def test_gold_price_reaction_cannot_update_core_timeline():
    core = event("a", "2026-08-06T01:00:00+00:00", "霍尔木兹谈判开始")
    follow_up = event(
        "a2", "2026-08-06T01:30:00+00:00", "伊朗回应谈判安排",
        action_family="OFFICIAL_STATEMENT", relation_to_prior="RESPONDS_TO",
    )
    reaction = event(
        "b", "2026-08-06T02:00:00+00:00", "霍尔木兹消息推动现货黄金上涨",
        record_kind="MARKET_REACTION", actor="现货黄金", canonical_actor_id="spot_gold",
        action="上涨", action_family="OTHER_FACT", object="金价",
        canonical_object_id="gold_price", relation_to_prior="MARKET_REACTS_TO",
    )
    story = storyline_rows([core, follow_up, reaction])[0]
    assert story["event_count"] == 2
    assert [row["headline"] for row in story["market_reactions"]] == [reaction["canonical_headline"]]


def test_us_july_jobs_aliases_are_one_material_event_not_many_stories():
    released = event(
        "jobs-a", "2026-08-07T12:30:00+00:00", "美国7月非农就业人口意外减少",
        primary_category="inflation_employment", actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国7月非农就业报告",
        canonical_object_id="us_nonfarm_payrolls_july_2026", location="美国",
        canonical_location_id="united_states", episode_key="us_nfp_july_2026",
        material_event_key="bls_release_2026_08_07", primary_story_title_zh="美国2026年7月就业报告",
    )
    same_release = event(
        "jobs-b", "2026-08-07T12:35:00+00:00", "美国7月就业报告显示岗位减少",
        primary_category="inflation_employment", actor="美国劳工部",
        canonical_actor_id="us_labor_department", action="公布",
        action_family="ECONOMIC_RELEASE", object="美国就业报告",
        canonical_object_id="us_jobs_report_2026_07", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_2026_07",
        material_event_key="jobs_report_july_2026", relation_to_prior="CONFIRMS",
        publisher_domains=("apnews.com",), primary_story_title_zh="美国7月就业报告疲软",
    )
    graph = temporal_event_graph([released, same_release])
    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    assert graph["event_candidates"][0]["episode_key"] == "us_employment_report_2026_07"
    assert graph["event_candidates"][0]["evidence_documents"] == 2


@pytest.mark.parametrize(
    ("actor_a", "actor_b", "object_a", "object_b", "episode_a", "episode_b"),
    [
        (
            "Statistics South Africa", "Stats SA", "Quarterly Labour Force Survey",
            "South Africa unemployment rate 33.6%", "south_africa_unemployment_q2_2026",
            "south_africa_unemployment_2026_q2",
        ),
        (
            "Bureau of Labor Statistics", "US Labor Department", "Consumer Price Index July 2026",
            "US CPI for 2026-07", "us_cpi_july_2026", "bls_consumer_prices_2026_07",
        ),
        (
            "Bureau of Economic Analysis", "US BEA", "GDP second quarter 2026",
            "Q2 2026 gross domestic product", "us_gdp_2026_q2", "bea_gdp_q2_2026",
        ),
    ],
)
def test_semantically_resolved_economic_release_family_is_one_real_event(
    actor_a, actor_b, object_a, object_b, episode_a, episode_b,
):
    first = event(
        "release-a", "2026-08-11T10:00:00+00:00", "官方经济数据发布",
        primary_category="inflation_employment", actor=actor_a,
        canonical_actor_id=actor_a, action="released", action_family="ECONOMIC_RELEASE",
        object=object_a, canonical_object_id=object_a, location="",
        canonical_location_id="", episode_key=episode_a,
        material_event_key=episode_a, resolved_episode_id="release-family-episode",
        resolved_event_id="release-family-event",
        resolved_identity_relation="NEW_EPISODE",
    )
    second = event(
        "release-b", "2026-08-11T10:05:00+00:00", "媒体以另一种写法报道同一数据",
        primary_category="inflation_employment", actor=actor_b,
        canonical_actor_id=actor_b, action="reported", action_family="ECONOMIC_RELEASE",
        object=object_b, canonical_object_id=object_b, location="",
        canonical_location_id="", episode_key=episode_b,
        material_event_key=episode_b, relation_to_prior="CONFIRMS",
        publisher_domains=("apnews.com",),
        resolved_episode_id="release-family-episode",
        resolved_event_id="release-family-event",
        resolved_identity_relation="SAME_EVENT",
    )

    graph = temporal_event_graph([first, second])

    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    assert graph["event_candidates"][0]["evidence_documents"] == 2


def test_semantic_resolution_preserves_a_real_later_release_as_a_new_node():
    initial = event(
        "q2", "2026-08-11T10:00:00+00:00", "第二季度失业率发布",
        actor="Statistics South Africa", action="released",
        action_family="ECONOMIC_RELEASE", object="Q2 unemployment rate",
        canonical_actor_id="statistics_south_africa",
        canonical_object_id="unemployment_q2_2026", episode_key="free_text_a",
        resolved_episode_id="south-africa-labour-series",
        resolved_event_id="south-africa-q2-release",
        resolved_identity_relation="NEW_EPISODE",
    )
    revision = event(
        "q2-revision", "2026-08-12T10:00:00+00:00", "第二季度数据正式修订",
        actor="Stats SA", action="revised", action_family="ECONOMIC_RELEASE",
        object="Q2 unemployment rate revision", canonical_actor_id="stats_sa",
        canonical_object_id="q2_2026_unemployment_revision", episode_key="free_text_b",
        relation_to_prior="SUPERSEDES",
        resolved_episode_id="south-africa-labour-series",
        resolved_event_id="south-africa-q2-revision",
        resolved_identity_relation="SAME_EPISODE",
    )

    story = storyline_rows([initial, revision])[0]

    assert story["event_count"] == 2
    assert [row["relation"] for row in story["timeline"]] == ["STARTS", "SUPERSEDES"]


def test_unresolved_current_identity_cannot_create_a_storyline():
    unresolved = event(
        "unresolved", "2026-08-12T10:00:00+00:00", "多事件市场周报",
        resolved_identity_relation="UNRESOLVED",
        resolved_episode_id="placeholder-episode",
        resolved_event_id="placeholder-event",
    )

    graph = temporal_event_graph([unresolved])

    assert graph["stories"] == []
    assert graph["event_candidates"] == []
    assert graph["unassigned_events"][0]["reason"] == "INSUFFICIENT_STORY_MATCH"


def test_market_response_to_jobs_report_cannot_become_core_fact():
    reaction = event(
        "jobs-reaction", "2026-08-07T12:40:00+00:00",
        "美债收益率因美国就业报告疲软而下跌",
        primary_category="inflation_employment", record_kind="MARKET_REACTION",
        actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国7月就业报告",
        canonical_object_id="us_jobs_report_2026_07", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_july_2026",
        material_event_key="market_article_yields_jobs",
    )
    graph = temporal_event_graph([reaction])
    assert graph["stories"] == []
    assert graph["event_candidates"] == []
    assert graph["market_reaction_streams"][0]["stream_id"] == "treasury_yields"


def test_jobs_reporting_month_beats_release_month_episode_alias():
    released = event(
        "jobs-release-month", "2026-08-07T12:30:00+00:00",
        "美国7月就业报告显示就业岗位减少",
        primary_category="inflation_employment", actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国2026年7月就业报告",
        canonical_object_id="us_jobs_report_july_2026", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_2026_08",
        material_event_key="release_month_alias",
    )
    graph = temporal_event_graph([released])
    assert graph["event_candidates"][0]["episode_key"] == "us_employment_report_2026_07"


def test_bls_release_month_alias_is_shifted_to_previous_reporting_month():
    released = event(
        "jobs-generic-object", "2026-08-07T13:00:00+00:00",
        "美国就业报告意外疲软，市场讨论美联储政策",
        source_published_time="2026-08-07T12:30:00+00:00",
        primary_category="inflation_employment", actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国就业数据",
        canonical_object_id="us_employment_data", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_2026_08",
        material_event_key="release_month_generic_object",
    )
    graph = temporal_event_graph([released])
    assert graph["event_candidates"][0]["episode_key"] == "us_employment_report_2026_07"


def test_silver_response_to_jobs_report_cannot_become_core_fact():
    reaction = event(
        "jobs-silver-reaction", "2026-08-07T13:10:00+00:00",
        "白银价格因美国就业报告令人失望而上涨",
        primary_category="inflation_employment", record_kind="MARKET_REACTION",
        actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国就业报告",
        canonical_object_id="us_jobs_report", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_2026_08_07",
        material_event_key="silver_market_response",
    )
    graph = temporal_event_graph([reaction])
    assert graph["stories"] == []
    assert graph["event_candidates"] == []
    assert graph["market_reaction_streams"]


def test_market_bets_after_jobs_report_cannot_become_core_fact():
    reaction = event(
        "jobs-fed-bets", "2026-08-07T13:15:00+00:00",
        "美国就业报告疲软，市场押注美联储九月不会加息",
        primary_category="inflation_employment", record_kind="MARKET_REACTION",
        actor="美国劳工统计局",
        canonical_actor_id="bureau_of_labor_statistics", action="发布",
        action_family="ECONOMIC_RELEASE", object="美国就业数据",
        canonical_object_id="us_employment_data", location="美国",
        canonical_location_id="united_states", episode_key="us_jobs_report_2026_08",
        material_event_key="fed_bets_market_response",
    )
    graph = temporal_event_graph([reaction])
    assert graph["stories"] == []
    assert graph["event_candidates"] == []
    assert graph["market_reaction_streams"]


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
    assert [(row["title"], row["item_count"]) for row in graph["theme_streams"]] == [("黄金与风险情绪 / 避险", 3)]


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


def test_declared_publisher_and_its_domain_count_as_one_organization():
    row = event(
        "cnbc", "2026-08-07T18:00:00+00:00", "同一家媒体的一篇报道",
        source_organizations=("cnbc",), source_organization_id="cnbc",
        publisher_domains=("cnbc.com",),
    )
    candidate = temporal_event_graph([row])["event_candidates"][0]
    assert candidate["independent_publishers"] == 1


def test_lisa_cook_aliases_are_one_event_candidate_not_three_stories():
    rows = [
        event(
            "cnbc", "2026-08-07T17:45:00+00:00",
            "特朗普重启解雇美联储理事丽莎·库克的努力",
            canonical_actor_id="donald_trump", actor="Donald Trump",
            action="renews bid to fire", action_family="OFFICIAL_STATEMENT",
            object="Lisa Cook", canonical_object_id="lisa_cook",
            location="Washington", canonical_location_id="washington_dc",
            episode_key="trump_fires_lisa_cook_2026_08",
            material_event_key="trump_lisa_cook_dismissal_effort_2026",
            relation_to_prior="ESCALATES", source_organizations=("cnbc",),
            publisher_domains=("cnbc.com",),
        ),
        event(
            "guardian", "2026-08-07T18:47:00+00:00",
            "特朗普不顾法院裁决，再次企图解雇丽莎·库克",
            canonical_actor_id="donald_trump", actor="Donald Trump",
            action="renews bid to fire", action_family="POLICY_DECISION",
            object="Lisa Cook", canonical_object_id="lisa_cook",
            location="Washington", canonical_location_id="washington_dc",
            episode_key="trump_fire_cook_aug_2026",
            material_event_key="trump_lisa_cook_firing_2026_08",
            relation_to_prior="ESCALATES", source_organizations=("the_guardian",),
            publisher_domains=("theguardian.com",),
        ),
        event(
            "npr", "2026-08-07T19:35:00+00:00",
            "特朗普再度推进罢免美联储理事丽莎·库克",
            canonical_actor_id="white_house", actor="White House",
            action="threatens to fire", action_family="REGULATORY_ACTION",
            object="Lisa Cook", canonical_object_id="lisa_cook",
            location="Washington", canonical_location_id="washington_dc",
            episode_key="trump_fires_lisa_cook_aug_2026",
            material_event_key="trump_lisa_cook_dismissal_push_2026_08",
            relation_to_prior="ESCALATES", source_organizations=("npr",),
            publisher_domains=("npr.org",),
        ),
    ]
    graph = temporal_event_graph(rows)
    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    candidate = graph["event_candidates"][0]
    assert candidate["episode_key"] == "lisa_cook_removal_2026_08"
    assert candidate["evidence_documents"] == 3
    assert candidate["independent_publishers"] == 3


def test_lisa_cook_court_decision_is_a_distinct_story_development():
    attempt = event(
        "attempt", "2026-08-07T18:00:00+00:00",
        "特朗普重启罢免丽莎·库克的行动",
        canonical_actor_id="donald_trump", action_family="REGULATORY_ACTION",
        canonical_object_id="lisa_cook", object="Lisa Cook",
        episode_key="trump_fires_lisa_cook_2026_08",
        material_event_key="removal_attempt", relation_to_prior="ESCALATES",
    )
    ruling = event(
        "ruling", "2026-08-08T15:00:00+00:00",
        "法院裁决驳回罢免丽莎·库克的申请",
        canonical_actor_id="supreme_court", actor="法院",
        action="作出法院裁决", action_family="COURT_DECISION",
        canonical_object_id="lisa_cook_removal", object="丽莎·库克罢免案",
        episode_key="lisa_cook_firing_case_2026_08",
        material_event_key="cook_court_ruling", relation_to_prior="DEESCALATES",
    )
    story = storyline_rows([attempt, ruling])[0]
    assert story["episode_key"] == "lisa_cook_removal_2026_08"
    assert story["event_count"] == 2


def test_lisa_cook_rate_reports_collapse_to_one_development():
    rows = [
        event(
            "cook-a", "2026-08-05T20:13:00+00:00",
            "丽莎·库克称通胀高企时准备加息",
            canonical_actor_id="lisa_cook", actor="Lisa Cook",
            action_family="OFFICIAL_STATEMENT", action="prepared to raise rates",
            canonical_object_id="interest_rates", object="interest rates",
            episode_key="lisa_cook_interest_rates_2026_08",
            material_event_key="cook_rate_policy_a", record_kind="OFFICIAL_CLAIM",
        ),
        event(
            "cook-b", "2026-08-05T20:36:00+00:00",
            "库克表示若通胀持续将采取加息行动",
            canonical_actor_id="lisa_cook", actor="Lisa Cook",
            action_family="OFFICIAL_STATEMENT", action="prepared to act on rate hike",
            canonical_object_id="interest_rates", object="interest rates",
            episode_key="lisa_cook_rate_hike_warning_2026_08",
            material_event_key="cook_rate_policy_b", record_kind="OFFICIAL_CLAIM",
            publisher_domains=("cnbc.com",),
        ),
    ]

    graph = temporal_event_graph(rows)

    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    assert graph["event_candidates"][0]["evidence_documents"] == 2


def test_tbac_report_and_minutes_are_one_meeting_development():
    rows = [
        event(
            "tbac-report", "2026-08-05T12:30:00+00:00", "借款咨询委员会提交季度报告",
            canonical_actor_id="treasury_borrowing_advisory_committee",
            actor="Treasury Borrowing Advisory Committee",
            action_family="OFFICIAL_STATEMENT", action="submitted report",
            canonical_object_id="secretary_of_the_treasury", object="Treasury Secretary",
            canonical_location_id="washington_dc", location="Washington",
            episode_key="tbac_report_2026_08", material_event_key="tbac_report",
            record_kind="OFFICIAL_CLAIM",
        ),
        event(
            "tbac-minutes", "2026-08-05T12:30:00+00:00", "借款咨询委员会发布会议纪要",
            canonical_actor_id="treasury_borrowing_advisory_committee",
            actor="Treasury Borrowing Advisory Committee",
            action_family="POLICY_DECISION", action="recommended auction sizes",
            canonical_object_id="treasury_auction_sizes", object="auction sizes",
            canonical_location_id="washington_dc", location="Washington",
            episode_key="tbac_meeting_aug_2026", material_event_key="tbac_minutes",
            record_kind="FACT_EVENT",
        ),
    ]

    graph = temporal_event_graph(rows)

    assert graph["stories"] == []
    assert len(graph["event_candidates"]) == 1
    assert graph["event_candidates"][0]["evidence_documents"] == 2


def test_gold_breakout_article_is_market_reaction_not_jobs_release():
    row = event(
        "gold-reaction", "2026-08-07T21:05:00+00:00",
        "黄金在疲软就业数据公布后实现突破",
        record_kind="MARKET_REACTION",
        canonical_actor_id="bureau_of_labor_statistics", actor="BLS",
        action_family="ECONOMIC_RELEASE", action="reported",
        canonical_object_id="us_employment_data", object="July jobs",
        episode_key="us_jobs_report_jul_2026", material_event_key="jobs-gold-reaction",
    )

    graph = temporal_event_graph([row])

    assert graph["event_candidates"] == []
    assert graph["market_reaction_streams"][0]["latest_headline"] == row["canonical_headline"]


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


def test_future_llm_event_time_cannot_become_story_start():
    first = event(
        "a", "2026-08-08T10:00:00+00:00", "官方发布就业报告",
        event_time="2026-08-08T15:00:00+00:00",
        source_published_time="2026-08-08T09:55:00+00:00",
        parsed_at="2026-08-08T10:00:00+00:00",
    )
    second = event(
        "b", "2026-08-08T10:05:00+00:00", "官方随后说明报告",
        event_time="2026-08-08T10:04:00+00:00",
        relation_to_prior="RESPONDS_TO",
    )

    story = storyline_rows([first, second])[0]

    assert story["timeline"][0]["headline"] == "官方发布就业报告"
    assert story["timeline"][0]["event_time"] == "2026-08-08T09:55:00+00:00"


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
        record_kind="COMMENTARY_FORECAST", relation_to_prior="FOLLOWED_BY",
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
    assert graph["market_narrative_candidates"] == []
