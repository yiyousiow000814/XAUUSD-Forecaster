"""Collapse article copies in the bounded reader projection, not model facts."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from xauusd_forecaster.news.semantics.article_source import readable_source_text


def _text(value: str) -> str:
    return " ".join(value.split())


def _family(row: dict) -> tuple[str, str]:
    title = str(row.get("source_article_title") or row.get("original_headline") or row.get("headline") or "")
    # Google RSS appends the publisher to its discovery headline. This key
    # only selects possible peers; content must independently agree below.
    if not row.get("source_article_title") and str(row.get("source", "")).startswith("google_news_"):
        title = title.rsplit(" - ", 1)[0]
    day = str(row.get("source_published_time") or "")[:10]
    return _text(title).casefold(), day


def group_article_copies(rows: list[dict]) -> list[dict]:
    """Compare bounded local bodies once, before remote counts and pagination.

    Exact bodies and excerpts can share a row when every paragraph is preserved
    by one unambiguous complete version. Divergent facts and material updates
    stay apart. Source documents and interpretations are never changed.
    """
    families: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        if row.get("parsed_at"):
            body, incomplete = readable_source_text(str(row.get("body") or ""))
            row["body"] = "\n".join(dict.fromkeys(body.splitlines()))
            if incomplete:
                row["source_text_incomplete"] = True
            row["content_characters"] = len(row["body"])
        families[_family(row)].append(row)
    result = []
    for (title, day), members in families.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in members:
            body = _text(str(row.get("body") or ""))
            # Unparsed pages and empty inputs cannot prove identical articles.
            key = body if title and day and body and row.get("parsed_at") and row.get("impact_update_type") != "MATERIAL_UPDATE" else "\0" + str((row.get("source"), row.get("source_item_id"), row.get("revision_number")))
            groups[key].append(row)
        paragraphs = {key: frozenset(_text(p) for p in peers[0]["body"].splitlines() if _text(p))
                      for key, peers in groups.items() if not key.startswith("\0") and len(key) >= 100}
        containing: dict[str, set[str]] = defaultdict(set)
        for key, parts in paragraphs.items():
            for part in parts:
                containing[part].add(key)
        supersets = {}
        for key, parts in paragraphs.items():
            candidates = set.intersection(*(containing[p] for p in parts))
            supersets[key] = candidates
        # Match directly to maximal texts; do not allow traversal order to
        # choose an interpretation when two different full stories fit a lead.
        maximal = {key for key, candidates in supersets.items() if candidates == {key}}
        for key in sorted(paragraphs):
            matches = supersets[key] & maximal
            if key not in maximal and len(matches) == 1:
                owner = next(iter(matches))
                groups[owner].extend(groups[key])
                groups[key] = []
        for key, copies in groups.items():
            if not copies:
                continue
            # A readable, reviewed copy owns its complete interpretation.
            # Do not blend a title from one interpretation with another's date.
            copies.sort(key=lambda r: (bool(r.get("source_text_incomplete")),
                        not bool(r.get("parsed_at")),
                        -len(str(r.get("body") or "")),
                        str(r.get("collector_first_seen_time") or ""),
                        str(r.get("source")), str(r.get("source_item_id"))))
            representative = dict(copies[0])
            if len(copies) > 1:
                representative["syndicated_sources"] = [{
                    field: r.get(field) for field in (
                        "source", "source_item_id", "revision_number", "link",
                        "source_published_time", "collector_first_seen_time",
                        "content_hash", "annotation_status", "source_text_incomplete",
                    )
                } for r in copies]
                representative["syndicated_source_count"] = len(copies)
                representative["cluster_id"] = "article-" + hashlib.sha256(
                    (title + "\0" + day + "\0" + key).encode()
                ).hexdigest()
            result.append(representative)
    return result
