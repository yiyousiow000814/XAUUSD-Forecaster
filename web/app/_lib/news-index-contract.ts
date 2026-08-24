export type NewsTotalsScope =
  | "VERIFIED_CURRENT_GENERATION" | "RECENT_WINDOW" | "BUILD_SNAPSHOT" | "LOADING";

type NewsIndexTotals = {
  total: number;
  all_total: number;
  readable_total?: number;
  parsed_total?: number;
  model_candidate_total?: number;
  totals_scope?: NewsTotalsScope;
  projection_state?: "CURRENT" | "RECOVERY_REQUIRED" | "REPLAYING" | "VERIFYING" | "DEGRADED";
  verified_complete?: boolean;
  generation_id?: string;
  snapshot_id?: string;
  source_digest?: string;
  receipt_digest?: string;
  source_receipt_digest?: string;
};

export type AuthoritativeNewsTotals = {
  category: number;
  readable: number;
  parsed: number;
  modelCandidates: number;
};

const count = (value: unknown): number => (
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0
);

const digest = (value: unknown): value is string => (
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value)
);

/** Only one exact, receipt-matched CURRENT generation may claim the 60-day total. */
export function authoritativeNewsTotals(index: NewsIndexTotals): AuthoritativeNewsTotals | null {
  if (
    index.totals_scope !== "VERIFIED_CURRENT_GENERATION"
    || index.projection_state !== "CURRENT" || index.verified_complete !== true
    || !digest(index.generation_id) || !digest(index.snapshot_id)
    || !digest(index.source_digest) || !digest(index.receipt_digest)
    || index.receipt_digest !== index.source_receipt_digest
  ) return null;
  return {
    category: count(index.total),
    readable: count(index.readable_total ?? index.all_total),
    parsed: count(index.parsed_total),
    modelCandidates: count(index.model_candidate_total),
  };
}
