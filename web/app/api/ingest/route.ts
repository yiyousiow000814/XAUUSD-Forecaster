import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { rejectPreviewWrite } from "../_shared/preview";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
  validateJsonWithD1,
} from "../_shared/release-validation";
import {
  readBoundedBody,
  writeDashboardStatusSnapshots,
} from "../_shared/dashboard-snapshot";
import {
  d1CapabilityFailure,
  D1CapabilityError,
  requireD1Capabilities,
} from "../_shared/d1-capabilities";

declare const __AURUM_DEPLOYMENT__: {
  branch: string;
  commit_sha: string;
  is_preview: boolean;
};

function deploymentStatus() {
  const deployment = __AURUM_DEPLOYMENT__;
  return {
    status: "OK",
    main_revision:
      deployment.branch === "main" && /^[0-9a-f]{40}$/.test(deployment.commit_sha)
        ? deployment.commit_sha
        : null,
  };
}

export async function GET() {
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({
      status: "ERROR", error: "database unavailable", error_code: "D1_BINDING_MISSING",
    }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  try {
    await requireD1Capabilities(binding, [
      "operator_retry_scheduling", "paged_news_evidence",
    ]);
    return NextResponse.json(deploymentStatus(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    if (error instanceof D1CapabilityError) {
      return NextResponse.json(d1CapabilityFailure(error), {
        status: 503, headers: { "Cache-Control": "no-store" },
      });
    }
    throw error;
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "status-ingest", isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  const body = await readBoundedBody(request);
  if (body.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const writeResult = isReleaseValidationContext(validation)
    ? await validateJsonWithD1(binding, body.serialized) ? "validated" : "invalid"
    : await writeDashboardStatusSnapshots(body.serialized, binding);
  if (writeResult === "invalid") {
    return NextResponse.json({ error: "invalid status payload" }, { status: 400 });
  }
  if (writeResult === "validated" && isReleaseValidationContext(validation)) {
    return releaseValidationResponse(validation, {
      body: "bounded-read", json: "d1-json1", mutation_boundary: "snapshot-upsert",
    });
  }
  return NextResponse.json(deploymentStatus());
}
