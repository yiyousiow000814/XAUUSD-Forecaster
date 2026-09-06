export type AuditDetailResource = "briefs" | "stories" | "decisions";

const requiredArray = {
  briefs: "daily_news_briefs",
  stories: "storylines",
  decisions: "recent_decisions",
} as const;

const record = (value: unknown): value is Record<string, unknown> => (
  Boolean(value) && typeof value === "object" && !Array.isArray(value)
);
const records = (value: unknown): value is Record<string, unknown>[] => (
  Array.isArray(value) && value.every(record)
);
const strings = (value: unknown): value is string[] => (
  Array.isArray(value) && value.every(item => typeof item === "string")
);

function validRow(view: AuditDetailResource, row: Record<string, unknown>): boolean {
  if (view === "briefs") {
    const brief = row.brief;
    return typeof row.model_version === "string" && (row.phase == null || typeof row.phase === "string") && record(brief)
      && records(brief.items) && brief.items.every(item => typeof item.headline === "string"
        && typeof item.summary === "string" && strings(item.evidence_ids))
      && (brief.drivers == null || strings(brief.drivers))
      && [brief.overview, brief.watch_next].every(value => value == null || typeof value === "string");
  }
  if (view === "stories") {
    return ["covered_roles", "missing_roles", "timeline", "market_reactions", "commentary", "background"]
      .every(field => records(row[field]));
  }
  return records(row.predictions)
    && [row.bid, row.ask, row.long_return, row.short_return].every(value => value == null || typeof value === "number")
    && (row.outcome_status == null || row.outcome_status === "VALID" || strings(row.outcome_reason_codes))
    && row.predictions.every(prediction => (
      ["predicted_direction_u5", "predicted_news_residual_u5", "ev_long_u5", "ev_short_u5", "uncertainty_u5"]
        .every(field => prediction[field] == null || typeof prediction[field] === "number")
    ));
}

export function validAuditDetailPayload(view: AuditDetailResource, value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  const rows = body[requiredArray[view]];
  if ("error" in body || !records(rows) || !rows.every(row => validRow(view, row))) return false;
  if (view === "stories" && ["market_narrative_candidates", "archived_storylines", "archived_story_event_candidates", "story_event_candidates", "market_reaction_streams", "theme_streams", "unassigned_story_events"]
    .some(field => body[field] != null && !records(body[field]))) return false;
  if (view === "stories" && records(body.archived_storylines)
    && !body.archived_storylines.every(row => validRow(view, row))) return false;
  return body.generated_at === undefined || (typeof body.generated_at === "string"
    && Number.isFinite(Date.parse(body.generated_at)));
}
