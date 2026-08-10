type JsonObject = Record<string, unknown>;

const STATUS_SUMMARY_KEYS = [
  "generated_at", "system", "training", "counts", "news_evidence_summary",
  "storyline_summary", "factor_coverage", "annotation_queue", "news_source_health",
  "llm_routing", "gemini_quota", "gemini_31_quota", "gemma_quota", "latest",
  "research_forecast", "u5_context", "outcome_summary", "sources", "forward_epoch",
] as const;

/** Keep Worker startup memory independent of the growing audit snapshot. */
export function compactPreviewStatus(status: JsonObject): JsonObject {
  const result: JsonObject = { preview_status_summary: true };
  for (const key of STATUS_SUMMARY_KEYS) result[key] = status[key];
  return result;
}

/** Keep only the first visible page; later pages already come from D1. */
export function compactPreviewNewsIndex(index: JsonObject): JsonObject {
  const items = Array.isArray(index.items) ? index.items : [];
  return { ...index, items: items.slice(0, 12), page: 1, page_size: 12 };
}

/** Keep first paint useful without compiling the complete learning ledger. */
export function compactPreviewLearning(learning: JsonObject): JsonObject {
  const curves = (learning.learning_curves ?? {}) as JsonObject;
  const models = Array.isArray(curves.models) ? curves.models : [];
  const versionGroups = Array.isArray(curves.version_groups) ? curves.version_groups : [];
  return {
    learning_preview_summary: true,
    generated_at: learning.generated_at,
    learning_curves: {
      collection_epoch: curves.collection_epoch,
      evaluation_epoch_v2: curves.evaluation_epoch_v2,
      learning_stage: curves.learning_stage,
      models: models.filter(row => (
        row && typeof row === "object" && (row as JsonObject).active_rank !== null
      )),
      version_groups: versionGroups.filter(row => (
        row && typeof row === "object" && (row as JsonObject).lifecycle_status === "LATEST"
      )),
      rolling_processes: curves.rolling_processes,
      // Curves and execution histories are loaded from D1 when the league is
      // visible.  Keeping them in the Worker module caused 1102 isolate OOMs.
      identity_curves: [],
    },
  };
}
