import {
  ACTIVE_NEWS_SQL,
  NEWS_REVIEW_STATE_CASE_SQL,
  NEWS_REVIEW_STATE_INVARIANT_SQL,
  type NewsReviewState,
} from "../../_lib/news-review-state";

export const NEWS_PROJECTION_CONTRACT_VERSION = "news-projection-generation-v4";
const NEWS_PROJECTION_RECEIPT_INDEX_CONTRACT = "news-projection-generation-v4";
export const NEWS_GENERATION_ID = /^[a-f0-9]{64}$/;
export const NEWS_PROJECTION_MAX_ITEMS = 10_000;
export const NEWS_INDEX_MAX_BATCH_ITEMS = 4;
export const NEWS_DETAIL_MAX_BATCH_ITEMS = 8;
export const NEWS_PROJECTION_CATEGORIES = [
  "利率/Fed", "通胀/就业", "增长/经济", "美元/流动性", "油价/能源",
  "战争/地缘", "央行购金", "风险情绪 / 避险", "监管/其他", "其他",
] as const;
const NEWS_PROJECTION_CATEGORIES_SQL = NEWS_PROJECTION_CATEGORIES
  .map(value => `'${value}'`).join(",");
export const NEWS_PROJECTION_STAGING_TTL_MS = 24 * 60 * 60_000;
export const EMPTY_RECEIPT_DIGEST =
  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

const SHA256 = /^[a-f0-9]{64}$/;
const CONTRACT = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const CANONICAL_EXPIRY = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$/;

export type NewsProjectionManifest = {
  generation_id: string;
  snapshot_id: string;
  contract_version: string;
  window_start: string;
  watermark: string;
  expected_index_count: number;
  expected_detail_count: number;
  withdrawal_count: number;
  source_digest: string;
  expected_receipt_digest: string;
};

export type NewsProjectionIndexItem = {
  detail_key?: unknown;
  category?: unknown;
  cluster_id?: unknown;
  source_published_time?: unknown;
  collector_first_seen_time?: unknown;
  parsed_at?: unknown;
  model_visibility?: unknown;
  impact_expires_at?: unknown;
  mirror_contract?: unknown;
  annotation_status?: unknown;
  [key: string]: unknown;
};

export type NewsProjectionDetailItem = {
  detail_key?: unknown;
  detail_hash?: unknown;
  payload?: unknown;
};

type GenerationRow = {
  generation_id: string;
  snapshot_id: string;
  state: string;
  contract_version: string;
  window_start: string;
  watermark: string;
  expected_index_count: number;
  expected_detail_count: number;
  withdrawal_count: number;
  source_digest: string;
  expected_receipt_digest: string;
  receipt_digest: string;
  next_detail_offset: number;
  next_index_offset: number;
  staged_detail_count: number;
  staged_index_count: number;
  missing_detail_count: number;
  invariant_violation_count: number;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
};

export type NewsProjectionState = {
  active_generation_id: string;
  snapshot_id: string;
  contract_version: string;
  source_digest: string;
  receipt_digest: string;
  index_count: number;
  detail_count: number;
  missing_detail_count: number;
  invariant_violation_count: number;
  projection_state: string;
  activated_at: string;
  verified_at: string;
};

export class NewsProjectionProtocolError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    message: string, status: number, code: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function safeCount(value: unknown) {
  return Number.isSafeInteger(value) && Number(value) >= 0
    && Number(value) <= NEWS_PROJECTION_MAX_ITEMS;
}

export function validateNewsProjectionManifest(value: unknown): NewsProjectionManifest {
  const row = value as Partial<NewsProjectionManifest> | null;
  if (
    !row || !NEWS_GENERATION_ID.test(String(row.generation_id ?? ""))
    || !NEWS_GENERATION_ID.test(String(row.snapshot_id ?? ""))
    || row.contract_version !== NEWS_PROJECTION_CONTRACT_VERSION
    || typeof row.window_start !== "string" || Number.isNaN(Date.parse(row.window_start))
    || typeof row.watermark !== "string" || Number.isNaN(Date.parse(row.watermark))
    || row.window_start > row.watermark
    || !safeCount(row.expected_index_count) || !safeCount(row.expected_detail_count)
    || Number(row.expected_detail_count) < Number(row.expected_index_count)
    || !safeCount(row.withdrawal_count)
    || !SHA256.test(String(row.source_digest ?? ""))
    || !SHA256.test(String(row.expected_receipt_digest ?? ""))
  ) {
    throw new NewsProjectionProtocolError(
      "invalid news projection manifest", 400, "NEWS_PROJECTION_MANIFEST_INVALID",
    );
  }
  return row as NewsProjectionManifest;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, "0")).join("");
}

function utf8Compare(left: string, right: string) {
  const encodedLeft = new TextEncoder().encode(left);
  const encodedRight = new TextEncoder().encode(right);
  const length = Math.min(encodedLeft.length, encodedRight.length);
  for (let index = 0; index < length; index += 1) {
    if (encodedLeft[index] !== encodedRight[index]) {
      return encodedLeft[index] - encodedRight[index];
    }
  }
  return encodedLeft.length - encodedRight.length;
}

function canonicalReceiptValue(value: unknown): string {
  if (value === null) return "n;";
  if (value === true) return "t;";
  if (value === false) return "f;";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new NewsProjectionProtocolError(
        "receipt number is outside the supported JSON range", 400,
        "NEWS_PROJECTION_RECEIPT_NUMBER_INVALID",
      );
    }
    const buffer = new ArrayBuffer(8);
    new DataView(buffer).setFloat64(0, Object.is(value, -0) ? 0 : value, false);
    const hex = Array.from(new Uint8Array(buffer), byte =>
      byte.toString(16).padStart(2, "0")).join("");
    return `d${hex};`;
  }
  if (typeof value === "string") {
    const length = new TextEncoder().encode(value).length;
    return `s${length}:${value};`;
  }
  if (Array.isArray(value)) {
    return `a${value.length}:${value.map(canonicalReceiptValue).join("")};`;
  }
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    const keys = Object.keys(row).sort(utf8Compare);
    return `o${keys.length}:${keys.map(key =>
      canonicalReceiptValue(key) + canonicalReceiptValue(row[key])).join("")};`;
  }
  throw new NewsProjectionProtocolError(
    "receipt value is not valid JSON", 400, "NEWS_PROJECTION_RECEIPT_VALUE_INVALID",
  );
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== typeof right) return false;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => jsonValuesEqual(value, right[index]));
  }
  if (typeof left !== "object") return false;
  const leftRow = left as Record<string, unknown>;
  const rightRow = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRow);
  const rightKeys = Object.keys(rightRow);
  return leftKeys.length === rightKeys.length && leftKeys.every(key =>
    Object.prototype.hasOwnProperty.call(rightRow, key)
    && jsonValuesEqual(leftRow[key], rightRow[key]));
}

export async function newsProjectionPayloadHash(value: unknown) {
  return sha256(canonicalReceiptValue(value));
}

export async function advanceNewsReceiptDigest(
  previous: string, kind: "detail" | "index", offset: number,
  itemCount: number, payloadHash: string,
) {
  return sha256(`${previous}\n${kind}|${offset}|${itemCount}|${payloadHash}`);
}

async function advanceNewsIdentityDigest(
  previous: string, items: Array<{ detail_key?: unknown }>,
) {
  let digest = previous;
  for (const item of items) digest = await sha256(`${digest}\n${String(item.detail_key)}`);
  return digest;
}

function manifestMatches(row: GenerationRow, manifest: NewsProjectionManifest) {
  return row.snapshot_id === manifest.snapshot_id
    && row.contract_version === manifest.contract_version
    && row.window_start === manifest.window_start
    && row.watermark === manifest.watermark
    && Number(row.expected_index_count) === manifest.expected_index_count
    && Number(row.expected_detail_count) === manifest.expected_detail_count
    && Number(row.withdrawal_count) === manifest.withdrawal_count
    && row.source_digest === manifest.source_digest
    && row.expected_receipt_digest === manifest.expected_receipt_digest;
}

async function generation(
  binding: D1Database, generationId: string,
): Promise<GenerationRow | null> {
  return binding.prepare(
    "SELECT * FROM news_projection_generations WHERE generation_id=?",
  ).bind(generationId).first<GenerationRow>();
}

async function generationProgress(binding: D1Database, generationId: string) {
  return binding.prepare(
    `SELECT
       COALESCE((SELECT batch_offset+item_count FROM news_projection_receipts_v2
         WHERE generation_id=? AND batch_kind='detail'
         ORDER BY batch_offset DESC LIMIT 1),0) next_detail_offset,
       COALESCE((SELECT batch_offset+item_count FROM news_projection_receipts_v2
         WHERE generation_id=? AND batch_kind='index'
         ORDER BY batch_offset DESC LIMIT 1),0) next_index_offset,
       COALESCE(
         (SELECT receipt_digest FROM news_projection_receipts_v2
           WHERE generation_id=? AND batch_kind='index'
           ORDER BY batch_offset DESC LIMIT 1),
         (SELECT receipt_digest FROM news_projection_receipts_v2
           WHERE generation_id=? AND batch_kind='detail'
           ORDER BY batch_offset DESC LIMIT 1),
         ?) receipt_digest,
       COALESCE((SELECT identity_digest FROM news_projection_receipts_v2
         WHERE generation_id=? AND batch_kind='detail'
         ORDER BY batch_offset DESC LIMIT 1),?) detail_identity_digest,
       COALESCE((SELECT identity_digest FROM news_projection_receipts_v2
         WHERE generation_id=? AND batch_kind='index'
         ORDER BY batch_offset DESC LIMIT 1),?) index_identity_digest,
       COALESCE(
         (SELECT updated_at FROM news_projection_receipts_v2
           WHERE generation_id=? AND batch_kind='index'
           ORDER BY batch_offset DESC LIMIT 1),
         (SELECT updated_at FROM news_projection_receipts_v2
           WHERE generation_id=? AND batch_kind='detail'
           ORDER BY batch_offset DESC LIMIT 1),
         NULL) updated_at`,
  ).bind(
    generationId, generationId, generationId, generationId,
    EMPTY_RECEIPT_DIGEST, generationId, EMPTY_RECEIPT_DIGEST,
    generationId, EMPTY_RECEIPT_DIGEST, generationId, generationId,
  ).first<{
    next_detail_offset: number; next_index_offset: number;
    receipt_digest: string; detail_identity_digest: string;
    index_identity_digest: string; updated_at: string | null;
  }>();
}

export async function readNewsProjectionState(binding: D1Database) {
  return binding.prepare(
    `SELECT active_generation_id,snapshot_id,contract_version,source_digest,
            receipt_digest,index_count,detail_count,missing_detail_count,
            invariant_violation_count,projection_state,activated_at,verified_at
       FROM news_projection_state WHERE id=1`,
  ).first<NewsProjectionState>();
}

async function deleteGenerationStatements(
  binding: D1Database, generationId: string,
) {
  return [
    binding.prepare("DELETE FROM news_projection_counts WHERE generation_id=?").bind(generationId),
    binding.prepare("DELETE FROM news_projection_index WHERE generation_id=?").bind(generationId),
    binding.prepare("DELETE FROM news_projection_details WHERE generation_id=?").bind(generationId),
    binding.prepare("DELETE FROM news_projection_receipts_v2 WHERE generation_id=?").bind(generationId),
    binding.prepare("DELETE FROM news_projection_batches WHERE generation_id=?").bind(generationId),
    binding.prepare("DELETE FROM news_projection_generations WHERE generation_id=?").bind(generationId),
    binding.prepare(
      `DELETE FROM news_index
        WHERE COALESCE(json_extract(payload,'$.annotation_status'),'')='SUPERSEDED_CONTRACT'
          AND NOT EXISTS (
            SELECT 1 FROM news_projection_index retained
             WHERE retained.detail_key=news_index.detail_key
          )`,
    ),
    binding.prepare(
      `DELETE FROM news_details
        WHERE NOT EXISTS (
                SELECT 1 FROM news_index retained
                 WHERE retained.detail_key=news_details.detail_key
              )
          AND NOT EXISTS (
                SELECT 1 FROM news_projection_index retained
                 WHERE retained.detail_key=news_details.detail_key
              )`,
    ),
  ];
}

export async function abandonNewsProjection(
  binding: D1Database, generationId: string,
) {
  if (!NEWS_GENERATION_ID.test(generationId)) {
    throw new NewsProjectionProtocolError(
      "invalid news generation", 400, "NEWS_PROJECTION_GENERATION_INVALID",
    );
  }
  const row = await generation(binding, generationId);
  if (!row) return { status: "OK", abandoned: generationId, unchanged: true };
  if (row.state !== "STAGING") {
    throw new NewsProjectionProtocolError(
      "only a staging news generation can be abandoned", 409,
      "NEWS_PROJECTION_ABANDON_CURRENT_REJECTED",
    );
  }
  await binding.batch(await deleteGenerationStatements(binding, generationId));
  return { status: "OK", abandoned: generationId };
}

export async function prepareNewsProjection(
  binding: D1Database, rawManifest: unknown,
) {
  const manifest = validateNewsProjectionManifest(rawManifest);
  const active = await readNewsProjectionState(binding);
  if (
    active?.active_generation_id === manifest.generation_id
    && active.snapshot_id === manifest.snapshot_id
    && active.source_digest === manifest.source_digest
    && active.receipt_digest === manifest.expected_receipt_digest
    && Number(active.index_count) === manifest.expected_index_count
    && Number(active.detail_count) === manifest.expected_detail_count
    && Number(active.missing_detail_count) === 0
    && Number(active.invariant_violation_count) === 0
  ) {
    return {
      status: "OK", active: true, generation_id: manifest.generation_id,
      next_detail_offset: manifest.expected_detail_count,
      next_index_offset: manifest.expected_index_count,
    };
  }
  let existing = await generation(binding, manifest.generation_id);
  if (existing) {
    if (!manifestMatches(existing, manifest)) {
      throw new NewsProjectionProtocolError(
        "news generation manifest changed", 409, "NEWS_PROJECTION_MANIFEST_MISMATCH",
      );
    }
    const progress = await generationProgress(binding, existing.generation_id);
    const expired = existing.state === "STAGING"
      && Date.parse(progress?.updated_at ?? existing.updated_at)
        <= Date.now() - NEWS_PROJECTION_STAGING_TTL_MS;
    if (!expired) {
      return {
        status: "OK", active: false, generation_id: existing.generation_id,
        next_detail_offset: Number(progress?.next_detail_offset ?? 0),
        next_index_offset: Number(progress?.next_index_offset ?? 0),
        receipt_digest: progress?.receipt_digest ?? EMPTY_RECEIPT_DIGEST,
      };
    }
    await binding.batch(await deleteGenerationStatements(binding, existing.generation_id));
    existing = null;
  }

  const staging = await binding.prepare(
    `SELECT generation_id,updated_at FROM news_projection_generations
      WHERE state='STAGING' LIMIT 1`,
  ).first<{ generation_id: string; updated_at: string }>();
  const statements: D1PreparedStatement[] = [];
  if (staging) {
    const stagingProgress = await generationProgress(binding, staging.generation_id);
    const expired = Date.parse(stagingProgress?.updated_at ?? staging.updated_at)
      <= Date.now() - NEWS_PROJECTION_STAGING_TTL_MS;
    if (!expired) {
      throw new NewsProjectionProtocolError(
        "another news generation is staging", 409, "NEWS_PROJECTION_STAGING_BUSY",
        { staging_generation_id: staging.generation_id },
      );
    }
    statements.push(...await deleteGenerationStatements(binding, staging.generation_id));
  }
  const superseded = await binding.prepare(
    `SELECT generation_id FROM news_projection_generations
      WHERE state='SUPERSEDED' ORDER BY activated_at DESC`,
  ).all<{ generation_id: string }>();
  for (const row of superseded.results) {
    statements.push(...await deleteGenerationStatements(binding, row.generation_id));
  }
  const now = new Date().toISOString();
  statements.push(binding.prepare(
    `INSERT INTO news_projection_generations
       (generation_id,snapshot_id,state,contract_version,window_start,watermark,
        expected_index_count,expected_detail_count,withdrawal_count,source_digest,
        expected_receipt_digest,receipt_digest,next_detail_offset,next_index_offset,
        staged_detail_count,staged_index_count,missing_detail_count,
        invariant_violation_count,created_at,updated_at,activated_at)
     VALUES (?,?,'STAGING',?,?,?,?,?,?,?,?,?,0,0,0,0,0,0,?,?,NULL)`,
  ).bind(
    manifest.generation_id, manifest.snapshot_id, manifest.contract_version,
    manifest.window_start, manifest.watermark, manifest.expected_index_count,
    manifest.expected_detail_count, manifest.withdrawal_count, manifest.source_digest,
    manifest.expected_receipt_digest, EMPTY_RECEIPT_DIGEST, now, now,
  ));
  await binding.batch(statements);
  return {
    status: "OK", active: false, generation_id: manifest.generation_id,
    next_detail_offset: 0, next_index_offset: 0,
    receipt_digest: EMPTY_RECEIPT_DIGEST,
  };
}

function validDetail(item: NewsProjectionDetailItem) {
  return typeof item.detail_key === "string" && SHA256.test(item.detail_key)
    && typeof item.detail_hash === "string" && SHA256.test(item.detail_hash)
    && item.payload !== null && typeof item.payload === "object";
}

function validIndex(item: NewsProjectionIndexItem) {
  return typeof item.detail_key === "string" && SHA256.test(item.detail_key)
    && typeof item.category === "string"
    && (NEWS_PROJECTION_CATEGORIES as readonly string[]).includes(item.category)
    && typeof item.cluster_id === "string"
    && typeof item.collector_first_seen_time === "string"
    && typeof item.mirror_contract === "string" && CONTRACT.test(item.mirror_contract)
    && (item.model_visibility !== "MODEL_VISIBLE"
      || (typeof item.impact_expires_at === "string"
        && CANONICAL_EXPIRY.test(item.impact_expires_at)));
}

export async function stageNewsProjectionBatch(
  binding: D1Database, kind: "detail" | "index", generationId: string,
  offset: number, items: Array<NewsProjectionDetailItem | NewsProjectionIndexItem>,
) {
  if (
    !NEWS_GENERATION_ID.test(generationId) || !Number.isSafeInteger(offset) || offset < 0
    || !Array.isArray(items) || items.length < 1
    || items.length > (kind === "detail"
      ? NEWS_DETAIL_MAX_BATCH_ITEMS : NEWS_INDEX_MAX_BATCH_ITEMS)
    || items.some(item => kind === "detail"
      ? !validDetail(item as NewsProjectionDetailItem)
      : !validIndex(item as NewsProjectionIndexItem))
  ) {
    throw new NewsProjectionProtocolError(
      "invalid news projection batch", 400, "NEWS_PROJECTION_BATCH_INVALID",
    );
  }
  const row = await generation(binding, generationId);
  if (!row || row.state !== "STAGING") {
    throw new NewsProjectionProtocolError(
      "news generation is not staging", 409, "NEWS_PROJECTION_NOT_STAGING",
    );
  }
  const expected = kind === "detail"
    ? Number(row.expected_detail_count) : Number(row.expected_index_count);
  const progress = await generationProgress(binding, generationId);
  const nextOffset = kind === "detail"
    ? Number(progress?.next_detail_offset ?? 0) : Number(progress?.next_index_offset ?? 0);
  if (kind === "index"
      && Number(progress?.next_detail_offset ?? 0) !== Number(row.expected_detail_count)) {
    throw new NewsProjectionProtocolError(
      "news details are incomplete", 409, "NEWS_PROJECTION_DETAILS_INCOMPLETE",
    );
  }
  if (offset + items.length > expected) {
    throw new NewsProjectionProtocolError(
      "news batch exceeds manifest", 409, "NEWS_PROJECTION_BATCH_OVERFLOW",
    );
  }
  const payloadHash = await newsProjectionPayloadHash(items);
  if (offset < nextOffset) {
    const receipt = await binding.prepare(
      `SELECT item_count,payload_hash,receipt_digest FROM news_projection_receipts_v2
        WHERE generation_id=? AND batch_kind=? AND batch_offset=?`,
    ).bind(generationId, kind, offset).first<{
      item_count: number; payload_hash: string; receipt_digest: string;
    }>();
    if (
      receipt && Number(receipt.item_count) === items.length
      && receipt.payload_hash === payloadHash && offset + items.length <= nextOffset
    ) {
      return {
        status: "OK", duplicate: true, received: items.length,
        receipt_digest: receipt.receipt_digest,
      };
    }
    throw new NewsProjectionProtocolError(
      "news batch contradicts its receipt", 409, "NEWS_PROJECTION_RECEIPT_CONTRADICTION",
    );
  }
  if (offset !== nextOffset) {
    throw new NewsProjectionProtocolError(
      "news batch offset mismatch", 409, "NEWS_PROJECTION_OFFSET_MISMATCH",
      { expected: nextOffset, received: offset },
    );
  }
  const receiptDigest = await advanceNewsReceiptDigest(
    progress?.receipt_digest ?? EMPTY_RECEIPT_DIGEST,
    kind, offset, items.length, payloadHash,
  );
  const identityDigest = await advanceNewsIdentityDigest(
    (kind === "detail"
      ? progress?.detail_identity_digest : progress?.index_identity_digest)
      ?? EMPTY_RECEIPT_DIGEST,
    items,
  );
  const now = new Date().toISOString();
  const statements: D1PreparedStatement[] = [];
  if (kind === "detail") {
    const detailItems = items as NewsProjectionDetailItem[];
    const detailKeys = detailItems.map(item => String(item.detail_key));
    const placeholders = detailKeys.map(() => "?").join(",");
    const existing = await binding.prepare(
      `SELECT detail_key,detail_hash,payload FROM news_details
        WHERE detail_key IN (${placeholders})`,
    ).bind(...detailKeys).all<{
      detail_key: string; detail_hash: string; payload: string;
    }>();
    const byKey = new Map(existing.results.map(row => [row.detail_key, row]));
    for (const item of detailItems) {
      const serialized = JSON.stringify(item.payload);
      const prior = byKey.get(String(item.detail_key));
      if (prior) {
        let priorPayload: unknown;
        try {
          priorPayload = JSON.parse(prior.payload);
        } catch {
          priorPayload = undefined;
        }
        if (
          prior.detail_hash !== item.detail_hash
          || !jsonValuesEqual(priorPayload, item.payload)
        ) {
          throw new NewsProjectionProtocolError(
            "news detail contradicts immutable evidence", 409,
            "NEWS_PROJECTION_DETAIL_CONTRADICTION",
          );
        }
        continue;
      }
      statements.push(binding.prepare(
        `INSERT INTO news_details (detail_key,detail_hash,payload,received_at)
         VALUES (?,?,?,?)`,
      ).bind(item.detail_key, item.detail_hash, serialized, now));
    }
  }
  const identityKeys = items.map(item => String(item.detail_key));
  statements.push(binding.prepare(
    `INSERT INTO news_projection_receipts_v2
       (generation_id,batch_kind,batch_offset,item_count,payload_hash,receipt_digest,
        identity_digest,identity_keys_json,items_json,updated_at)
     VALUES (?,?,?,?,?,?,?,?,?,?)`,
  ).bind(
    generationId, kind, offset, items.length, payloadHash, receiptDigest,
    identityDigest, JSON.stringify(identityKeys),
    kind === "index" ? JSON.stringify(items) : "[]", now,
  ));
  await binding.batch(statements);
  return { status: "OK", received: items.length, receipt_digest: receiptDigest };
}

const receiptIndexRowsSql = `
  SELECT r.generation_id,
         json_extract(j.value,'$.detail_key') detail_key,
         r.batch_offset+CAST(j.key AS INTEGER) ordinal,
         json_extract(j.value,'$.category') category,
         json_extract(j.value,'$.cluster_id') cluster_id,
         COALESCE(json_extract(j.value,'$.source_published_time'),
                  json_extract(j.value,'$.collector_first_seen_time')) published_time,
         json_extract(j.value,'$.collector_first_seen_time') collector_first_seen_time,
         CASE WHEN json_type(j.value,'$.parsed_at')='text' THEN 1 ELSE 0 END parsed,
         CASE WHEN json_extract(j.value,'$.model_visibility')='MODEL_VISIBLE'
              THEN 1 ELSE 0 END model_candidate,
         json_extract(j.value,'$.impact_expires_at') impact_expires_at,
         json_extract(j.value,'$.mirror_contract') mirror_contract,
         j.value payload
    FROM news_projection_receipts_v2 r,json_each(r.items_json) j
   WHERE r.generation_id=? AND r.batch_kind='index'`;

async function projectionCounts(
  binding: D1Database, generationId: string, contractVersion?: string,
) {
  const contract = contractVersion ?? (await generation(binding, generationId))?.contract_version;
  if (contract === NEWS_PROJECTION_RECEIPT_INDEX_CONTRACT) {
    return binding.prepare(
      `WITH projection AS (${receiptIndexRowsSql})
       SELECT
         (SELECT count(*) FROM projection) index_count,
         (SELECT count(*) FROM projection i WHERE EXISTS (
            SELECT 1 FROM news_details d WHERE d.detail_key=i.detail_key
          )) detail_count,
         (SELECT count(*) FROM projection i WHERE NOT EXISTS (
            SELECT 1 FROM news_details d WHERE d.detail_key=i.detail_key
          )) missing_detail_count,
         (SELECT count(*) FROM projection WHERE (
            NOT ${NEWS_REVIEW_STATE_INVARIANT_SQL}
            OR category NOT IN (${NEWS_PROJECTION_CATEGORIES_SQL})
            OR (model_candidate=1 AND (
              impact_expires_at IS NULL OR length(impact_expires_at)<>32
              OR substr(impact_expires_at,27)<>'+00:00'
            ))
          )) review_violation_count,
         (SELECT count(*) FROM (
            SELECT cluster_id FROM projection GROUP BY cluster_id HAVING count(*)>1
          )) duplicate_cluster_count`,
    ).bind(generationId).first<{
      index_count: number; detail_count: number; missing_detail_count: number;
      review_violation_count: number; duplicate_cluster_count: number;
    }>();
  }
  return binding.prepare(
    `SELECT
       (SELECT count(*) FROM news_projection_index WHERE generation_id=?) index_count,
       (SELECT count(*) FROM news_projection_index i
         WHERE i.generation_id=? AND EXISTS (
           SELECT 1 FROM news_details d WHERE d.detail_key=i.detail_key
         )) detail_count,
       (SELECT count(*) FROM news_projection_index i
         WHERE i.generation_id=? AND NOT EXISTS (
           SELECT 1 FROM news_details d WHERE d.detail_key=i.detail_key
         )) missing_detail_count,
       (SELECT count(*) FROM news_projection_index
         WHERE generation_id=? AND (
           NOT ${NEWS_REVIEW_STATE_INVARIANT_SQL}
           OR category NOT IN (${NEWS_PROJECTION_CATEGORIES_SQL})
           OR (model_candidate=1 AND (
             impact_expires_at IS NULL OR length(impact_expires_at)<>32
             OR substr(impact_expires_at,27)<>'+00:00'
           ))
         ))
         review_violation_count,
       (SELECT count(*) FROM (
          SELECT cluster_id FROM news_projection_index WHERE generation_id=?
           GROUP BY cluster_id HAVING count(*)>1
        )) duplicate_cluster_count`,
  ).bind(
    generationId, generationId, generationId, generationId, generationId,
  ).first<{
    index_count: number; detail_count: number; missing_detail_count: number;
    review_violation_count: number; duplicate_cluster_count: number;
  }>();
}

async function reverseProjectionViolationCount(
  binding: D1Database, generationId: string, contractVersion: string,
) {
  if (contractVersion !== NEWS_PROJECTION_RECEIPT_INDEX_CONTRACT) return 0;
  const row = await binding.prepare(
    `WITH projection AS (${receiptIndexRowsSql})
     SELECT
       (SELECT count(*) FROM projection p
         WHERE NOT EXISTS (
           SELECT 1 FROM news_index n WHERE n.detail_key=p.detail_key
             AND ${ACTIVE_NEWS_SQL.replaceAll("payload", "n.payload")}
             AND n.category IS p.category AND n.cluster_id IS p.cluster_id
             AND n.published_time IS p.published_time
             AND n.collector_first_seen_time IS p.collector_first_seen_time
             AND n.parsed IS p.parsed AND n.model_candidate IS p.model_candidate
             AND n.impact_expires_at IS p.impact_expires_at
             AND n.mirror_contract IS p.mirror_contract AND n.payload IS p.payload
         ))
       +
       (SELECT count(*) FROM news_index n
         WHERE ${ACTIVE_NEWS_SQL.replaceAll("payload", "n.payload")}
           AND n.detail_key NOT IN (
             SELECT detail_key FROM projection
           )) violation_count`,
  ).bind(generationId).first<{ violation_count: number }>();
  return Number(row?.violation_count ?? -1);
}

export async function activateNewsProjection(
  binding: D1Database, generationId: string,
) {
  if (!NEWS_GENERATION_ID.test(generationId)) {
    throw new NewsProjectionProtocolError(
      "invalid news generation", 400, "NEWS_PROJECTION_GENERATION_INVALID",
    );
  }
  const active = await readNewsProjectionState(binding);
  if (active?.active_generation_id === generationId
      && active.projection_state === "CURRENT") {
    return {
      status: "OK", activated: generationId, unchanged: true,
      index_count: Number(active.index_count), detail_count: Number(active.detail_count),
    };
  }
  const row = await generation(binding, generationId);
  if (!row || row.state !== "STAGING") {
    throw new NewsProjectionProtocolError(
      "news generation is not staging", 409, "NEWS_PROJECTION_NOT_STAGING",
    );
  }
  const progress = await generationProgress(binding, generationId);
  const counts = await projectionCounts(binding, generationId, row.contract_version);
  const invariantViolations = Number(counts?.review_violation_count ?? -1)
    + Number(counts?.duplicate_cluster_count ?? -1);
  const complete = Number(progress?.next_detail_offset ?? -1) === Number(row.expected_detail_count)
    && Number(progress?.next_index_offset ?? -1) === Number(row.expected_index_count)
    && Number(counts?.detail_count ?? -1) === Number(row.expected_detail_count)
    && Number(counts?.index_count ?? -1) === Number(row.expected_index_count)
    && Number(counts?.missing_detail_count ?? -1) === 0
    && invariantViolations === 0
    && progress?.detail_identity_digest === progress?.index_identity_digest
    && progress?.receipt_digest === row.expected_receipt_digest;
  if (!complete) {
    throw new NewsProjectionProtocolError(
      "news generation is incomplete", 409, "NEWS_PROJECTION_INCOMPLETE", {
        expected_index_count: Number(row.expected_index_count),
        expected_detail_count: Number(row.expected_detail_count),
        staged_index_count: Number(progress?.next_index_offset ?? -1),
        staged_detail_count: Number(progress?.next_detail_offset ?? -1),
        stored_index_count: Number(counts?.index_count ?? -1),
        stored_detail_count: Number(counts?.detail_count ?? -1),
        missing_detail_count: Number(counts?.missing_detail_count ?? -1),
        invariant_violation_count: invariantViolations,
        identity_match: progress?.detail_identity_digest === progress?.index_identity_digest,
        receipt_match: progress?.receipt_digest === row.expected_receipt_digest,
      },
    );
  }
  const now = new Date().toISOString();
  const projectionSource = row.contract_version === NEWS_PROJECTION_RECEIPT_INDEX_CONTRACT
    ? receiptIndexRowsSql
    : `SELECT generation_id,detail_key,ordinal,category,cluster_id,published_time,
              collector_first_seen_time,parsed,model_candidate,impact_expires_at,
              mirror_contract,payload
         FROM news_projection_index WHERE generation_id=?`;
  await binding.batch([
    binding.prepare(
      `WITH projection AS (${projectionSource})
       INSERT INTO news_projection_counts
         (generation_id,review_state,category,item_count,parsed_count,candidate_expiries)
       SELECT generation_id,review_state,category,count(*),sum(parsed),''
         FROM (
           SELECT generation_id,category,parsed,
                   ${NEWS_REVIEW_STATE_CASE_SQL} review_state
             FROM projection
          ) GROUP BY generation_id,review_state,category
       UNION ALL
       SELECT generation_id,review_state,'',count(*),sum(parsed),''
         FROM (
           SELECT generation_id,parsed,
                   ${NEWS_REVIEW_STATE_CASE_SQL} review_state
             FROM projection
          ) GROUP BY generation_id,review_state
       UNION ALL
       SELECT generation_id,'ALL','',count(*),sum(parsed),
              COALESCE(group_concat(
                CASE WHEN model_candidate=1 THEN impact_expires_at END,
                char(10) ORDER BY impact_expires_at
              ),'')
         FROM projection GROUP BY generation_id`,
    ).bind(generationId),
    binding.prepare(
      `UPDATE news_projection_generations SET state='SUPERSEDED',updated_at=?
        WHERE state='CURRENT' AND generation_id<>?`,
    ).bind(now, generationId),
    binding.prepare(
      `UPDATE news_projection_generations
          SET state='CURRENT',missing_detail_count=0,invariant_violation_count=0,
              receipt_digest=?,next_detail_offset=?,next_index_offset=?,
              staged_detail_count=?,staged_index_count=?,activated_at=?,updated_at=?
        WHERE generation_id=? AND state='STAGING'`,
    ).bind(
      progress?.receipt_digest, Number(progress?.next_detail_offset),
      Number(progress?.next_index_offset), Number(progress?.next_detail_offset),
      Number(progress?.next_index_offset), now, now, generationId,
    ),
    binding.prepare(
      `INSERT INTO news_projection_state
         (id,active_generation_id,snapshot_id,contract_version,source_digest,
          receipt_digest,index_count,detail_count,missing_detail_count,
          invariant_violation_count,projection_state,activated_at,verified_at)
       VALUES (1,?,?,?,?,?,?,?,?,?,'ACTIVATING',?,?)
       ON CONFLICT(id) DO UPDATE SET
         active_generation_id=excluded.active_generation_id,
         snapshot_id=excluded.snapshot_id,contract_version=excluded.contract_version,
         source_digest=excluded.source_digest,receipt_digest=excluded.receipt_digest,
         index_count=excluded.index_count,detail_count=excluded.detail_count,
         missing_detail_count=0,invariant_violation_count=0,
         projection_state='ACTIVATING',activated_at=excluded.activated_at,
         verified_at=excluded.verified_at`,
    ).bind(
      generationId, row.snapshot_id, row.contract_version, row.source_digest,
      progress?.receipt_digest, Number(row.expected_index_count),
      Number(row.expected_detail_count), 0, 0, now, now,
    ),
    binding.prepare(
      `WITH projection AS (${projectionSource})
       INSERT INTO news_index
         (detail_key,category,cluster_id,published_time,collector_first_seen_time,
           parsed,model_candidate,impact_expires_at,mirror_contract,payload,received_at)
       SELECT detail_key,category,cluster_id,published_time,collector_first_seen_time,
               parsed,model_candidate,impact_expires_at,mirror_contract,payload,?
          FROM projection WHERE true
       ON CONFLICT(detail_key) DO UPDATE SET
         category=excluded.category,cluster_id=excluded.cluster_id,
         published_time=excluded.published_time,
         collector_first_seen_time=excluded.collector_first_seen_time,
         parsed=excluded.parsed,model_candidate=excluded.model_candidate,
          impact_expires_at=excluded.impact_expires_at,
          mirror_contract=excluded.mirror_contract,payload=excluded.payload,
          received_at=excluded.received_at
       WHERE news_index.category IS NOT excluded.category
          OR news_index.cluster_id IS NOT excluded.cluster_id
          OR news_index.published_time IS NOT excluded.published_time
          OR news_index.collector_first_seen_time IS NOT excluded.collector_first_seen_time
          OR news_index.parsed IS NOT excluded.parsed
          OR news_index.model_candidate IS NOT excluded.model_candidate
           OR news_index.impact_expires_at IS NOT excluded.impact_expires_at
           OR news_index.mirror_contract IS NOT excluded.mirror_contract
           OR news_index.payload IS NOT excluded.payload`,
    ).bind(generationId, now),
    binding.prepare(
      `WITH projection AS (${projectionSource})
       UPDATE news_index
          SET parsed=0,model_candidate=0,
              payload=json_set(
                json_set(
                  json_set(payload,'$.annotation_status','SUPERSEDED_CONTRACT'),
                  '$.model_visibility','MODEL_INELIGIBLE'
                ),
                '$.parsed_at',json('null')
              )
        WHERE COALESCE(json_extract(payload,'$.annotation_status'),'')<>
                'SUPERSEDED_CONTRACT'
           AND detail_key NOT IN (
             SELECT detail_key FROM projection
           )`,
    ).bind(generationId),
    binding.prepare(
      `UPDATE news_projection_state SET projection_state='CURRENT'
        WHERE id=1 AND active_generation_id=? AND projection_state='ACTIVATING'`,
    ).bind(generationId),
  ]);
  return {
    status: "OK", activated: generationId,
    index_count: Number(row.expected_index_count),
    detail_count: Number(row.expected_detail_count),
    source_digest: row.source_digest, receipt_digest: progress?.receipt_digest,
  };
}

export async function verifyNewsProjection(
  binding: D1Database, generationId: string,
) {
  const state = await readNewsProjectionState(binding);
  if (!state || state.active_generation_id !== generationId) {
    throw new NewsProjectionProtocolError(
      "news generation is not current", 409, "NEWS_PROJECTION_NOT_CURRENT",
    );
  }
  const generationRow = await generation(binding, generationId);
  const counts = await projectionCounts(
    binding, generationId, generationRow?.contract_version ?? state.contract_version,
  );
  const missing = Number(counts?.missing_detail_count ?? -1);
  const violations = Number(counts?.review_violation_count ?? -1)
    + Number(counts?.duplicate_cluster_count ?? -1)
    + await reverseProjectionViolationCount(
      binding, generationId, generationRow?.contract_version ?? state.contract_version,
    );
  const current = Number(counts?.index_count ?? -1) === Number(state.index_count)
    && Number(counts?.detail_count ?? -1) === Number(state.detail_count)
    && missing === 0 && violations === 0;
  const now = new Date().toISOString();
  await binding.batch([
    binding.prepare(
      `UPDATE news_projection_state
          SET missing_detail_count=?,invariant_violation_count=?,projection_state=?,verified_at=?
        WHERE id=1 AND active_generation_id=?`,
    ).bind(missing, violations, current ? "CURRENT" : "RECOVERY_REQUIRED", now, generationId),
    binding.prepare(
      `UPDATE news_projection_generations
          SET missing_detail_count=?,invariant_violation_count=?,updated_at=?
        WHERE generation_id=?`,
    ).bind(missing, violations, now, generationId),
  ]);
  if (!current) {
    throw new NewsProjectionProtocolError(
      "current news generation requires recovery", 409,
      "NEWS_PROJECTION_RECOVERY_REQUIRED", {
        missing_detail_count: missing, invariant_violation_count: violations,
      },
    );
  }
  return { status: "OK", generation_id: generationId, verified_at: now };
}

export async function readNewsProjectionHealth(binding: D1Database) {
  const state = await readNewsProjectionState(binding);
  let staging = await binding.prepare(
    `SELECT generation_id,next_detail_offset,next_index_offset,
            expected_detail_count,expected_index_count,updated_at
       FROM news_projection_generations WHERE state='STAGING' LIMIT 1`,
  ).first<Record<string, unknown>>();
  if (staging?.generation_id) {
    const progress = await generationProgress(binding, String(staging.generation_id));
    staging = {
      ...staging,
      next_detail_offset: Number(progress?.next_detail_offset ?? 0),
      next_index_offset: Number(progress?.next_index_offset ?? 0),
      updated_at: progress?.updated_at ?? staging.updated_at,
    };
  }
  if (!state) {
    return {
      status: "ERROR", projection_state: staging ? "REPLAYING" : "RECOVERY_REQUIRED",
      verified_complete: false, active_generation_id: null, staging,
      error_code: "NEWS_PROJECTION_NOT_SYNCHRONIZED",
    };
  }
  const verified = state.projection_state === "CURRENT"
    && Number(state.missing_detail_count) === 0
    && Number(state.invariant_violation_count) === 0;
  return {
    status: verified ? "OK" : "ERROR",
    projection_state: staging ? "REPLAYING" : state.projection_state,
    verified_complete: verified,
    active_generation_id: state.active_generation_id,
    snapshot_id: state.snapshot_id,
    source_digest: state.source_digest,
    receipt_digest: state.receipt_digest,
    source_receipt_digest: state.receipt_digest,
    index_count: Number(state.index_count), detail_count: Number(state.detail_count),
    missing_detail_count: Number(state.missing_detail_count),
    invariant_violation_count: Number(state.invariant_violation_count),
    activated_at: state.activated_at, verified_at: state.verified_at, staging,
  };
}

const EXPIRY_WIDTH = 32;
const EXPIRY_STRIDE = EXPIRY_WIDTH + 1;

function activeCandidateCount(serialized: string, now: string) {
  if (serialized === "") return 0;
  if ((serialized.length + 1) % EXPIRY_STRIDE !== 0) {
    throw new NewsProjectionProtocolError(
      "news candidate summary is invalid", 503, "NEWS_PROJECTION_SUMMARY_INVALID",
    );
  }
  const total = (serialized.length + 1) / EXPIRY_STRIDE;
  const valueAt = (index: number) => serialized.slice(
    index * EXPIRY_STRIDE, index * EXPIRY_STRIDE + EXPIRY_WIDTH,
  );
  if (!CANONICAL_EXPIRY.test(valueAt(0)) || !CANONICAL_EXPIRY.test(valueAt(total - 1))) {
    throw new NewsProjectionProtocolError(
      "news candidate summary is invalid", 503, "NEWS_PROJECTION_SUMMARY_INVALID",
    );
  }
  const nowKey = `${now.slice(0, 23)}000+00:00`;
  let low = 0;
  let high = total;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (valueAt(middle) <= nowKey) low = middle + 1;
    else high = middle;
  }
  return total - low;
}

export async function readNewsProjectionPage(
  binding: D1Database, options: {
    page: number; pageSize: number; category: string; reviewState: NewsReviewState;
    expectedGenerationId?: string;
  },
) {
  const state = await readNewsProjectionState(binding);
  if (!state || state.projection_state !== "CURRENT"
      || Number(state.missing_detail_count) !== 0
      || Number(state.invariant_violation_count) !== 0) {
    throw new NewsProjectionProtocolError(
      "verified news archive is recovering", 503,
      "NEWS_PROJECTION_RECOVERY_REQUIRED", {
        projection_state: state?.projection_state ?? "RECOVERY_REQUIRED",
        verified_complete: false,
      },
    );
  }
  if (
    options.expectedGenerationId
    && options.expectedGenerationId !== state.active_generation_id
  ) {
    throw new NewsProjectionProtocolError(
      "news generation changed during pagination", 409,
      "NEWS_PROJECTION_GENERATION_CHANGED", {
        expected_generation_id: options.expectedGenerationId,
        active_generation_id: state.active_generation_id,
      },
    );
  }
  const receiptIndexed = state.contract_version === NEWS_PROJECTION_RECEIPT_INDEX_CONTRACT;
  const conditions = receiptIndexed
    ? [ACTIVE_NEWS_SQL, `(${NEWS_REVIEW_STATE_CASE_SQL})=?`]
    : ["generation_id=?", `(${NEWS_REVIEW_STATE_CASE_SQL})=?`];
  const binds: Array<string | number> = receiptIndexed
    ? [options.reviewState] : [state.active_generation_id, options.reviewState];
  if (options.category) {
    conditions.push("category=?"); binds.push(options.category);
  }
  const where = conditions.join(" AND ");
  const offset = (options.page - 1) * options.pageSize;
  const now = new Date().toISOString();
  const pageData = await binding.prepare(
    `WITH page_rows(payload,published_time,collector_first_seen_time,detail_key) AS (
       SELECT payload,published_time,collector_first_seen_time,detail_key
         FROM ${receiptIndexed ? "news_index" : "news_projection_index"} WHERE ${where}
        ORDER BY published_time DESC,collector_first_seen_time DESC,detail_key DESC
        LIMIT ? OFFSET ?
     )
     SELECT COALESCE((SELECT json_group_array(json(payload) ORDER BY
                              published_time DESC,collector_first_seen_time DESC,detail_key DESC)
                        FROM page_rows),'[]') items_json,
            COALESCE((SELECT item_count FROM news_projection_counts
                       WHERE generation_id=? AND review_state=? AND category=?),0) total,
            COALESCE((SELECT parsed_count FROM news_projection_counts
                       WHERE generation_id=? AND review_state='ALL' AND category=''),0) parsed,
            COALESCE((SELECT candidate_expiries FROM news_projection_counts
                       WHERE generation_id=? AND review_state='ALL' AND category=''),'')
              candidate_expiries,
            COALESCE((SELECT json_group_object(category,item_count)
                       FROM news_projection_counts WHERE generation_id=?
                         AND review_state=? AND category<>''),'{}') categories_json,
            COALESCE((SELECT json_group_object(review_state,item_count)
                       FROM news_projection_counts WHERE generation_id=?
                         AND review_state<>'ALL' AND category=''),'{}') reviews_json,
            (SELECT json_object('generation_id',generation_id,'updated_at',updated_at)
               FROM news_projection_generations WHERE state='STAGING' LIMIT 1) staging_json`,
  ).bind(
    ...binds, options.pageSize, offset,
    state.active_generation_id, options.reviewState, options.category,
    state.active_generation_id,
    state.active_generation_id,
    state.active_generation_id, options.reviewState,
    state.active_generation_id,
  ).first<{
    items_json: string; total: number; parsed: number; candidate_expiries: string;
    categories_json: string; reviews_json: string; staging_json: string | null;
  }>();
  if (!pageData) {
    throw new NewsProjectionProtocolError(
      "verified news archive page is unavailable", 503,
      "NEWS_PROJECTION_PAGE_UNAVAILABLE",
    );
  }
  const items = JSON.parse(pageData.items_json) as NewsProjectionIndexItem[];
  const categoryCounts = JSON.parse(pageData.categories_json) as Record<string, number>;
  const reviewCounts = JSON.parse(pageData.reviews_json) as Record<NewsReviewState, number>;
  const staging = pageData.staging_json
    ? JSON.parse(pageData.staging_json) as { generation_id: string; updated_at: string }
    : null;
  return {
    items,
    total: Number(pageData.total ?? 0),
    all_total: Number(state.index_count), readable_total: Number(state.index_count),
    parsed_total: Number(pageData.parsed ?? 0),
    model_candidate_total: activeCandidateCount(pageData.candidate_expiries, now),
    category_counts: categoryCounts,
    review_state_counts: reviewCounts,
    review_state: options.reviewState, page: options.page, page_size: options.pageSize,
    window_days: 60, totals_scope: "VERIFIED_CURRENT_GENERATION",
    projection_state: staging ? "REPLAYING" : "CURRENT", verified_complete: true,
    replacement_generation_id: staging?.generation_id ?? null,
    generation_id: state.active_generation_id, snapshot_id: state.snapshot_id,
    source_digest: state.source_digest, receipt_digest: state.receipt_digest,
    source_receipt_digest: state.receipt_digest,
    activated_at: state.activated_at, verified_at: state.verified_at,
  };
}

export async function readNewsProjectionDetails(
  binding: D1Database, detailKeys: string[],
) {
  const state = await readNewsProjectionState(binding);
  if (!state || state.projection_state !== "CURRENT") {
    throw new NewsProjectionProtocolError(
      "verified news archive is recovering", 503, "NEWS_PROJECTION_RECOVERY_REQUIRED",
    );
  }
  const placeholders = detailKeys.map(() => "?").join(",");
  const rows = await binding.prepare(
    `SELECT detail_key,detail_hash,payload FROM news_details
      WHERE detail_key IN (${placeholders})`,
  ).bind(...detailKeys).all<{
    detail_key: string; detail_hash: string; payload: string;
  }>();
  return {
    generation_id: state.active_generation_id,
    items: Object.fromEntries(rows.results.map(row => [row.detail_key, {
      detail_hash: row.detail_hash, payload: JSON.parse(row.payload),
    }])),
    missing: detailKeys.filter(key => !rows.results.some(row => row.detail_key === key)),
  };
}
