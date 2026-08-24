import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson, rejectPreviewWrite } from "./preview";
import {
  AUDIT_SNAPSHOT_IDS,
  AUDIT_DETAIL_SNAPSHOT_BYTES,
  writeDashboardSnapshot,
} from "./dashboard-snapshot";
import { isIngestAuthorized } from "./ingest-auth";
import {
  authorizeReleaseValidation,
  isReleaseValidationContext,
  releaseValidationResponse,
} from "./release-validation";

export async function readAuditDetailSnapshot(
  snapshotId: number,
  fields: string[],
  unavailableLabel: string,
) {
  if (previewBundle) {
    const resource = snapshotId === AUDIT_SNAPSHOT_IDS.briefs
      ? previewBundle.audit_briefs
      : snapshotId === AUDIT_SNAPSHOT_IDS.stories
        ? previewBundle.audit_stories
        : snapshotId === AUDIT_SNAPSHOT_IDS.decisions
          ? previewBundle.audit_decisions
          : null;
    if (!resource) {
      return previewJson({
        error: unavailableLabel,
        availability: "UNAVAILABLE_IN_BUILD_SNAPSHOT",
      }, 503, "unavailable-build-snapshot-resource");
    }
    return previewJson(Object.fromEntries(
      fields.filter(field => field in resource).map(
        field => [field, resource[field]],
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
  validationFamily: string,
) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, validationFamily, isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json(
    { error: "database unavailable" }, { status: 503 },
  );
  const result = await writeDashboardSnapshot(
    request, binding, snapshotId, {
      dryRun: isReleaseValidationContext(validation),
      maxBytes: AUDIT_DETAIL_SNAPSHOT_BYTES,
    },
  );
  if (result === "too_large") return NextResponse.json(
    { error: "payload too large" }, { status: 413 },
  );
  if (result === "invalid") return NextResponse.json(
    { error: invalidLabel }, { status: 400 },
  );
  if (result === "validated" && isReleaseValidationContext(validation)) {
    return releaseValidationResponse(validation, {
      body: "bounded-read",
      json: "d1-json1",
      mutation_boundary: `audit-snapshot-${snapshotId}-upsert`,
    });
  }
  return NextResponse.json({ status: "OK" });
}
