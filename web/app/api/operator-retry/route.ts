import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { authenticateAssistantRequest } from "../_shared/assistant-auth";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import {
  createOperatorRetryRequests,
  listOperatorRetryJobs,
  OperatorRetryInputError,
  parseOperatorRetryCustomTime,
  parseOperatorRetryIdempotencyKey,
  parseOperatorRetryMode,
  parseOperatorRetryReason,
} from "../_shared/operator-retry";
import { isPreviewDeployment, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const json = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status, headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const body = async (request: Request) => {
  const bounded = await readBoundedBody(request, 50_000);
  if (bounded.status === "too_large") throw new OperatorRetryInputError("PAYLOAD_TOO_LARGE", "内容过长");
  let parsed: unknown;
  try { parsed = JSON.parse(bounded.serialized); } catch { throw new OperatorRetryInputError("INVALID_JSON", "请求内容无效"); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new OperatorRetryInputError("INVALID_JSON", "请求内容无效");
  }
  return parsed as Record<string, unknown>;
};

export async function GET(request: Request) {
  if (isPreviewDeployment) {
    return previewJson({ items: [], requests: [], preview: true }, 200, "synthetic-empty-operator-retry");
  }
  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return json({ error: "操作员身份验证失败" }, 401);
  if (!env.DB) return json({ error: "重试控制暂不可用" }, 503);
  const requested = Number(new URL(request.url).searchParams.get("limit") ?? 200);
  try {
    return json(await listOperatorRetryJobs(env.DB, Number.isSafeInteger(requested) ? requested : 200));
  } catch {
    return json({ error: "重试任务读取失败" }, 503);
  }
}

export async function POST(request: Request) {
  const preview = rejectPreviewWrite();
  if (preview) return preview;
  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return json({ error: "操作员身份验证失败" }, 401);
  if (!env.DB) return json({ error: "重试控制暂不可用" }, 503);
  try {
    const input = await body(request);
    const mode = parseOperatorRetryMode(input.mode);
    const reason = parseOperatorRetryReason(input.reason);
    const requestedAvailableAt = parseOperatorRetryCustomTime(mode, input.requested_available_at);
    const jobIds = Array.isArray(input.job_ids) ? input.job_ids.map(String) : [];
    const items = await createOperatorRetryRequests(env.DB, {
      operatorId: actor.actor_id,
      idempotencyKey: parseOperatorRetryIdempotencyKey(request.headers.get("idempotency-key")),
      jobIds, mode, reason, requestedAvailableAt,
    });
    const accepted = items.filter(item => !["REJECTED", "CONFLICT"].includes(String(item.status))).length;
    return json({ items, accepted }, accepted === items.length ? 202 : 207);
  } catch (error) {
    if (error instanceof OperatorRetryInputError) {
      return json({ error: error.message, code: error.code }, error.code === "PAYLOAD_TOO_LARGE" ? 413 : 400);
    }
    return json({ error: "重试计划提交失败" }, 503);
  }
}
