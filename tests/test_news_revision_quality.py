from datetime import UTC, datetime, timedelta

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.collection.intake import collect_direct_full_text_html_news


def test_listing_poll_does_not_supersede_existing_full_text(tmp_path) -> None:
    first_seen = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    link = "https://home.treasury.gov/news/press-releases/sb1"
    headline = "Treasury borrowing report"
    ledger.append_news_revision(
        {
            "source": "us_treasury_press_releases",
            "source_item_id": link,
            "source_published_time": first_seen,
            "collector_first_seen_time": first_seen,
            "fetched_time": first_seen,
            "headline": headline,
            "body": "[FULL_TEXT source=test chars=800]\n" + "evidence " * 100,
            "link": link,
            "content_hash": "full-text-hash",
            "cluster_id": "cluster",
        }
    )

    page = b'<a href="/news/press-releases/sb1">Treasury borrowing report</a>'
    result = collect_direct_full_text_html_news(
        ledger,
        first_seen + timedelta(minutes=11),
        lambda url: page
        if "treasury.gov" in url
        else b'<a href="/news/2026/gdp-release">GDP release</a>',
        lambda url: ("official release evidence " * 30, url),
    )

    latest = ledger.connection.execute(
        """SELECT revision_number, body FROM news_revisions
        WHERE source='us_treasury_press_releases' AND source_item_id=?
        ORDER BY revision_number DESC LIMIT 1""",
        (link,),
    ).fetchone()
    treasury = next(
        item for item in result if item["source"] == "us_treasury_press_releases"
    )
    assert latest["revision_number"] == 1
    assert latest["body"].startswith("[FULL_TEXT")
    assert treasury["preserved_full_text"] == 1
