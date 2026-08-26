from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from xauusd_forecaster.news.scheduler.task_registry import AI_TASK_ROUTE_BY_TYPE
from xauusd_forecaster.news.annotation.product import (
    IMPACT_PROMPT_VERSION,
    PROMPT_VERSION,
)
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.scheduler.state import (
    CONTRACT_BACKFILL_LANE,
    LIVE_LANE,
    WORK_PROVENANCE_VERSION,
    WorkProvenance,
    _install_downstream_work_provenance,
    claim_job,
    enqueue_derived_ai_job,
    enqueue_job,
    sync_pending_jobs,
    ROUTINE_POOL,
)
from xauusd_forecaster.operational_health import scheduler_health_snapshot


NOW = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)


def _seed_annotation_origin(
    ledger: ForwardLedger,
    item: str,
    *,
    lane: str,
    created_at: datetime = NOW,
) -> str:
    digest = hashlib.sha256(item.encode()).hexdigest()
    annotation_id = f"annotation-{item}"
    ledger.append_news_revision({
        "source": "provenance", "source_item_id": item,
        "source_published_time": created_at,
        "collector_first_seen_time": created_at,
        "fetched_time": created_at, "headline": f"headline {item}",
        "body": "source evidence", "content_hash": digest,
        "cluster_id": item,
    })
    ledger.connection.execute(
        """INSERT INTO news_annotations VALUES (
           ?,'provenance',?,1,?,'EVENT','[]',0,0,0,0,0,0,1,
           'gemini-3.5-flash-lite',?,?,?,?)""",
        (
            annotation_id, item, digest, PROMPT_VERSION,
            created_at.isoformat(), created_at.isoformat(), "{}",
        ),
    )
    parent_job = enqueue_job(
        ledger.connection,
        task_type="ACTIVE_ANNOTATION", source="provenance",
        source_item_id=item, revision_number=1,
        prompt_version=PROMPT_VERSION,
        priority="BACKGROUND" if lane == CONTRACT_BACKFILL_LANE else "FAST",
        work_lane=lane, now=created_at,
    )
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='COMPLETED',completed_at=?,updated_at=? WHERE job_id=?""",
        (created_at.isoformat(), created_at.isoformat(), parent_job),
    )
    ledger.connection.commit()
    return annotation_id


def test_ai_route_registry_declares_every_workload_origin() -> None:
    assert set(AI_TASK_ROUTE_BY_TYPE) == {
        "ACTIVE_ANNOTATION", "ACTIVE_IMPACT", "TITLE_TRANSLATION",
        "DAILY_BRIEF", "NEWS_EMBEDDING",
    }
    assert AI_TASK_ROUTE_BY_TYPE["ACTIVE_IMPACT"].provenance_source \
        == "ACTIVE_ANNOTATION"
    assert AI_TASK_ROUTE_BY_TYPE["TITLE_TRANSLATION"].provenance_source \
        == "ACTIVE_ANNOTATION"
    assert AI_TASK_ROUTE_BY_TYPE["NEWS_EMBEDDING"].provenance_source \
        == "CALLER_OPERATION"
    assert all(route.provenance_source for route in AI_TASK_ROUTE_BY_TYPE.values())


def test_historical_annotation_provenance_survives_impact_and_title_discovery(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.news.annotation.product as annotation

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    records = []
    for index in range(100):
        item = f"historical-{index:03d}"
        annotation_id = _seed_annotation_origin(
            ledger, item, lane=CONTRACT_BACKFILL_LANE,
        )
        records.append({
            "source": "provenance", "source_item_id": item,
            "revision_number": 1, "annotation_id": annotation_id,
            "annotation": {"review_priority": "IMMEDIATE"},
        })

    monkeypatch.setattr(
        annotation, "pending_annotation_records", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        annotation, "pending_impact_records",
        lambda *_args, limit, selection_order, **_kwargs: (
            records[:limit] if selection_order == "oldest" else records[-limit:]
        ),
    )
    monkeypatch.setattr(
        annotation, "pending_title_translation_records",
        lambda *_args, limit, **_kwargs: records[:limit],
    )

    discovered = sync_pending_jobs(ledger.connection, now=NOW, limit=100)
    downstream = ledger.connection.execute(
        """SELECT task_type,work_lane,priority,provenance_resolved,
                  provenance_origin_task
           FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')"""
    ).fetchall()

    assert discovered["ACTIVE_IMPACT"] == 100
    assert discovered["TITLE_TRANSLATION"] == 100
    assert len(downstream) == 200
    assert {row["work_lane"] for row in downstream} == {CONTRACT_BACKFILL_LANE}
    assert {row["priority"] for row in downstream} == {"BACKGROUND"}
    assert {row["provenance_resolved"] for row in downstream} == {1}
    assert {row["provenance_origin_task"] for row in downstream} \
        == {"ACTIVE_ANNOTATION"}
    ledger.close()


def test_live_annotation_provenance_survives_downstream_discovery(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.news.annotation.product as annotation

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    annotation_id = _seed_annotation_origin(ledger, "live", lane=LIVE_LANE)
    record = {
        "source": "provenance", "source_item_id": "live",
        "revision_number": 1, "annotation_id": annotation_id,
        "annotation": {"review_priority": "FAST"},
    }
    monkeypatch.setattr(
        annotation, "pending_annotation_records", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        annotation, "pending_impact_records",
        lambda *_args, selection_order, **_kwargs: (
            [record] if selection_order == "oldest" else []
        ),
    )
    monkeypatch.setattr(
        annotation, "pending_title_translation_records",
        lambda *_args, **_kwargs: [record],
    )

    sync_pending_jobs(ledger.connection, now=NOW, limit=2)
    downstream = ledger.connection.execute(
        """SELECT task_type,work_lane FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')"""
    ).fetchall()

    assert {tuple(row) for row in downstream} == {
        ("ACTIVE_IMPACT", LIVE_LANE), ("TITLE_TRANSLATION", LIVE_LANE),
    }
    ledger.close()


def test_unresolved_annotation_origin_never_defaults_downstream_to_live(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.news.annotation.product as annotation

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    annotation_id = _seed_annotation_origin(ledger, "unknown", lane=LIVE_LANE)
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET lane_classified=0,provenance_resolved=0,provenance_version=NULL
           WHERE task_type='ACTIVE_ANNOTATION'"""
    )
    ledger.connection.execute(
        """INSERT INTO news_annotation_work_lane_migrations_v1
           (prompt_version,activated_at,state,updated_at)
           VALUES (?,?,'COMPLETE',?)""",
        (PROMPT_VERSION, NOW.isoformat(), NOW.isoformat()),
    )
    ledger.connection.commit()
    record = {
        "source": "provenance", "source_item_id": "unknown",
        "revision_number": 1, "annotation_id": annotation_id,
        "annotation": {"review_priority": "FAST"},
    }
    monkeypatch.setattr(
        annotation, "pending_annotation_records", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        annotation, "pending_impact_records",
        lambda *_args, selection_order, **_kwargs: (
            [record] if selection_order == "oldest" else []
        ),
    )
    monkeypatch.setattr(
        annotation, "pending_title_translation_records",
        lambda *_args, **_kwargs: [record],
    )

    discovered = sync_pending_jobs(ledger.connection, now=NOW, limit=2)

    assert discovered["ACTIVE_IMPACT"] == 0
    assert discovered["TITLE_TRANSLATION"] == 0
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')"""
    ).fetchone()[0] == 0
    ledger.close()


def test_downstream_provenance_migration_is_bounded_resumable_and_lease_safe(
    tmp_path,
) -> None:
    path = tmp_path / "production-shaped.sqlite3"
    ledger = ForwardLedger(path, now=NOW)
    total = 1_205
    for index in range(total):
        item = f"legacy-{index:04d}"
        annotation_id = _seed_annotation_origin(
            ledger, item, lane=CONTRACT_BACKFILL_LANE,
        )
        job_id = enqueue_job(
            ledger.connection,
            task_type="ACTIVE_IMPACT", source="provenance",
            source_item_id=item, revision_number=1,
            annotation_id=annotation_id, prompt_version=IMPACT_PROMPT_VERSION,
            priority="FAST", now=NOW,
        )
        state = "COMPLETED" if index % 10 == 0 else "QUEUED"
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1
               SET work_lane='LIVE',lane_classified=1,provenance_resolved=0,
                   provenance_version=NULL,provenance_origin_task=NULL,
                   provenance_origin_ref=NULL,attempt_count=3,state=?,
                   completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE NULL END
               WHERE job_id=?""",
            (state, state, NOW.isoformat(), job_id),
        )
    first = ledger.connection.execute(
        """SELECT job_id FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_IMPACT'
           ORDER BY created_at,job_id LIMIT 1"""
    ).fetchone()[0]
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='LEASED',lease_owner='active-worker',lease_expires_at=?
           WHERE job_id=?""",
        ((NOW + timedelta(minutes=3)).isoformat(), first),
    )
    ledger.connection.execute(
        """INSERT INTO news_annotation_work_lane_migrations_v1
           (prompt_version,activated_at,state,updated_at)
           VALUES (?,?,'COMPLETE',?)""",
        (PROMPT_VERSION, NOW.isoformat(), NOW.isoformat()),
    )
    ledger.connection.commit()
    before = {
        str(row["job_id"]): tuple(row[name] for name in (
            "attempt_count", "state", "available_at", "lease_owner",
            "lease_expires_at", "completed_at",
        ))
        for row in ledger.connection.execute(
            "SELECT * FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_IMPACT'"
        )
    }

    first_page = _install_downstream_work_provenance(
        ledger.connection, current_prompt_version=PROMPT_VERSION, now=NOW,
    )
    migrated_after_page = ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_IMPACT' AND provenance_version=?""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()[0]

    assert first_page["processed"] == 100
    assert first_page["leased_skipped"] == 0
    assert migrated_after_page == 100
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_IMPACT' AND state='COMPLETED'
             AND provenance_version=?""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT work_lane FROM news_ai_jobs_v1 WHERE job_id=?", (first,),
    ).fetchone()[0] == LIVE_LANE
    ledger.close()

    ledger = ForwardLedger(path)
    for _ in range(20):
        result = _install_downstream_work_provenance(
            ledger.connection, current_prompt_version=PROMPT_VERSION,
            now=NOW + timedelta(seconds=1),
        )
        state = ledger.connection.execute(
            """SELECT state FROM news_downstream_provenance_migrations_v1
               WHERE provenance_version=?""",
            (WORK_PROVENANCE_VERSION,),
        ).fetchone()[0]
        if state == "WAITING_INPUT" and result["leased_skipped"] == 1:
            break
    assert state == "WAITING_INPUT"
    assert result["leased_skipped"] == 1
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1 SET state='QUEUED',lease_owner=NULL,
                  lease_expires_at=NULL WHERE job_id=?""",
        (first,),
    )
    ledger.connection.commit()
    _install_downstream_work_provenance(
        ledger.connection, current_prompt_version=PROMPT_VERSION,
        now=NOW + timedelta(minutes=4),
    )

    final_rows = ledger.connection.execute(
        """SELECT * FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_IMPACT'"""
    ).fetchall()
    assert len(final_rows) == total
    assert {row["work_lane"] for row in final_rows} == {CONTRACT_BACKFILL_LANE}
    assert {row["priority"] for row in final_rows} == {"BACKGROUND"}
    assert {row["provenance_version"] for row in final_rows} \
        == {WORK_PROVENANCE_VERSION}
    for row in final_rows:
        prior = before[str(row["job_id"])]
        assert row["attempt_count"] == prior[0]
        if str(row["job_id"]) != first:
            assert tuple(row[name] for name in (
                "state", "available_at", "lease_owner", "lease_expires_at",
                "completed_at",
            )) == prior[1:]
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    ledger.close()


def test_historical_gemma_age_never_degrades_live_health(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    old = NOW - timedelta(minutes=237)
    for index in range(1_180):
        enqueue_derived_ai_job(
            ledger.connection,
            provenance=WorkProvenance(
                CONTRACT_BACKFILL_LANE, "ACTIVE_ANNOTATION", f"parent-{index}",
            ),
            task_type="ACTIVE_IMPACT", source="health",
            source_item_id=f"backfill-{index}", revision_number=1,
            annotation_id=f"annotation-{index}",
            prompt_version=IMPACT_PROMPT_VERSION,
            priority="IMMEDIATE", now=old,
        )

    snapshot = scheduler_health_snapshot(ledger.connection, now=NOW)
    codes = {(alert["code"], alert["scope"]) for alert in snapshot["alerts"]}
    impact_live = next(
        item for item in snapshot["scheduler"]["tasks"]
        if item["task_type"] == "ACTIVE_IMPACT"
    )
    impact_backfill = next(
        item for item in snapshot["scheduler"]["contract_backfill"]["tasks"]
        if item["task_type"] == "ACTIVE_IMPACT"
    )

    assert ("OPS_AI_BACKLOG_OVERDUE", "ACTIVE_IMPACT") not in codes
    assert ("OPS_AI_PIPELINE_STALLED", "ACTIVE_IMPACT") not in codes
    assert impact_live["queued"] == 0
    assert impact_backfill["states"]["queued"] == 1_180
    assert impact_backfill["oldest_age_seconds"] == 237 * 60

    enqueue_derived_ai_job(
        ledger.connection,
        provenance=WorkProvenance(LIVE_LANE, "ACTIVE_ANNOTATION", "live-parent"),
        task_type="ACTIVE_IMPACT", source="health", source_item_id="live",
        revision_number=1, annotation_id="live-annotation",
        prompt_version=IMPACT_PROMPT_VERSION, priority="FAST", now=old,
    )
    alerted = scheduler_health_snapshot(ledger.connection, now=NOW)
    alerted_codes = {
        (alert["code"], alert["scope"]) for alert in alerted["alerts"]
    }
    assert ("OPS_AI_BACKLOG_OVERDUE", "ACTIVE_IMPACT") in alerted_codes
    assert ("OPS_AI_PIPELINE_STALLED", "ACTIVE_IMPACT") in alerted_codes
    claimed = claim_job(
        ledger.connection, worker_id="live-first", pool=ROUTINE_POOL,
        task_types=("ACTIVE_IMPACT",), now=NOW,
    )
    assert claimed is not None
    assert claimed.work_lane == LIVE_LANE
    assert claimed.source_item_id == "live"
    ledger.close()
