import {
  PREVIEW_NEWS_PAGE_SIZE,
  PREVIEW_RESOURCES,
  PREVIEW_AUDIT_INLINE_KEYS,
  PREVIEW_STATUS_INLINE_KEYS,
} from "../app/_lib/preview-manifest";
import { validAuditDetailPayload } from "../app/_lib/audit-detail-contract";

type JsonObject = Record<string, unknown>;

/** Admit the same renderable resource contract used by routes and the browser. */
export function admitPreviewAuditDetails(bundle: JsonObject): void {
  const status = bundle.status as JsonObject | undefined;
  const preview = status?.preview as JsonObject | undefined;
  const resources = preview?.resources as JsonObject | undefined;
  for (const family of ["briefs", "stories"] as const) {
    const key = `audit_${family}`;
    if (validAuditDetailPayload(family, bundle[key])) continue;
    bundle[key] = null;
    if (resources) resources[key] = {
      availability: "UNAVAILABLE_IN_BUILD_SNAPSHOT",
      requested_path: `/api/audit-${family}`,
      source_path: null,
      compatibility_fallback: false,
      reason: "INVALID_AUDIT_DETAIL_SOURCE",
    };
  }
}

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
};

/** Keep Worker startup memory independent of the growing audit snapshot. */
export function compactPreviewStatus(status: JsonObject): JsonObject {
  const result: JsonObject = {
    preview_status_summary: true,
    observation_scope: "BUILD_SNAPSHOT",
  };
  for (const key of PREVIEW_STATUS_INLINE_KEYS) {
    const value = status[key];
    result[key] = value;
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
