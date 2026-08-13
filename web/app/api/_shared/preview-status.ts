type JsonObject = Record<string, unknown>;

/** Keep Preview identity and authority boundaries on current read-only metrics. */
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
      source_of_truth: "生产 D1 当前只读数据",
      sites_mirror: "PR 分支预览（无运行权限）",
      deployment: frozenSystem.deployment,
    },
  };
}
