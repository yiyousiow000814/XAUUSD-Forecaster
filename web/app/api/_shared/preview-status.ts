import {
  PREVIEW_BRANCH_SNAPSHOT_STATUS_PATHS,
  PREVIEW_FALLBACK_STATUS_PATHS,
} from "../../_lib/preview-manifest.ts";

type JsonObject = Record<string, unknown>;

function isJsonObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function overlayPath(target: JsonObject, source: JsonObject, path: string): boolean {
  const segments = path.split(".");
  let sourceCursor: JsonObject = source;
  let targetCursor: JsonObject = target;
  for (let index = 0; index < segments.length - 1; index += 1) {
    const segment = segments[index];
    const sourceValue = sourceCursor[segment];
    if (!isJsonObject(sourceValue)) return false;
    sourceCursor = sourceValue;
    const targetValue = targetCursor[segment];
    const nextTarget = isJsonObject(targetValue) ? { ...targetValue } : {};
    targetCursor[segment] = nextTarget;
    targetCursor = nextTarget;
  }
  const leaf = segments.at(-1);
  if (!leaf || !Object.hasOwn(sourceCursor, leaf)) return false;
  targetCursor[leaf] = sourceCursor[leaf];
  return true;
}

function hasPath(target: JsonObject, path: string): boolean {
  let cursor: unknown = target;
  for (const segment of path.split(".")) {
    if (!isJsonObject(cursor) || !Object.hasOwn(cursor, segment)) return false;
    cursor = cursor[segment];
  }
  return true;
}

/** Keep Preview identity and authority boundaries on current read-only metrics. */
export function withPreviewIdentity(current: JsonObject, frozen: JsonObject): JsonObject {
  const currentSystem = current.system && typeof current.system === "object"
    ? current.system as JsonObject : {};
  const frozenSystem = frozen.system && typeof frozen.system === "object"
    ? frozen.system as JsonObject : {};
  const frozenPreview = frozen.preview && typeof frozen.preview === "object"
    ? frozen.preview as JsonObject : {};
  const merged: JsonObject = {
    ...current,
    preview_status_summary: false,
    observation_scope: "D1_SNAPSHOT",
    preview: {
      ...frozenPreview,
      branch_snapshot: {
        generated_at: frozenPreview.snapshot_generated_at ?? frozen.generated_at ?? null,
        status_paths: [],
      },
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
  const appliedPaths = PREVIEW_BRANCH_SNAPSHOT_STATUS_PATHS.filter(
    path => overlayPath(merged, frozen, path),
  );
  for (const path of PREVIEW_FALLBACK_STATUS_PATHS) {
    if (!hasPath(current, path) && overlayPath(merged, frozen, path)) {
      appliedPaths.push(path);
    }
  }
  const preview = merged.preview as JsonObject;
  const branchSnapshot = preview.branch_snapshot as JsonObject;
  branchSnapshot.status_paths = appliedPaths;
  return merged;
}
