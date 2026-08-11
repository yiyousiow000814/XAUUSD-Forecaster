export const MAX_DASHBOARD_SNAPSHOT_BYTES = 800_000;

export type SnapshotWriteResult = "stored" | "invalid" | "too_large";

export type BoundedBodyResult =
  | { status: "ok"; serialized: string }
  | { status: "too_large" };

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
  if (!request.body) return { status: "ok", serialized: "" };

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
    return { status: "ok", serialized: decoded.join("") };
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
): Promise<SnapshotWriteResult> {
  const body = await readBoundedBody(request);
  if (body.status === "too_large") return "too_large";

  const result = await binding.prepare(
    `WITH incoming(payload) AS (SELECT ?)
     INSERT INTO dashboard_snapshots (id, payload, received_at)
     SELECT ?, payload, ? FROM incoming WHERE json_valid(payload)
     ON CONFLICT(id) DO UPDATE SET
       payload=excluded.payload, received_at=excluded.received_at`,
  ).bind(body.serialized, snapshotId, new Date().toISOString()).run();

  return Number(result.meta.changes ?? 0) > 0 ? "stored" : "invalid";
}
