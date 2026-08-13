type JsonObject = Record<string, unknown>;

/** Keep branch identity and safety state while reading current public metrics. */
export function withPreviewIdentity(current: JsonObject, frozen: JsonObject): JsonObject {
  const currentSystem = current.system && typeof current.system === "object"
    ? current.system as JsonObject : {};
  const frozenSystem = frozen.system && typeof frozen.system === "object"
    ? frozen.system as JsonObject : {};
  const currentQueue = current.annotation_queue && typeof current.annotation_queue === "object"
    ? current.annotation_queue as JsonObject : {};
  const frozenQueue = frozen.annotation_queue && typeof frozen.annotation_queue === "object"
    ? frozen.annotation_queue as JsonObject : {};
  return {
    ...current,
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
      source_of_truth: frozenSystem.source_of_truth,
      sites_mirror: frozenSystem.sites_mirror,
      deployment: frozenSystem.deployment,
    },
  };
}
