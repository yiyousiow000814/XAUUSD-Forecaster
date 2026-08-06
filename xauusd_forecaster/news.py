"""Official point-in-time news and macro adapters for Forward collection."""

from __future__ import annotations

import hashlib
import html
import io
import csv
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from bs4 import BeautifulSoup

from .content import (
    hydrate_pending_federal_reserve_content,
    hydrate_pending_non_fed_content,
)
from .forward_ledger import ForwardLedger


UTC = timezone.utc
USER_AGENT = "XAUUSD-Forward-Evidence/0.1 (+local research collector)"


@dataclass(frozen=True)
class RssSource:
    name: str
    url: str


OFFICIAL_RSS_SOURCES = (
    RssSource("federal_reserve_press_all", "https://www.federalreserve.gov/feeds/press_all.xml"),
    RssSource("federal_reserve_monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    RssSource(
        "federal_reserve_speeches_testimony",
        "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
    ),
)

DIRECT_FULL_TEXT_RSS_SOURCES = (
    RssSource("eia_today_in_energy", "https://www.eia.gov/rss/todayinenergy.xml"),
    RssSource("eia_press_releases", "https://www.eia.gov/rss/press_rss.xml"),
    RssSource("ecb_press_releases", "https://www.ecb.europa.eu/rss/press.html"),
)


@dataclass(frozen=True)
class HtmlNewsSource:
    name: str
    url: str
    link_prefix: str
    relevance_terms: tuple[str, ...]


DIRECT_FULL_TEXT_HTML_SOURCES = (
    HtmlNewsSource(
        "us_treasury_press_releases",
        "https://home.treasury.gov/news/press-releases",
        "/news/press-releases/",
        (
            "sanction", "iran", "russia", "war", "terror", "oil", "hormuz",
            "foreign exchange", "currency", "borrowing", "treasury market",
            "financial stability", "debt",
        ),
    ),
    HtmlNewsSource(
        "bea_economic_releases",
        "https://www.bea.gov/news/current-releases",
        "/news/20",
        (
            "gross domestic product", "gdp", "personal income", "outlays",
            "pce", "international trade", "corporate profits",
        ),
    ),
)
DIRECT_RSS_RELEVANCE_TERMS = {
    "eia_today_in_energy": (
        "oil", "crude", "petroleum", "gasoline", "diesel", "opec",
        "hormuz", "strait", "production", "global demand", "supply disruption",
    ),
    "eia_press_releases": (
        "oil", "crude", "petroleum", "gasoline", "diesel", "opec",
        "hormuz", "strait", "production", "global demand", "supply disruption",
    ),
    "ecb_press_releases": (
        "monetary policy", "interest rate", "inflation", "liquidity",
        "balance sheet", "exchange rate", "financial stability", "euro area economy",
    ),
}

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_SOURCE = "bls_public_api"
BLS_SERIES = {
    "CES0000000001": ("Total nonfarm payroll employment", "thousands"),
    "CES0500000003": ("Average hourly earnings, total private", "USD/hour"),
    "LNS14000000": ("Civilian unemployment rate", "percent"),
    "CUSR0000SA0": ("CPI-U all items, seasonally adjusted", "index"),
    "CUSR0000SA0L1E": ("CPI-U less food and energy, seasonally adjusted", "index"),
    "JTS000000000000000JOL": ("Total nonfarm job openings", "thousands"),
}


@dataclass(frozen=True)
class FredSeries:
    series_id: str
    title: str
    unit: str


FRED_SOURCE = "fred_graph_csv"
FRED_SERIES = (
    FredSeries("DGS2", "2-Year Treasury Constant Maturity Rate", "percent"),
    FredSeries("DFII10", "10-Year Treasury Inflation-Indexed Security", "percent"),
    FredSeries("DTWEXBGS", "Nominal Broad U.S. Dollar Index", "index"),
    FredSeries("DCOILWTICO", "WTI Crude Oil Spot Price", "USD/barrel"),
    FredSeries("WALCL", "Federal Reserve Total Assets", "USD millions"),
    FredSeries("VIXCLS", "CBOE Volatility Index", "index"),
)
GDELT_SOURCE = "gdelt_gold_geopolitics"
GDELT_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc?"
    "query=" + urllib.parse.quote(
        "gold (war OR conflict OR sanctions OR geopolitical OR Fed OR rates "
        "OR yield OR dollar OR inflation OR payrolls OR jobs OR oil OR central bank)"
    )
    + "&mode=artlist&maxrecords=25&timespan=2h&format=json"
)
GOOGLE_GEO_SOURCE = "google_news_gold_context"
GOOGLE_GEO_URL = (
    "https://news.google.com/rss/search?"
    + urllib.parse.urlencode(
        {
            "q": (
                "gold (Fed OR rates OR yield OR dollar OR inflation OR payrolls "
                "OR jobs OR oil OR war OR conflict OR sanctions OR geopolitical "
                "OR central bank)"
            ),
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
    )
)
WGC_SOURCE = "world_gold_council_central_banks"
WGC_URL = "https://www.gold.org/blog-categories/central-banks"


def _text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return child.text.strip()
    return ""


def _published(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC)
        except ValueError:
            return None


def _clean(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return " ".join(plain.split())


def parse_rss(payload: bytes, source: RssSource, fetched_at: datetime) -> list[dict]:
    root = ET.fromstring(payload)
    items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    records: list[dict] = []
    for item in items:
        headline = _clean(_text(item, ("title",)))
        link = _text(item, ("link",))
        if not link:
            for child in item.iter():
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        if link:
            link = urllib.parse.urljoin(source.url, link)
        item_id = _text(item, ("guid", "id")) or link or headline
        body = _clean(_text(item, ("description", "summary", "content")))
        published = _published(_text(item, ("pubdate", "published", "updated")))
        content_hash = hashlib.sha256(
            f"{headline}\n{body}\n{link}".encode("utf-8")
        ).hexdigest()
        cluster_key = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        records.append(
            {
                "source": source.name,
                "source_item_id": item_id,
                "source_published_time": published,
                "collector_first_seen_time": fetched_at,
                "fetched_time": fetched_at,
                "headline": headline,
                "body": body,
                "link": link,
                "content_hash": content_hash,
                "cluster_id": hashlib.sha256(cluster_key.encode("utf-8")).hexdigest(),
            }
        )
    return records


def fetch_rss(source: RssSource, timeout_seconds: float = 15.0) -> bytes:
    request = urllib.request.Request(source.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def fetch_bls_api(fetched_at: datetime, timeout_seconds: float = 20.0) -> bytes:
    payload: dict[str, object] = {
        "seriesid": list(BLS_SERIES),
        "startyear": str(fetched_at.year - 1),
        "endyear": str(fetched_at.year),
    }
    key = os.environ.get("BLS_API_KEY", "").strip()
    if key:
        payload["registrationkey"] = key
    request = urllib.request.Request(
        BLS_API_URL,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def fetch_url(url: str, timeout_seconds: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def decode_google_news_publisher_url(url: str) -> str:
    """Best-effort Google wrapper decoding; failure leaves the discovery URL intact."""
    if urllib.parse.urlparse(url).hostname != "news.google.com":
        return url
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(url)
        decoded = str(result.get("decoded_url") or "") if result.get("status") else ""
        parsed = urllib.parse.urlparse(decoded)
        if parsed.scheme in {"http", "https"} and parsed.hostname != "news.google.com":
            return decoded
    except Exception:
        pass
    return url


def collect_fred_macro(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict[str, object]:
    """Collect free public FRED graph CSV snapshots with first-seen revisions."""
    interval = timedelta(minutes=60)
    poll_source = f"{FRED_SOURCE}:bundle"
    last_poll = ledger.latest_source_poll_time(poll_source)
    if last_poll is not None and fetched_at - last_poll < interval:
        return {"source": FRED_SOURCE, "status": "SKIPPED_INTERVAL"}
    inserted = 0
    unchanged = 0
    errors: list[str] = []
    hashes: list[str] = []
    start = (fetched_at - timedelta(days=45)).date().isoformat()
    for series in FRED_SERIES:
        try:
            url = (
                "https://fred.stlouisfed.org/graph/fredgraph.csv?"
                + urllib.parse.urlencode({"id": series.series_id, "cosd": start})
            )
            raw = fetcher(url)
            hashes.append(hashlib.sha256(raw).hexdigest())
            rows = []
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
                raw_value = (row.get(series.series_id) or "").strip()
                if not raw_value or raw_value == ".":
                    continue
                rows.append((row["observation_date"], float(raw_value)))
            if not rows:
                raise ValueError(f"{series.series_id} returned no finite observations")
            # Two values initialize a current forward state. Their first-seen time is
            # now; they do not create historical decisions or historical labels.
            for period, value in rows[-2:]:
                stored = {
                    "series_id": series.series_id,
                    "title": series.title,
                    "observation_period": period,
                    "value": value,
                    "retrieved_from": url,
                }
                digest = hashlib.sha256(
                    json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                _, created = ledger.append_macro_observation(
                    {
                        "source": FRED_SOURCE,
                        "series_id": series.series_id,
                        "observation_period": period,
                        "collector_first_seen_time": fetched_at,
                        "fetched_time": fetched_at,
                        "value": value,
                        "unit": series.unit,
                        "payload": stored,
                        "content_hash": digest,
                    }
                )
                inserted += int(created)
                unchanged += int(not created)
        except Exception as error:
            errors.append(f"{series.series_id}:{type(error).__name__}:{str(error)[:120]}")
    status = "OK" if not errors else ("PARTIAL" if inserted or unchanged else "ERROR")
    ledger.append_source_poll(
        {
            "poll_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{poll_source}|{fetched_at.isoformat()}")),
            "source": poll_source,
            "fetched_time": fetched_at,
            "status": status,
            "payload_hash": hashlib.sha256("".join(hashes).encode()).hexdigest() if hashes else None,
            "error_type": "SeriesErrors" if errors else None,
            "error": " | ".join(errors)[:500] if errors else None,
        }
    )
    return {
        "source": FRED_SOURCE,
        "status": status,
        "inserted_revisions": inserted,
        "unchanged_items": unchanged,
        "errors": errors,
    }


def collect_gdelt_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict[str, object]:
    """Collect GDELT with an append-only 429 circuit breaker.

    Google News is collected independently, so a GDELT cooldown never stops the
    geopolitical-news lane.  A successful probe closes the circuit naturally.
    """
    last_poll = ledger.latest_source_poll_time(GDELT_SOURCE)
    recent_polls = ledger.connection.execute(
        """SELECT fetched_time,status,error FROM source_polls
           WHERE source=? ORDER BY fetched_time DESC,poll_id DESC LIMIT 8""",
        (GDELT_SOURCE,),
    ).fetchall()
    rate_limit_streak = 0
    for row in recent_polls:
        if row["status"] == "ERROR" and "429" in str(row["error"] or ""):
            rate_limit_streak += 1
        else:
            break
    cooldown_minutes = (
        min(360, 60 * (2 ** min(rate_limit_streak, 3)))
        if rate_limit_streak else 60
    )
    retry_at = last_poll + timedelta(minutes=cooldown_minutes) if last_poll else None
    if retry_at is not None and fetched_at < retry_at:
        return {
            "source": GDELT_SOURCE,
            "status": "SKIPPED_BACKOFF" if rate_limit_streak else "SKIPPED_INTERVAL",
            "fallback_source": GOOGLE_GEO_SOURCE if rate_limit_streak else None,
            "retry_at": retry_at.isoformat(),
            "rate_limit_streak": rate_limit_streak,
        }
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{GDELT_SOURCE}|{fetched_at.isoformat()}"))
    try:
        raw = fetcher(GDELT_URL)
        inserted = 0
        unchanged = 0
        for article in json.loads(raw).get("articles", [])[:25]:
            link = str(article.get("url") or "").strip()
            headline = _clean(str(article.get("title") or ""))
            if not link or not headline:
                continue
            published = _published(str(article.get("seendate") or ""))
            body = " · ".join(
                value for value in (
                    str(article.get("domain") or "").strip(),
                    str(article.get("sourcecountry") or "").strip(),
                    str(article.get("language") or "").strip(),
                ) if value
            )
            digest = hashlib.sha256(f"{headline}\n{body}\n{link}".encode()).hexdigest()
            _, created = ledger.append_news_revision(
                {
                    "source": GDELT_SOURCE,
                    "source_item_id": link,
                    "source_published_time": published,
                    "collector_first_seen_time": fetched_at,
                    "fetched_time": fetched_at,
                    "headline": headline,
                    "body": body,
                    "link": link,
                    "content_hash": digest,
                    "cluster_id": hashlib.sha256(headline.lower().encode()).hexdigest(),
                }
            )
            inserted += int(created)
            unchanged += int(not created)
        ledger.append_source_poll({"poll_id": poll_id, "source": GDELT_SOURCE, "fetched_time": fetched_at, "status": "OK", "payload_hash": hashlib.sha256(raw).hexdigest()})
        return {"source": GDELT_SOURCE, "status": "OK", "inserted_revisions": inserted, "unchanged_items": unchanged}
    except Exception as error:
        message = str(error)[:500]
        ledger.append_source_poll({"poll_id": poll_id, "source": GDELT_SOURCE, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": message})
        next_streak = rate_limit_streak + int("429" in message)
        next_cooldown = min(360, 60 * (2 ** min(next_streak, 3))) if next_streak else 60
        return {
            "source": GDELT_SOURCE,
            "status": "ERROR",
            "error_type": type(error).__name__,
            "error": message,
            "fallback_source": GOOGLE_GEO_SOURCE,
            "retry_at": (fetched_at + timedelta(minutes=next_cooldown)).isoformat(),
            "rate_limit_streak": next_streak,
        }


def collect_direct_full_text_rss_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
) -> list[dict[str, object]]:
    """Collect bounded official feeds whose publisher pages can be hydrated."""
    statuses: list[dict[str, object]] = []
    for source in DIRECT_FULL_TEXT_RSS_SOURCES:
        last_poll = ledger.latest_source_poll_time(source.name)
        if last_poll is not None and fetched_at - last_poll < timedelta(minutes=10):
            statuses.append({"source": source.name, "status": "SKIPPED_INTERVAL"})
            continue
        poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.name}|{fetched_at.isoformat()}"))
        try:
            raw = fetcher(source)
            inserted = 0
            unchanged = 0
            relevant = []
            terms = DIRECT_RSS_RELEVANCE_TERMS[source.name]
            for record in parse_rss(raw, source, fetched_at):
                searchable = f"{record['headline']} {record['body']}".lower()
                if any(term in searchable for term in terms):
                    relevant.append(record)
            for record in relevant[:5]:
                _, created = ledger.append_news_revision(record)
                inserted += int(created)
                unchanged += int(not created)
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": "OK",
                    "payload_hash": hashlib.sha256(raw).hexdigest(),
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": "OK",
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                }
            )
        except Exception as error:
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    return statuses


def collect_direct_full_text_html_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> list[dict[str, object]]:
    """Monitor bounded official listing pages with stable direct article links."""
    statuses: list[dict[str, object]] = []
    for source in DIRECT_FULL_TEXT_HTML_SOURCES:
        last_poll = ledger.latest_source_poll_time(source.name)
        if last_poll is not None and fetched_at - last_poll < timedelta(minutes=10):
            statuses.append({"source": source.name, "status": "SKIPPED_INTERVAL"})
            continue
        poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.name}|{fetched_at.isoformat()}"))
        try:
            raw = fetcher(source.url)
            soup = BeautifulSoup(raw, "html.parser")
            records: list[dict[str, object]] = []
            seen: set[str] = set()
            for anchor in soup.select("a[href]"):
                path = str(anchor.get("href") or "").strip()
                headline = _clean(anchor.get_text(" ", strip=True))
                if not path.startswith(source.link_prefix) or not headline:
                    continue
                link = urllib.parse.urljoin(source.url, path)
                if link in seen or not any(
                    term in headline.lower() for term in source.relevance_terms
                ):
                    continue
                seen.add(link)
                body = f"Official {source.name} listing discovery"
                records.append(
                    {
                        "source": source.name,
                        "source_item_id": link,
                        "source_published_time": None,
                        "collector_first_seen_time": fetched_at,
                        "fetched_time": fetched_at,
                        "headline": headline,
                        "body": body,
                        "link": link,
                        "content_hash": hashlib.sha256(
                            f"{headline}\n{body}\n{link}".encode()
                        ).hexdigest(),
                        "cluster_id": hashlib.sha256(
                            re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip().encode()
                        ).hexdigest(),
                    }
                )
                if len(records) >= 5:
                    break
            inserted = 0
            unchanged = 0
            for record in records:
                _, created = ledger.append_news_revision(record)
                inserted += int(created)
                unchanged += int(not created)
            if not records:
                raise ValueError(f"{source.name} returned no relevant direct links")
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": "OK",
                    "payload_hash": hashlib.sha256(raw).hexdigest(),
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": "OK",
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                }
            )
        except Exception as error:
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    return statuses


def collect_world_gold_council_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict[str, object]:
    """Collect World Gold Council central-bank research headlines."""
    last_poll = ledger.latest_source_poll_time(WGC_SOURCE)
    if last_poll is not None and fetched_at - last_poll < timedelta(hours=6):
        return {"source": WGC_SOURCE, "status": "SKIPPED_INTERVAL"}
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{WGC_SOURCE}|{fetched_at.isoformat()}"))
    try:
        raw = fetcher(WGC_URL)
        page = raw.decode("utf-8", "replace")
        matches = re.findall(
            r'<a href="(/goldhub/gold-focus/[^"]+)"[^>]*>\s*(.*?)\s*</a>',
            page,
            flags=re.IGNORECASE | re.DOTALL,
        )
        inserted = 0
        unchanged = 0
        seen: set[str] = set()
        for path, raw_title in matches:
            if path in seen or len(seen) >= 8:
                continue
            seen.add(path)
            headline = _clean(raw_title)
            if not headline or headline.lower().startswith("read more"):
                continue
            link = urllib.parse.urljoin(WGC_URL, path)
            digest = hashlib.sha256(f"{headline}\n{link}".encode()).hexdigest()
            _, created = ledger.append_news_revision(
                {
                    "source": WGC_SOURCE,
                    "source_item_id": link,
                    "collector_first_seen_time": fetched_at,
                    "fetched_time": fetched_at,
                    "headline": headline,
                    "body": "World Gold Council central-bank research monitor",
                    "link": link,
                    "content_hash": digest,
                    "cluster_id": hashlib.sha256(headline.lower().encode()).hexdigest(),
                }
            )
            inserted += int(created)
            unchanged += int(not created)
        if not seen:
            raise ValueError("World Gold Council page returned no central-bank links")
        ledger.append_source_poll({"poll_id": poll_id, "source": WGC_SOURCE, "fetched_time": fetched_at, "status": "OK", "payload_hash": hashlib.sha256(raw).hexdigest()})
        return {"source": WGC_SOURCE, "status": "OK", "inserted_revisions": inserted, "unchanged_items": unchanged}
    except Exception as error:
        ledger.append_source_poll({"poll_id": poll_id, "source": WGC_SOURCE, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]})
        return {"source": WGC_SOURCE, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]}


def collect_google_geopolitical_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    decoder: Callable[[str], str] = decode_google_news_publisher_url,
) -> dict[str, object]:
    """Independent free fallback when GDELT rate-limits its DOC endpoint."""
    last_poll = ledger.latest_source_poll_time(GOOGLE_GEO_SOURCE)
    if last_poll is not None and fetched_at - last_poll < timedelta(minutes=20):
        return {"source": GOOGLE_GEO_SOURCE, "status": "SKIPPED_INTERVAL"}
    source = RssSource(GOOGLE_GEO_SOURCE, GOOGLE_GEO_URL)
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{GOOGLE_GEO_SOURCE}|{fetched_at.isoformat()}"))
    try:
        raw = fetcher(source)
        inserted = 0
        unchanged = 0
        for record in parse_rss(raw, source, fetched_at)[:10]:
            existing = ledger.connection.execute(
                """SELECT link FROM news_revisions
                WHERE source=? AND source_item_id=?
                ORDER BY revision_number DESC LIMIT 1""",
                (record["source"], record["source_item_id"]),
            ).fetchone()
            existing_link = str(existing["link"] or "") if existing else ""
            if existing_link and urllib.parse.urlparse(existing_link).hostname != "news.google.com":
                resolved = existing_link
            else:
                resolved = decoder(record["link"])
            if resolved != record["link"]:
                record["link"] = resolved
                record["content_hash"] = hashlib.sha256(
                    f"{record['headline']}\n{record['body']}\n{resolved}".encode()
                ).hexdigest()
            _, created = ledger.append_news_revision(record)
            inserted += int(created)
            unchanged += int(not created)
        ledger.append_source_poll({"poll_id": poll_id, "source": GOOGLE_GEO_SOURCE, "fetched_time": fetched_at, "status": "OK", "payload_hash": hashlib.sha256(raw).hexdigest()})
        return {"source": GOOGLE_GEO_SOURCE, "status": "OK", "inserted_revisions": inserted, "unchanged_items": unchanged}
    except Exception as error:
        ledger.append_source_poll({"poll_id": poll_id, "source": GOOGLE_GEO_SOURCE, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]})
        return {"source": GOOGLE_GEO_SOURCE, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]}


def collect_bls_macro(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[datetime], bytes] = fetch_bls_api,
) -> dict[str, object]:
    key_configured = bool(os.environ.get("BLS_API_KEY", "").strip())
    interval = timedelta(minutes=5 if key_configured else 65)
    last_poll = ledger.latest_source_poll_time(BLS_SOURCE)
    if last_poll is not None and fetched_at - last_poll < interval:
        return {
            "source": BLS_SOURCE,
            "status": "SKIPPED_INTERVAL",
            "next_poll_after": (last_poll + interval).isoformat(),
            "registered": key_configured,
        }
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BLS_SOURCE}|{fetched_at.isoformat()}"))
    try:
        raw = fetcher(fetched_at)
        payload_hash = hashlib.sha256(raw).hexdigest()
        envelope = json.loads(raw)
        if envelope.get("status") != "REQUEST_SUCCEEDED":
            raise ValueError(f"BLS API status: {envelope.get('status')}")
        inserted = 0
        unchanged = 0
        for series in envelope.get("Results", {}).get("series", []):
            series_id = series.get("seriesID")
            points = [
                point for point in series.get("data", [])
                if str(point.get("period", "")).startswith("M")
                and point.get("period") != "M13"
            ]
            if series_id not in BLS_SERIES or not points:
                continue
            point = points[0]
            period = f"{point['year']}-{point['period']}"
            title, unit = BLS_SERIES[series_id]
            stored_payload = {
                "series_id": series_id,
                "title": title,
                "year": point["year"],
                "period": point["period"],
                "period_name": point.get("periodName"),
                "value": point["value"],
                "footnotes": point.get("footnotes", []),
            }
            content_hash = hashlib.sha256(
                json.dumps(stored_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            _, created = ledger.append_macro_observation(
                {
                    "source": BLS_SOURCE,
                    "series_id": series_id,
                    "observation_period": period,
                    "collector_first_seen_time": fetched_at,
                    "fetched_time": fetched_at,
                    "value": float(point["value"]),
                    "unit": unit,
                    "footnotes": point.get("footnotes", []),
                    "payload": stored_payload,
                    "content_hash": content_hash,
                }
            )
            inserted += int(created)
            unchanged += int(not created)
        ledger.append_source_poll(
            {
                "poll_id": poll_id,
                "source": BLS_SOURCE,
                "fetched_time": fetched_at,
                "status": "OK",
                "payload_hash": payload_hash,
            }
        )
        return {
            "source": BLS_SOURCE,
            "status": "OK",
            "inserted_revisions": inserted,
            "unchanged_items": unchanged,
            "registered": key_configured,
        }
    except Exception as error:
        ledger.append_source_poll(
            {
                "poll_id": poll_id,
                "source": BLS_SOURCE,
                "fetched_time": fetched_at,
                "status": "ERROR",
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            }
        )
        return {
            "source": BLS_SOURCE,
            "status": "ERROR",
            "error_type": type(error).__name__,
            "error": str(error)[:500],
            "registered": key_configured,
        }


def collect_official_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
) -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    for source in OFFICIAL_RSS_SOURCES:
        inserted = 0
        unchanged = 0
        try:
            for record in parse_rss(fetcher(source), source, fetched_at):
                _, created = ledger.append_news_revision(record)
                inserted += int(created)
                unchanged += int(not created)
            statuses.append(
                {
                    "source": source.name,
                    "status": "OK",
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                }
            )
        except Exception as error:  # source failure must not hide other feeds
            statuses.append(
                {
                    "source": source.name,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    statuses.append(hydrate_pending_federal_reserve_content(ledger, fetched_at))
    statuses.append(collect_bls_macro(ledger, fetched_at))
    statuses.append(collect_fred_macro(ledger, fetched_at))
    statuses.extend(collect_direct_full_text_rss_news(ledger, fetched_at, fetcher))
    statuses.extend(collect_direct_full_text_html_news(ledger, fetched_at))
    statuses.append(collect_gdelt_news(ledger, fetched_at))
    statuses.append(collect_google_geopolitical_news(ledger, fetched_at))
    statuses.append(collect_world_gold_council_news(ledger, fetched_at))
    statuses.append(hydrate_pending_non_fed_content(ledger, fetched_at))
    return statuses
