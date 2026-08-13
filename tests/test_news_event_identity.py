import pytest
import sqlite3

from xauusd_forecaster.news_event_identity import resolve_event_identity
from xauusd_forecaster.news_impact import validate_impact_assessment


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
