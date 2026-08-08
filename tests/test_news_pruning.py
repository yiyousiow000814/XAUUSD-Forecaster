from datetime import UTC, datetime, timedelta
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_pruning import build_news_prune_plan, prune_unused_news


def _news(source: str, item: str, seen: datetime, body: str, published: datetime):
    return {
        "source": source,
        "source_item_id": item,
        "source_published_time": published,
        "collector_first_seen_time": seen,
        "fetched_time": seen,
        "headline": "Gold inflation and Federal Reserve evidence",
        "body": body,
        "link": f"https://example.test/{item}",
        "content_hash": f"hash-{item}",
        "cluster_id": item,
    }


def test_prune_plan_keeps_only_timely_full_text_evidence(tmp_path) -> None:
    epoch = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    full = "[FULL_TEXT source=x chars=600]\n" + ("usable evidence " * 45)
    ledger.append_news_revision(_news("google_news_fed_rates", "keep", epoch, full, epoch))
    ledger.append_news_revision(
        _news("google_news_fed_rates", "no-body", epoch, "headline only", epoch)
    )
    ledger.append_news_revision(
        _news("google_news_fed_rates", "old", epoch, full, epoch - timedelta(days=4))
    )

    plan = build_news_prune_plan(ledger.connection, forward_epoch=epoch)

    assert plan.keep_items == 1
    assert plan.delete_items == 2
    assert plan.delete_unused_revisions == 0
    assert set(plan.keys) == {
        ("google_news_fed_rates", "no-body"),
        ("google_news_fed_rates", "old"),
    }


def test_prune_classifies_unusable_news_without_deleting_raw_rows(tmp_path) -> None:
    epoch = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=epoch)
    ledger.append_news_revision(
        _news("google_news_fed_rates", "no-body", epoch, "headline only", epoch)
    )
    ledger.close()

    receipt = prune_unused_news(
        database, backup_directory=tmp_path / "backups", dry_run=False
    )

    assert receipt["delete_items"] == 1
    assert receipt["destructive"] is False
    assert receipt["remaining_items"] == 1
    assert receipt["remaining_revisions"] == 1
    check = ForwardLedger(database, now=epoch)
    classification = check.connection.execute(
        "SELECT visibility_status,reason_code FROM news_item_classifications_v1"
    ).fetchone()
    assert classification["visibility_status"] == "CONTENT_UNAVAILABLE"
    assert classification["reason_code"] == "NO_FULL_TEXT"


def test_prune_classifies_placeholder_and_keeps_both_revisions(tmp_path) -> None:
    epoch = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=epoch)
    ledger.append_news_revision(
        _news("google_news_fed_rates", "upgrade", epoch, "headline only", epoch)
    )
    full = "[FULL_TEXT source=x chars=600]\n" + ("usable evidence " * 45)
    upgraded = _news("google_news_fed_rates", "upgrade", epoch, full, epoch)
    upgraded["content_hash"] = "hash-upgrade-full"
    ledger.append_news_revision(upgraded)

    plan = build_news_prune_plan(ledger.connection, forward_epoch=epoch)
    assert plan.keep_items == 1
    assert plan.delete_items == 0
    assert plan.delete_unused_revisions == 1
    assert plan.revision_keys == (("google_news_fed_rates", "upgrade", 1),)
    ledger.close()

    receipt = prune_unused_news(
        database, backup_directory=tmp_path / "backups", dry_run=False
    )
    assert receipt["remaining_items"] == 1
    assert receipt["remaining_revisions"] == 2
    check = ForwardLedger(database, now=epoch)
    rows = check.connection.execute(
        "SELECT revision_number, body FROM news_revisions"
    ).fetchall()
    assert [(row["revision_number"], row["body"].startswith("[FULL_TEXT")) for row in rows] == [
        (1, False), (2, True)
    ]
    classification = check.connection.execute(
        "SELECT revision_number,visibility_status FROM news_item_classifications_v1"
    ).fetchone()
    assert tuple(classification) == (1, "DUPLICATE_DOCUMENT")
