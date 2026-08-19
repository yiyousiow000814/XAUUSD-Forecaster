import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { authenticateDashboardOperatorRequest } from "../_shared/dashboard-operator-auth";
import { readDashboardStatus } from "../_shared/dashboard-status";
import { previewBundle, previewJson } from "../_shared/preview";

export const dynamic = "force-dynamic";

const json = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status, headers: { "Cache-Control": "private, no-store, max-age=0" },
});

export async function GET(request: Request) {
  if (previewBundle) {
    return previewJson(previewBundle.status, 200, "synthetic-admin-status");
  }
  const actor = await authenticateDashboardOperatorRequest(request, env);
  if (!actor) return json({ error: "操作员身份验证失败" }, 401);
  const current = await readDashboardStatus(
    env.DB as D1Database | undefined,
    process.env.STATUS_RELAY_URL,
  );
  if (!current) return json({ error: "AI 模型用量暂不可用" }, 503);
  return json({ ...current.payload, observation_scope: current.scope }, current.status);
}
