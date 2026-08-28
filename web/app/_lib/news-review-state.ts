export const NEWS_REVIEW_STATES = ["COMPLETED", "PROCESSING", "ISOLATED"] as const;

export type NewsReviewState = typeof NEWS_REVIEW_STATES[number];

const COMPLETED_ANNOTATION_STATUSES = ["READY", "NOT_REQUIRED"] as const;
const ISOLATED_ANNOTATION_STATUSES = ["DEAD_LETTER", "CONTENT_UNAVAILABLE"] as const;
const ALIGNED_UNPARSED_ANNOTATION_STATUSES = [
  "REPAIRING_DISPLAY", "BACKING_OFF", "DEAD_LETTER",
  "WAITING_CONTENT", "CONTENT_UNAVAILABLE",
] as const;

const sqlValues = (values: readonly string[]) =>
  values.map(value => `'${value}'`).join(",");

/** SQL and TypeScript readers share one annotation-to-review-state contract. */
export const NEWS_REVIEW_STATE_SQL: Record<NewsReviewState, string> = {
  COMPLETED: `json_extract(payload, '$.annotation_status') IN (${sqlValues(COMPLETED_ANNOTATION_STATUSES)})`,
  ISOLATED: `json_extract(payload, '$.annotation_status') IN (${sqlValues(ISOLATED_ANNOTATION_STATUSES)})`,
  PROCESSING: `COALESCE(json_extract(payload, '$.annotation_status'), '') NOT IN (${sqlValues([
    ...COMPLETED_ANNOTATION_STATUSES, ...ISOLATED_ANNOTATION_STATUSES,
  ])})`,
};

export const NEWS_REVIEW_STATE_CASE_SQL = `CASE
  WHEN ${NEWS_REVIEW_STATE_SQL.COMPLETED} THEN 'COMPLETED'
  WHEN ${NEWS_REVIEW_STATE_SQL.ISOLATED} THEN 'ISOLATED'
  ELSE 'PROCESSING' END`;

type NewsReviewStateFields = {
  annotation_status?: unknown;
  model_visibility?: unknown;
  parsed_at?: unknown;
};

export const ACTIVE_NEWS_SQL =
  "COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT'";

type NewsReviewStateSqlFields = {
  annotationStatus: string;
  modelVisibility: string;
  parsedAt: string;
};

/** Build the shared review invariant for either JSON fields or projected columns. */
export const newsReviewStateInvariantSql = ({
  annotationStatus, modelVisibility, parsedAt,
}: NewsReviewStateSqlFields) => `(
  (${annotationStatus}='NOT_REQUIRED'
    AND ${modelVisibility}='MODEL_INELIGIBLE'
    AND ${parsedAt} IS NULL)
  OR (${annotationStatus}='QUEUED'
    AND ${modelVisibility}='NOT_YET_PARSED'
    AND ${parsedAt} IS NULL)
  OR (${annotationStatus}='READY'
    AND ${modelVisibility}<>'NOT_YET_PARSED'
    AND ${parsedAt} IS NOT NULL)
  OR (${annotationStatus} IN (
      ${sqlValues(ALIGNED_UNPARSED_ANNOTATION_STATUSES)}
    )
    AND ${modelVisibility} = ${annotationStatus}
    AND ${parsedAt} IS NULL)
)`;

export const NEWS_REVIEW_STATE_INVARIANT_SQL = newsReviewStateInvariantSql({
  annotationStatus: "json_extract(payload, '$.annotation_status')",
  modelVisibility: "json_extract(payload, '$.model_visibility')",
  parsedAt: "json_extract(payload, '$.parsed_at')",
});

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
  const alignedUnparsedState = (ALIGNED_UNPARSED_ANNOTATION_STATUSES as readonly string[])
    .includes(status);
  return alignedUnparsedState && visibility === status && !parsed;
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
  if ((COMPLETED_ANNOTATION_STATUSES as readonly string[]).includes(status)) {
    return "COMPLETED";
  }
  if ((ISOLATED_ANNOTATION_STATUSES as readonly string[]).includes(status)) {
    return "ISOLATED";
  }
  return "PROCESSING";
};
