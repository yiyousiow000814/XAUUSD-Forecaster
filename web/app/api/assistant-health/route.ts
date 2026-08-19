import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { assistantOperationalHealth } from "../_shared/assistant-operational-health";
import { authenticateDashboardOperatorRequest } from "../_shared/dashboard-operator-auth";
import { isPreviewDeployment, previewJson } from "../_shared/preview";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (isPreviewDeployment) {
    return previewJson({
      schema_version: "assistant-operational-health.v1",
      observed_at: null,
      status: "SNAPSHOT_UNAVAILABLE",
      alerts: [],
      queues: [],
      current: false,
    });
  }
  const actor = await authenticateDashboardOperatorRequest(request, env);
  if (!actor) return NextResponse.json({ error: "操作员身份验证失败" }, {
    status: 401, headers: { "Cache-Control": "private, no-store, max-age=0" },
  });
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
