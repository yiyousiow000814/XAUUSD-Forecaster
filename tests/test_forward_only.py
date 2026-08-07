import hashlib
import json
import math
import os
import sqlite3
import urllib.error
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xauusd_forecaster.annotation as annotation_module

from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.content import (
    extract_article_full_text,
    extract_federal_reserve_full_text,
    fetch_content,
    hydrate_pending_federal_reserve_content,
    hydrate_pending_non_fed_content,
)
from xauusd_forecaster.inference import build_shadow_predictions
from xauusd_forecaster.annotation import annotate_pending_news, translate_pending_headlines
from xauusd_forecaster.factors import aggregate_news_features
from xauusd_forecaster.gemini_quota import GeminiQuotaLedger
from xauusd_forecaster.maintenance import (
    archive_completed_quote_days,
    backup_forward_ledger,
)
from xauusd_forecaster.market import (
    JsonlMarketProvider,
    MarketObservation,
    build_forward_snapshot,
)
from xauusd_forecaster.news import (
    RssSource,
    collect_bls_macro,
    collect_direct_full_text_rss_news,
    collect_direct_full_text_html_news,
    collect_fred_macro,
    collect_gdelt_news,
    collect_google_geopolitical_news,
    collect_world_gold_council_news,
    parse_rss,
)
from xauusd_forecaster.ridge import train_ridge
from xauusd_forecaster.shadow_simulation import shadow_league
from xauusd_forecaster.u5_state import U5State
from xauusd_forecaster.training import (
    MARKET_FEATURES,
    auto_train_due,
    train_market_challenger,
)


UTC = timezone.utc


def _snapshot(
    ledger: ForwardLedger,
    decision: datetime,
    *,
    role: str = "FORWARD",
    u5: float | None = 0.01,
    health: str = "OK",
) -> str:
    snapshot_id = f"snapshot-{decision.timestamp()}-{role}"
    ledger.append_snapshot(
        {
            "snapshot_id": snapshot_id,
            "decision_time": decision,
            "collected_at": decision,
            "data_role": role,
            "source": "synthetic",
            "source_event_time": decision,
            "source_received_time": decision,
            "bid": 2400.0,
            "ask": 2400.2,
            "spread": 0.2,
            "features": {
                name: (decision.minute + index + 1) / 100_000.0
                for index, name in enumerate(MARKET_FEATURES)
            },
            "feature_version": "test-v1",
            "u5": u5,
            "u5_status": "READY" if u5 else "WARMUP",
            "data_health": health,
            "active_signal": False,
            "reason_codes": (),
        }
    )
    return snapshot_id


def _decision(ledger: ForwardLedger, decision: datetime, snapshot_id: str) -> str:
    decision_id = f"decision-{decision.timestamp()}"
    ledger.append_decision(
        {
            "decision_id": decision_id,
            "decision_time": decision,
            "snapshot_id": snapshot_id,
            "created_at": decision,
            "data_health": "OK",
            "reason_codes": (),
            "predictions": [
                {
                    "model_version": "always-wait-v1",
                    "model_identity": "CHAMPION_0",
                    "predicted_direction_u5": 0.0,
                    "predicted_news_residual_u5": 0.0,
                    "ev_long_u5": 0.0,
                    "ev_short_u5": 0.0,
                    "uncertainty_u5": 0.0,
                    "recommended_action": "WAIT",
                    "prediction_status": "READY",
                }
            ],
        }
    )
    return decision_id


def _valid_outcome(ledger: ForwardLedger, decision_id: str, decision: datetime) -> None:
    long_return = math.log(2402.0 / 2400.2)
    short_return = math.log(2400.0 / 2402.2)
    ledger.append_outcome(
        {
            "decision_id": decision_id,
            "entry_time": decision + timedelta(seconds=1),
            "exit_time": decision + timedelta(minutes=30, seconds=1),
            "horizon": timedelta(minutes=30),
            "appended_at": decision + timedelta(minutes=31),
            "label_version": "test-label-v1",
            "outcome_status": "VALID",
            "reason_codes": (),
            "long_return": long_return,
            "short_return": short_return,
            "direction_move": (long_return - short_return) / 2.0,
            "spread_quote_cost": -(long_return + short_return) / 2.0,
            "long_mfe": long_return + 0.001,
            "long_mae": long_return - 0.001,
            "short_mfe": short_return + 0.001,
            "short_mae": short_return - 0.001,
            "maximum_spread": 0.2,
            "quote_coverage": 1.0,
            "source_hash": "synthetic-path-hash",
        }
    )


def test_forward_epoch_is_real_once_and_immutable(tmp_path) -> None:
    first = datetime(2026, 8, 5, 10, 0, 3, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first)
    assert ledger.forward_epoch == first
    ledger.close()

    reopened = ForwardLedger(
        tmp_path / "forward.sqlite3",
        now=first + timedelta(days=1),
    )
    assert reopened.forward_epoch == first
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reopened.connection.execute(
            "UPDATE runtime_metadata SET value='backdated' WHERE key='FORWARD_EPOCH'"
        )


def test_market_snapshot_rejects_post_decision_receipts_and_missing_data_waits(
    tmp_path,
) -> None:
    epoch = datetime(2026, 8, 5, 9, 59, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    bad = _snapshot_record = {
        "snapshot_id": "bad",
        "decision_time": decision,
        "collected_at": decision + timedelta(seconds=2),
        "data_role": "FORWARD",
        "source": "synthetic",
        "source_received_time": decision + timedelta(seconds=1),
        "features": {},
        "feature_version": "v1",
        "u5_status": "WARMUP",
        "data_health": "STALE",
    }
    with pytest.raises(ValueError, match="received after decision"):
        ledger.append_snapshot(bad)

    snapshot = build_forward_snapshot([], decision, decision, "unconfigured")
    ledger.append_snapshot(snapshot)
    engine = ForwardEngine(ledger, _EmptyProvider())
    _, decision_id = engine.append_clock_event(
        decision + timedelta(minutes=5), decision + timedelta(minutes=5)
    )
    row = ledger.connection.execute(
        "SELECT effective_action, reason_codes_json FROM decision_events WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    assert row["effective_action"] == "WAIT"
    assert "MARKET_DATA_MISSING" in row["reason_codes_json"]


def test_news_first_seen_revision_and_annotation_cutoffs(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    base = {
        "source": "fed",
        "source_item_id": "item-1",
        "source_published_time": epoch - timedelta(minutes=10),
        "collector_first_seen_time": epoch + timedelta(minutes=1),
        "fetched_time": epoch + timedelta(minutes=1),
        "headline": "Policy statement",
        "body": "First text",
        "link": "https://example.test/1",
        "content_hash": hashlib.sha256(b"first").hexdigest(),
        "cluster_id": "cluster",
    }
    revision, created = ledger.append_news_revision(base)
    assert (revision, created) == (1, True)
    assert ledger.append_news_revision(base) == (1, False)
    changed = {
        **base,
        "collector_first_seen_time": epoch + timedelta(minutes=6),
        "fetched_time": epoch + timedelta(minutes=6),
        "body": "Revised text",
        "content_hash": hashlib.sha256(b"second").hexdigest(),
    }
    assert ledger.append_news_revision(changed) == (2, True)
    reverted = {
        **base,
        "collector_first_seen_time": epoch + timedelta(minutes=7),
        "fetched_time": epoch + timedelta(minutes=7),
    }
    assert ledger.append_news_revision(reverted) == (1, False)
    assert ledger.visible_news(epoch) == []
    assert ledger.visible_news(epoch + timedelta(minutes=5))[0]["revision_number"] == 1
    assert ledger.visible_news(epoch + timedelta(minutes=7))[0]["revision_number"] == 2

    parsed = epoch + timedelta(minutes=8)
    ledger.append_annotation(
        {
            "annotation_id": "ann-1",
            "source": "fed",
            "source_item_id": "item-1",
            "revision_number": 2,
            "raw_content_hash": changed["content_hash"],
            "annotation": {
                "event_type": "monetary_policy",
                "entities": ["Federal Reserve"],
                "hawkishness": 0.5,
                "inflation_impulse": 0.1,
                "growth_impulse": -0.1,
                "geopolitical_risk": 0.0,
                "usd_impulse": 0.3,
                "novelty": 0.4,
                "confidence": 0.8,
            },
            "llm_model_version": "fixed-model-v1",
            "prompt_version": "news-json-v1",
            "parse_started_at": parsed - timedelta(seconds=2),
            "parsed_at": parsed,
        }
    )
    assert ledger.visible_annotations(parsed - timedelta(microseconds=1)) == []
    assert len(ledger.visible_annotations(parsed)) == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute("DELETE FROM news_revisions")


def test_outcome_must_be_scored_before_forward_training_and_warmup_is_isolated(
    tmp_path,
) -> None:
    epoch = datetime(2026, 8, 5, 9, 59, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision_id = _decision(ledger, decision, _snapshot(ledger, decision))
    with pytest.raises(ValueError, match="appended outcome"):
        ledger.mark_training_eligible(decision_id, decision + timedelta(minutes=31))
    _valid_outcome(ledger, decision_id, decision)
    with pytest.raises(ValueError, match="scored"):
        ledger.mark_training_eligible(decision_id, decision + timedelta(minutes=31))
    ledger.append_score(
        {
            "decision_id": decision_id,
            "model_version": "always-wait-v1",
            "scored_at": decision + timedelta(minutes=31),
            "score": {"squared_error": 0.1},
        }
    )
    ledger.mark_training_eligible(decision_id, decision + timedelta(minutes=31))
    first_hash = ledger.training_dataset_hash(decision + timedelta(hours=1))
    second_hash = ledger.training_dataset_hash(decision + timedelta(hours=1))
    assert first_hash == second_hash
    assert first_hash[1] == 1

    warmup_time = decision + timedelta(minutes=5)
    warmup_id = _snapshot(ledger, warmup_time, role="WARMUP_ONLY")
    with pytest.raises(ValueError, match="FORWARD snapshot"):
        _decision(ledger, warmup_time, warmup_id)


def test_ridge_artifact_and_dataset_hash_are_deterministic() -> None:
    rows = np.array([[1.0, 2.0], [2.0, 1.0], [3.0, 4.0], [4.0, 3.0]])
    target = np.array([0.1, -0.1, 0.3, -0.2])
    first = train_ridge(rows, target, ("a", "b"), 100.0, "dataset-hash")
    second = train_ridge(rows, target, ("a", "b"), 100.0, "dataset-hash")
    assert first.as_dict() == second.as_dict()
    assert first.artifact_hash == second.artifact_hash
    np.testing.assert_array_equal(first.predict(rows), second.predict(rows))


def test_official_rss_parser_stamps_real_fetch_time() -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    xml = b"""<rss><channel><item><guid>x1</guid><title>Headline</title>
    <description>Body</description><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
    <link>https://example.test/x1</link></item></channel></rss>"""
    row = parse_rss(xml, RssSource("official", "https://example.test"), fetched)[0]
    assert row["collector_first_seen_time"] == fetched
    assert row["source_published_time"] < row["collector_first_seen_time"]


def test_fed_release_hydration_follows_accessible_full_text_and_appends_revision(
    tmp_path,
) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    release_url = "https://www.federalreserve.gov/newsevents/release.htm"
    full_url = "https://www.federalreserve.gov/monetarypolicy/minutes.htm"
    pages = {
        release_url: b'<div id="content"><a href="/monetarypolicy/minutes.htm">HTML</a></div>',
        full_url: (
            '<div id="content"><h1>FOMC Minutes</h1><p>'
            + "Policy discussion and participant views. " * 20
            + "</p></div>"
        ).encode(),
    }
    text, source = extract_federal_reserve_full_text(
        release_url, lambda url: pages[url]
    )
    assert source == full_url
    assert "participant views" in text

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)
    digest = hashlib.sha256(b"headline-only").hexdigest()
    ledger.append_news_revision(
        {
            "source": "federal_reserve_monetary",
            "source_item_id": release_url,
            "collector_first_seen_time": fetched,
            "fetched_time": fetched,
            "headline": "FOMC Minutes",
            "body": "FOMC Minutes",
            "link": release_url,
            "content_hash": digest,
            "cluster_id": "minutes",
        }
    )
    result = hydrate_pending_federal_reserve_content(
        ledger,
        fetched + timedelta(seconds=1),
        extractor=lambda _: (text, source),
    )
    assert result["inserted_revisions"] == 1
    latest = ledger.connection.execute(
        "SELECT * FROM news_revisions ORDER BY revision_number DESC LIMIT 1"
    ).fetchone()
    assert latest["revision_number"] == 2
    assert latest["body"].startswith("[FULL_TEXT")
    assert "participant views" in latest["body"]


def test_fed_full_text_is_not_truncated_before_annotation() -> None:
    url = "https://www.federalreserve.gov/newsevents/long-release.htm"
    marker = "COMPLETE_SOURCE_END_MARKER"
    content = "Policy evidence. " * 10_000 + marker
    page = f'<div id="content"><p>{content}</p></div>'.encode()
    text, source = extract_federal_reserve_full_text(url, lambda _: page)
    assert source == url
    assert text.endswith(marker)
    assert len(text) > 120_000


def test_non_fed_article_hydration_appends_auditable_revision(tmp_path) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    article_url = "https://www.gold.org/goldhub/gold-focus/example"
    page = (
        "<html><nav>Navigation</nav><article><h1>Central-bank gold</h1><p>"
        + "Reported purchases and reserve allocation evidence. " * 30
        + "</p></article><footer>Disclaimer</footer></html>"
    ).encode()
    text, source = extract_article_full_text(article_url, lambda _: page)
    assert source == article_url
    assert "Reported purchases" in text
    assert "Navigation" not in text

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)
    ledger.append_news_revision(
        {
            "source": "world_gold_council_central_banks",
            "source_item_id": article_url,
            "collector_first_seen_time": fetched,
            "fetched_time": fetched,
            "headline": "Central-bank gold",
            "body": "World Gold Council central-bank research monitor",
            "link": article_url,
            "content_hash": hashlib.sha256(b"headline-only").hexdigest(),
            "cluster_id": "central-bank-gold",
        }
    )
    result = hydrate_pending_non_fed_content(
        ledger,
        fetched + timedelta(seconds=1),
        extractor=lambda _: (text, source),
    )
    assert result["inserted_revisions"] == 1
    latest = ledger.connection.execute(
        "SELECT * FROM news_revisions ORDER BY revision_number DESC LIMIT 1"
    ).fetchone()
    assert latest["revision_number"] == 2
    assert latest["body"].startswith("[FULL_TEXT")


def test_article_fetch_uses_browser_document_headers(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"publisher article"

    def open_request(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return Response()

    import xauusd_forecaster.content as content_module

    monkeypatch.setattr(content_module.urllib.request, "urlopen", open_request)
    assert fetch_content("https://publisher.example/article") == b"publisher article"
    assert captured["headers"]["User-agent"].startswith("Mozilla/5.0")
    assert "text/html" in captured["headers"]["Accept"]
    assert captured["timeout"] == 12.0


@pytest.mark.parametrize(
    "denial",
    [
        urllib.error.HTTPError(
            "https://publisher.example/protected", 403, "Forbidden", {}, None
        ),
        urllib.error.HTTPError(
            "https://publisher.example/protected", 302, "Redirect loop", {}, None
        ),
        urllib.error.URLError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        ),
    ],
)
def test_non_fed_permanent_denial_is_quarantined_without_component_failure(
    tmp_path, denial,
) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)
    article_url = "https://publisher.example/protected"
    ledger.append_news_revision(
        {
            "source": "google_news_gold_context",
            "source_item_id": "protected-article",
            "collector_first_seen_time": fetched,
            "fetched_time": fetched,
            "headline": "Protected article",
            "body": "Headline-only discovery record",
            "link": article_url,
            "content_hash": hashlib.sha256(b"protected").hexdigest(),
            "cluster_id": "protected-cluster",
        }
    )
    calls = 0

    def denied(url):
        nonlocal calls
        calls += 1
        raise denial

    first = hydrate_pending_non_fed_content(
        ledger, fetched + timedelta(minutes=5), extractor=denied
    )
    second = hydrate_pending_non_fed_content(
        ledger, fetched + timedelta(minutes=10), extractor=denied
    )

    assert first["status"] == "OK"
    assert first["unavailable"] == 1
    assert second["status"] == "OK"
    assert second["attempted"] == 0
    assert calls == 1
    failure = ledger.connection.execute(
        "SELECT * FROM news_content_failures"
    ).fetchone()
    assert failure["is_terminal"] == 1
    assert failure["next_retry_at"] is None


def test_bls_api_values_are_versioned_and_rate_limited(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)
    envelope = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": "LNS14000000",
                    "data": [
                        {
                            "year": "2026",
                            "period": "M07",
                            "periodName": "July",
                            "value": "4.2",
                            "footnotes": [{}],
                        }
                    ],
                }
            ]
        },
    }
    calls = 0

    def fetcher(_):
        nonlocal calls
        calls += 1
        return json.dumps(envelope).encode()

    first = collect_bls_macro(ledger, fetched, fetcher)
    second = collect_bls_macro(ledger, fetched + timedelta(minutes=5), fetcher)
    assert first["status"] == "OK"
    assert first["inserted_revisions"] == 1
    assert second["status"] == "SKIPPED_INTERVAL"
    assert calls == 1
    assert ledger.count("macro_observations") == 1
    assert ledger.count("source_polls") == 1


def test_broad_free_sources_are_first_seen_versioned_and_rate_limited(tmp_path) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def fred_fetcher(url: str) -> bytes:
        series = url.split("id=")[1].split("&")[0]
        return f"observation_date,{series}\n2026-08-03,10.0\n2026-08-04,11.0\n".encode()

    first = collect_fred_macro(ledger, fetched, fred_fetcher)
    second = collect_fred_macro(ledger, fetched + timedelta(minutes=5), fred_fetcher)
    assert first["status"] == "OK"
    assert first["inserted_revisions"] == 12
    assert second["status"] == "SKIPPED_INTERVAL"

    gdelt = json.dumps({"articles": [{
        "url": "https://example.test/geopolitics",
        "title": "Gold reacts to sanctions",
        "seendate": "20260805T100000Z",
        "domain": "example.test",
        "sourcecountry": "US",
        "language": "English",
    }]}).encode()
    assert collect_gdelt_news(ledger, fetched, lambda _: gdelt)["status"] == "OK"

    geo_rss = b"""<rss><channel><item><guid>geo-2</guid><title>Gold conflict update</title>
    <description>Geopolitical monitor</description><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
    <link>https://example.test/geo-2</link></item></channel></rss>"""
    assert collect_google_geopolitical_news(
        ledger, fetched, lambda _: geo_rss
    )["status"] == "OK"

    wgc = b'''<a href="/goldhub/gold-focus/2026/08/central-bank-gold">Central bank gold buying</a>'''
    assert collect_world_gold_council_news(
        ledger, fetched, lambda _: wgc
    )["status"] == "OK"
    assert ledger.count("macro_observations") == 12
    assert ledger.count("news_revisions") == 3


def test_gdelt_429_uses_exponential_backoff_without_blocking_fallback(tmp_path) -> None:
    fetched = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def rate_limited(_url: str) -> bytes:
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    failed = collect_gdelt_news(ledger, fetched, rate_limited)
    assert failed["status"] == "ERROR"
    assert failed["fallback_source"] == "google_news_gold_context"
    assert failed["retry_at"] == (fetched + timedelta(hours=2)).isoformat()

    skipped = collect_gdelt_news(
        ledger, fetched + timedelta(minutes=61),
        lambda _url: b'{"articles": []}',
    )
    assert skipped["status"] == "SKIPPED_BACKOFF"
    assert skipped["rate_limit_streak"] == 1
    assert skipped["retry_at"] == (fetched + timedelta(hours=2)).isoformat()

    recovered = collect_gdelt_news(
        ledger, fetched + timedelta(hours=2),
        lambda _url: b'{"articles": []}',
    )
    assert recovered["status"] == "OK"


def test_direct_official_rss_sources_are_bounded_and_rate_limited(tmp_path) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def fetcher(source: RssSource) -> bytes:
        topic = "oil production" if source.name.startswith("eia_") else "monetary policy"
        link = (
            "/pressroom/releases/example.php"
            if source.name == "eia_press_releases"
            else f"https://example.test/{source.name}/1"
        )
        return f"""<rss><channel><item><guid>{source.name}-1</guid>
        <title>{source.name} {topic} update</title>
        <description>Official source summary</description>
        <pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <link>{link}</link></item></channel></rss>""".encode()

    first = collect_direct_full_text_rss_news(ledger, fetched, fetcher)
    second = collect_direct_full_text_rss_news(
        ledger, fetched + timedelta(minutes=5), fetcher
    )
    assert [item["status"] for item in first] == ["OK", "OK", "OK"]
    assert [item["status"] for item in second] == [
        "SKIPPED_INTERVAL", "SKIPPED_INTERVAL", "SKIPPED_INTERVAL"
    ]
    assert ledger.count("news_revisions") == 3
    stored = ledger.connection.execute(
        "SELECT link FROM news_revisions WHERE source='eia_press_releases'"
    ).fetchone()
    assert stored["link"] == "https://www.eia.gov/pressroom/releases/example.php"


def test_direct_official_html_sources_are_filtered_and_bounded(tmp_path) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def fetcher(url: str) -> bytes:
        if "treasury.gov" in url:
            return b'''<div><time datetime="2026-08-05T09:00:00Z">5 August</time>
            <a href="/news/press-releases/sb1">Treasury sanctions Iran oil network</a></div>
            <a href="/news/press-releases/sb2">Unrelated office update</a>'''
        return b'''<tr><td><a href="/news/2026/gdp-release">GDP (Advance Estimate)</a></td>
        <td><time datetime="2026-08-05T05:30:00-04:00">5 August</time></td></tr>
        <a href="/news/2026/direct-investment">Direct Investment</a>'''

    results = collect_direct_full_text_html_news(ledger, fetched, fetcher)
    assert [item["status"] for item in results] == ["OK", "OK"]
    rows = ledger.connection.execute(
        "SELECT source, headline, link, source_published_time FROM news_revisions ORDER BY source"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["source"] == "bea_economic_releases"
    assert rows[0]["source_published_time"] == "2026-08-05T09:30:00.000000+00:00"
    assert rows[1]["source"] == "us_treasury_press_releases"
    assert rows[1]["source_published_time"] == "2026-08-05T09:00:00.000000+00:00"
    assert rows[1]["link"].startswith("https://home.treasury.gov/")


def test_google_news_revision_uses_resolved_publisher_url(tmp_path) -> None:
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)
    rss = b'''<rss><channel><item><guid>google-1</guid><title>Gold and rates</title>
    <description>Publisher summary</description>
    <pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
    <link>https://news.google.com/rss/articles/encoded</link></item></channel></rss>'''
    result = collect_google_geopolitical_news(
        ledger,
        fetched,
        lambda _: rss,
        lambda _: "https://publisher.example/gold-rates",
    )
    assert result["status"] == "OK"
    row = ledger.connection.execute(
        "SELECT link FROM news_revisions WHERE source='google_news_gold_context'"
    ).fetchone()
    assert row["link"] == "https://publisher.example/gold-rates"


def test_generic_article_extractor_reads_pdf(monkeypatch) -> None:
    class Page:
        def extract_text(self) -> str:
            return "monetary policy " * 40

    class Reader:
        pages = [Page()]

    import xauusd_forecaster.content as content_module

    monkeypatch.setattr(content_module, "PdfReader", lambda _: Reader())
    text, source = extract_article_full_text(
        "https://example.test/statement.pdf", lambda _: b"%PDF-fixture"
    )
    assert len(text) >= 500
    assert source.endswith("statement.pdf")


def test_generic_article_extractor_prefers_treasury_news_body_over_navigation() -> None:
    navigation = "About Treasury General Information " * 30
    article = "Gold sanctions oil and foreign-exchange policy evidence. " * 30
    page = f"""<html><body>
    <article class="node--type-mega_menu_panel">{navigation}</article>
    <div class="field--name-field-news-body">{article}</div>
    </body></html>""".encode()
    text, _ = extract_article_full_text(
        "https://home.treasury.gov/news/press-releases/example",
        lambda _: page,
    )
    assert "Gold sanctions oil" in text
    assert "About Treasury" not in text


def test_forward_engine_appends_strict_executable_30_minute_outcome(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 9, 59, tzinfo=UTC)
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    observations = [
        MarketObservation(decision, decision, 2400.0, 2400.2)
    ]
    for minute in range(31):
        timestamp = decision + timedelta(minutes=minute, seconds=1)
        bid = 2400.0 + minute * 0.1
        observations.append(MarketObservation(timestamp, timestamp, bid, bid + 0.2))
    provider = _FixedProvider(observations)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    engine = ForwardEngine(ledger, provider)
    _, decision_id = engine.append_clock_event(decision, decision)
    completed = engine.settle_due_outcomes(decision + timedelta(minutes=31, seconds=20))
    assert completed == [decision_id]
    outcome = ledger.connection.execute(
        "SELECT * FROM outcomes WHERE decision_id=?", (decision_id,)
    ).fetchone()
    assert outcome["outcome_status"] == "VALID"
    assert datetime.fromisoformat(outcome["entry_time"]) == decision + timedelta(seconds=1)
    assert datetime.fromisoformat(outcome["exit_time"]) == decision + timedelta(
        minutes=30, seconds=1
    )
    assert outcome["long_return"] == pytest.approx(
        outcome["direction_move"] - outcome["spread_quote_cost"]
    )
    assert outcome["short_return"] == pytest.approx(
        -outcome["direction_move"] - outcome["spread_quote_cost"]
    )
    assert ledger.count("prediction_scores") == 4
    assert ledger.count("training_eligibility") == 0


def test_u5_state_matures_deterministically_and_round_trips(tmp_path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = U5State()
    for index in range(10_030):
        mid = 2000.0 + index * 0.01
        state.update(start + timedelta(minutes=index), mid - 0.05, mid + 0.05)
    assert state.status == "READY"
    assert state.last_u5 is not None
    path = tmp_path / "u5.json"
    state.save(path)
    restored = U5State.load(path)
    assert restored.as_dict() == state.as_dict()


def test_challenger_training_rejects_small_or_immature_forward_pool(tmp_path) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3",
        now=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="200 matured Forward rows"):
        train_market_challenger(
            ledger,
            datetime(2026, 8, 6, tzinfo=UTC),
            tmp_path / "models",
        )


def test_auto_training_builds_all_shadow_challengers_once_due(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 9, 59, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    for index in range(2):
        decision = datetime(2026, 8, 5, 10, index * 5, tzinfo=UTC)
        decision_id = _decision(ledger, decision, _snapshot(ledger, decision))
        _valid_outcome(ledger, decision_id, decision)
        ledger.append_score(
            {
                "decision_id": decision_id,
                "model_version": "always-wait-v1",
                "scored_at": decision + timedelta(minutes=31),
                "score": {"squared_error": 0.1},
            }
        )
        ledger.mark_training_eligible(
            decision_id, decision + timedelta(minutes=31)
        )
    cutoff = datetime(2026, 8, 5, 11, 0, tzinfo=UTC)
    first = auto_train_due(
        ledger, cutoff, tmp_path / "models", minimum_rows=2, retrain_interval=10
    )
    assert [row["model_identity"] for row in first] == [
        "CHALLENGER_A",
        "CHALLENGER_B",
        "CHALLENGER_FULL",
    ]
    assert ledger.count("model_updates") == 3
    second = auto_train_due(
        ledger, cutoff, tmp_path / "models", minimum_rows=2, retrain_interval=10
    )
    assert second[0]["status"] == "NOT_DUE"
    assert ledger.count("model_updates") == 3

    snapshot = {
        "data_health": "OK",
        "u5_status": "READY",
        "u5": 0.01,
        "bid": 2400.0,
        "ask": 2400.2,
        "features": {
            name: (15 + index + 1) / 100_000.0
            for index, name in enumerate(MARKET_FEATURES)
        },
    }
    predictions = build_shadow_predictions(ledger, snapshot, cutoff)
    assert len(predictions) == 4
    assert predictions[-1]["model_identity"] == "CHALLENGER_FULL"
    assert predictions[-1]["prediction_status"] == "READY"
    league = shadow_league(ledger.connection)
    assert [row["model_identity"] for row in league["models"]] == [
        "CHALLENGER_A",
        "CHALLENGER_FULL",
    ]

    decision = datetime(2026, 8, 5, 11, 5, tzinfo=UTC)
    decision_id = _decision(ledger, decision, _snapshot(ledger, decision))
    _valid_outcome(ledger, decision_id, decision)
    ledger.append_score(
        {
            "decision_id": decision_id,
            "model_version": "always-wait-v1",
            "scored_at": decision + timedelta(minutes=31),
            "score": {"squared_error": 0.1},
        }
    )
    ledger.mark_training_eligible(decision_id, decision + timedelta(minutes=31))
    second_cutoff = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    retrained = auto_train_due(
        ledger,
        second_cutoff,
        tmp_path / "models",
        minimum_rows=2,
        retrain_interval=1,
    )
    assert [row["model_identity"] for row in retrained] == [
        "CHALLENGER_A",
        "CHALLENGER_B",
        "CHALLENGER_FULL",
    ]
    parallel = build_shadow_predictions(ledger, snapshot, second_cutoff)
    assert len(parallel) == 7
    assert sum(row["model_identity"] == "CHALLENGER_A" for row in parallel) == 2
    assert sum(row["model_identity"] == "CHALLENGER_FULL" for row in parallel) == 2


def test_shadow_simulation_freezes_admission_and_settles_executable_return(
    tmp_path,
) -> None:
    epoch = datetime(2026, 8, 5, 9, 59, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)

    def append_action(decision: datetime, action: str) -> str:
        decision_id = f"shadow-{decision.timestamp()}"
        ledger.append_decision(
            {
                "decision_id": decision_id,
                "decision_time": decision,
                "snapshot_id": _snapshot(ledger, decision),
                "created_at": decision,
                "data_health": "OK",
                "reason_codes": (),
                "predictions": [
                    {
                        "model_version": "market-v1",
                        "model_identity": "CHALLENGER_A",
                        "predicted_direction_u5": 0.2,
                        "predicted_news_residual_u5": None,
                        "ev_long_u5": 0.1,
                        "ev_short_u5": -0.3,
                        "uncertainty_u5": 0.01,
                        "recommended_action": action,
                        "prediction_status": "READY",
                    }
                ],
            }
        )
        return decision_id

    first = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    first_id = append_action(first, "LONG")
    second = first + timedelta(minutes=5)
    append_action(second, "LONG")
    intents = ledger.connection.execute(
        "SELECT admission_status, simulated_action FROM shadow_trade_intents ORDER BY decision_time"
    ).fetchall()
    assert [row["admission_status"] for row in intents] == [
        "ADMITTED",
        "OVERLAP_BLOCK",
    ]
    assert [row["simulated_action"] for row in intents] == ["LONG", "WAIT"]

    _valid_outcome(ledger, first_id, first)
    result = ledger.connection.execute(
        "SELECT * FROM shadow_trade_results WHERE decision_id=?",
        (first_id,),
    ).fetchone()
    assert result["result_status"] == "VALID"
    assert result["pnl_log_return"] > 0
    assert result["pnl_u5"] == pytest.approx(result["pnl_log_return"] / 0.01)
    assert result["cost_model"] == "EXECUTABLE_BID_ASK;COMMISSION_0;SLIPPAGE_0"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute(
            "UPDATE shadow_trade_intents SET simulated_action='SHORT'"
        )


def test_jsonl_provider_reads_directory_incrementally_and_ignores_partial_line(
    tmp_path,
) -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    path = tmp_path / "xauusd-quotes-20260805.jsonl"
    first = {
        "symbol": "XAUUSD",
        "event_time": (decision - timedelta(seconds=2)).isoformat(),
        "received_time": (decision - timedelta(seconds=1)).isoformat(),
        "bid": 2400.0,
        "ask": 2400.2,
    }
    path.write_text(json.dumps(first) + "\n{", encoding="utf-8")
    provider = JsonlMarketProvider(tmp_path)
    assert len(provider.observations(decision)) == 1
    second = {**first, "bid": 2400.1, "ask": 2400.3}
    path.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    rows = provider.observations(decision)
    assert len(rows) == 2
    assert rows[-1].bid == 2400.1


def test_gemini_annotation_is_fail_closed_without_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3",
        now=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
    )
    assert annotate_pending_news(ledger, provider="gemini") == [
        {"status": "DISABLED", "reason": "GEMINI_API_KEY_MISSING"}
    ]


def test_gemini_receives_complete_body_and_returns_chinese_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}
    vector = {
        "headline_zh": "政策更新",
        "summary_zh": "这是一份完整正文摘要，包含事件、关键数字及其与黄金的关系。",
        "event_type": "monetary_policy",
        "entities": ["Federal Reserve"],
        "hawkishness": 0.0,
        "inflation_impulse": 0.0,
        "growth_impulse": 0.0,
        "geopolitical_risk": 0.0,
        "usd_impulse": 0.0,
        "novelty": 0.5,
        "confidence": 0.8,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(
                {
                    "modelVersion": "gemini-3.5-flash-lite",
                    "candidates": [
                        {"content": {"parts": [{"text": json.dumps(vector)}]}}
                    ],
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(annotation_module.urllib.request, "urlopen", fake_urlopen)
    source = "A" * 70_000 + "COMPLETE_END_MARKER"
    result, model = annotation_module._call_gemini(
        "test-key", "gemini-3.5-flash-lite", "Policy update", source
    )
    prompt = captured["payload"]["contents"][0]["parts"][0]["text"]
    assert "COMPLETE_END_MARKER" in prompt
    assert result["headline_zh"] == "政策更新"
    assert result["summary_zh"].startswith("这是一份")
    assert model == "gemini-3.5-flash-lite"


def test_gemini_rejects_non_chinese_translation_fields() -> None:
    vector = {
        "headline_zh": "Gold market update",
        "summary_zh": "This summary was not translated.",
        "event_type": "other", "entities": [], "hawkishness": 0.0,
        "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 0.0, "confidence": 1.0,
    }

    with pytest.raises(ValueError, match="headline_zh is not Simplified Chinese"):
        annotation_module._validate_chinese_result(vector)


def test_gemini_repairs_mixed_language_summary_with_counted_request(
    tmp_path, monkeypatch
) -> None:
    mixed = {
        "headline_zh": "黄金上涨",
        "summary_zh": "黄金上涨。Bu metin Türkçe olarak devam ediyor ve çevrilmedi.",
        "primary_story_title_zh": "",
    }
    repaired = {
        "headline_zh": "黄金上涨",
        "summary_zh": "黄金价格上涨，美元走弱，市场正在关注后续经济数据。",
        "primary_story_title_zh": "",
    }
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (dict(mixed), "gemini-3.5-flash-lite"),
    )
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini_chinese_repair",
        lambda *_: dict(repaired),
    )
    quota = GeminiQuotaLedger(tmp_path / "quota.json")
    pool = annotation_module._GeminiRequestPool(("key-a", "key-b"), quota)
    result, _ = pool.call(0, "model", "headline", "body")
    assert result["summary_zh"] == repaired["summary_zh"]
    assert quota.snapshot(("key-a", "key-b"))["total_sent"] == 2


def test_gemini_repairs_mixed_script_story_identity_with_counted_request(
    tmp_path, monkeypatch
) -> None:
    mixed = {
        "headline_zh": "霍尔木兹海峡重新开放",
        "summary_zh": "报道讨论霍尔木兹海峡重新开放。",
        "primary_story_title_zh": "霍尔omuz海峡重新开放事件",
    }
    repaired = {
        **mixed,
        "primary_story_title_zh": "霍尔木兹海峡重新开放事件",
    }
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (dict(mixed), "gemini-3.5-flash-lite"),
    )
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini_chinese_repair",
        lambda *_: dict(repaired),
    )
    quota = GeminiQuotaLedger(tmp_path / "quota.json")
    pool = annotation_module._GeminiRequestPool(("key-a", "key-b"), quota)
    result, _ = pool.call(0, "model", "headline", "body")
    assert result["primary_story_title_zh"] == "霍尔木兹海峡重新开放事件"
    assert quota.snapshot(("key-a", "key-b"))["total_sent"] == 2


def test_gemini_restores_source_number_lexemes_and_rejects_invention() -> None:
    result = {
        "headline_zh": "黄金上涨1.3%",
        "summary_zh": "黄金上涨1.3%，价格达到4.127,04美元。",
    }
    annotation_module._restore_source_number_lexemes(
        result,
        "Altın yüzde 1,3 arttı",
        "Fiyat 4.127,04 dolara ulaştı.",
    )
    assert "1,3" in result["headline_zh"]
    assert "1,3" in result["summary_zh"]
    invented = {"headline_zh": "黄金上涨2.0%", "summary_zh": "黄金上涨。"}
    with pytest.raises(ValueError, match="number absent from source"):
        annotation_module._restore_source_number_lexemes(
            invented, "Altın yüzde 1,3 arttı", "",
        )


def test_gemini_accepts_source_numbers_with_aggregator_spacing() -> None:
    result = {
        "headline_zh": "报告称黄金可能先回调6-8%，再涨至5,500美元以上",
        "summary_zh": "报告预计未来12-15个月可能上涨。",
    }
    annotation_module._restore_source_number_lexemes(
        result,
        "Gold may correct 6 - 8 % before rallying to USD 5 , 500+",
        "The report covers the next 12 - 15 months.",
    )
    assert "6-8" in result["headline_zh"]
    assert "5,500" in result["headline_zh"]
    assert "12-15" in result["summary_zh"]


def test_gemini_named_month_translation_does_not_invent_numeric_month() -> None:
    result = {
        "headline_zh": "黄金价格于8月5日上涨",
        "summary_zh": "市场正在关注8月的黄金走势。",
    }
    source = "Altın fiyatı 5 Ağustos tarihinde yükseldi"
    annotation_module._normalize_translated_named_months(result, source)
    annotation_module._restore_source_number_lexemes(result, source, "")
    assert result["headline_zh"] == "黄金价格于八月5日上涨"
    assert result["summary_zh"] == "市场正在关注八月的黄金走势。"


def test_gemini_locally_recovers_unverifiable_display_numbers() -> None:
    result = {
        "headline_zh": "黄金预计上涨2.0%",
        "summary_zh": "来源称黄金上涨2.0%，但原文没有给出该数值。",
        "confidence": 0.9,
    }
    annotation_module._recover_display_fields(
        result, "Altın yüzde 1,3 arttı", "Fiyat hareketi devam etti."
    )
    assert "2.0" not in result["headline_zh"]
    assert "相关数值" in result["headline_zh"]
    assert "相关数值" in result["summary_zh"]
    assert result["confidence"] == 0.5


def test_invalid_language_fallback_is_neutral_and_auditable() -> None:
    result = {
        "headline_zh": "Gold market update",
        "summary_zh": "This response was not translated into Chinese.",
        "hawkishness": 0.8, "inflation_impulse": 0.7,
        "growth_impulse": -0.4, "geopolitical_risk": 0.9,
        "usd_impulse": 0.6, "novelty": 0.8, "confidence": 0.9,
    }
    annotation_module._neutralize_unvalidated_language(result)
    annotation_module._validate_chinese_result(result)
    assert result["confidence"] == 0.0
    assert result["geopolitical_risk"] == 0.0
    assert "用于审计" in result["summary_zh"]


def test_annotation_appends_neutral_record_when_translation_repair_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source text without numeric claims. " * 20
    ledger.append_news_revision(
        {
            "source": "language-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Gold market update", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "language-cluster",
        }
    )
    vector = {
        "headline_zh": "Gold market update 99",
        "summary_zh": "This response remained in English and invented 99.",
        "event_type": "other", "entities": [], "hawkishness": 0.7,
        "inflation_impulse": 0.6, "growth_impulse": -0.4,
        "geopolitical_risk": 0.8, "usd_impulse": 0.5,
        "novelty": 0.9, "confidence": 0.9,
    }
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (dict(vector), annotation_module.DEFAULT_GEMINI_MODEL),
    )
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini_chinese_repair",
        lambda *_: (_ for _ in ()).throw(RuntimeError("repair unavailable")),
    )
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    assert statuses[0]["status"] == "OK"
    saved = ledger.connection.execute(
        "SELECT * FROM news_annotations WHERE source='language-test'"
    ).fetchone()
    assert saved["confidence"] == 0.0
    assert saved["geopolitical_risk"] == 0.0
    assert ledger.count("news_llm_failures") == 0


def test_llm_failure_is_persisted_and_blocks_immediate_retry(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source text with enough content for annotation. " * 10
    ledger.append_news_revision(
        {
            "source": "failure-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Gold report", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "failure-cluster",
        }
    )
    calls = 0

    def fail_once(*_args):
        nonlocal calls
        calls += 1
        raise ValueError("Gemini summary_zh contains a number absent from source")

    monkeypatch.setattr(annotation_module, "_call_gemini", fail_once)
    first = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    second = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    assert first[0]["retry_state"] == "BACKING_OFF"
    assert second == []
    assert calls == 1
    assert ledger.count("news_llm_failures") == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute("DELETE FROM news_llm_failures")


def test_repeated_same_validation_failure_enters_dead_letter(tmp_path) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source text with enough content for annotation. " * 10
    digest = hashlib.sha256(body.encode()).hexdigest()
    row = {
        "source": "failure-test", "source_item_id": "two",
        "collector_first_seen_time": now, "fetched_time": now,
        "headline": "Gold report", "body": body,
        "content_hash": digest, "cluster_id": "failure-cluster-two",
    }
    ledger.append_news_revision(row)
    parsed = {
        "row": {**row, "revision_number": 1},
        "error_type": "ValueError",
        "error": "Gemini summary_zh contains a number absent from source",
        "error_code": None,
        "model_version": annotation_module.DEFAULT_GEMINI_MODEL,
    }
    first = annotation_module._append_llm_failure(
        ledger, parsed, "ANNOTATION", annotation_module.PROMPT_VERSION
    )
    second = annotation_module._append_llm_failure(
        ledger, parsed, "ANNOTATION", annotation_module.PROMPT_VERSION
    )
    assert first["retry_state"] == "BACKING_OFF"
    assert second["retry_state"] == "DEAD_LETTER"
    latest = ledger.connection.execute(
        "SELECT * FROM news_llm_failures ORDER BY attempt_number DESC LIMIT 1"
    ).fetchone()
    assert latest["is_terminal"] == 1
    assert latest["next_retry_at"] is None


def test_batch_rpm_exhaustion_is_deferred_without_failure_row(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source text for a deferred annotation. " * 10
    ledger.append_news_revision(
        {
            "source": "capacity-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Gold report", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "capacity-test-one",
        }
    )
    monkeypatch.setattr(
        annotation_module._GeminiRequestPool,
        "call",
        lambda *_: (_ for _ in ()).throw(
            annotation_module.GeminiBatchCapacityExhausted(
                "Gemini RPM slots used; retained for the next batch"
            )
        ),
    )

    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )

    assert statuses[0]["status"] == "DEFERRED"
    assert ledger.count("news_llm_failures") == 0


def test_flash_reserve_is_unavailable_to_routine_news_but_kept_for_priority(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    for source, item, headline in (
        ("routine", "one", "Ordinary market story"),
        ("federal_reserve_monetary", "two", "FOMC statement"),
    ):
        body = f"Complete audited source {item}. " * 20
        ledger.append_news_revision(
            {
                "source": source, "source_item_id": item,
                "collector_first_seen_time": now, "fetched_time": now,
                "headline": headline, "body": body,
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "cluster_id": f"cluster-{item}",
            }
        )
    key = "test-key"
    GeminiQuotaLedger(tmp_path / "gemini-quota.json").seed(
        key, 500 - annotation_module.GEMINI_DAILY_PRIORITY_RESERVE
    )
    calls: list[str] = []
    vector = {
        "headline_zh": "联邦公开市场委员会声明",
        "summary_zh": "这是一份重要政策声明的完整中文摘要，用于验证优先配额保留。",
        "event_type": "monetary_policy", "entities": ["Federal Reserve"],
        "hawkishness": 0.0, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0,
        "usd_impulse": 0.0, "novelty": 0.5, "confidence": 0.8,
    }

    def fake_call(_key, _model, headline, _body):
        calls.append(headline)
        return dict(vector), annotation_module.DEFAULT_GEMINI_MODEL

    monkeypatch.setattr(annotation_module, "_call_gemini", fake_call)
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key=key, limit=10
    )
    assert len(statuses) == 1
    assert calls == ["FOMC statement"]


def test_headline_only_translation_is_display_only(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "short"
    ledger.append_news_revision(
        {
            "source": "title-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Altın fiyatı yükseldi", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "title-cluster",
        }
    )
    called = {}
    def fake_title_call(_key, model, _headline):
        called["model"] = model
        return "黄金价格上涨", model
    monkeypatch.setattr(annotation_module, "_call_gemini_title", fake_title_call)
    statuses = translate_pending_headlines(ledger, api_key="test-key")
    assert statuses[0]["status"] == "OK"
    assert called["model"] == "gemma-4-31b-it"
    assert ledger.count("news_title_translations") == 1
    assert ledger.count("news_annotations") == 0
    assert not (tmp_path / "gemini-quota.json").exists()
    assert (tmp_path / "gemma-quota.json").exists()


def test_placeholder_title_is_retried_append_only(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "full source text " * 30
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision(
        {
            "source": "title-test", "source_item_id": "retry",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Federal Reserve requests comment", "body": body,
            "content_hash": content_hash, "cluster_id": "title-retry",
        }
    )
    ledger.append_title_translation(
        {
            "translation_id": "old-placeholder", "source": "title-test",
            "source_item_id": "retry", "revision_number": 1,
            "raw_content_hash": content_hash,
            "headline_zh": annotation_module.INVALID_CHINESE_TITLE,
            "llm_model_version": "gemma-4-31b-it",
            "prompt_version": "headline-zh-v2-local-display-recovery",
            "parse_started_at": now, "parsed_at": now,
        }
    )
    monkeypatch.setattr(
        annotation_module, "_call_gemini_title",
        lambda *_: ("美联储就提案征求意见", "gemma-4-31b-it"),
    )
    statuses = translate_pending_headlines(ledger, api_key="test-key")
    assert statuses[0]["status"] == "OK"
    assert ledger.count("news_title_translations") == 2
    assert translate_pending_headlines(ledger, api_key="test-key") == []


def test_json_object_decoder_accepts_fence_and_trailing_text() -> None:
    assert annotation_module._decode_json_object(
        '```json\n{"headline_zh":"黄金上涨"}\n``` extra'
    ) == {"headline_zh": "黄金上涨"}


def test_duplicate_cluster_prefers_full_content_for_annotation(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    for item_id, body in (
        ("mirror-short", "headline only"),
        ("publisher-full", "Complete audited publisher article. " * 20),
    ):
        ledger.append_news_revision(
            {
                "source": "duplicate-test", "source_item_id": item_id,
                "collector_first_seen_time": now, "fetched_time": now,
                "headline": "Same syndicated headline", "body": body,
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "cluster_id": "same-cluster",
            }
        )
    vector = {
        "headline_zh": "同一篇联合发布新闻",
        "summary_zh": "系统选择具有完整正文的发布版本，并生成可靠的完整中文摘要。",
        "event_type": "other", "entities": [], "hawkishness": 0.0,
        "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 0.0, "confidence": 1.0,
    }
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (dict(vector), "gemini-3.5-flash-lite"),
    )
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    assert statuses[0]["source_item_id"] == "publisher-full"
    assert ledger.count("news_annotations") == 1


def test_gemini_annotation_does_not_treat_other_model_as_complete(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    digest = hashlib.sha256(b"news").hexdigest()
    ledger.append_news_revision(
        {
            "source": "fed",
            "source_item_id": "one",
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Policy update",
            "body": "Neutral source text with sufficient audited content. " * 8,
            "content_hash": digest,
            "cluster_id": "cluster",
        }
    )
    vector = {
        "headline_zh": "政策更新",
        "summary_zh": "这是一份政策更新的完整中文摘要，内容足以用于测试。",
        "event_type": "monetary_policy",
        "entities": ["Federal Reserve"],
        "hawkishness": 0.0,
        "inflation_impulse": 0.0,
        "growth_impulse": 0.0,
        "geopolitical_risk": 0.0,
        "usd_impulse": 0.0,
        "novelty": 0.5,
        "confidence": 0.8,
    }
    ledger.append_annotation(
        {
            "annotation_id": "old-model",
            "source": "fed",
            "source_item_id": "one",
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation": vector,
            "llm_model_version": "ollama:qwen3.5:9b",
            "prompt_version": annotation_module.PROMPT_VERSION,
            "parse_started_at": now,
            "parsed_at": now,
        }
    )
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (vector, "gemini-3.5-flash-lite"),
    )
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    assert statuses[0]["status"] == "OK"
    assert ledger.count("news_annotations") == 2


def test_v8_success_is_readable_but_receives_one_v10_category_backfill(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete audited source content. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision(
        {
            "source": "compatible-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Policy update", "body": body,
            "content_hash": digest, "cluster_id": "compatible-cluster",
        }
    )
    vector = {
        "headline_zh": "政策更新",
        "summary_zh": "这是一份已经通过旧版严格规则的完整中文摘要，不应再次消耗模型配额。",
        "event_type": "monetary_policy", "entities": [],
        "hawkishness": 0.0, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0,
        "usd_impulse": 0.0, "novelty": 0.2, "confidence": 0.8,
        "primary_category": "rates_fed", "secondary_categories": [],
        "emerging_topic_zh": "政策更新",
    }
    ledger.append_annotation(
        {
            "annotation_id": "v8-success", "source": "compatible-test",
            "source_item_id": "one", "revision_number": 1,
            "raw_content_hash": digest, "annotation": vector,
            "llm_model_version": annotation_module.DEFAULT_GEMINI_MODEL,
            "prompt_version": "news-json-v8-strict-zh-source-number-lexemes",
            "parse_started_at": now, "parsed_at": now,
        }
    )
    monkeypatch.setattr(
        annotation_module, "_call_gemini",
        lambda *_: (vector, annotation_module.DEFAULT_GEMINI_MODEL),
    )
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    )
    assert len(statuses) == 1
    assert statuses[0]["status"] == "OK"
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotations WHERE prompt_version=?",
        (annotation_module.PROMPT_VERSION,),
    ).fetchone()[0] == 1
    assert annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=1
    ) == []


def test_gemini_batch_is_capped_below_provider_rpm_limit(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    for index in range(annotation_module.GEMINI_REQUESTS_PER_MINUTE_PER_KEY + 1):
        body = f"Audited full source {index}. " * 20
        ledger.append_news_revision(
            {
                "source": "quota-test",
                "source_item_id": str(index),
                "collector_first_seen_time": now,
                "fetched_time": now,
                "headline": f"News {index}",
                "body": body,
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "cluster_id": f"cluster-{index}",
            }
        )
    vector = {
        "headline_zh": "新闻更新",
        "summary_zh": "这是一份用于安全批次容量测试的完整中文新闻摘要。",
        "event_type": "other",
        "entities": [],
        "hawkishness": 0.0,
        "inflation_impulse": 0.0,
        "growth_impulse": 0.0,
        "geopolitical_risk": 0.0,
        "usd_impulse": 0.0,
        "novelty": 0.0,
        "confidence": 1.0,
    }
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_: (vector, "gemini-3.5-flash-lite"),
    )
    statuses = annotate_pending_news(
        ledger, provider="gemini", api_key="test-key", limit=999
    )
    assert len(statuses) == annotation_module.GEMINI_REQUESTS_PER_MINUTE_PER_KEY
    assert ledger.count("news_annotations") == 12


def test_gemini_key_pool_distributes_safe_capacity(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    expected = annotation_module.GEMINI_REQUESTS_PER_MINUTE_PER_KEY * 2
    for index in range(expected + 1):
        body = f"Audited pooled source {index}. " * 20
        ledger.append_news_revision(
            {
                "source": "pool-test",
                "source_item_id": str(index),
                "collector_first_seen_time": now,
                "fetched_time": now,
                "headline": f"Pooled news {index}",
                "body": body,
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "cluster_id": f"cluster-{index}",
            }
        )
    calls: list[str] = []
    vector = {
        "headline_zh": "汇总新闻", "summary_zh": "这是用于多密钥轮换容量测试的完整中文摘要。",
        "event_type": "other", "entities": [], "hawkishness": 0.0,
        "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 0.0, "confidence": 1.0,
    }

    def fake_call(key, *_):
        calls.append(key)
        return vector, "gemini-3.5-flash-lite"

    monkeypatch.setenv("GEMINI_API_KEYS", "key-a;key-b;key-a")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(annotation_module, "_call_gemini", fake_call)
    statuses = annotate_pending_news(ledger, provider="gemini", limit=999)
    assert len(statuses) == expected
    assert calls.count("key-a") == 12
    assert calls.count("key-b") == 12


def test_gemini_key_pool_falls_back_on_quota_error(monkeypatch) -> None:
    calls: list[str] = []

    def fake_call(key, *_):
        calls.append(key)
        if key == "exhausted":
            raise annotation_module.urllib.error.HTTPError(
                "https://example.invalid", 429, "quota", {}, None
            )
        return {"summary_zh": "ok"}, "gemini-3.5-flash-lite"

    monkeypatch.setattr(annotation_module, "_call_gemini", fake_call)
    result, model = annotation_module._call_gemini_with_fallback(
        ("exhausted", "available"), 0, "model", "headline", "body"
    )
    assert calls == ["exhausted", "available"]
    assert result == {"summary_zh": "ok"}
    assert model == "gemini-3.5-flash-lite"


def test_gemini_daily_quota_counts_attempts_and_resets_at_pacific_midnight(
    tmp_path,
) -> None:
    path = tmp_path / "gemini-quota.json"
    quota = GeminiQuotaLedger(path, daily_limit=2)
    key = "secret-test-key"
    before_reset = datetime(2026, 8, 6, 6, 59, tzinfo=UTC)
    after_reset = datetime(2026, 8, 6, 7, 0, tzinfo=UTC)
    assert quota.reserve(key, before_reset)
    assert quota.reserve(key, before_reset)
    assert not quota.reserve(key, before_reset)
    snapshot = quota.snapshot((key,), before_reset)
    assert snapshot["keys"][0]["sent"] == 2
    assert snapshot["keys"][0]["remaining"] == 0
    assert snapshot["keys"][0]["status"] == "DAILY_LIMIT"
    assert key not in path.read_text(encoding="utf-8")
    assert quota.snapshot((key,), after_reset)["keys"][0]["sent"] == 0
    assert quota.reserve(key, after_reset)


def test_gemini_31_has_an_independent_fallback_quota(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    key = "test-key"
    assert annotation_module.gemini_routine_remaining(
        ledger, annotation_module.DEFAULT_GEMINI_MODEL, key
    ) == 350
    assert annotation_module.gemini_routine_remaining(
        ledger, annotation_module.FALLBACK_GEMINI_MODEL, key
    ) == 500
    primary = GeminiQuotaLedger(tmp_path / "gemini-quota.json")
    primary.seed(key, 500)
    assert annotation_module.gemini_routine_remaining(
        ledger, annotation_module.DEFAULT_GEMINI_MODEL, key
    ) == 0
    assert annotation_module.gemini_routine_remaining(
        ledger, annotation_module.FALLBACK_GEMINI_MODEL, key
    ) == 500


def test_gemini_31_annotation_is_training_visible_and_not_reprocessed(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Gold geopolitical source text without numeric claims. " * 20
    ledger.append_news_revision(
        {
            "source": "fallback-test", "source_item_id": "one",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Gold geopolitical update", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "fallback-test-cluster",
        }
    )
    vector = {
        "headline_zh": "黄金地缘局势更新",
        "summary_zh": "来源报道黄金相关地缘局势出现更新，内容已经完整保存。",
        "event_type": "geopolitical", "entities": [],
        "hawkishness": 0.0, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.8,
        "usd_impulse": 0.0, "novelty": 0.7, "confidence": 0.9,
    }

    def fallback_call(_key, model, *_args):
        assert model == annotation_module.FALLBACK_GEMINI_MODEL
        return dict(vector), model

    monkeypatch.setattr(annotation_module, "_call_gemini", fallback_call)
    statuses = annotate_pending_news(
        ledger,
        provider="gemini",
        api_key="test-key",
        model=annotation_module.FALLBACK_GEMINI_MODEL,
        limit=1,
    )
    assert statuses[0]["status"] == "OK"
    features = aggregate_news_features(
        ledger, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert features["news_geopolitical_risk"] == pytest.approx(0.8)
    assert annotate_pending_news(
        ledger,
        provider="gemini",
        api_key="test-key",
        model=annotation_module.DEFAULT_GEMINI_MODEL,
        limit=1,
    ) == []


def test_completed_quotes_are_archived_and_ledger_backup_is_valid(tmp_path) -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    quote_root = tmp_path / "quotes"
    quote_root.mkdir()
    old = quote_root / "xauusd-quotes-20260804.jsonl"
    current = quote_root / "xauusd-quotes-20260805.jsonl"
    old.write_text('{"symbol":"XAUUSD"}\n', encoding="utf-8")
    current.write_text('{"symbol":"XAUUSD"}\n', encoding="utf-8")
    old_timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(old, (old_timestamp, old_timestamp))
    archived = archive_completed_quote_days(quote_root, now)
    assert archived == [quote_root / "xauusd-quotes-20260804.jsonl.gz"]
    assert not old.exists()
    assert current.exists()
    assert archived[0].with_suffix(".gz.receipt.json").exists()

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    backup = backup_forward_ledger(ledger, tmp_path / "backups", now)
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


class _EmptyProvider:
    name = "empty"

    def observations(self, decision_time):
        return []


class _FixedProvider:
    name = "synthetic-fixed"

    def __init__(self, observations):
        self.rows = observations

    def observations(self, decision_time):
        return [row for row in self.rows if row.received_time <= decision_time]
