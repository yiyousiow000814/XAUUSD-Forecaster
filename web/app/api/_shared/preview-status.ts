type JsonObject = Record<string, unknown>;

const BRANCH_RECOMPUTED_KEYS = [
  "factor_coverage",
  "storyline_summary",
  "storylines",
  "market_narrative_candidates",
  "story_event_candidates",
] as const;

/** Keep Preview identity and authority boundaries on current read-only metrics. */
export function withPreviewIdentity(current: JsonObject, frozen: JsonObject): JsonObject {
  const currentSystem = current.system && typeof current.system === "object"
    ? current.system as JsonObject : {};
  const frozenSystem = frozen.system && typeof frozen.system === "object"
    ? frozen.system as JsonObject : {};
  const branchRecomputed = Object.fromEntries(
    BRANCH_RECOMPUTED_KEYS
      .filter(key => Object.hasOwn(frozen, key))
      .map(key => [key, frozen[key]]),
  );
  const currentQueue = current.annotation_queue && typeof current.annotation_queue === "object"
    ? current.annotation_queue as JsonObject : {};
  const frozenQueue = frozen.annotation_queue && typeof frozen.annotation_queue === "object"
    ? frozen.annotation_queue as JsonObject : {};
  return {
    ...current,
    ...branchRecomputed,
    preview_status_summary: false,
    observation_scope: "D1_SNAPSHOT",
    preview: frozen.preview,
    annotation_queue: {
      ...currentQueue,
      requests_per_minute_per_key: frozenQueue.requests_per_minute_per_key,
      requests_per_minute: frozenQueue.requests_per_minute,
      input_tokens_per_minute: frozenQueue.input_tokens_per_minute,
      minute_scope: frozenQueue.minute_scope,
    },
    system: {
      ...currentSystem,
      online: false,
      market_session: "DATA_UNAVAILABLE",
      source_of_truth: "生产 D1 当前只读数据",
      sites_mirror: "PR 分支预览（无运行权限）",
      deployment: frozenSystem.deployment,
    },
  };
}
