import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import {
  AUDIT_SNAPSHOT_IDS,
  AUDIT_SUMMARY_SNAPSHOT_BYTES,
  writeDashboardSnapshot,
} from "../_shared/dashboard-snapshot";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
} from "../_shared/release-validation";

export const dynamic = "force-dynamic";

export async function GET() {
  if (previewBundle?.audit) return previewJson(previewBundle.audit);
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const row = await binding.prepare(
        "SELECT payload FROM dashboard_snapshots WHERE id = ?",
      ).bind(AUDIT_SNAPSHOT_IDS.summary).first<{ payload: string }>();
      if (row) {
        return new Response(row.payload, {
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "private, max-age=15",
          },
        });
      }
    }
  } catch {
    // The audit first page is optional and owns its own availability.
  }
  return NextResponse.json({ error: "等待审计首屏首次同步" }, { status: 503 });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "audit-write", isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const writeResult = await writeDashboardSnapshot(request, binding, AUDIT_SNAPSHOT_IDS.summary, {
    dryRun: isReleaseValidationContext(validation),
    maxBytes: AUDIT_SUMMARY_SNAPSHOT_BYTES,
  },
  );
  if (writeResult === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  if (writeResult === "invalid") {
    return NextResponse.json({ error: "invalid audit payload" }, { status: 400 });
  }
  if (writeResult === "validated" && isReleaseValidationContext(validation)) {
    return releaseValidationResponse(validation, {
      body: "bounded-read", json: "d1-json1", mutation_boundary: "snapshot-upsert",
    });
  }
  return NextResponse.json({ status: "OK" });
}
