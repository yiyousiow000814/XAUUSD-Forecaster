export const NEWS_REVIEW_STATES = ["COMPLETED", "PROCESSING", "ISOLATED"] as const;

export type NewsReviewState = typeof NEWS_REVIEW_STATES[number];

export const parseNewsReviewState = (value: string | null): NewsReviewState | null => {
  if (value === null || value === "") return "COMPLETED";
  return NEWS_REVIEW_STATES.includes(value as NewsReviewState)
    ? value as NewsReviewState
    : null;
};

export const newsReviewStateOf = (
  item: { annotation_status?: unknown },
): NewsReviewState => {
  const status = String(item.annotation_status ?? "");
  if (status === "READY" || status === "NOT_REQUIRED") return "COMPLETED";
  if (status === "DEAD_LETTER" || status === "CONTENT_UNAVAILABLE") {
    return "ISOLATED";
  }
  return "PROCESSING";
};
