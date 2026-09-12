"""Full-text hydration for auditable official news revisions."""

from __future__ import annotations

import hashlib
import io
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Callable

from bs4 import BeautifulSoup
from pypdf import PdfReader

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.semantics.article_source import PAGE_TEXT_MARKER
from xauusd_forecaster.news.semantics.relevance import google_news_item_is_relevant
from xauusd_forecaster.news.collection.source_polling import source_poll_gate


USER_AGENT = "XAUUSD-Forward-Evidence/0.1 (+local research collector)"
ARTICLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)
FED_SOURCES = (
    "federal_reserve_press_all",
    "federal_reserve_monetary",
    "federal_reserve_speeches_testimony",
)
NON_FED_FULL_TEXT_SOURCES = (
    "gdelt_gold_geopolitics",
    "world_gold_council_central_banks",
    "eia_today_in_energy",
    "eia_press_releases",
    "ecb_press_releases",
    "us_treasury_press_releases",
    "bea_economic_releases",
    "google_news_gold_context",
    "google_news_gold_geopolitics",
    "google_news_bls_official_releases",
    "google_news_us_employment",
    "google_news_us_inflation",
    "google_news_fed_rates",
    "bls_employment_situation",
    "bls_consumer_price_index",
    "bls_job_openings",
)


def fetch_content(url: str, timeout_seconds: float = 12.0) -> bytes:
    # Publisher pages commonly reject unknown crawler agents even when the same
    # public article is readable in an ordinary browser.  Use a normal document
    # request profile; this does not bypass authentication or paywalls.
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": ARTICLE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def extract_federal_reserve_full_text(
    url: str,
    fetcher: Callable[[str], bytes] = fetch_content,
) -> tuple[str, str]:
    """Prefer accessible HTML, then PDF, then the release page itself."""
    raw = fetcher(url)
    soup = BeautifulSoup(raw, "html.parser")
    content = soup.select_one("#content") or soup.body
    if content is None:
        raise ValueError("Federal Reserve page has no content container")

    candidates: list[tuple[int, str, str]] = []
    for anchor in content.select("a[href]"):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        href = urllib.parse.urljoin(url, anchor.get("href", ""))
        lower = label.lower()
        if lower == "html":
            candidates.append((0, href, "html"))
        elif "accessible materials" in lower:
            candidates.append((1, href, "html"))
        elif "attachment" in lower and "pdf" in lower:
            candidates.append((2, href, "pdf"))

    source_url = url
    text = ""
    if candidates:
        _, source_url, kind = sorted(candidates)[0]
        attachment = fetcher(source_url)
        if kind == "pdf":
            reader = PdfReader(io.BytesIO(attachment))
            text = "\n".join((page.extract_text() or "").strip() for page in reader.pages)
        else:
            text = _page_text(attachment)
    if not text:
        text = _page_text(raw)
    text = text.strip()
    if len(text) < 240:
        raise ValueError(f"full text extraction produced only {len(text)} characters")
    return text, source_url


def extract_article_full_text(
    url: str,
    fetcher: Callable[[str], bytes] = fetch_content,
) -> tuple[str, str]:
    """Extract auditable article text from direct publisher or research pages."""
    raw = fetcher(url)
    if raw.startswith(b"%PDF") or urllib.parse.urlparse(url).path.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join((page.extract_text() or "").strip() for page in reader.pages)
        text = text.strip()
        if len(text) < 500:
            raise ValueError(f"PDF article extraction produced only {len(text)} characters")
        return text, url
    return _page_text(raw), url


def _page_text(raw: bytes) -> str:
    """Keep page text for AI selection; do not guess an article by length."""
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup.select("script,style,noscript"):
        node.decompose()
    container = soup.body or soup
    lines = [line.strip() for text in container.stripped_strings
             for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    if len(text) < 240:
        raise ValueError(f"page extraction produced only {len(text)} characters")
    return PAGE_TEXT_MARKER + text


def hydrate_pending_non_fed_content(
    ledger: ForwardLedger,
    fetched_at: datetime,
    *,
    limit: int = 16,
    extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
) -> dict[str, object]:
    poll_source = "non_fed_full_text"
    gate = source_poll_gate(
        ledger.connection, poll_source,
        observed_at=fetched_at, success_interval=timedelta(minutes=5),
    )
    if gate is not None:
        return gate
    placeholders = ",".join("?" for _ in NON_FED_FULL_TEXT_SOURCES)
    rows = ledger.connection.execute(
        f"""SELECT n.* FROM news_revisions n
            WHERE n.source IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer
                WHERE newer.source=n.source
                  AND newer.source_item_id=n.source_item_id
                  AND newer.revision_number>n.revision_number)
              AND n.link LIKE 'https://%'
              AND n.link NOT LIKE 'https://news.google.com/%'
              AND NOT EXISTS (
                SELECT 1 FROM news_content_failures f
                WHERE f.source=n.source
                  AND f.source_item_id=n.source_item_id
                  AND f.revision_number=n.revision_number
                  AND f.attempt_number=(
                    SELECT max(f2.attempt_number) FROM news_content_failures f2
                    WHERE f2.source=f.source
                      AND f2.source_item_id=f.source_item_id
                      AND f2.revision_number=f.revision_number)
                  AND julianday(COALESCE(f.next_retry_at,
                        datetime(f.failed_at, '+12 hours'))) > julianday(?)
              )
              AND (
                n.body NOT LIKE '[FULL_TEXT%'
                OR (n.source='us_treasury_press_releases'
                    AND n.body LIKE '%About Treasury%General Information%'
                    AND instr(n.body, '[PAGE_TEXT_V1]')=0)
              )
            ORDER BY CASE
                       WHEN n.source IN ('bls_employment_situation',
                                         'bls_consumer_price_index',
                                         'bls_job_openings',
                                         'world_gold_council_central_banks',
                                         'eia_today_in_energy',
                                         'eia_press_releases',
                                         'ecb_press_releases',
                                         'us_treasury_press_releases',
                                         'bea_economic_releases') THEN 0
                       WHEN n.source IN ('google_news_gold_context',
                                         'google_news_bls_official_releases',
                                         'google_news_us_employment',
                                         'google_news_us_inflation',
                                         'google_news_fed_rates') THEN 1
                       ELSE 2
                     END,
                     COALESCE(n.source_published_time,
                              n.collector_first_seen_time) DESC
            LIMIT ?""",
        (
            *NON_FED_FULL_TEXT_SOURCES,
            fetched_at.astimezone(UTC).isoformat(timespec="microseconds"),
            max(limit * 25, 250),
        ),
    ).fetchall()
    admitted_rows = []
    rejected_irrelevant = 0
    for row in rows:
        published_at = (
            datetime.fromisoformat(row["source_published_time"])
            if row["source_published_time"] else None
        )
        allowed, _ = google_news_item_is_relevant(
            str(row["source"]), str(row["headline"] or ""),
            published_at, fetched_at,
        )
        if allowed:
            admitted_rows.append(row)
        else:
            rejected_irrelevant += 1
    rows = admitted_rows[:limit]
    inserted = 0
    errors: list[str] = []
    operational_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, len(rows) or 1)) as pool:
        extracted = list(pool.map(lambda row: _extract_safely(extractor, row["link"]), rows))
    for row, result in zip(rows, extracted):
        if isinstance(result, Exception):
            failure = _append_content_failure(ledger, row, result, fetched_at)
            description = (
                f"{row['source_item_id']}:{type(result).__name__}:{str(result)[:160]}"
            )
            errors.append(description)
            if not failure["source_unavailable"]:
                operational_errors.append(description)
            continue
        try:
            text, source_url = result
            body = f"[FULL_TEXT source={source_url} chars={len(text)}]\n{text}"
            digest = hashlib.sha256(
                f"{row['headline']}\n{body}\n{row['link']}".encode("utf-8")
            ).hexdigest()
            _, created = ledger.append_news_revision(
                {
                    "source": row["source"],
                    "source_item_id": row["source_item_id"],
                    "source_published_time": (
                        datetime.fromisoformat(row["source_published_time"])
                        if row["source_published_time"] else None
                    ),
                    "collector_first_seen_time": fetched_at,
                    "fetched_time": fetched_at,
                    "headline": row["headline"],
                    "body": body,
                    "link": row["link"],
                    "content_hash": digest,
                    "cluster_id": row["cluster_id"],
                }
            )
            inserted += int(created)
        except Exception as error:
            errors.append(
                f"{row['source_item_id']}:{type(error).__name__}:{str(error)[:160]}"
            )
    status = (
        "OK" if not operational_errors
        else ("PARTIAL" if inserted else "ERROR")
    )
    summary = {
        "attempted": len(rows),
        "rejected_irrelevant": rejected_irrelevant,
        "inserted": inserted,
        "unavailable": len(errors) - len(operational_errors),
        "retrying": len(operational_errors),
    }
    ledger.append_source_poll(
        {
            "poll_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{poll_source}|{fetched_at.isoformat()}")),
            "source": poll_source,
            "fetched_time": fetched_at,
            "status": status,
            "payload_hash": hashlib.sha256(
                repr(sorted(summary.items())).encode("utf-8")
            ).hexdigest(),
            "error_type": "HydrationErrors" if operational_errors else None,
            "error": (
                " | ".join(operational_errors)[:500]
                if operational_errors else None
            ),
        }
    )
    return {
        "source": poll_source,
        "status": status,
        "inserted_revisions": inserted,
        "errors": errors,
        **summary,
    }


def _append_content_failure(
    ledger: ForwardLedger,
    row,
    error: Exception,
    failed_at: datetime,
) -> dict[str, object]:
    normalized = re.sub(r"\s+", " ", str(error)).strip()
    error_type = type(error).__name__
    signature = hashlib.sha256(
        f"{error_type}|{normalized}".encode("utf-8")
    ).hexdigest()
    prior = ledger.connection.execute(
        """SELECT attempt_number, error_signature FROM news_content_failures
        WHERE source=? AND source_item_id=? AND revision_number=?
        ORDER BY attempt_number DESC LIMIT 1""",
        (row["source"], row["source_item_id"], row["revision_number"]),
    ).fetchone()
    attempt = 1 if prior is None else int(prior["attempt_number"]) + 1
    next_retry = failed_at + timedelta(
        minutes=(15, 60, 360, 720)[min(attempt - 1, 3)]
    )
    identity = "|".join(
        [
            str(row["source"]), str(row["source_item_id"]),
            str(row["revision_number"]), str(attempt), signature,
        ]
    )
    ledger.append_content_failure(
        {
            "failure_id": str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            "source": row["source"],
            "source_item_id": row["source_item_id"],
            "revision_number": row["revision_number"],
            "raw_content_hash": row["content_hash"],
            "attempt_number": attempt,
            "error_type": error_type,
            "error_signature": signature,
            "error": normalized,
            "failed_at": failed_at,
            "next_retry_at": next_retry,
            "is_terminal": False,
        }
    )
    # An individual inaccessible page must not pause hydration of other publishers.
    # This classifies diagnostics only: every failure above retains a next retry.
    http_code = error.code if isinstance(error, urllib.error.HTTPError) else None
    source_unavailable = (
        http_code in {301, 302, 303, 307, 308, 401, 403, 404, 410, 451}
        or (isinstance(error, ValueError) and (
            "content container" in normalized or "produced only" in normalized
        ))
        or (isinstance(error, urllib.error.URLError)
            and "certificate verify failed" in normalized.casefold())
    )
    return {"is_terminal": False, "next_retry_at": next_retry,
            "source_unavailable": source_unavailable}


def _extract_safely(
    extractor: Callable[[str], tuple[str, str]],
    link: str,
) -> tuple[str, str] | Exception:
    try:
        return extractor(link)
    except Exception as error:
        return error


def hydrate_pending_federal_reserve_content(
    ledger: ForwardLedger,
    fetched_at: datetime,
    *,
    limit: int = 8,
    extractor: Callable[[str], tuple[str, str]] = extract_federal_reserve_full_text,
) -> dict[str, object]:
    placeholders = ",".join("?" for _ in FED_SOURCES)
    rows = ledger.connection.execute(
        f"""SELECT n.* FROM news_revisions n
            WHERE n.source IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer
                WHERE newer.source=n.source
                  AND newer.source_item_id=n.source_item_id
                  AND newer.revision_number>n.revision_number)
              AND n.link LIKE 'https://www.federalreserve.gov/%'
              AND n.body NOT LIKE '[FULL_TEXT%'
            ORDER BY COALESCE(n.source_published_time,
                              n.collector_first_seen_time) DESC
            LIMIT ?""",
        (*FED_SOURCES, limit),
    ).fetchall()
    inserted = 0
    errors: list[str] = []
    for row in rows:
        try:
            text, source_url = extractor(row["link"])
            body = f"[FULL_TEXT source={source_url} chars={len(text)}]\n{text}"
            digest = hashlib.sha256(
                f"{row['headline']}\n{body}\n{row['link']}".encode("utf-8")
            ).hexdigest()
            _, created = ledger.append_news_revision(
                {
                    "source": row["source"],
                    "source_item_id": row["source_item_id"],
                    "source_published_time": (
                        datetime.fromisoformat(row["source_published_time"])
                        if row["source_published_time"] else None
                    ),
                    "collector_first_seen_time": fetched_at,
                    "fetched_time": fetched_at,
                    "headline": row["headline"],
                    "body": body,
                    "link": row["link"],
                    "content_hash": digest,
                    "cluster_id": row["cluster_id"],
                }
            )
            inserted += int(created)
        except Exception as error:
            errors.append(
                f"{row['source_item_id']}:{type(error).__name__}:{str(error)[:160]}"
            )
    ledger.append_source_poll(
        {
            "poll_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"fed_full_text|{fetched_at.isoformat()}")),
            "source": "federal_reserve_full_text",
            "fetched_time": fetched_at,
            "status": "OK" if not errors else ("PARTIAL" if inserted else "ERROR"),
            "error_type": "HydrationErrors" if errors else None,
            "error": " | ".join(errors)[:500] if errors else None,
        }
    )
    return {
        "source": "federal_reserve_full_text",
        "status": "OK" if not errors else ("PARTIAL" if inserted else "ERROR"),
        "inserted_revisions": inserted,
        "errors": errors,
    }
