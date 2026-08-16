type NewsEvidenceTime = {
  event_key: string;
  source_published_time?: string | null;
  collector_first_seen_time?: string | null;
};

function effectiveEvidenceTime(row: NewsEvidenceTime): string {
  return row.source_published_time || row.collector_first_seen_time || "";
}

export function sortNewsEvidenceByTime<T extends NewsEvidenceTime>(rows: Iterable<T>): T[] {
  return Array.from(rows).sort((left, right) => (
    effectiveEvidenceTime(right).localeCompare(effectiveEvidenceTime(left))
    || (right.collector_first_seen_time || "").localeCompare(
      left.collector_first_seen_time || "",
    )
    || right.event_key.localeCompare(left.event_key)
  ));
}
