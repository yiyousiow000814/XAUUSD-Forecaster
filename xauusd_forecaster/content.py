"""Full-text hydration for auditable official news revisions."""

from __future__ import annotations

import hashlib
import io
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .forward_ledger import ForwardLedger


USER_AGENT = "XAUUSD-Forward-Evidence/0.1 (+local research collector)"
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
)


def fetch_content(url: str, timeout_seconds: float = 12.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _clean_text(node) -> str:
    for unwanted in node.select("script,style,noscript,nav,form,button,.share,footer"):
        unwanted.decompose()
    return "\n".join(
        line.strip() for line in node.get_text("\n", strip=True).splitlines()
        if line.strip()
    )


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
            attachment_soup = BeautifulSoup(attachment, "html.parser")
            attachment_content = attachment_soup.select_one("#content") or attachment_soup.body
            if attachment_content is not None:
                text = _clean_text(attachment_content)
    if not text:
        text = _clean_text(content)
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
    soup = BeautifulSoup(raw, "html.parser")
    candidates = [
        soup.select_one(selector)
        for selector in (
            ".field--name-field-news-body",
            "article",
            "#story-body",
            "[itemprop='articleBody']",
            ".article-body",
            ".story-body",
            ".entry-content",
            ".post-content",
            ".field--name-body",
            "main",
        )
    ]
    candidates = [node for node in candidates if node is not None]
    if not candidates and soup.body is not None:
        candidates.append(soup.body)
    if not candidates:
        raise ValueError("article page has no usable content container")
    text = max((_clean_text(node).strip() for node in candidates), key=len)
    if len(text) < 500:
        raise ValueError(f"article extraction produced only {len(text)} characters")
    return text, url


def hydrate_pending_non_fed_content(
    ledger: ForwardLedger,
    fetched_at: datetime,
    *,
    limit: int = 16,
    extractor: Callable[[str], tuple[str, str]] = extract_article_full_text,
) -> dict[str, object]:
    poll_source = "non_fed_full_text"
    last_poll = ledger.latest_source_poll_time(poll_source)
    if last_poll is not None and (fetched_at - last_poll).total_seconds() < 5 * 60:
        return {"source": poll_source, "status": "SKIPPED_INTERVAL"}
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
              AND (
                n.body NOT LIKE '[FULL_TEXT%'
                OR (n.source='us_treasury_press_releases'
                    AND n.body LIKE '%About Treasury%General Information%')
              )
            ORDER BY CASE
                       WHEN n.source IN ('world_gold_council_central_banks',
                                         'eia_today_in_energy',
                                         'eia_press_releases',
                                         'ecb_press_releases',
                                         'us_treasury_press_releases',
                                         'bea_economic_releases') THEN 0
                       WHEN n.source='google_news_gold_context' THEN 1
                       ELSE 2
                     END,
                     COALESCE(n.source_published_time,
                              n.collector_first_seen_time) DESC
            LIMIT ?""",
        (*NON_FED_FULL_TEXT_SOURCES, limit),
    ).fetchall()
    inserted = 0
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, len(rows) or 1)) as pool:
        extracted = list(pool.map(lambda row: _extract_safely(extractor, row["link"]), rows))
    for row, result in zip(rows, extracted):
        if isinstance(result, Exception):
            errors.append(
                f"{row['source_item_id']}:{type(result).__name__}:{str(result)[:160]}"
            )
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
    status = "OK" if not errors else ("PARTIAL" if inserted else "ERROR")
    ledger.append_source_poll(
        {
            "poll_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{poll_source}|{fetched_at.isoformat()}")),
            "source": poll_source,
            "fetched_time": fetched_at,
            "status": status,
            "error_type": "HydrationErrors" if errors else None,
            "error": " | ".join(errors)[:500] if errors else None,
        }
    )
    return {
        "source": poll_source,
        "status": status,
        "inserted_revisions": inserted,
        "errors": errors,
    }


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
