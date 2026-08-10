type JsonObject = Record<string, unknown>;

/** Keep first paint useful without compiling the complete learning ledger. */
export function compactPreviewLearning(learning: JsonObject): JsonObject {
  const curves = (learning.learning_curves ?? {}) as JsonObject;
  const models = Array.isArray(curves.models) ? curves.models : [];
  const versionGroups = Array.isArray(curves.version_groups) ? curves.version_groups : [];
  return {
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
      identity_curves: curves.identity_curves,
    },
  };
}
