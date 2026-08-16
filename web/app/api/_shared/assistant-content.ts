export const ASSISTANT_CONTENT_PROTOCOL_VERSION = "assistant.content.v1" as const;
export const MAX_ASSISTANT_CONTENT_BLOCKS = 12;
export const MAX_ASSISTANT_CONTENT_BYTES = 65_536;

export type AssistantMarkdownBlock = {
  id: string;
  type: "markdown";
  version: "v1";
  data: { text: string };
  content_sha256: string;
};

export type AssistantNewsCardBlock = {
  id: string;
  type: "news_card";
  version: "v1";
  data: {
    evidence_id: string;
    source: string;
    published_at: string | null;
    received_at: string | null;
    headline: string;
    summary: string;
    category: string;
    impact: string;
    relevance: string | null;
    source_url: string | null;
  };
  content_sha256: string;
};

export type AssistantTableCell = string | number | boolean | null;
export type AssistantTableBlock = {
  id: string;
  type: "table";
  version: "v1";
  data: {
    caption: string | null;
    columns: Array<{ key: string; label: string; align: "left" | "right" | "center" }>;
    rows: AssistantTableCell[][];
  };
  content_sha256: string;
};

export type AssistantMetricBlock = {
  id: string;
  type: "metric";
  version: "v1";
  data: {
    label: string;
    value: string;
    unit: string | null;
    trend: "UP" | "DOWN" | "FLAT" | "UNKNOWN";
    detail: string | null;
  };
  content_sha256: string;
};

export type AssistantCalloutBlock = {
  id: string;
  type: "callout";
  version: "v1";
  data: {
    tone: "INFO" | "WARNING" | "INSUFFICIENT_EVIDENCE" | "BOUNDARY";
    title: string;
    body: string;
  };
  content_sha256: string;
};

export type AssistantContentBlock =
  | AssistantMarkdownBlock
  | AssistantNewsCardBlock
  | AssistantTableBlock
  | AssistantMetricBlock
  | AssistantCalloutBlock;

export type AssistantContentDocument = {
  protocol: typeof ASSISTANT_CONTENT_PROTOCOL_VERSION;
  blocks: AssistantContentBlock[];
  document_sha256: string;
};

export class AssistantContentInputError extends Error {
  readonly code = "INVALID_ASSISTANT_CONTENT";
}

const blockTypes = new Set(["markdown", "news_card", "table", "metric", "callout"]);
const blockId = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const evidenceId = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const columnKey = /^[a-z][a-z0-9_]{0,31}$/;
const digest = /^[0-9a-f]{64}$/;
const canonicalTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

const fail = (message: string): never => {
  throw new AssistantContentInputError(message);
};

const strictObject = (value: unknown, keys: readonly string[], label: string) => {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype) {
    return fail(`${label} 无效`);
  }
  const raw = value as Record<string, unknown>;
  if (Object.keys(raw).sort().join("|") !== [...keys].sort().join("|")) {
    return fail(`${label} 字段无效`);
  }
  return raw;
};

const text = (
  value: unknown,
  label: string,
  options: { minimum?: number; maximum: number; multiline?: boolean },
) => {
  if (typeof value !== "string" || [...value].some(character => {
    const code = character.charCodeAt(0);
    return code === 0 || code === 11 || code === 12;
  })) {
    return fail(`${label} 无效`);
  }
  const normalized = value.replace(/\r\n?/gu, "\n");
  if (!options.multiline && /[\n\t]/u.test(normalized)) return fail(`${label} 必须为单行`);
  const minimum = options.minimum ?? 0;
  const characterCount = [...normalized].length;
  if (characterCount < minimum || characterCount > options.maximum) {
    return fail(`${label} 长度无效`);
  }
  return normalized;
};

const nullableText = (value: unknown, label: string, maximum: number) => (
  value === null ? null : text(value, label, { maximum })
);

const time = (value: unknown, label: string) => {
  if (value === null) return null;
  const timestamp = text(value, label, { minimum: 24, maximum: 24 });
  const milliseconds = Date.parse(timestamp);
  if (!canonicalTime.test(timestamp)
    || !Number.isFinite(milliseconds)
    || new Date(milliseconds).toISOString() !== timestamp) {
    return fail(`${label} 时间无效`);
  }
  return timestamp;
};

const httpsUrl = (value: unknown) => {
  if (value === null) return null;
  const sourceUrl = text(value, "news_card.source_url", { minimum: 1, maximum: 2_048 });
  let parsed: URL;
  try {
    parsed = new URL(sourceUrl);
  } catch {
    return fail("news_card.source_url 无效");
  }
  if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) {
    return fail("news_card.source_url 必须为公开 HTTPS 地址");
  }
  return sourceUrl;
};

const tableCell = (value: unknown): AssistantTableCell => {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || Math.abs(value) > 1_000_000_000_000_000) {
      return fail("table 数字无效");
    }
    return value;
  }
  if (typeof value === "string") {
    return text(value, "table cell", { maximum: 500, multiline: true });
  }
  return fail("table cell 类型无效");
};

const dataFor = (type: string, value: unknown): AssistantContentBlock["data"] => {
  if (type === "markdown") {
    const raw = strictObject(value, ["text"], "markdown");
    return { text: text(raw.text, "markdown.text", {
      minimum: 1, maximum: 32_000, multiline: true,
    }) };
  }
  if (type === "news_card") {
    const raw = strictObject(value, [
      "evidence_id", "source", "published_at", "received_at", "headline",
      "summary", "category", "impact", "relevance", "source_url",
    ], "news_card");
    const id = text(raw.evidence_id, "news_card.evidence_id", {
      minimum: 1, maximum: 128,
    });
    if (!evidenceId.test(id)) return fail("news_card.evidence_id 无效");
    return {
      evidence_id: id,
      source: text(raw.source, "news_card.source", { maximum: 100 }),
      published_at: time(raw.published_at, "news_card.published_at"),
      received_at: time(raw.received_at, "news_card.received_at"),
      headline: text(raw.headline, "news_card.headline", { minimum: 1, maximum: 300 }),
      summary: text(raw.summary, "news_card.summary", { maximum: 600, multiline: true }),
      category: text(raw.category, "news_card.category", { maximum: 80 }),
      impact: text(raw.impact, "news_card.impact", { maximum: 600, multiline: true }),
      relevance: nullableText(raw.relevance, "news_card.relevance", 600),
      source_url: httpsUrl(raw.source_url),
    };
  }
  if (type === "table") {
    const raw = strictObject(value, ["caption", "columns", "rows"], "table");
    if (!Array.isArray(raw.columns) || raw.columns.length < 1 || raw.columns.length > 6) {
      return fail("table columns 无效");
    }
    const keys = new Set<string>();
    const columns = raw.columns.map(value => {
      const column = strictObject(value, ["key", "label", "align"], "table column");
      const key = text(column.key, "table column key", { minimum: 1, maximum: 32 });
      const align = text(column.align, "table column align", { minimum: 1, maximum: 8 });
      if (!columnKey.test(key) || keys.has(key)
        || (align !== "left" && align !== "right" && align !== "center")) {
        return fail("table column 无效");
      }
      keys.add(key);
      return {
        key,
        label: text(column.label, "table column label", { minimum: 1, maximum: 80 }),
        align,
      };
    });
    if (!Array.isArray(raw.rows) || raw.rows.length < 1 || raw.rows.length > 20) {
      return fail("table rows 无效");
    }
    const rows = raw.rows.map(row => {
      if (!Array.isArray(row) || row.length !== columns.length) {
        return fail("table row 宽度无效");
      }
      return row.map(tableCell);
    });
    return {
      caption: nullableText(raw.caption, "table.caption", 160),
      columns,
      rows,
    };
  }
  if (type === "metric") {
    const raw = strictObject(value, ["label", "value", "unit", "trend", "detail"], "metric");
    const trend = text(raw.trend, "metric.trend", { minimum: 2, maximum: 7 });
    if (trend !== "UP" && trend !== "DOWN" && trend !== "FLAT" && trend !== "UNKNOWN") {
      return fail("metric.trend 无效");
    }
    return {
      label: text(raw.label, "metric.label", { minimum: 1, maximum: 80 }),
      value: text(raw.value, "metric.value", { minimum: 1, maximum: 80 }),
      unit: nullableText(raw.unit, "metric.unit", 32),
      trend,
      detail: nullableText(raw.detail, "metric.detail", 240),
    };
  }
  if (type === "callout") {
    const raw = strictObject(value, ["tone", "title", "body"], "callout");
    const tone = text(raw.tone, "callout.tone", { minimum: 4, maximum: 32 });
    if (tone !== "INFO" && tone !== "WARNING"
      && tone !== "INSUFFICIENT_EVIDENCE" && tone !== "BOUNDARY") {
      return fail("callout.tone 无效");
    }
    return {
      tone,
      title: text(raw.title, "callout.title", { minimum: 1, maximum: 120 }),
      body: text(raw.body, "callout.body", { minimum: 1, maximum: 1_000, multiline: true }),
    };
  }
  return fail("Assistant content block 类型不支持");
};

export function parseAssistantContentDocument(
  value: unknown,
  options: { answer: string; evidenceIds?: readonly string[] },
): AssistantContentDocument {
  const document = strictObject(value, ["protocol", "blocks", "document_sha256"], "content document");
  if (document.protocol !== ASSISTANT_CONTENT_PROTOCOL_VERSION
    || !Array.isArray(document.blocks)
    || document.blocks.length < 1
    || document.blocks.length > MAX_ASSISTANT_CONTENT_BLOCKS) {
    return fail("Assistant content document 无效");
  }
  const allowedEvidence = new Set(options.evidenceIds ?? []);
  const seenIds = new Set<string>();
  const seenNews = new Set<string>();
  const blocks = document.blocks.map(value => {
    const raw = strictObject(
      value, ["id", "type", "version", "data", "content_sha256"], "content block",
    );
    const id = text(raw.id, "content block id", { minimum: 1, maximum: 128 });
    const type = text(raw.type, "content block type", { minimum: 1, maximum: 32 });
    if (!blockId.test(id) || seenIds.has(id) || !blockTypes.has(type) || raw.version !== "v1") {
      return fail("Assistant content block identity 无效");
    }
    seenIds.add(id);
    const data = dataFor(type, raw.data);
    if (type === "news_card") {
      const id = (data as AssistantNewsCardBlock["data"]).evidence_id;
      if (!allowedEvidence.has(id) || seenNews.has(id)) {
        return fail("news_card evidence 不属于本轮来源");
      }
      seenNews.add(id);
    }
    if (typeof raw.content_sha256 !== "string" || !digest.test(raw.content_sha256)) {
      return fail("Assistant content block hash 无效");
    }
    return { id, type, version: "v1", data, content_sha256: raw.content_sha256 } as AssistantContentBlock;
  });
  if (blocks[0].type !== "markdown" || blocks[0].data.text !== options.answer) {
    return fail("首个 markdown block 必须等于 canonical answer");
  }
  if (typeof document.document_sha256 !== "string" || !digest.test(document.document_sha256)) {
    return fail("Assistant content document hash 无效");
  }
  const normalized = {
    protocol: ASSISTANT_CONTENT_PROTOCOL_VERSION,
    blocks,
    document_sha256: document.document_sha256,
  } satisfies AssistantContentDocument;
  if (new TextEncoder().encode(canonicalJson(normalized)).length > MAX_ASSISTANT_CONTENT_BYTES) {
    return fail("Assistant content document 过大");
  }
  return structuredClone(normalized);
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) return fail("Assistant content number 无效");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
    return fail("Assistant content JSON 无效");
  }
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
    .join(",")}}`;
}

const sha256 = async (value: unknown) => {
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
};

export async function verifyAssistantContentDocument(
  value: unknown,
  options: { answer: string; evidenceIds?: readonly string[] },
) {
  const document = parseAssistantContentDocument(value, options);
  for (const block of document.blocks) {
    const core = { id: block.id, type: block.type, version: block.version, data: block.data };
    if (block.content_sha256 !== await sha256(core)) {
      return fail("Assistant content block hash 不匹配");
    }
  }
  const core = { protocol: document.protocol, blocks: document.blocks };
  if (document.document_sha256 !== await sha256(core)) {
    return fail("Assistant content document hash 不匹配");
  }
  return document;
}

const hashedBlock = async (
  id: string,
  type: "markdown" | "metric" | "callout",
  data: Record<string, unknown>,
) => {
  const core = { id, type, version: "v1" as const, data };
  return { ...core, content_sha256: await sha256(core) };
};

export async function buildAssistantTextContentDocument(
  answer: string,
  options: { evidenceIds?: readonly string[]; insufficientEvidence?: boolean } = {},
) {
  const evidenceIds = [...new Set(options.evidenceIds ?? [])];
  const blocks: Array<Record<string, unknown>> = [await hashedBlock(
    "block:answer", "markdown", { text: answer },
  )];
  if (evidenceIds.length) {
    blocks.push(await hashedBlock("block:metric:evidence", "metric", {
      label: "本轮检索证据",
      value: String(evidenceIds.length),
      unit: "条",
      trend: "UNKNOWN",
      detail: "已通过 shared news retrieval 验证",
    }));
  }
  if (options.insufficientEvidence) {
    blocks.push(await hashedBlock("block:insufficient-evidence", "callout", {
      tone: "INSUFFICIENT_EVIDENCE",
      title: "证据不足",
      body: "当前没有足够的已收录证据支持具体市场解释。",
    }));
  }
  const core = { protocol: ASSISTANT_CONTENT_PROTOCOL_VERSION, blocks };
  const document = { ...core, document_sha256: await sha256(core) };
  return verifyAssistantContentDocument(document, { answer, evidenceIds });
}
