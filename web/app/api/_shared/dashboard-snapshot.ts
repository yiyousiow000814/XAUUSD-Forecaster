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

import { validateJsonBytesWithD1 } from "./release-validation";

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

  if (options.dryRun) {
    return await validateJsonBytesWithD1(binding, exactArrayBuffer(body.bytes))
      ? "validated" : "invalid";
  }

  return writeDashboardSnapshotPayload(
    exactArrayBuffer(body.bytes), binding, snapshotId,
  );
}

async function writeDashboardSnapshotPayload(
  payload: string | ArrayBuffer,
  binding: D1Database,
  snapshotId: number,
): Promise<SnapshotWriteResult> {
  const result = await binding.prepare(snapshotUpsertSql)
    .bind(payload, snapshotId, new Date().toISOString()).run();
  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
}

export async function writeSerializedDashboardSnapshot(
  serialized: string,
  binding: D1Database,
  snapshotId: number,
): Promise<SnapshotWriteResult> {
  return writeDashboardSnapshotPayload(serialized, binding, snapshotId);
}

export async function writeDashboardStatusSnapshots(
  serialized: string,
  binding: D1Database,
): Promise<SnapshotWriteResult> {
  const receivedAt = new Date().toISOString();
  const result = await binding.prepare(
    `WITH incoming(payload, received_at) AS (SELECT ?, ?)
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT 1, payload, received_at FROM incoming WHERE json_valid(payload)
     UNION ALL
     SELECT 5, ${publicStatusJsonExpression()}, received_at
     FROM incoming WHERE json_valid(payload)
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at`,
  ).bind(serialized, receivedAt).run();

  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
}
