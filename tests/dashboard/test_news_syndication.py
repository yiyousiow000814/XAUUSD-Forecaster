from copy import deepcopy
from itertools import permutations

from xauusd_forecaster.dashboard.news_syndication import group_article_copies
from xauusd_forecaster.news.semantics.article_source import readable_source_text

LEAD = "The central bank has scheduled its next policy meeting for Wednesday, while the decision and accompanying statement have not yet been announced."
DETAIL = "A separate retail report will follow. Analysts describe two conditional outcomes rather than an announcement that rates have already changed."


def article(item, body, **extra):
    return dict(source="google_news_fed_rates", source_item_id=item,
                revision_number=1, original_headline=f"Weekly outlook - {item}",
                source_article_title="Weekly outlook", headline="本周展望",
                source_published_time="2026-09-12T08:00:00Z",
                collector_first_seen_time=f"2026-09-12T08:00:0{item[-1] if item[-1].isdigit() else '0'}Z",
                parsed_at="2026-09-12T09:00:00Z", body=body,
                cluster_id=item, annotation_status="READY", model_visibility="MODEL_INELIGIBLE",
                event_type="macro_preview", emerging_topic_zh="政策会议展望",
                **extra)


def test_syndicated_article_uses_one_complete_interpretation_and_preserves_sources():
    rows = [article("publisher1", LEAD+"\n"+DETAIL),
            article("publisher2", LEAD+"\n"+DETAIL),
            article("publisher3", LEAD+"\nkAm%96 6?4@565 A2C28C2A9k^Am")]
    rows[1].update(event_type="monetary_policy_meeting", emerging_topic_zh="决议已经公布")
    original = deepcopy(rows)
    expected = group_article_copies(rows)
    assert len(expected) == 1
    assert expected[0]["syndicated_source_count"] == 3
    assert expected[0]["event_type"] == "macro_preview"
    assert expected[0]["emerging_topic_zh"] == "政策会议展望"
    assert expected[0]["body"] == LEAD+"\n"+DETAIL
    assert {s["source_item_id"] for s in expected[0]["syndicated_sources"]} == {r["source_item_id"] for r in rows}
    assert expected[0]["model_visibility"] == "MODEL_INELIGIBLE"
    assert rows == original
    for order in permutations(rows):
        assert group_article_copies(list(order)) == expected


def test_divergent_facts_material_updates_and_ambiguous_excerpts_stay_separate():
    rows = [article("one", LEAD+"\n"+DETAIL),
            article("two", LEAD+"\nThe decision is now final and the statement is published."),
            article("partial", LEAD+"\nkAm6?4@565k^Am"),
            article("update", LEAD+"\n"+DETAIL, impact_update_type="MATERIAL_UPDATE")]
    assert len(group_article_copies(rows)) == 4
    rows[1]["source_published_time"] = "2026-09-13T08:00:00Z"
    rows[1]["body"] = rows[0]["body"]
    assert len(group_article_copies(rows)) == 3


def test_unannotated_page_is_not_declared_reviewed_by_copy_grouping():
    rows = [article("one", LEAD+"\n"+DETAIL), article("two", LEAD+"\n"+DETAIL)]
    rows[1]["parsed_at"] = None
    assert len(group_article_copies(rows)) == 2
    assert readable_source_text(LEAD+"\nkAm6?4@565k^Am") == (LEAD, True)


def test_repeated_page_paragraphs_do_not_create_another_story():
    rows = [article("one", LEAD+"\n"+DETAIL),
            article("two", LEAD+"\n"+LEAD+"\n"+DETAIL)]
    assert group_article_copies(rows)[0]["syndicated_source_count"] == 2
