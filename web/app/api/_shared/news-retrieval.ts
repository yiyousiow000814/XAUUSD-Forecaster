import { publicNewsRecord } from "../../_lib/public-news-copy";

export const MAX_QUERY_CHARACTERS = 80;
export const MAX_QUERY_TOKENS = 6;
export const MAX_PAGE_SIZE = 20;
export const MAX_PAGE = 1_000;

export type NewsRetrievalFilters = {
  published_from: string | null;
  published_to: string | null;
  received_from: string | null;
  received_to: string | null;
  evidence_id: string | null;
  source: string | null;
  category: string | null;
};

export type NewsRetrievalRequest = {
  query: string;
  tokens: string[];
  page: number;
  pageSize: number;
  filters: NewsRetrievalFilters;
  hasCriteria: boolean;
};

export type NewsRetrievalSourceMode =
  | "D1_ARCHIVE"
  | "READ_ONLY_D1_ARCHIVE"
  | "IMMUTABLE_PREVIEW_SNAPSHOT"
  | "NOT_QUERIED";

export type NewsRetrievalItem = Record<string, unknown> & {
  evidence_id: string;
  detail_key: string;
};

export type NewsRetrievalPayload = {
  items: NewsRetrievalItem[];
  total: number;
  page: number;
  page_size: number;
  query: string;
  filters: NewsRetrievalFilters;
  source_mode: NewsRetrievalSourceMode;
  archive_complete: boolean | null;
  has_more: boolean;
  retrieval: {
    ordering: readonly ["published_time DESC", "collector_first_seen_time DESC", "detail_key DESC"];
    cutoff: string | null;
    result_limit: number;
    canonical_evidence_ids: string[];
    fallback_reason: "AUTHORITATIVE_STORE_UNAVAILABLE" | null;
  };
};

export type NewsRetrievalOutcome =
  | { ok: true; payload: NewsRetrievalPayload }
  | {
    ok: false;
    status: 503;
    code: "NEWS_RETRIEVAL_UNAVAILABLE";
    error: string;
  };

export type NewsRetrievalParseResult =
  | { ok: true; value: NewsRetrievalRequest }
  | { ok: false; status: 400; code: string; error: string };

type D1NewsRow = { payload: string; detail_key: string };

const ORDERING = [
  "published_time DESC",
  "collector_first_seen_time DESC",
  "detail_key DESC",
] as const;

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

const normalizedText = (value: unknown) => String(value ?? "")
  .normalize("NFKC")
  .toLocaleLowerCase("zh-CN");

const boundedText = (value: string | null, limit: number) => {
  const normalized = (value ?? "").normalize("NFKC").trim().replace(/\s+/g, " ");
  return normalized ? normalized.slice(0, limit) : null;
};

const boundedPositiveInteger = (value: string | null, fallback: number, maximum: number) => {
  if (!value || !/^\d+$/.test(value)) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) return fallback;
  return Math.min(maximum, Math.max(1, parsed));
};

const normalizeBoundary = (value: string | null, endOfDay: boolean) => {
  const raw = value?.trim();
  if (!raw) return { ok: true as const, value: null };
  if (DATE_ONLY.test(raw)) {
    const start = new Date(`${raw}T00:00:00.000Z`);
    if (Number.isNaN(start.getTime()) || start.toISOString().slice(0, 10) !== raw) {
      return { ok: false as const };
    }
    return {
      ok: true as const,
      value: endOfDay
        ? new Date(start.getTime() + 86_400_000 - 1).toISOString()
        : start.toISOString(),
    };
  }
  const epoch = Date.parse(raw);
  if (Number.isNaN(epoch)) return { ok: false as const };
  return { ok: true as const, value: new Date(epoch).toISOString() };
};

const boundaryError = (code: string, error: string): NewsRetrievalParseResult => ({
  ok: false,
  status: 400,
  code,
  error,
});

export function parseNewsRetrievalRequest(input: Request | URL | string): NewsRetrievalParseResult {
  const url = input instanceof Request
    ? new URL(input.url)
    : input instanceof URL ? input : new URL(input, "https://retrieval.invalid");
  const params = url.searchParams;
  const normalizedQuery = (params.get("q") ?? "")
    .normalize("NFKC")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, MAX_QUERY_CHARACTERS);
  const queryTokens = normalizedQuery ? normalizedQuery.split(" ").slice(0, MAX_QUERY_TOKENS) : [];
  const query = queryTokens.join(" ");

  const publishedFrom = normalizeBoundary(params.get("published_from"), false);
  if (!publishedFrom.ok) return boundaryError("INVALID_PUBLISHED_FROM", "发布时间起点无效");
  const publishedTo = normalizeBoundary(params.get("published_to"), true);
  if (!publishedTo.ok) return boundaryError("INVALID_PUBLISHED_TO", "发布时间终点无效");
  const receivedFrom = normalizeBoundary(params.get("received_from"), false);
  if (!receivedFrom.ok) return boundaryError("INVALID_RECEIVED_FROM", "系统收到时间起点无效");
  const receivedTo = normalizeBoundary(params.get("received_to"), true);
  if (!receivedTo.ok) return boundaryError("INVALID_RECEIVED_TO", "系统收到时间终点无效");

  if (
    publishedFrom.value && publishedTo.value
    && Date.parse(publishedFrom.value) > Date.parse(publishedTo.value)
  ) return boundaryError("INVALID_PUBLISHED_RANGE", "发布时间范围前后颠倒");
  if (
    receivedFrom.value && receivedTo.value
    && Date.parse(receivedFrom.value) > Date.parse(receivedTo.value)
  ) return boundaryError("INVALID_RECEIVED_RANGE", "系统收到时间范围前后颠倒");

  const evidenceId = boundedText(params.get("evidence_id"), 128);
  if (evidenceId && !/^[A-Za-z0-9:._-]+$/.test(evidenceId)) {
    return boundaryError("INVALID_EVIDENCE_ID", "证据 ID 格式无效");
  }
  const filters: NewsRetrievalFilters = {
    published_from: publishedFrom.value,
    published_to: publishedTo.value,
    received_from: receivedFrom.value,
    received_to: receivedTo.value,
    evidence_id: evidenceId,
    source: boundedText(params.get("source"), 80),
    category: boundedText(params.get("category"), 40),
  };
  return {
    ok: true,
    value: {
      query,
      tokens: queryTokens.map(normalizedText),
      page: boundedPositiveInteger(params.get("page"), 1, MAX_PAGE),
      pageSize: boundedPositiveInteger(params.get("limit"), 10, MAX_PAGE_SIZE),
      filters,
      hasCriteria: Boolean(query || Object.values(filters).some(Boolean)),
    },
  };
}

export const escapeSqlLike = (value: string) => value.replace(/[\\%_]/g, "\\$&");

export function buildNewsRetrievalSql(request: NewsRetrievalRequest) {
  const clauses: string[] = [];
  const bindings: unknown[] = [];
  const searchable = `lower(COALESCE(json_extract(payload,'$.headline'),'') || ' ' ||
    COALESCE(json_extract(payload,'$.source'),'') || ' ' ||
    COALESCE(json_extract(payload,'$.emerging_topic_zh'),'') || ' ' ||
    COALESCE(json_extract(payload,'$.impact_reason_zh'),''))`;

  for (const token of request.tokens) {
    clauses.push(`${searchable} LIKE ? ESCAPE '\\'`);
    bindings.push(`%${escapeSqlLike(token)}%`);
  }
  if (request.filters.published_from) {
    clauses.push("julianday(published_time) >= julianday(?)");
    bindings.push(request.filters.published_from);
  }
  if (request.filters.published_to) {
    clauses.push("julianday(published_time) <= julianday(?)");
    bindings.push(request.filters.published_to);
  }
  if (request.filters.received_from) {
    clauses.push("julianday(collector_first_seen_time) >= julianday(?)");
    bindings.push(request.filters.received_from);
  }
  if (request.filters.received_to) {
    clauses.push("julianday(collector_first_seen_time) <= julianday(?)");
    bindings.push(request.filters.received_to);
  }
  if (request.filters.evidence_id) {
    clauses.push("detail_key = ?");
    bindings.push(request.filters.evidence_id);
  }
  if (request.filters.source) {
    clauses.push("lower(COALESCE(json_extract(payload,'$.source'),'')) = ?");
    bindings.push(normalizedText(request.filters.source));
  }
  if (request.filters.category) {
    clauses.push("lower(category) = ?");
    bindings.push(normalizedText(request.filters.category));
  }
  return {
    whereSql: clauses.length ? `WHERE ${clauses.join(" AND ")}` : "",
    bindings,
  };
}

const evidenceId = (row: Record<string, unknown>) => String(
  row.evidence_id ?? row.detail_key ?? row.event_version_id ?? "",
);

const canonicalItem = (row: Record<string, unknown>, forcedEvidenceId?: string) => {
  const canonicalId = forcedEvidenceId ?? evidenceId(row);
  if (!canonicalId) throw new Error("news retrieval row has no stable evidence id");
  return publicNewsRecord({
    ...row, evidence_id: canonicalId, detail_key: canonicalId,
  }) as NewsRetrievalItem;
};

const rowTime = (row: Record<string, unknown>, kind: "published" | "received") => {
  if (kind === "received") return String(row.collector_first_seen_time ?? "");
  return String(row.source_published_time ?? row.published_time ?? row.collector_first_seen_time ?? "");
};

const inRange = (value: string, lower: string | null, upper: string | null) => {
  const epoch = Date.parse(value);
  if (Number.isNaN(epoch)) return !lower && !upper;
  return (!lower || epoch >= Date.parse(lower)) && (!upper || epoch <= Date.parse(upper));
};

export function matchesNewsRetrieval(row: Record<string, unknown>, request: NewsRetrievalRequest) {
  const haystack = [row.headline, row.source, row.emerging_topic_zh, row.impact_reason_zh]
    .map(normalizedText)
    .join("\n");
  if (!request.tokens.every(token => haystack.includes(token))) return false;
  if (
    request.filters.evidence_id
    && evidenceId(row) !== request.filters.evidence_id
  ) return false;
  if (
    request.filters.source
    && normalizedText(row.source) !== normalizedText(request.filters.source)
  ) return false;
  if (
    request.filters.category
    && normalizedText(row.category) !== normalizedText(request.filters.category)
  ) return false;
  if (!inRange(
    rowTime(row, "published"),
    request.filters.published_from,
    request.filters.published_to,
  )) return false;
  return inRange(
    rowTime(row, "received"),
    request.filters.received_from,
    request.filters.received_to,
  );
}

const compareRows = (left: Record<string, unknown>, right: Record<string, unknown>) => {
  for (const value of [
    rowTime(right, "published").localeCompare(rowTime(left, "published")),
    rowTime(right, "received").localeCompare(rowTime(left, "received")),
    evidenceId(right).localeCompare(evidenceId(left)),
  ]) if (value) return value;
  return 0;
};

const responsePayload = (
  request: NewsRetrievalRequest,
  items: NewsRetrievalItem[],
  total: number,
  sourceMode: NewsRetrievalSourceMode,
  archiveComplete: boolean | null,
  fallbackReason: "AUTHORITATIVE_STORE_UNAVAILABLE" | null = null,
): NewsRetrievalPayload => ({
  items,
  total,
  page: request.page,
  page_size: request.pageSize,
  query: request.query,
  filters: request.filters,
  source_mode: sourceMode,
  archive_complete: archiveComplete,
  has_more: request.page * request.pageSize < total,
  retrieval: {
    ordering: ORDERING,
    cutoff: request.filters.received_to,
    result_limit: request.pageSize,
    canonical_evidence_ids: items.map(item => item.evidence_id),
    fallback_reason: fallbackReason,
  },
});

const retrieveFromD1 = async (
  binding: D1Database,
  request: NewsRetrievalRequest,
  preview: boolean,
) => {
  const { whereSql, bindings } = buildNewsRetrievalSql(request);
  const offset = (request.page - 1) * request.pageSize;
  const [rows, count] = await Promise.all([
    binding.prepare(
      `SELECT payload, detail_key FROM news_index ${whereSql}
       ORDER BY published_time DESC, collector_first_seen_time DESC, detail_key DESC
       LIMIT ? OFFSET ?`,
    ).bind(...bindings, request.pageSize, offset).all<D1NewsRow>(),
    binding.prepare(`SELECT count(*) AS count FROM news_index ${whereSql}`)
      .bind(...bindings).first<{ count: number }>(),
  ]);
  const items = rows.results.map(row => {
    const parsed = JSON.parse(row.payload) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("news retrieval payload is not an object");
    }
    return canonicalItem(parsed as Record<string, unknown>, row.detail_key);
  });
  return responsePayload(
    request,
    items,
    Number(count?.count ?? 0),
    preview ? "READ_ONLY_D1_ARCHIVE" : "D1_ARCHIVE",
    true,
  );
};

const retrieveFromPreview = (
  previewItems: Array<Record<string, unknown>>,
  request: NewsRetrievalRequest,
) => {
  const filtered = previewItems.filter(row => matchesNewsRetrieval(row, request)).sort(compareRows);
  const offset = (request.page - 1) * request.pageSize;
  const items = filtered
    .slice(offset, offset + request.pageSize)
    .map(row => canonicalItem(row));
  return responsePayload(
    request,
    items,
    filtered.length,
    "IMMUTABLE_PREVIEW_SNAPSHOT",
    false,
    "AUTHORITATIVE_STORE_UNAVAILABLE",
  );
};

export async function retrieveNews(options: {
  binding?: D1Database;
  request: NewsRetrievalRequest;
  previewItems?: Array<Record<string, unknown>>;
}): Promise<NewsRetrievalOutcome> {
  if (!options.request.hasCriteria) {
    return {
      ok: true,
      payload: responsePayload(options.request, [], 0, "NOT_QUERIED", null),
    };
  }
  if (options.binding) {
    try {
      return {
        ok: true,
        payload: await retrieveFromD1(
          options.binding,
          options.request,
          options.previewItems !== undefined,
        ),
      };
    } catch {
      // A Preview may fall back to its bounded immutable build snapshot below.
    }
  }
  if (options.previewItems !== undefined) {
    return { ok: true, payload: retrieveFromPreview(options.previewItems, options.request) };
  }
  return {
    ok: false,
    status: 503,
    code: "NEWS_RETRIEVAL_UNAVAILABLE",
    error: "新闻搜索暂不可用",
  };
}
