export const MAX_DASHBOARD_SNAPSHOT_BYTES = 800_000;
export const AUDIT_SUMMARY_SNAPSHOT_BYTES = 16_000;
export const AUDIT_DETAIL_SNAPSHOT_BYTES = 120_000;
export const AUDIT_SNAPSHOT_IDS = Object.freeze({
  // Keep the split summary isolated from the legacy full audit snapshot (id 4)
  // until the candidate is explicitly promoted with its matching sync owner.
  summary: 9,
  decisions: 6,
  briefs: 7,
  stories: 8,
});

import { validateJsonPayloadWithD1 } from "./release-validation";
import {
  AUDIT_DETAIL_PROJECTION_CONTRACT, auditDetailRequiredArray, auditStorySiblingArrays,
  type AuditDetailResource,
} from "../../_lib/audit-detail-contract";

function auditDetailResource(snapshotId: number): AuditDetailResource | null {
  if (snapshotId === AUDIT_SNAPSHOT_IDS.briefs) return "briefs";
  if (snapshotId === AUDIT_SNAPSHOT_IDS.stories) return "stories";
  if (snapshotId === AUDIT_SNAPSHOT_IDS.decisions) return "decisions";
  return null;
}

/** Check the source before legacy projection can turn an absent array into []. */
export function auditDetailSourceSql(snapshotId: number, payload = "payload"): string {
  const resource = auditDetailResource(snapshotId);
  if (!resource) return "1";
  const field = auditDetailRequiredArray[resource];
  return `(json_type(${payload}, '$.error') IS NULL
    AND json_type(${payload}, '$.${field}') = 'array'
    AND (json_type(${payload}, '$.projection_contract') IS NULL
      OR json_extract(${payload}, '$.projection_contract') = '${AUDIT_DETAIL_PROJECTION_CONTRACT}')
    AND (json_array_length(${payload}, '$.${field}') > 0
      OR json_extract(${payload}, '$.projection_contract') = '${AUDIT_DETAIL_PROJECTION_CONTRACT}'))`;
}

/** Match the UI row contract in D1, without decoding the bounded JSON in JS. */
export function auditDetailPayloadSql(snapshotId: number, payload = "payload"): string {
  const resource = auditDetailResource(snapshotId);
  if (!resource) return `json_valid(${payload})`;
  const field = auditDetailRequiredArray[resource];
  const array = (value: string, path: string, rowType = "object") => (
    `(json_type(${value}, '$.${path}') = 'array' AND NOT EXISTS (
      SELECT 1 FROM json_each(${value}, '$.${path}') WHERE type != '${rowType}'))`
  );
  const optional = (value: string, path: string, valid: string) => (
    `(json_type(${value}, '$.${path}') IS NULL OR json_type(${value}, '$.${path}') = 'null' OR ${valid})`
  );
  const numeric = (value: string, path: string) => optional(
    value, path, `json_type(${value}, '$.${path}') IN ('integer','real')`,
  );
  const storyRow = (value: string) => [
    "covered_roles", "missing_roles", "timeline", "market_reactions", "commentary", "background",
  ].map(path => array(value, path)).join(" AND ");
  const row = "detail.value";
  const rowValid = resource === "stories" ? storyRow(row)
    : resource === "briefs" ? `json_type(${row}, '$.model_version') = 'text'
      AND ${optional(row, "phase", `json_type(${row}, '$.phase') = 'text'`)}
      AND json_type(${row}, '$.brief') = 'object'
      AND ${array(row, "brief.items")}
      AND NOT EXISTS (SELECT 1 FROM json_each(${row}, '$.brief.items') brief_item
        WHERE NOT CASE WHEN brief_item.type = 'object' THEN coalesce((json_type(brief_item.value, '$.headline') = 'text'
          AND json_type(brief_item.value, '$.summary') = 'text'
          AND ${array("brief_item.value", "evidence_ids", "text")}), 0) ELSE 0 END)
      AND ${optional(row, "brief.drivers", array(row, "brief.drivers", "text"))}
      AND ${["brief.overview", "brief.watch_next"].map(path => optional(row, path, `json_type(${row}, '$.${path}') = 'text'`)).join(" AND ")}`
    : `${array(row, "predictions")}
      AND ${["bid", "ask", "long_return", "short_return"].map(path => numeric(row, path)).join(" AND ")}
      AND ${optional(row, "outcome_status", `(json_extract(${row}, '$.outcome_status') = 'VALID' OR ${array(row, "outcome_reason_codes", "text")})`)}
      AND NOT EXISTS (SELECT 1 FROM json_each(${row}, '$.predictions') prediction
        WHERE NOT CASE WHEN prediction.type = 'object' THEN coalesce((${["predicted_direction_u5", "predicted_news_residual_u5", "ev_long_u5", "ev_short_u5", "uncertainty_u5"].map(path => numeric("prediction.value", path)).join(" AND ")}), 0) ELSE 0 END)`;
  const siblings = resource === "stories" ? auditStorySiblingArrays.map(path => (
    `(json_type(${payload}, '$.${path}') IS NULL OR ${array(payload, path)})`
  )).join(" AND ") + ` AND NOT EXISTS (
    SELECT 1 FROM json_each(${payload}, '$.archived_storylines') detail
    WHERE NOT CASE WHEN detail.type = 'object' THEN coalesce((${storyRow(row)}), 0) ELSE 0 END)` : "1";
  return `CASE WHEN json_valid(${payload}) THEN coalesce((
    ${auditDetailSourceSql(snapshotId, payload)}
    AND (json_type(${payload}, '$.generated_at') IS NULL OR
      (json_type(${payload}, '$.generated_at') = 'text' AND julianday(json_extract(${payload}, '$.generated_at')) IS NOT NULL))
    AND NOT EXISTS (SELECT 1 FROM json_each(${payload}, '$.${field}') detail
      WHERE NOT CASE WHEN detail.type = 'object' THEN coalesce((${rowValid}), 0) ELSE 0 END)
    AND ${siblings}), 0) ELSE 0 END`;
}

export type SnapshotWriteResult = "stored" | "validated" | "invalid" | "too_large";

export type BoundedBodyResult =
  | { status: "ok"; serialized: string; receivedBytes: number }
  | { status: "too_large" };

export type BoundedBodyBytesResult =
  | { status: "ok"; bytes: Uint8Array; receivedBytes: number }
  | { status: "too_large" };

export const PUBLIC_STATUS_PRIVATE_FIELDS = [
  "annotation_queue", "gemini_quota", "gemini_31_quota",
  "gemma_quota", "gemini_embedding_quota", "llm_routing",
] as const;

const snapshotUpsertSql = (valid: string) => `WITH incoming(payload) AS (SELECT CAST(? AS TEXT))
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT ?, payload, ? FROM incoming WHERE ${valid}
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at
     WHERE dashboard_snapshots.payload IS NOT excluded.payload`;

const auditDetailWriteStatements = new Map<number, {validation: string; upsert: string}>([
  AUDIT_SNAPSHOT_IDS.briefs, AUDIT_SNAPSHOT_IDS.stories, AUDIT_SNAPSHOT_IDS.decisions,
].map(id => {
  const valid = auditDetailPayloadSql(id);
  return [id, {
    validation: `WITH incoming(payload) AS (SELECT CAST(? AS TEXT)) SELECT ${valid} AS valid FROM incoming`,
    upsert: snapshotUpsertSql(valid),
  }];
}));
const genericSnapshotWriteStatements = {
  validation: "SELECT json_valid(CAST(? AS TEXT)) AS valid",
  upsert: snapshotUpsertSql("json_valid(payload)"),
};

// D1's bridge charges materially more Worker CPU when a larger ArrayBuffer is
// bound than when the same already-bounded UTF-8 JSON is bound as text. Keep
// small snapshots on the zero-decode byte path, but cross the measured D1
// transport boundary once as strict UTF-8 before the single D1 operation. This
// is deliberately independent from each route's business payload envelope.
export const SNAPSHOT_TEXT_BIND_THRESHOLD_BYTES = 64_000;

export function publicStatusJsonExpression() {
  return `json_remove(payload, ${PUBLIC_STATUS_PRIVATE_FIELDS
    .map(field => `'$.${field}'`).join(", ")})`;
}

function declaredBodyBytes(request: Request): number | null {
  const raw = request.headers.get("content-length");
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

export async function readBoundedBodyBytes(
  request: Request,
  maxBytes = MAX_DASHBOARD_SNAPSHOT_BYTES,
): Promise<BoundedBodyBytesResult> {
  const declaredBytes = declaredBodyBytes(request);
  if (declaredBytes !== null && declaredBytes > maxBytes) {
    return { status: "too_large" };
  }
  if (!request.body) {
    return { status: "ok", bytes: new Uint8Array(), receivedBytes: 0 };
  }

  // Authenticated production writers send Content-Length. Let the runtime
  // materialize that already-bounded body once instead of copying stream
  // chunks through JavaScript on every normal snapshot write. The post-read
  // check preserves fail-closed behavior when a sender understates the header.
  if (declaredBytes !== null) {
    const bytes = new Uint8Array(await request.arrayBuffer());
    return bytes.byteLength <= maxBytes
      ? { status: "ok", bytes, receivedBytes: bytes.byteLength }
      : { status: "too_large" };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let receivedBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > maxBytes) {
        await reader.cancel().catch(() => undefined);
        return { status: "too_large" };
      }
      chunks.push(value);
    }
    if (chunks.length === 0) {
      return { status: "ok", bytes: new Uint8Array(), receivedBytes };
    }
    if (chunks.length === 1) {
      return { status: "ok", bytes: chunks[0], receivedBytes };
    }
    const combined = new Uint8Array(receivedBytes);
    let offset = 0;
    for (const chunk of chunks) {
      combined.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return { status: "ok", bytes: combined, receivedBytes };
  } finally {
    reader.releaseLock();
  }
}

function exactArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  if (bytes.buffer instanceof ArrayBuffer && bytes.byteOffset === 0 &&
      bytes.byteLength === bytes.buffer.byteLength) {
    return bytes.buffer;
  }
  return bytes.slice().buffer;
}

function snapshotD1Payload(bytes: Uint8Array): string | ArrayBuffer | null {
  if (bytes.byteLength <= SNAPSHOT_TEXT_BIND_THRESHOLD_BYTES) {
    return exactArrayBuffer(bytes);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

export async function readBoundedBody(
  request: Request,
  maxBytes = MAX_DASHBOARD_SNAPSHOT_BYTES,
): Promise<BoundedBodyResult> {
  const body = await readBoundedBodyBytes(request, maxBytes);
  if (body.status === "too_large") return body;
  return {
    status: "ok",
    serialized: new TextDecoder().decode(body.bytes),
    receivedBytes: body.receivedBytes,
  };
}

/**
 * Store one authenticated JSON snapshot without parsing it in the Worker.
 *
 * D1's JSON1 engine validates the payload inside the database operation. This
 * keeps large snapshots off the Free-plan Worker's 10 ms JavaScript CPU path
 * while preserving the existing fail-closed JSON contract.
 */
export async function writeDashboardSnapshot(
  request: Request,
  binding: D1Database,
  snapshotId: number,
  options: { dryRun?: boolean; maxBytes?: number } = {},
): Promise<SnapshotWriteResult> {
  const body = await readBoundedBodyBytes(
    request, options.maxBytes ?? MAX_DASHBOARD_SNAPSHOT_BYTES,
  );
  if (body.status === "too_large") return "too_large";
  return writeDashboardSnapshotBytes(body.bytes, binding, snapshotId, options);
}

export async function writeDashboardSnapshotBytes(
  bytes: Uint8Array,
  binding: D1Database,
  snapshotId: number,
  options: { dryRun?: boolean } = {},
): Promise<SnapshotWriteResult> {
  const payload = snapshotD1Payload(bytes);
  if (payload === null) return "invalid";
  const statements = auditDetailWriteStatements.get(snapshotId) ?? genericSnapshotWriteStatements;
  if (options.dryRun) {
    if (auditDetailResource(snapshotId)) {
      const validation = await binding.prepare(statements.validation).bind(payload).first<{ valid: number }>();
      return Number(validation?.valid) === 1 ? "validated" : "invalid";
    }
    return await validateJsonPayloadWithD1(binding, payload)
      ? "validated" : "invalid";
  }
  const [validation] = await binding.batch([
    binding.prepare(statements.validation).bind(payload),
    binding.prepare(statements.upsert)
      .bind(payload, snapshotId, new Date().toISOString()),
  ]);
  const valid = Number((validation.results?.[0] as { valid?: number } | undefined)?.valid ?? 0);
  return valid === 1 ? "stored" : "invalid";
}

export async function writeDashboardStatusSnapshotBytes(
  bytes: Uint8Array,
  binding: D1Database,
  options: { dryRun?: boolean } = {},
): Promise<SnapshotWriteResult> {
  const payload = exactArrayBuffer(bytes);
  if (options.dryRun) {
    return await validateJsonPayloadWithD1(binding, payload)
      ? "validated" : "invalid";
  }
  const receivedAt = new Date().toISOString();
  const [validation] = await binding.batch([
    binding.prepare("SELECT json_valid(CAST(? AS TEXT)) AS valid").bind(payload),
    binding.prepare(
    `WITH incoming(payload, received_at) AS (SELECT CAST(? AS TEXT), ?),
          valid(payload, received_at) AS (
            SELECT payload,received_at FROM incoming WHERE json_valid(payload)
          )
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT 1, payload, received_at FROM valid
     UNION ALL
     SELECT 5, ${publicStatusJsonExpression()}, received_at
     FROM valid WHERE true
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at
     WHERE dashboard_snapshots.payload IS NOT excluded.payload`,
    ).bind(payload, receivedAt),
  ]);

  const valid = Number((validation.results?.[0] as { valid?: number } | undefined)?.valid ?? 0);
  return valid === 1 ? "stored" : "invalid";
}
