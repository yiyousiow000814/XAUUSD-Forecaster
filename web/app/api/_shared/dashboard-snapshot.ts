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

const snapshotUpsertSql = `WITH incoming(payload) AS (SELECT CAST(? AS TEXT))
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT ?, payload, ? FROM incoming WHERE json_valid(payload)
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at`;

// D1's bridge charges materially more Worker CPU when a large ArrayBuffer is
// bound than when the same already-bounded UTF-8 JSON is bound as text. Keep
// smaller snapshots on the zero-decode byte path, but cross the large-payload
// boundary once as strict UTF-8 before the single D1 operation.
export const SNAPSHOT_TEXT_BIND_THRESHOLD_BYTES = AUDIT_DETAIL_SNAPSHOT_BYTES;

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
  if (options.dryRun) {
    return await validateJsonPayloadWithD1(binding, payload)
      ? "validated" : "invalid";
  }
  const result = await binding.prepare(snapshotUpsertSql)
    .bind(payload, snapshotId, new Date().toISOString()).run();
  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
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
  const result = await binding.prepare(
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
       payload=excluded.payload, received_at=excluded.received_at`,
  ).bind(payload, receivedAt).run();

  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
}
