import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "./ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "./preview";
import {
  AUDIT_DETAIL_SNAPSHOT_BYTES,
  writeDashboardSnapshot,
} from "./dashboard-snapshot";

export async function readAuditDetailSnapshot(
  snapshotId: number,
  fields: string[],
  unavailableLabel: string,
) {
  if (previewBundle?.audit) {
    return previewJson(Object.fromEntries(
      fields.filter(field => field in previewBundle.audit!).map(
        field => [field, previewBundle.audit![field]],
      ),
    ));
  }
  try {
    const binding = env.DB as D1Database | undefined;
    const row = binding ? await binding.prepare(
      "SELECT payload FROM dashboard_snapshots WHERE id = ?",
    ).bind(snapshotId).first<{ payload: string }>() : null;
    if (row) return new Response(row.payload, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "private, max-age=15",
      },
    });
  } catch {
    // Each optional audit detail owns its availability.
  }
  return NextResponse.json({ error: unavailableLabel }, { status: 503 });
}

export async function writeAuditDetailSnapshot(
  request: Request,
  snapshotId: number,
  invalidLabel: string,
) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json(
    { error: "database unavailable" }, { status: 503 },
  );
  const result = await writeDashboardSnapshot(
    request, binding, snapshotId, AUDIT_DETAIL_SNAPSHOT_BYTES,
  );
  if (result === "too_large") return NextResponse.json(
    { error: "payload too large" }, { status: 413 },
  );
  if (result === "invalid") return NextResponse.json(
    { error: invalidLabel }, { status: 400 },
  );
  return NextResponse.json({ status: "OK" });
}
