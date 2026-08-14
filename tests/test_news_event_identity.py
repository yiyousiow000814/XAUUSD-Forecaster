import pytest
import sqlite3

from xauusd_forecaster.news_event_identity import resolve_event_identity
from xauusd_forecaster.news_impact import (
    prior_identity_similarity, validate_impact_assessment,
)


def assessment(update_type="DUPLICATE_REPORT", relation="SAME_EVENT", candidate="prior"):
    core_changes = ["核心状态从先前值变为当前值。"] if relation == "SAME_EPISODE" else []
    identity_differences = ["当前报道属于不同发生批次。"] if relation == "NEW_EPISODE" else []
    return {
        "impact_class": "DATA_RELEASE",
        "event_state": "COMPLETED",
        "update_type": update_type,
        "identity_relation": relation,
        "matched_candidate_id": candidate,
        "identity_anchor_zh": "同一主体、对象和发生批次。",
        "core_fact_changes_zh": core_changes,
        "identity_differences_zh": identity_differences,
        "context_differences_zh": [],
        "confidence": 0.95,
        "reason_zh": "正文与候选描述的是同一次官方数据发布。",
    }


def test_duplicate_report_reuses_the_selected_prior_identity():
    row = {
        "annotation_id": "current",
        "prior_event_context": [{
            "candidate_id": "prior",
            "canonical_episode_id": "episode-one",
            "canonical_event_id": "event-one",
        }],
    }
    result = validate_impact_assessment(assessment(), candidate_ids={"prior"})

    resolved = resolve_event_identity(row, result)

    assert resolved["canonical_episode_id"] == "episode-one"
    assert resolved["canonical_event_id"] == "event-one"
    assert resolved["matched_annotation_id"] == "prior"


def test_material_update_reuses_episode_but_mints_a_distinct_event():
    row = {
        "annotation_id": "revision",
        "prior_event_context": [{
            "candidate_id": "prior",
            "canonical_episode_id": "episode-one",
            "canonical_event_id": "event-one",
        }],
    }
    result = validate_impact_assessment(
        assessment("MATERIAL_UPDATE", "SAME_EPISODE"), candidate_ids={"prior"},
    )

    resolved = resolve_event_identity(row, result)

    assert resolved["canonical_episode_id"] == "episode-one"
    assert resolved["canonical_event_id"] != "event-one"


def test_model_cannot_select_an_identity_that_the_system_did_not_offer():
    with pytest.raises(ValueError, match="offered candidate"):
        validate_impact_assessment(assessment(candidate="invented"), candidate_ids={"prior"})


def test_commentary_candidate_cannot_become_the_fact_anchor():
    with pytest.raises(ValueError, match="core fact candidate"):
        validate_impact_assessment(
            assessment(), candidate_ids={"prior"}, same_event_candidate_ids=set(),
        )


@pytest.mark.parametrize(
    ("update_type", "relation"),
    [
        ("DUPLICATE_REPORT", "SAME_EPISODE"),
        ("MATERIAL_UPDATE", "SAME_EVENT"),
        ("NEW_EVENT", "SAME_EPISODE"),
    ],
)
def test_material_change_and_identity_relation_cannot_contradict_each_other(
    update_type, relation,
):
    candidate = "prior" if relation != "NEW_EPISODE" else ""
    with pytest.raises(ValueError):
        validate_impact_assessment(
            assessment(update_type, relation, candidate), candidate_ids={"prior"},
        )


@pytest.mark.parametrize("changed_field", [
    "数值", "状态", "决定", "行动", "规模", "生效时间", "结果", "修订",
])
def test_changed_core_fact_cannot_be_collapsed_into_same_event(changed_field):
    result = assessment()
    result["core_fact_changes_zh"] = [f"当前报道新增或改变了核心{changed_field}。"]

    with pytest.raises(ValueError, match="factual equivalence"):
        validate_impact_assessment(
            result, candidate_ids={"prior"}, same_event_candidate_ids={"prior"},
        )


def test_source_language_and_wording_differences_remain_same_event_context():
    result = assessment()
    result["context_differences_zh"] = ["来源、语言和标题措辞不同。"]

    validated = validate_impact_assessment(
        result, candidate_ids={"prior"}, same_event_candidate_ids={"prior"},
    )

    assert validated["identity_relation"] == "SAME_EVENT"
    assert validated["core_fact_changes_zh"] == []


def test_incomplete_candidate_context_cannot_claim_a_new_episode():
    with pytest.raises(ValueError, match="complete candidate context"):
        validate_impact_assessment(
            assessment("NEW_EVENT", "NEW_EPISODE", ""),
            candidate_ids={"prior"}, candidate_context_complete=False,
        )


def test_same_continuous_market_object_does_not_create_identity_similarity():
    current = {
        "material_event_key": "gold_pullback_aug_2026",
        "episode_key": "gold_pullback_2026_08",
        "canonical_actor_id": "investors",
        "canonical_object_id": "gold",
        "actor": "Investors", "object": "gold", "entities": ["Gold"],
    }
    prior = {
        "material_event_key": "gold_price_surpasses_4400_aug_2026",
        "episode_key": "gold_price_rebound_2026_08",
        "canonical_actor_id": "korea_gold_exchange",
        "canonical_object_id": "gold",
        "actor": "Korea Gold Exchange", "object": "gold prices",
        "entities": ["Gold"],
    }

    assert prior_identity_similarity(current, prior) == 0.0


def test_shared_collection_cluster_recalls_semantically_drifted_syndication():
    current = {
        "cluster_id": "shared-source-document",
        "canonical_actor_id": "television_academy",
        "canonical_object_id": "hall_of_fame_inductees",
    }
    prior = {
        "cluster_id": "shared-source-document",
        "canonical_actor_id": "the_television_academy",
        "canonical_object_id": "jean_smart_and_ted_danson",
    }

    assert prior_identity_similarity(current, prior) == 1.0


def test_shared_subject_without_an_occurrence_anchor_is_not_recalled():
    current = {
        "cluster_id": "morning-report",
        "canonical_actor_id": "market_participants",
        "canonical_object_id": "gold_price",
    }
    prior = {
        "cluster_id": "evening-report",
        "canonical_actor_id": "different_market_observer",
        "canonical_object_id": "gold_price",
    }

    assert prior_identity_similarity(current, prior) == 0.0


def test_same_actor_and_object_remain_candidates_across_key_wording() -> None:
    current = {
        "material_event_key": "south_africa_jobs_q2",
        "episode_key": "south_africa_labor_2026_q2",
        "canonical_actor_id": "statistics_south_africa",
        "canonical_object_id": "unemployment_rate",
    }
    prior = {
        "material_event_key": "za_unemployment_2026q2",
        "episode_key": "za_jobs_q2_2026",
        "canonical_actor_id": "statistics_south_africa",
        "canonical_object_id": "unemployment_rate",
    }

    assert prior_identity_similarity(current, prior) >= 0.75


@pytest.mark.parametrize(
    ("current", "prior", "expected_similarity"),
    [
        pytest.param(
            {
                "material_event_key": "south-africa-jobs-q2",
                "episode_key": "south africa labor 2026 q2",
                "canonical_actor_id": "Statistics-South-Africa",
                "canonical_object_id": "Unemployment Rate",
            },
            {
                "material_event_key": "za_unemployment_2026q2",
                "episode_key": "za_jobs_q2_2026",
                "canonical_actor_id": "statistics_south_africa",
                "canonical_object_id": "unemployment_rate",
            },
            0.75,
            id="same-release-different-wording",
        ),
        pytest.param(
            {
                "material_event_key": "july_2026_us_jobs_report_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "us_jobs_report_july",
            },
            {
                "material_event_key": "us_july_2026_jobs_report_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "us_july_jobs_report",
            },
            1.0,
            id="current-cross-publisher-jobs-example",
        ),
        pytest.param(
            {
                "canonical_actor_id": "us_bls",
                "canonical_object_id": "consumer_price_index",
            },
            {
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            0.75,
            id="current-bls-alias-example",
        ),
        pytest.param(
            {
                "material_event_key": "us_july_2026_ppi_release",
                "episode_key": "us_ppi_release_2026_08",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "us_ppi_report",
            },
            {
                "material_event_key": "us_cpi_release_2026_08_14",
                "episode_key": "us_cpi_release_2026_08",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            0.0,
            id="current-bls-ppi-and-cpi-remain-separate",
        ),
        pytest.param(
            {
                "material_event_key": "fed_holds_rates_july",
                "episode_key": "fomc_july_2026",
                "canonical_actor_id": "federal_reserve",
                "canonical_object_id": "federal_funds_target",
            },
            {
                "material_event_key": "fomc_rate_decision_202607",
                "episode_key": "fed_meeting_2026_07",
                "canonical_actor_id": "Federal Reserve",
                "canonical_object_id": "federal-funds-target",
            },
            0.75,
            id="same-policy-decision-different-keys",
        ),
        pytest.param(
            {
                "material_event_key": "us_cpi_2026_08",
                "episode_key": "us_inflation_august_2026",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            {
                "material_event_key": "us_cpi_2026_07",
                "episode_key": "us_inflation_july_2026",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            0.75,
            id="same-series-new-period-is-still-compared",
        ),
        pytest.param(
            {
                "material_event_key": "gold_evening_pullback",
                "episode_key": "gold_market_evening",
                "canonical_actor_id": "spot_gold_market",
                "canonical_object_id": "gold_price",
            },
            {
                "material_event_key": "gold_morning_rally",
                "episode_key": "gold_market_morning",
                "canonical_actor_id": "korea_gold_exchange",
                "canonical_object_id": "gold_price",
            },
            0.0,
            id="same-asset-different-actor",
        ),
        pytest.param(
            {
                "material_event_key": "bls_cpi_release",
                "episode_key": "us_inflation_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            {
                "material_event_key": "bls_payroll_release",
                "episode_key": "us_employment_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "nonfarm_payrolls",
            },
            0.0,
            id="same-institution-different-object",
        ),
        pytest.param(
            {
                "material_event_key": "bls_core_cpi_release",
                "episode_key": "us_core_inflation_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "core_consumer_price_index",
            },
            {
                "material_event_key": "bls_cpi_release",
                "episode_key": "us_inflation_release",
                "canonical_actor_id": "bureau_of_labor_statistics",
                "canonical_object_id": "consumer_price_index",
            },
            0.5,
            id="related-multiword-object-is-lower-priority",
        ),
        pytest.param(
            {
                "material_event_key": "central_bank_rate_comment",
                "episode_key": "rate_comment",
                "canonical_actor_id": "federal_reserve",
                "canonical_object_id": "rate",
            },
            {
                "material_event_key": "fed_interest_rate_decision",
                "episode_key": "policy_decision",
                "canonical_actor_id": "federal_reserve",
                "canonical_object_id": "interest_rate",
            },
            0.0,
            id="generic-single-token-object-does-not-match",
        ),
    ],
)
def test_candidate_admission_uses_identity_family_not_incident_keywords(
    current, prior, expected_similarity,
) -> None:
    assert prior_identity_similarity(current, prior) == expected_similarity


@pytest.mark.parametrize(
    ("update_type", "relation", "matched", "core_changes", "identity_differences"),
    [
        pytest.param(
            "DUPLICATE_REPORT", "SAME_EVENT", "prior", [], [],
            id="same-release-from-another-publisher",
        ),
        pytest.param(
            "MATERIAL_UPDATE", "SAME_EPISODE", "prior",
            ["同一统计期的公布值被正式修订。"], [],
            id="formal-revision-within-release",
        ),
        pytest.param(
            "NEW_EVENT", "NEW_EPISODE", "", [],
            ["参考期间不同，属于新的官方发布批次。"],
            id="same-series-new-reference-period",
        ),
        pytest.param(
            "NEW_EVENT", "NEW_EPISODE", "", [],
            ["观察时段和明确驱动不同，属于另一段市场变化。"],
            id="same-asset-separate-observation",
        ),
        pytest.param(
            "COMMENTARY", "UNRESOLVED", "", [], [],
            id="insufficient-visible-evidence",
        ),
    ],
)
def test_identity_outcome_matrix_keeps_merge_and_split_paths_open(
    update_type, relation, matched, core_changes, identity_differences,
) -> None:
    result = assessment(update_type, relation, matched)
    result["core_fact_changes_zh"] = core_changes
    result["identity_differences_zh"] = identity_differences

    validated = validate_impact_assessment(
        result,
        candidate_ids={"prior"},
        same_event_candidate_ids={"prior"},
    )

    assert validated["update_type"] == update_type
    assert validated["identity_relation"] == relation
    assert validated["matched_candidate_id"] == matched


def test_batch_resolution_refreshes_a_prior_identity_persisted_after_selection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE news_event_identity_resolutions_v1 (
        annotation_id TEXT, canonical_episode_id TEXT, canonical_event_id TEXT,
        resolved_at TEXT)"""
    )
    connection.execute(
        "INSERT INTO news_event_identity_resolutions_v1 VALUES (?,?,?,?)",
        ("prior", "root-episode", "root-event", "2026-08-13T00:00:00+00:00"),
    )
    row = {
        "annotation_id": "current",
        "prior_event_context": [{"candidate_id": "prior"}],
    }

    resolved = resolve_event_identity(row, assessment(), connection=connection)

    assert resolved["canonical_episode_id"] == "root-episode"
    assert resolved["canonical_event_id"] == "root-event"
