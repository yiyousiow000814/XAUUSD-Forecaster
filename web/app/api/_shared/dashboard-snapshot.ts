export const MAX_DASHBOARD_SNAPSHOT_BYTES = 800_000;

import { validateJsonWithD1 } from "./release-validation";

export type SnapshotWriteResult = "stored" | "validated" | "invalid" | "too_large";

export type BoundedBodyResult =
  | { status: "ok"; serialized: string; receivedBytes: number }
  | { status: "too_large" };

export const PUBLIC_STATUS_PRIVATE_FIELDS = [
  "annotation_queue", "gemini_quota", "gemini_31_quota",
  "gemma_quota", "gemini_embedding_quota", "llm_routing",
] as const;

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

export async function readBoundedBody(
  request: Request,
  maxBytes = MAX_DASHBOARD_SNAPSHOT_BYTES,
): Promise<BoundedBodyResult> {
  const declaredBytes = declaredBodyBytes(request);
  if (declaredBytes !== null && declaredBytes > maxBytes) {
    return { status: "too_large" };
  }
  if (!request.body) return { status: "ok", serialized: "", receivedBytes: 0 };

  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  const decoded: string[] = [];
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
      decoded.push(decoder.decode(value, { stream: true }));
    }
    decoded.push(decoder.decode());
    return { status: "ok", serialized: decoded.join(""), receivedBytes };
  } finally {
    reader.releaseLock();
  }
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
  options: { dryRun?: boolean } = {},
): Promise<SnapshotWriteResult> {
  const body = await readBoundedBody(request);
  if (body.status === "too_large") return "too_large";

  if (options.dryRun) {
    return await validateJsonWithD1(binding, body.serialized)
      ? "validated" : "invalid";
  }

  return writeSerializedDashboardSnapshot(body.serialized, binding, snapshotId);
}

export async function writeSerializedDashboardSnapshot(
  serialized: string,
  binding: D1Database,
  snapshotId: number,
): Promise<SnapshotWriteResult> {
  const result = await binding.prepare(
    `WITH incoming(payload) AS (SELECT ?)
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT ?, payload, ? FROM incoming WHERE json_valid(payload)
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at`,
  ).bind(serialized, snapshotId, new Date().toISOString()).run();

  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
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
