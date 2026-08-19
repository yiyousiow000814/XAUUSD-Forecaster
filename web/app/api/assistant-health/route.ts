import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { assistantOperationalHealth } from "../_shared/assistant-operational-health";
import {
  authenticateDashboardOperatorRequest,
  dashboardOperatorAuthFailure,
} from "../_shared/dashboard-operator-auth";
import { isPreviewDeployment, previewJson } from "../_shared/preview";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (isPreviewDeployment) {
    return previewJson({
      schema_version: "assistant-operational-health.v1",
      observed_at: null,
      status: "HEALTHY",
      alerts: [],
      queues: [],
      current: false,
    });
  }
  const authorization = await authenticateDashboardOperatorRequest(request, env);
  if (authorization.state !== "AUTHORIZED") return dashboardOperatorAuthFailure(authorization);
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({
      code: "OPS_ASSISTANT_HEALTH_UNAVAILABLE",
      error: "Assistant 运行状态暂不可用",
    }, { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } });
  }
  try {
    return NextResponse.json({
      ...await assistantOperationalHealth(binding),
      current: true,
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch {
    return NextResponse.json({
      code: "OPS_ASSISTANT_HEALTH_UNAVAILABLE",
      error: "Assistant 运行状态读取失败",
    }, { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } });
  }
}
