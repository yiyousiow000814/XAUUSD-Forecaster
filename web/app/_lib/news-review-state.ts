export const NEWS_REVIEW_STATES = ["COMPLETED", "PROCESSING", "ISOLATED"] as const;

export type NewsReviewState = typeof NEWS_REVIEW_STATES[number];

type NewsReviewStateFields = {
  annotation_status?: unknown;
  model_visibility?: unknown;
  parsed_at?: unknown;
};

export const ACTIVE_NEWS_SQL =
  "COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT'";

export const NEWS_REVIEW_STATE_INVARIANT_SQL = `(
  (json_extract(payload, '$.annotation_status')='NOT_REQUIRED'
    AND json_extract(payload, '$.model_visibility')='MODEL_INELIGIBLE'
    AND json_extract(payload, '$.parsed_at') IS NULL)
  OR (json_extract(payload, '$.annotation_status')='QUEUED'
    AND json_extract(payload, '$.model_visibility')='NOT_YET_PARSED'
    AND json_extract(payload, '$.parsed_at') IS NULL)
  OR (json_extract(payload, '$.annotation_status')='READY'
    AND json_extract(payload, '$.model_visibility')<>'NOT_YET_PARSED'
    AND json_extract(payload, '$.parsed_at') IS NOT NULL)
  OR (json_extract(payload, '$.annotation_status') IN (
      'BACKING_OFF','DEAD_LETTER','WAITING_CONTENT','CONTENT_UNAVAILABLE'
    )
    AND json_extract(payload, '$.model_visibility') =
        json_extract(payload, '$.annotation_status')
    AND json_extract(payload, '$.parsed_at') IS NULL)
)`;

/** One public row cannot simultaneously claim incompatible workflow states. */
export const newsReviewStateInvariantHolds = (
  item: NewsReviewStateFields,
): boolean => {
  const status = String(item.annotation_status ?? "");
  const visibility = String(item.model_visibility ?? "");
  const parsed = typeof item.parsed_at === "string" && item.parsed_at.length > 0;
  if (status === "NOT_REQUIRED") {
    return visibility === "MODEL_INELIGIBLE" && !parsed;
  }
  if (status === "QUEUED") {
    return visibility === "NOT_YET_PARSED" && !parsed;
  }
  if (status === "READY") return visibility !== "NOT_YET_PARSED" && parsed;
  const terminalOrWaiting = [
    "BACKING_OFF", "DEAD_LETTER", "WAITING_CONTENT", "CONTENT_UNAVAILABLE",
  ].includes(status);
  return terminalOrWaiting && visibility === status && !parsed;
};

export const parseNewsReviewState = (value: string | null): NewsReviewState | null => {
  if (value === null || value === "") return "COMPLETED";
  return NEWS_REVIEW_STATES.includes(value as NewsReviewState)
    ? value as NewsReviewState
    : null;
};

export const newsReviewStateOf = (
  item: NewsReviewStateFields,
): NewsReviewState => {
  const status = String(item.annotation_status ?? "");
  if (status === "READY" || status === "NOT_REQUIRED") return "COMPLETED";
  if (status === "DEAD_LETTER" || status === "CONTENT_UNAVAILABLE") {
    return "ISOLATED";
  }
  return "PROCESSING";
};
