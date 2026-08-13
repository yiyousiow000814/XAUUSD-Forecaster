export type NewsTotalsScope = "D1_ARCHIVE" | "RECENT_WINDOW" | "BUILD_SNAPSHOT" | "LOADING";

type NewsIndexTotals = {
  total: number;
  all_total: number;
  readable_total?: number;
  parsed_total?: number;
  model_candidate_total?: number;
  totals_scope?: NewsTotalsScope;
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

/** Only the live D1 archive may describe itself as the complete 60-day total. */
export function authoritativeNewsTotals(index: NewsIndexTotals): AuthoritativeNewsTotals | null {
  if (index.totals_scope !== "D1_ARCHIVE") return null;
  return {
    category: count(index.total),
    readable: count(index.readable_total ?? index.all_total),
    parsed: count(index.parsed_total),
    modelCandidates: count(index.model_candidate_total),
  };
}
