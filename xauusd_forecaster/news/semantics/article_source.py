"""Original page segments selected within the existing annotation request."""

PAGE_TEXT_MARKER = "[PAGE_TEXT_V1]\n"
SELECTION_FIELDS = ("source_title_segment_ids", "source_body_segment_ids")
SELECTION_SCHEMA = {
    field: {"type": "array", "items": {"type": "integer"}, "minItems": 1}
    for field in SELECTION_FIELDS
}


def page_segments(body: str) -> list[str] | None:
    text = body
    if text.startswith("[FULL_TEXT "):
        text = text.partition("\n")[2]
    if not text.startswith(PAGE_TEXT_MARKER):
        return None
    return text[len(PAGE_TEXT_MARKER):].splitlines()


def selected_article(headline: str, body: str, annotation: dict) -> tuple[str, str]:
    """Resolve selected IDs against their immutable raw source, never model prose."""
    segments = page_segments(body)
    if segments is None:
        return headline, body
    selected = []
    for field in SELECTION_FIELDS:
        ids = annotation.get(field)
        if (not isinstance(ids, list) or not ids
                or any(type(i) is not int or i < 0 or i >= len(segments) for i in ids)
                or ids != sorted(set(ids))):
            raise ValueError(f"invalid article source selection: {field}")
        selected.append("\n".join(segments[i] for i in ids))
    return selected[0], selected[1]
