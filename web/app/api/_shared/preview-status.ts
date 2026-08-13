type JsonObject = Record<string, unknown>;

/** Keep branch identity and safety state while reading current public metrics. */
export function withPreviewIdentity(current: JsonObject, frozen: JsonObject): JsonObject {
  const currentSystem = current.system && typeof current.system === "object"
    ? current.system as JsonObject : {};
  const frozenSystem = frozen.system && typeof frozen.system === "object"
    ? frozen.system as JsonObject : {};
  return {
    ...current,
    preview_status_summary: false,
    observation_scope: "D1_SNAPSHOT",
    preview: frozen.preview,
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
