import manifest from "../../preview-manifest.json" with { type: "json" };

export const PREVIEW_NEWS_PAGE_SIZE = manifest.newsPageSize;
export const PREVIEW_RESOURCES = Object.freeze(manifest.resources);
export const PREVIEW_STATUS_INLINE_KEYS = Object.freeze(manifest.statusInlineKeys);
export const PREVIEW_AUDIT_INLINE_KEYS = Object.freeze(manifest.auditInlineKeys);
export const PREVIEW_BRANCH_SNAPSHOT_STATUS_PATHS = Object.freeze(
  manifest.branchSnapshotStatusPaths,
);
export const PREVIEW_FALLBACK_STATUS_PATHS = Object.freeze(
  manifest.fallbackStatusPaths,
);
