import {
  PREVIEW_NEWS_PAGE_SIZE,
  PREVIEW_RESOURCES,
  PREVIEW_AUDIT_INLINE_KEYS,
  PREVIEW_STATUS_INLINE_KEYS,
} from "../app/_lib/preview-manifest";

type JsonObject = Record<string, unknown>;

const PREVIEW_AUDIT_ARRAY_LIMITS: Record<string, number> = {
  daily_news_briefs: 2,
  storylines: 5,
  market_narrative_candidates: 5,
  archived_storylines: 5,
  archived_story_event_candidates: 5,
  story_event_candidates: 10,
  market_reaction_streams: 5,
  theme_streams: 5,
  unassigned_story_events: 10,
  recent_decisions: 12,
};

/** Keep Worker startup memory independent of the growing audit snapshot. */
export function compactPreviewStatus(status: JsonObject): JsonObject {
  const result: JsonObject = {
    preview_status_summary: true,
    observation_scope: "BUILD_SNAPSHOT",
  };
  for (const key of PREVIEW_STATUS_INLINE_KEYS) {
    const value = status[key];
    if (key === "recent_decisions" && Array.isArray(value)) {
      result[key] = value.slice(0, 18).map(row => {
        if (!row || typeof row !== "object") return row;
        const { features: _features, predictions: _predictions, ...bounded } = row as JsonObject;
        return bounded;
      });
    } else {
      result[key] = value;
    }
  }
  const market = status.market_chart && typeof status.market_chart === "object"
    ? status.market_chart as JsonObject
    : {};
  // The candles stay in D1, but the compact first paint must retain the route
  // that loads them.  Dropping both data and its resource pointer leaves the
  // K-line tab permanently empty in branch previews.
  result.market_chart = {
    history_resource: market.history_resource ?? PREVIEW_RESOURCES.marketHistory,
    candles: [],
    decisions: [],
    training_markers: market.training_markers ?? [],
    prediction_history_start: market.prediction_history_start ?? {},
  };
  return result;
}

/** Keep the optional audit first page bounded independently of live status. */
export function compactPreviewAudit(audit: JsonObject): JsonObject {
  const result: JsonObject = {};
  for (const key of PREVIEW_AUDIT_INLINE_KEYS) {
    const value = audit[key];
    const limit = PREVIEW_AUDIT_ARRAY_LIMITS[key];
    result[key] = Array.isArray(value) && limit ? value.slice(0, limit) : value;
  }
  return result;
}

/** Keep one independently owned audit detail snapshot bounded. */
export function compactPreviewAuditDetail(detail: JsonObject): JsonObject {
  const result: JsonObject = {};
  for (const [key, value] of Object.entries(detail)) {
    const limit = PREVIEW_AUDIT_ARRAY_LIMITS[key];
    result[key] = Array.isArray(value) && limit ? value.slice(0, limit) : value;
  }
  return result;
}

/** Keep only the first visible page; later pages already come from D1. */
export function compactPreviewNewsIndex(index: JsonObject): JsonObject {
  const items = Array.isArray(index.items) ? index.items : [];
  return {
    ...index,
    items: items.slice(0, PREVIEW_NEWS_PAGE_SIZE),
    page: 1,
    page_size: PREVIEW_NEWS_PAGE_SIZE,
    // The embedded page remains useful while D1 loads, but its build-time
    // aggregates must never masquerade as the current 60-day archive total.
    totals_scope: "BUILD_SNAPSHOT",
  };
}

/** Keep first paint useful without compiling the complete learning ledger. */
export function compactPreviewLearning(learning: JsonObject): JsonObject {
  const curves = (learning.learning_curves ?? {}) as JsonObject;
  const models = Array.isArray(curves.models) ? curves.models : [];
  const versionGroups = Array.isArray(curves.version_groups) ? curves.version_groups : [];
  const execution = (learning.execution_learning ?? {}) as JsonObject;
  const executionModels = Array.isArray(execution.models) ? execution.models : [];
  return {
    learning_preview_summary: true,
    learning_history_resource: PREVIEW_RESOURCES.learningHistory,
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
      // Complete curves are loaded from D1 when the league is visible. Keeping
      // them in the Worker module caused 1102 isolate OOMs.
      identity_curves: [],
    },
    // The execution tab has no separate first-paint endpoint. Retain only its
    // bounded scorecard and recent chart/list data; complete execution rows
    // remain in the paged learning ledger.
    execution_learning: {
      ...execution,
      models: executionModels.map(value => {
        const model = value && typeof value === "object" ? value as JsonObject : {};
        const evaluation = model.evaluation && typeof model.evaluation === "object"
          ? model.evaluation as JsonObject : {};
        const points = Array.isArray(evaluation.points) ? evaluation.points : [];
        const results = Array.isArray(evaluation.results) ? evaluation.results : [];
        return {
          ...model,
          evaluation: {
            ...evaluation,
            points: points.slice(-48),
            results: results.slice(-20),
            result_total: evaluation.result_total ?? results.length,
          },
        };
      }),
    },
  };
}
