import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { rejectPreviewWrite } from "../_shared/preview";
import { writeDashboardSnapshot } from "../_shared/dashboard-snapshot";

declare const __AURUM_DEPLOYMENT__: {
  branch: string;
  commit_sha: string;
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
  return NextResponse.json(deploymentStatus(), {
    headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  const writeResult = await writeDashboardSnapshot(request, binding, 1);
  if (writeResult === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  if (writeResult === "invalid") {
    return NextResponse.json({ error: "invalid status payload" }, { status: 400 });
  }
  return NextResponse.json(deploymentStatus());
}
