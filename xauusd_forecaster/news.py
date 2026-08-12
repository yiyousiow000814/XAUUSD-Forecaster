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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from bs4 import BeautifulSoup

from .content import (
    extract_article_full_text,
    extract_federal_reserve_full_text,
)
from .forward_ledger import ForwardLedger
from .news_relevance import (
    google_news_item_is_relevant,
    google_news_quality_rank,
)


UTC = timezone.utc
USER_AGENT = "XAUUSD-Forward-Evidence/0.1 (+local research collector)"
NEWS_INTAKE_MAX_AGE = timedelta(hours=72)


@dataclass(frozen=True)
class RssSource:
    name: str
    url: str


def _current_forward_news(record: dict[str, object], ledger: ForwardLedger,
                          fetched_at: datetime) -> tuple[bool, str]:
    """Reject archive/search results before any durable news row is created."""
    published = record.get("source_published_time")
    if not isinstance(published, datetime):
        return False, "PUBLISHED_TIME_MISSING"
    if published < ledger.forward_epoch:
        return False, "PRE_FORWARD_PUBLICATION"
    if published > fetched_at + timedelta(minutes=5):
        return False, "FUTURE_PUBLICATION_TIME"
    if fetched_at - published > NEWS_INTAKE_MAX_AGE:
        return False, "STALE_PUBLICATION"
    return True, "ELIGIBLE"


def _append_after_full_text(
    ledger: ForwardLedger,
    record: dict[str, object],
    extractor: Callable[[str], tuple[str, str]],
) -> tuple[bool, str]:
    """Append exactly once, and only after auditable publisher text exists."""
    if _has_stored_full_text(
        ledger, str(record["source"]), str(record["source_item_id"])
    ):
        return False, "UNCHANGED_FULL_TEXT"
    link = str(record.get("link") or "").strip()
    if not link:
        return False, "SOURCE_URL_MISSING"
    try:
        text, source_url = extractor(link)
    except Exception:
        return False, "FULL_TEXT_UNAVAILABLE"
    record["link"] = source_url
    record["body"] = f"[FULL_TEXT source={source_url} chars={len(text)}]\n{text}"
    record["content_hash"] = hashlib.sha256(
        f"{record['headline']}\n{record['body']}\n{source_url}".encode()
    ).hexdigest()
    _, created = ledger.append_news_revision(record)
    return created, "INSERTED" if created else "UNCHANGED_FULL_TEXT"


def _has_stored_full_text(
    ledger: ForwardLedger, source: str, source_item_id: str,
) -> bool:
    latest = ledger.connection.execute(
        """SELECT body FROM news_revisions
           WHERE source=? AND source_item_id=?
           ORDER BY revision_number DESC LIMIT 1""",
        (source, source_item_id),
    ).fetchone()
    return bool(
        latest is not None
        and str(latest["body"] or "").startswith("[FULL_TEXT")
    )


def _redacted_error(error: Exception, *secrets: str, limit: int = 500) -> str:
    text = str(error)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:limit]


OFFICIAL_RSS_SOURCES = (
    RssSource("federal_reserve_press_all", "https://www.federalreserve.gov/feeds/press_all.xml"),
    RssSource("federal_reserve_monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    RssSource(
        "federal_reserve_speeches_testimony",
        "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
    ),
)

DIRECT_FULL_TEXT_RSS_SOURCES = (
    RssSource("bls_employment_situation", "https://www.bls.gov/feed/empsit.rss"),
    RssSource("bls_consumer_price_index", "https://www.bls.gov/feed/cpi.rss"),
    RssSource("bls_job_openings", "https://www.bls.gov/feed/jolts.rss"),
    RssSource("eia_today_in_energy", "https://www.eia.gov/rss/todayinenergy.xml"),
    RssSource("eia_press_releases", "https://www.eia.gov/rss/press_rss.xml"),
    RssSource("ecb_press_releases", "https://www.ecb.europa.eu/rss/press.html"),
)


@dataclass(frozen=True)
class HtmlNewsSource:
    name: str
    url: str
    link_prefix: str


DIRECT_FULL_TEXT_HTML_SOURCES = (
    HtmlNewsSource(
        "us_treasury_press_releases",
        "https://home.treasury.gov/news/press-releases",
        "/news/press-releases/",
    ),
    HtmlNewsSource(
        "bea_economic_releases",
        "https://www.bea.gov/news/current-releases",
        "/news/20",
    ),
)

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
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = (
    FredSeries("DGS2", "2-Year Treasury Constant Maturity Rate", "percent"),
    FredSeries("DFII10", "10-Year Treasury Inflation-Indexed Security", "percent"),
    FredSeries("DTWEXBGS", "Nominal Broad U.S. Dollar Index", "index"),
    FredSeries("DCOILWTICO", "WTI Crude Oil Spot Price", "USD/barrel"),
    FredSeries("WALCL", "Federal Reserve Total Assets", "USD millions"),
    FredSeries("VIXCLS", "CBOE Volatility Index", "index"),
)
EIA_API_SOURCE = "eia_open_data_api"
EIA_API_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
EIA_WTI_SERIES_ID = "EIA_RWTC"
BEA_API_SOURCE = "bea_public_api"
BEA_API_URL = "https://apps.bea.gov/api/data"


@dataclass(frozen=True)
class BeaSeries:
    series_id: str
    table_name: str
    line_number: str
    title: str
    unit: str


BEA_SERIES = (
    BeaSeries(
        "BEA_REAL_GDP_GROWTH_QOQ_ANNUALIZED", "T10101", "1",
        "Real gross domestic product growth", "percent annual rate",
    ),
    BeaSeries(
        "BEA_GDP_PRICE_INDEX_Q", "T10104", "1",
        "Gross domestic product price index", "index",
    ),
    BeaSeries(
        "BEA_PCE_PRICE_INDEX_Q", "T10104", "2",
        "Personal consumption expenditures price index", "index",
    ),
)
GDELT_SOURCE = "gdelt_gold_geopolitics"
GDELT_LAST_UPDATE_URL = (
    "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/lastupdate.txt"
)
GDELT_GCS_PREFIX = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/"
GDELT_MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
GDELT_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
GDELT_MAX_CANDIDATES = 25
GOOGLE_GEO_SOURCE = "google_news_gold_context"


@dataclass(frozen=True)
class GoogleNewsLane:
    name: str
    query: str

    @property
    def url(self) -> str:
        return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": self.query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        )


GOOGLE_NEWS_LANES = (
    GoogleNewsLane(
        GOOGLE_GEO_SOURCE,
        "gold (Fed OR rates OR yield OR dollar OR inflation OR payrolls OR jobs "
        "OR oil OR war OR conflict OR sanctions OR geopolitical OR central bank) when:3d",
    ),
    GoogleNewsLane(
        "google_news_bls_official_releases",
        'site:bls.gov ("Employment Situation" OR "Consumer Price Index" '
        'OR "Job Openings and Labor Turnover") when:3d',
    ),
    GoogleNewsLane(
        "google_news_us_employment",
        '("nonfarm payrolls" OR NFP OR "jobs report" OR "employment situation" '
        'OR "unemployment rate" OR "average hourly earnings" OR JOLTS) when:3d',
    ),
    GoogleNewsLane(
        "google_news_us_inflation",
        '((CPI OR inflation OR "consumer price index" OR PCE) '
        '(BLS OR BEA OR "Federal Reserve")) when:3d',
    ),
    GoogleNewsLane(
        "google_news_fed_rates",
        '(FOMC OR "Federal Reserve" OR "interest rates" OR "Treasury yields") when:3d',
    ),
)
GOOGLE_GEO_URL = GOOGLE_NEWS_LANES[0].url
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


def _published_near_anchor(anchor) -> datetime | None:
    """Read an official listing timestamp without guessing from the headline."""
    node = anchor
    for _ in range(5):
        node = getattr(node, "parent", None)
        if node is None:
            break
        stamped = node.select_one("time[datetime]")
        if stamped is not None:
            parsed = _published(str(stamped.get("datetime") or ""))
            if parsed is not None:
                return parsed
    return None


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


def extract_world_gold_council_article(
    url: str,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> tuple[datetime | None, str, str]:
    """Read WGC's visible publication date and article body in one request."""
    raw = fetcher(url)
    soup = BeautifulSoup(raw, "html.parser")
    published = None
    for paragraph in soup.select("main p, article p, body p"):
        value = " ".join(paragraph.get_text(" ", strip=True).split())
        if not re.fullmatch(r"\d{1,2} [A-Za-z]+, \d{4}", value):
            continue
        try:
            published = datetime.strptime(value, "%d %B, %Y").replace(tzinfo=UTC)
        except ValueError:
            continue
        break
    text, source_url = extract_article_full_text(url, fetcher=lambda _: raw)
    return published, text, source_url


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
    """Collect bounded official FRED snapshots with first-seen revisions."""
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
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    for series in FRED_SERIES:
        try:
            if api_key:
                url = FRED_API_URL + "?" + urllib.parse.urlencode({
                    "series_id": series.series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": start,
                    "sort_order": "desc",
                    "limit": 2,
                })
                provenance_url = FRED_API_URL
            else:
                url = (
                    "https://fred.stlouisfed.org/graph/fredgraph.csv?"
                    + urllib.parse.urlencode({"id": series.series_id, "cosd": start})
                )
                provenance_url = url
            raw = fetcher(url)
            hashes.append(hashlib.sha256(raw).hexdigest())
            if api_key:
                envelope = json.loads(raw)
                rows = [
                    (str(row["date"]), float(row["value"]))
                    for row in reversed(envelope.get("observations", []))
                    if str(row.get("value") or "").strip() not in {"", "."}
                ]
            else:
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
                    "retrieved_from": provenance_url,
                    "transport": "FRED_JSON_API" if api_key else "FRED_GRAPH_CSV",
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
            errors.append(
                f"{series.series_id}:{type(error).__name__}:"
                f"{_redacted_error(error, api_key, limit=120)}"
            )
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
        "registered": bool(api_key),
    }


def collect_eia_macro(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict[str, object]:
    """Collect bounded official EIA WTI observations as Forward evidence."""
    api_key = os.environ.get("EIA_API_KEY", "").strip()
    if not api_key:
        return {"source": EIA_API_SOURCE, "status": "DISABLED_KEY_MISSING"}
    last_poll = ledger.latest_source_poll_time(EIA_API_SOURCE)
    if last_poll is not None and fetched_at - last_poll < timedelta(hours=1):
        return {"source": EIA_API_SOURCE, "status": "SKIPPED_INTERVAL"}
    poll_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"{EIA_API_SOURCE}|{fetched_at.isoformat()}"
    ))
    try:
        url = EIA_API_URL + "?" + urllib.parse.urlencode({
            "api_key": api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": "RWTC",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": 2,
        })
        raw = fetcher(url)
        envelope = json.loads(raw)
        rows = list(reversed(envelope.get("response", {}).get("data", [])))
        if not rows:
            raise ValueError("EIA RWTC returned no observations")
        inserted = 0
        unchanged = 0
        for row in rows:
            period = str(row.get("period") or "").strip()
            value = str(row.get("value") or "").strip()
            if not period or not value or value == ".":
                continue
            stored = {
                "series_id": EIA_WTI_SERIES_ID,
                "eia_series": "RWTC",
                "title": "Cushing, OK WTI Spot Price FOB",
                "observation_period": period,
                "value": float(value),
                "retrieved_from": EIA_API_URL,
                "transport": "EIA_JSON_API_V2",
            }
            digest = hashlib.sha256(
                json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            _, created = ledger.append_macro_observation({
                "source": EIA_API_SOURCE,
                "series_id": EIA_WTI_SERIES_ID,
                "observation_period": period,
                "collector_first_seen_time": fetched_at,
                "fetched_time": fetched_at,
                "value": float(value),
                "unit": "USD/barrel",
                "payload": stored,
                "content_hash": digest,
            })
            inserted += int(created)
            unchanged += int(not created)
        if not inserted and not unchanged:
            raise ValueError("EIA RWTC returned no finite observations")
        ledger.append_source_poll({
            "poll_id": poll_id,
            "source": EIA_API_SOURCE,
            "fetched_time": fetched_at,
            "status": "OK",
            "payload_hash": hashlib.sha256(raw).hexdigest(),
        })
        return {
            "source": EIA_API_SOURCE,
            "status": "OK",
            "inserted_revisions": inserted,
            "unchanged_items": unchanged,
            "registered": True,
        }
    except Exception as error:
        rate_limited = getattr(error, "code", None) == 429
        safe_error = _redacted_error(error, api_key)
        ledger.append_source_poll({
            "poll_id": poll_id,
            "source": EIA_API_SOURCE,
            "fetched_time": fetched_at,
            "status": "ERROR",
            "error_type": "RateLimited" if rate_limited else type(error).__name__,
            "error": safe_error,
        })
        return {
            "source": EIA_API_SOURCE,
            "status": "ERROR",
            "error_type": "RateLimited" if rate_limited else type(error).__name__,
            "error": safe_error,
            "registered": True,
        }


def collect_bea_macro(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict[str, object]:
    """Collect bounded official BEA NIPA observations as Forward evidence."""
    api_key = os.environ.get("BEA_API_KEY", "").strip()
    if not api_key:
        return {"source": BEA_API_SOURCE, "status": "DISABLED_KEY_MISSING"}
    last_poll = ledger.latest_source_poll_time(BEA_API_SOURCE)
    if last_poll is not None and fetched_at - last_poll < timedelta(hours=1):
        return {"source": BEA_API_SOURCE, "status": "SKIPPED_INTERVAL"}
    poll_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"{BEA_API_SOURCE}|{fetched_at.isoformat()}"
    ))
    inserted = 0
    unchanged = 0
    hashes: list[str] = []
    errors: list[str] = []
    years = f"{fetched_at.year - 1},{fetched_at.year}"
    by_table: dict[str, list[BeaSeries]] = {}
    for series in BEA_SERIES:
        by_table.setdefault(series.table_name, []).append(series)
    for table_name, configured_series in by_table.items():
        try:
            url = BEA_API_URL + "?" + urllib.parse.urlencode({
                "UserID": api_key,
                "method": "GetData",
                "DatasetName": "NIPA",
                "TableName": table_name,
                "Frequency": "Q",
                "Year": years,
                "ResultFormat": "JSON",
            })
            raw = fetcher(url)
            hashes.append(hashlib.sha256(raw).hexdigest())
            envelope = json.loads(raw)
            results = envelope.get("BEAAPI", {}).get("Results", {})
            error = results.get("Error") or {}
            if str(error.get("APIErrorCode") or "").strip():
                raise ValueError(
                    f"BEA {error.get('APIErrorCode')}: "
                    f"{error.get('APIErrorDescription')}"
                )
            rows = results.get("Data") or []
            for series in configured_series:
                selected = sorted(
                    (
                        row for row in rows
                        if str(row.get("LineNumber") or "") == series.line_number
                    ),
                    key=lambda row: str(row.get("TimePeriod") or ""),
                )[-2:]
                if not selected:
                    raise ValueError(
                        f"{table_name} line {series.line_number} returned no observations"
                    )
                for row in selected:
                    period = str(row.get("TimePeriod") or "").strip()
                    raw_value = str(row.get("DataValue") or "").replace(",", "").strip()
                    if not period or not raw_value or raw_value == "---":
                        continue
                    stored = {
                        "series_id": series.series_id,
                        "bea_dataset": "NIPA",
                        "bea_table": table_name,
                        "bea_line": series.line_number,
                        "title": series.title,
                        "observation_period": period,
                        "value": float(raw_value),
                        "retrieved_from": BEA_API_URL,
                        "transport": "BEA_JSON_API",
                    }
                    digest = hashlib.sha256(
                        json.dumps(
                            stored, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    _, created = ledger.append_macro_observation({
                        "source": BEA_API_SOURCE,
                        "series_id": series.series_id,
                        "observation_period": period,
                        "collector_first_seen_time": fetched_at,
                        "fetched_time": fetched_at,
                        "value": float(raw_value),
                        "unit": series.unit,
                        "payload": stored,
                        "content_hash": digest,
                    })
                    inserted += int(created)
                    unchanged += int(not created)
        except Exception as error:
            errors.append(
                f"{table_name}:{type(error).__name__}:"
                f"{_redacted_error(error, api_key, limit=180)}"
            )
    status = "OK" if not errors else ("PARTIAL" if inserted or unchanged else "ERROR")
    ledger.append_source_poll({
        "poll_id": poll_id,
        "source": BEA_API_SOURCE,
        "fetched_time": fetched_at,
        "status": status,
        "payload_hash": hashlib.sha256("".join(hashes).encode()).hexdigest()
        if hashes else None,
        "error_type": "TableErrors" if errors else None,
        "error": " | ".join(errors)[:500] if errors else None,
    })
    return {
        "source": BEA_API_SOURCE,
        "status": status,
        "inserted_revisions": inserted,
        "unchanged_items": unchanged,
        "errors": errors,
        "registered": True,
    }


def collect_gdelt_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[str], bytes] = fetch_url,
    content_extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
) -> dict[str, object]:
    """Collect bounded gold candidates from GDELT's official 15-minute GKG feed."""
    last_poll = ledger.latest_source_poll_time(GDELT_SOURCE)
    if last_poll is not None and fetched_at < last_poll + timedelta(hours=1):
        return {
            "source": GDELT_SOURCE,
            "status": "SKIPPED_INTERVAL",
        }
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{GDELT_SOURCE}|{fetched_at.isoformat()}"))
    try:
        manifest = fetcher(GDELT_LAST_UPDATE_URL).decode("ascii")
        manifest_rows = [line.split(maxsplit=2) for line in manifest.splitlines()]
        gkg = next(
            parts
            for parts in manifest_rows
            if len(parts) == 3 and parts[2].endswith(".gkg.csv.zip")
        )
        expected_size = int(gkg[0])
        expected_md5 = gkg[1].lower()
        archive_name = urllib.parse.urlparse(gkg[2]).path.rsplit("/", 1)[-1]
        if not archive_name or expected_size > GDELT_MAX_COMPRESSED_BYTES:
            raise ValueError("GDELT compressed archive exceeds the safety limit")
        archive_url = GDELT_GCS_PREFIX + urllib.parse.quote(archive_name)
        raw = fetcher(archive_url)
        if len(raw) != expected_size:
            raise ValueError("GDELT archive size does not match the manifest")
        if hashlib.md5(raw, usedforsecurity=False).hexdigest().lower() != expected_md5:
            raise ValueError("GDELT archive MD5 does not match the manifest")

        with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
            members = [item for item in zipped.infolist() if not item.is_dir()]
            if len(members) != 1 or members[0].file_size > GDELT_MAX_UNCOMPRESSED_BYTES:
                raise ValueError("GDELT archive has an unsafe ZIP layout")
            payload = zipped.read(members[0])

        inserted = 0
        unchanged = 0
        rejected: dict[str, int] = {}
        discovered = 0
        rows = csv.reader(
            io.StringIO(payload.decode("utf-8", errors="replace")), delimiter="\t"
        )
        for fields in rows:
            if len(fields) < 27:
                rejected["MALFORMED_GKG_ROW"] = (
                    rejected.get("MALFORMED_GKG_ROW", 0) + 1
                )
                continue
            link = fields[4].strip()
            extras = fields[26]
            title_match = re.search(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", extras, re.DOTALL)
            headline = _clean(title_match.group(1) if title_match else "")
            if not link or not headline:
                continue
            discovery_text = " ".join((headline, fields[7], fields[8])).lower()
            if not re.search(
                r"(?:^|[^a-z])(gold|bullion|xauusd)(?:$|[^a-z])", discovery_text
            ):
                continue
            if discovered >= GDELT_MAX_CANDIDATES:
                break
            discovered += 1
            precise_match = re.search(
                r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>",
                extras,
            )
            timestamp = precise_match.group(1) if precise_match else fields[1]
            try:
                published = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                published = None
            record = {
                "source": GDELT_SOURCE,
                "source_item_id": link,
                "source_published_time": published,
                "collector_first_seen_time": fetched_at,
                "fetched_time": fetched_at,
                "headline": headline,
                "body": "",
                "link": link,
                "content_hash": "",
                "cluster_id": hashlib.sha256(headline.lower().encode()).hexdigest(),
            }
            eligible, reason = _current_forward_news(record, ledger, fetched_at)
            if not eligible:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            created, reason = _append_after_full_text(ledger, record, content_extractor)
            inserted += int(created)
            unchanged += int(reason == "UNCHANGED_FULL_TEXT")
            if reason not in {"INSERTED", "UNCHANGED_FULL_TEXT"}:
                rejected[reason] = rejected.get(reason, 0) + 1
        ledger.append_source_poll({"poll_id": poll_id, "source": GDELT_SOURCE, "fetched_time": fetched_at, "status": "OK", "payload_hash": hashlib.sha256(raw).hexdigest()})
        return {"source": GDELT_SOURCE, "status": "OK", "inserted_revisions": inserted,
                "unchanged_items": unchanged, "discovered_candidates": discovered,
                "archive": archive_name, "rejected_reasons": rejected}
    except Exception as error:
        message = str(error)[:500]
        ledger.append_source_poll({"poll_id": poll_id, "source": GDELT_SOURCE, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": message})
        return {
            "source": GDELT_SOURCE,
            "status": "ERROR",
            "error_type": type(error).__name__,
            "error": message,
            "fallback_source": GOOGLE_GEO_SOURCE,
        }


def collect_direct_full_text_rss_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    content_extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
) -> list[dict[str, object]]:
    """Collect bounded official feeds only after publisher text is available."""
    statuses: list[dict[str, object]] = []
    for source in DIRECT_FULL_TEXT_RSS_SOURCES:
        last_poll = ledger.latest_source_poll_time(source.name)
        recent_polls = ledger.connection.execute(
            """SELECT fetched_time,status,error FROM source_polls
            WHERE source=? ORDER BY fetched_time DESC,poll_id DESC LIMIT 3""",
            (source.name,),
        ).fetchall()
        forbidden_streak = 0
        for row in recent_polls:
            if row["status"] == "ERROR" and "403" in str(row["error"] or ""):
                forbidden_streak += 1
            else:
                break
        circuit_retry_at = (
            last_poll + timedelta(hours=6)
            if source.name.startswith("bls_") and forbidden_streak >= 3 and last_poll
            else None
        )
        if circuit_retry_at is not None and fetched_at < circuit_retry_at:
            statuses.append({
                "source": source.name,
                "status": "SKIPPED_CIRCUIT_OPEN",
                "failure_streak": forbidden_streak,
                "retry_at": circuit_retry_at.isoformat(),
                "fallback_source": BLS_SOURCE,
            })
            continue
        interval = timedelta(minutes=5 if source.name.startswith("bls_") else 10)
        if last_poll is not None and fetched_at - last_poll < interval:
            statuses.append({"source": source.name, "status": "SKIPPED_INTERVAL"})
            continue
        poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.name}|{fetched_at.isoformat()}"))
        try:
            raw = fetcher(source)
            inserted = 0
            unchanged = 0
            rejected: dict[str, int] = {}
            candidates = parse_rss(raw, source, fetched_at)
            eligible_records = []
            for record in candidates:
                eligible, reason = _current_forward_news(record, ledger, fetched_at)
                if eligible:
                    eligible_records.append(record)
                else:
                    rejected[reason] = rejected.get(reason, 0) + 1
            limit = 12 if source.name.startswith("bls_") else 5
            full_text_attempts = 0
            for record in eligible_records:
                if _has_stored_full_text(
                    ledger, str(record["source"]), str(record["source_item_id"])
                ):
                    unchanged += 1
                    continue
                if full_text_attempts >= limit:
                    continue
                full_text_attempts += 1
                created, reason = _append_after_full_text(
                    ledger, record, content_extractor
                )
                inserted += int(created)
                unchanged += int(reason == "UNCHANGED_FULL_TEXT")
                if reason not in {"INSERTED", "UNCHANGED_FULL_TEXT"}:
                    rejected[reason] = rejected.get(reason, 0) + 1
            content_blocked = bool(rejected.get("FULL_TEXT_UNAVAILABLE"))
            poll_status = "PARTIAL" if content_blocked else "OK"
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": poll_status,
                    "payload_hash": hashlib.sha256(raw).hexdigest(),
                    "error_type": "PublisherContentUnavailable" if content_blocked else None,
                    "error": (
                        "Eligible official release found, but publisher full text was unavailable"
                        if content_blocked else None
                    ),
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": poll_status,
                    "candidate_items": len(candidates),
                    "eligible_items": len(eligible_records),
                    "full_text_attempt_limit": limit,
                    "full_text_attempts": full_text_attempts,
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                    "rejected_reasons": rejected,
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
    content_extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
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
                if link in seen:
                    continue
                seen.add(link)
                body = f"Official {source.name} listing discovery"
                records.append(
                    {
                        "source": source.name,
                        "source_item_id": link,
                        "source_published_time": _published_near_anchor(anchor),
                        "collector_first_seen_time": fetched_at,
                        "fetched_time": fetched_at,
                        "headline": headline,
                        "body": body,
                        "link": link,
                        "content_hash": "",
                        "cluster_id": hashlib.sha256(
                            re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip().encode()
                        ).hexdigest(),
                    }
                )
                published = records[-1]["source_published_time"]
                records[-1]["content_hash"] = hashlib.sha256(
                    f"{headline}\n{body}\n{link}\n{published}".encode()
                ).hexdigest()
            inserted = 0
            unchanged = 0
            preserved_full_text = 0
            rejected: dict[str, int] = {}
            eligible_items = 0
            full_text_attempts = 0
            for record in records:
                if _has_stored_full_text(
                    ledger, str(record["source"]), str(record["source_item_id"])
                ):
                    # A listing page proves that the article still exists; it
                    # does not supersede an already captured article body.
                    unchanged += 1
                    preserved_full_text += 1
                    continue
                eligible, reason = _current_forward_news(record, ledger, fetched_at)
                if not eligible:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                eligible_items += 1
                if full_text_attempts >= 5:
                    continue
                full_text_attempts += 1
                created, reason = _append_after_full_text(
                    ledger, record, content_extractor
                )
                inserted += int(created)
                unchanged += int(reason == "UNCHANGED_FULL_TEXT")
                if reason not in {"INSERTED", "UNCHANGED_FULL_TEXT"}:
                    rejected[reason] = rejected.get(reason, 0) + 1
            if not records:
                raise ValueError(f"{source.name} returned no direct article links")
            content_blocked = bool(rejected.get("FULL_TEXT_UNAVAILABLE"))
            poll_status = "PARTIAL" if content_blocked else "OK"
            ledger.append_source_poll(
                {
                    "poll_id": poll_id,
                    "source": source.name,
                    "fetched_time": fetched_at,
                    "status": poll_status,
                    "payload_hash": hashlib.sha256(raw).hexdigest(),
                    "error_type": "PublisherContentUnavailable" if content_blocked else None,
                    "error": (
                        "Eligible official release found, but publisher full text was unavailable"
                        if content_blocked else None
                    ),
                }
            )
            statuses.append(
                {
                    "source": source.name,
                    "status": poll_status,
                    "candidate_items": len(records),
                    "eligible_items": eligible_items,
                    "full_text_attempt_limit": 5,
                    "full_text_attempts": full_text_attempts,
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                    "preserved_full_text": preserved_full_text,
                    "rejected_reasons": rejected,
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
    article_loader: Callable[
        [str], tuple[datetime | None, str, str]
    ] = extract_world_gold_council_article,
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
        rejected: dict[str, int] = {}
        article_errors: list[str] = []
        seen: set[str] = set()
        for path, raw_title in matches:
            if path in seen or len(seen) >= 8:
                continue
            seen.add(path)
            headline = _clean(raw_title)
            if not headline or headline.lower().startswith("read more"):
                continue
            link = urllib.parse.urljoin(WGC_URL, path)
            try:
                published, article_text, source_url = article_loader(link)
            except Exception as error:
                rejected["ARTICLE_LOAD_ERROR"] = rejected.get("ARTICLE_LOAD_ERROR", 0) + 1
                article_errors.append(f"{link}:{type(error).__name__}:{str(error)[:120]}")
                continue
            record = {
                "source": WGC_SOURCE,
                "source_item_id": link,
                "source_published_time": published,
                "collector_first_seen_time": fetched_at,
                "fetched_time": fetched_at,
                "headline": headline,
                "body": "",
                "link": link,
                "content_hash": "",
                "cluster_id": hashlib.sha256(headline.lower().encode()).hexdigest(),
            }
            eligible, reason = _current_forward_news(record, ledger, fetched_at)
            if not eligible:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            created, reason = _append_after_full_text(
                ledger,
                record,
                lambda _: (article_text, source_url),
            )
            inserted += int(created)
            unchanged += int(reason == "UNCHANGED_FULL_TEXT")
            if reason not in {"INSERTED", "UNCHANGED_FULL_TEXT"}:
                rejected[reason] = rejected.get(reason, 0) + 1
        if not seen:
            raise ValueError("World Gold Council page returned no central-bank links")
        technical_rejections = sum(
            rejected.get(reason, 0)
            for reason in (
                "PUBLISHED_TIME_MISSING", "ARTICLE_LOAD_ERROR",
                "FULL_TEXT_UNAVAILABLE", "SOURCE_URL_MISSING",
            )
        )
        status = (
            "PARTIAL" if technical_rejections and inserted + unchanged
            else "ERROR" if technical_rejections
            else "OK"
        )
        error = " | ".join(article_errors)[:500] if article_errors else (
            json.dumps(rejected, sort_keys=True)[:500] if technical_rejections else None
        )
        ledger.append_source_poll({
            "poll_id": poll_id,
            "source": WGC_SOURCE,
            "fetched_time": fetched_at,
            "status": status,
            "payload_hash": hashlib.sha256(raw).hexdigest(),
            "error_type": "WgcArticleIngestionError" if technical_rejections else None,
            "error": error,
        })
        return {"source": WGC_SOURCE, "status": status, "inserted_revisions": inserted,
                "unchanged_items": unchanged, "rejected_reasons": rejected}
    except Exception as error:
        ledger.append_source_poll({"poll_id": poll_id, "source": WGC_SOURCE, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]})
        return {"source": WGC_SOURCE, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]}


def collect_google_geopolitical_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    decoder: Callable[[str], str] = decode_google_news_publisher_url,
    content_extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
) -> dict[str, object]:
    """Compatibility wrapper for the original broad gold-context lane."""
    return collect_google_news_lane(
        ledger, fetched_at, GOOGLE_NEWS_LANES[0], fetcher=fetcher, decoder=decoder,
        content_extractor=content_extractor,
    )


def collect_google_news_lane(
    ledger: ForwardLedger,
    fetched_at: datetime,
    lane: GoogleNewsLane,
    *,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    decoder: Callable[[str], str] = decode_google_news_publisher_url,
    content_extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
    limit: int = 10,
) -> dict[str, object]:
    """Collect a bounded lane only after publisher full text is available.

    Google often returns old popular articles first.  Capping before dedupe made
    a stable top ten permanently hide fresh releases farther down the feed.
    Headline-only search candidates are not durable evidence and are therefore
    never appended to the immutable news ledger.
    """
    last_poll = ledger.latest_source_poll_time(lane.name)
    if last_poll is not None and fetched_at - last_poll < timedelta(minutes=20):
        return {"source": lane.name, "status": "SKIPPED_INTERVAL"}
    source = RssSource(lane.name, lane.url)
    poll_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lane.name}|{fetched_at.isoformat()}"))
    try:
        raw = fetcher(source)
        inserted = 0
        unchanged = 0
        records = parse_rss(raw, source, fetched_at)
        rejected = {}
        admitted = []
        for record in records:
            allowed, reason = google_news_item_is_relevant(
                lane.name,
                str(record.get("headline") or ""),
                record.get("source_published_time"),
                fetched_at,
            )
            if allowed:
                admitted.append(record)
            else:
                rejected[reason] = rejected.get(reason, 0) + 1
        records = admitted
        deduped: dict[str, dict] = {}
        for record in records:
            identity = str(record.get("cluster_id") or record["source_item_id"])
            deduped.setdefault(identity, record)
        ranked = sorted(
            deduped.values(),
            key=lambda record: (
                ledger.connection.execute(
                    "SELECT 1 FROM news_revisions WHERE source=? AND source_item_id=? LIMIT 1",
                    (record["source"], record["source_item_id"]),
                ).fetchone() is not None,
                google_news_quality_rank(str(record.get("headline") or "")),
                -(record["source_published_time"].timestamp()
                  if record.get("source_published_time") else 0.0),
                str(record["source_item_id"]),
            ),
        )
        # ``cluster_id``/``source_item_id`` above are the single mechanical
        # deduplication boundary. Event meaning belongs to the AI annotator.
        selected = ranked[:limit]
        full_text_records = []
        for record in selected:
            existing = ledger.connection.execute(
                """SELECT link, body FROM news_revisions
                WHERE source=? AND source_item_id=?
                ORDER BY revision_number DESC LIMIT 1""",
                (record["source"], record["source_item_id"]),
            ).fetchone()
            existing_link = str(existing["link"] or "") if existing else ""
            existing_body = str(existing["body"] or "") if existing else ""
            if existing_body.startswith("[FULL_TEXT"):
                unchanged += 1
                full_text_records.append(record)
                continue
            if existing_link and urllib.parse.urlparse(existing_link).hostname != "news.google.com":
                resolved = existing_link
            else:
                resolved = decoder(record["link"])
            if urllib.parse.urlparse(resolved).hostname == "news.google.com":
                rejected["PUBLISHER_URL_UNRESOLVED"] = rejected.get(
                    "PUBLISHER_URL_UNRESOLVED", 0
                ) + 1
                continue
            try:
                text, source_url = content_extractor(resolved)
            except Exception:
                rejected["FULL_TEXT_UNAVAILABLE"] = rejected.get(
                    "FULL_TEXT_UNAVAILABLE", 0
                ) + 1
                continue
            record["link"] = source_url
            record["body"] = f"[FULL_TEXT source={source_url} chars={len(text)}]\n{text}"
            record["content_hash"] = hashlib.sha256(
                f"{record['headline']}\n{record['body']}\n{source_url}".encode()
            ).hexdigest()
            _, created = ledger.append_news_revision(record)
            inserted += int(created)
            unchanged += int(not created)
            full_text_records.append(record)
        content_blocked = (
            bool(selected)
            and not full_text_records
            and any(rejected.get(reason, 0) for reason in (
                "PUBLISHER_URL_UNRESOLVED", "FULL_TEXT_UNAVAILABLE",
            ))
        )
        poll_status = "PARTIAL" if content_blocked else "OK"
        ledger.append_source_poll({
            "poll_id": poll_id, "source": lane.name, "fetched_time": fetched_at,
            "status": poll_status,
            "payload_hash": hashlib.sha256(raw).hexdigest(),
            "error_type": "PublisherContentUnavailable" if content_blocked else None,
            "error": (
                "Relevant search result found, but publisher full text was unavailable"
                if content_blocked else None
            ),
        })
        return {
            "source": lane.name,
            "status": poll_status,
            "feed_items": len(records),
            "deduped_items": len(deduped),
            "attempted_items": len(selected),
            "processed_items": len(full_text_records),
            "rejected_items": sum(rejected.values()),
            "rejected_reasons": rejected,
            "inserted_revisions": inserted,
            "unchanged_items": unchanged,
        }
    except Exception as error:
        ledger.append_source_poll({"poll_id": poll_id, "source": lane.name, "fetched_time": fetched_at, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]})
        return {"source": lane.name, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]}


def collect_bls_macro(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[datetime], bytes] = fetch_bls_api,
    *,
    force: bool = False,
) -> dict[str, object]:
    key_configured = bool(os.environ.get("BLS_API_KEY", "").strip())
    interval = timedelta(minutes=5 if key_configured else 65)
    last_poll = ledger.latest_source_poll_time(BLS_SOURCE)
    if not force and last_poll is not None and fetched_at - last_poll < interval:
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
            title, unit = BLS_SERIES[series_id]
            for index, point in enumerate(points[:3]):
                period = f"{point['year']}-{point['period']}"
                previous = points[index + 1] if index + 1 < len(points) else None
                prior_revision = ledger.connection.execute(
                    """SELECT value FROM macro_observations
                       WHERE source=? AND series_id=? AND observation_period=?
                       ORDER BY revision_number DESC LIMIT 1""",
                    (BLS_SOURCE, series_id, period),
                ).fetchone()
                stored_payload = {
                    "series_id": series_id,
                    "title": title,
                    "year": point["year"],
                    "period": point["period"],
                    "period_name": point.get("periodName"),
                    "value": point["value"],
                    "previous_period_value": previous.get("value") if previous else None,
                    "revision_from_last_seen": (
                        float(point["value"]) - float(prior_revision["value"])
                        if prior_revision is not None else None
                    ),
                    "consensus": None,
                    "surprise": None,
                    "consensus_status": "UNAVAILABLE_FREE_POINT_IN_TIME",
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


def collect_federal_reserve_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    fed_content_extractor: Callable[[str], tuple[str, str]] = extract_federal_reserve_full_text,
) -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    payload_hashes: list[str] = []
    errors: list[str] = []
    for source in OFFICIAL_RSS_SOURCES:
        inserted = 0
        unchanged = 0
        rejected: dict[str, int] = {}
        try:
            payload = fetcher(source)
            payload_hashes.append(hashlib.sha256(payload).hexdigest())
            for record in parse_rss(payload, source, fetched_at):
                eligible, reason = _current_forward_news(record, ledger, fetched_at)
                if not eligible:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                created, reason = _append_after_full_text(
                    ledger, record, fed_content_extractor
                )
                inserted += int(created)
                unchanged += int(reason == "UNCHANGED_FULL_TEXT")
                if reason not in {"INSERTED", "UNCHANGED_FULL_TEXT"}:
                    rejected[reason] = rejected.get(reason, 0) + 1
            statuses.append(
                {
                    "source": source.name,
                    "status": "OK",
                    "inserted_revisions": inserted,
                    "unchanged_items": unchanged,
                    "rejected_reasons": rejected,
                }
            )
            if rejected.get("FULL_TEXT_UNAVAILABLE"):
                errors.append(
                    f"{source.name}:FullTextUnavailable:"
                    f"{rejected['FULL_TEXT_UNAVAILABLE']}"
                )
        except Exception as error:  # source failure must not hide other feeds
            errors.append(f"{source.name}:{type(error).__name__}:{str(error)[:160]}")
            statuses.append(
                {
                    "source": source.name,
                    "status": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    aggregate_status = (
        "OK" if not errors else "PARTIAL" if len(errors) < len(OFFICIAL_RSS_SOURCES)
        else "ERROR"
    )
    ledger.append_source_poll({
        "poll_id": str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"federal_reserve_full_text|{fetched_at.isoformat()}"
        )),
        "source": "federal_reserve_full_text",
        "fetched_time": fetched_at,
        "status": aggregate_status,
        "payload_hash": hashlib.sha256("|".join(payload_hashes).encode()).hexdigest()
        if payload_hashes else None,
        "error_type": "FeedErrors" if errors else None,
        "error": " | ".join(errors)[:500] if errors else None,
    })
    return statuses


def collect_official_news(
    ledger: ForwardLedger,
    fetched_at: datetime,
    fetcher: Callable[[RssSource], bytes] = fetch_rss,
    fed_content_extractor: Callable[[str], tuple[str, str]] = extract_federal_reserve_full_text,
) -> list[dict[str, object]]:
    statuses = collect_federal_reserve_news(
        ledger, fetched_at, fetcher, fed_content_extractor
    )
    direct_rss = collect_direct_full_text_rss_news(ledger, fetched_at, fetcher)
    statuses.extend(direct_rss)
    force_bls = any(
        str(item.get("source", "")).startswith("bls_")
        and int(item.get("inserted_revisions", 0)) > 0
        for item in direct_rss
    )
    statuses.append(collect_bls_macro(ledger, fetched_at, force=force_bls))
    statuses.append(collect_fred_macro(ledger, fetched_at))
    statuses.append(collect_eia_macro(ledger, fetched_at))
    statuses.append(collect_bea_macro(ledger, fetched_at))
    statuses.extend(collect_direct_full_text_html_news(ledger, fetched_at))
    statuses.append(collect_gdelt_news(ledger, fetched_at))
    for lane in GOOGLE_NEWS_LANES:
        statuses.append(collect_google_news_lane(ledger, fetched_at, lane))
    statuses.append(collect_world_gold_council_news(ledger, fetched_at))
    return statuses
