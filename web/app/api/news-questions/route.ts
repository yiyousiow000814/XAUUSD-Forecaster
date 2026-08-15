import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { authenticateAssistantRequest } from "../_shared/assistant-auth";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import {
  claimNewsQuestion,
  completeNewsQuestion,
  createNewsQuestion,
  failNewsQuestion,
  getOwnerNewsQuestion,
  listOwnerNewsQuestions,
  NewsQuestionInputError,
  parseIdempotencyKey,
  parseQuestion,
} from "../_shared/news-questions";
import { isPreviewDeployment, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 身份验证失败" }, 401);
const unavailable = () => noStoreJson({ error: "新闻问答暂不可用" }, 503);

const boundedBody = async (request: Request) => {
  const body = await readBoundedBody(request, 10_000);
  if (body.status === "too_large") {
    throw new NewsQuestionInputError("PAYLOAD_TOO_LARGE", "内容过长");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body.serialized);
  } catch {
    throw new NewsQuestionInputError("INVALID_JSON", "请求内容无效");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new NewsQuestionInputError("INVALID_JSON", "请求内容无效");
  }
  return parsed as Record<string, unknown>;
};

const inputError = (error: NewsQuestionInputError) => noStoreJson(
  { error: error.message, code: error.code },
  error.code === "PAYLOAD_TOO_LARGE" ? 413 : 400,
);

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  if (isPreviewDeployment) {
    if (params.get("mode") === "claim") {
      return rejectPreviewWrite() ?? previewJson({ error: "Preview 只读" }, 403, "write-rejected");
    }
    return previewJson({ items: [], preview: true }, 200, "synthetic-empty-assistant");
  }
  if (params.get("mode") === "claim") {
    if (!await isIngestAuthorized(request)) return unauthorized();
    const binding = env.DB;
    if (!binding) return unavailable();
    const workerId = (params.get("worker_id") ?? "").trim();
    if (!/^[A-Za-z0-9._:-]{3,96}$/.test(workerId)) {
      return noStoreJson({ error: "invalid worker identity" }, 400);
    }
    try {
      const item = await claimNewsQuestion(binding, workerId);
      return noStoreJson({ item });
    } catch {
      return unavailable();
    }
  }

  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  const id = params.get("id")?.trim();
  try {
    if (id) {
      const item = await getOwnerNewsQuestion(binding, actor.actor_id, id);
      return item
        ? noStoreJson(item)
        : noStoreJson({ error: "找不到这个问题" }, 404);
    }
    const requestedLimit = Number(params.get("limit") ?? 10);
    const items = await listOwnerNewsQuestions(
      binding,
      actor.actor_id,
      Number.isSafeInteger(requestedLimit) ? requestedLimit : 10,
    );
    return noStoreJson({ items });
  } catch {
    return unavailable();
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const mode = new URL(request.url).searchParams.get("mode");

  if (mode === "machine") {
    if (!await isIngestAuthorized(request)) return unauthorized();
    const binding = env.DB;
    if (!binding) return unavailable();
    try {
      const body = await boundedBody(request);
      const action = String(body.action ?? "");
      let item;
      if (action === "COMPLETE") item = await completeNewsQuestion(binding, body);
      else if (action === "FAIL") item = await failNewsQuestion(binding, body);
      else throw new NewsQuestionInputError("INVALID_ACTION", "机器动作无效");
      return item
        ? noStoreJson({ status: "OK", item })
        : noStoreJson({ error: "租约已失效" }, 409);
    } catch (error) {
      if (error instanceof NewsQuestionInputError) return inputError(error);
      return unavailable();
    }
  }

  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  try {
    const body = await boundedBody(request);
    const question = parseQuestion(body.question);
    const idempotencyKey = parseIdempotencyKey(request.headers.get("idempotency-key"));
    const outcome = await createNewsQuestion(binding, {
      ownerId: actor.actor_id,
      idempotencyKey,
      question,
    });
    if (outcome.kind === "CONFLICT") {
      return noStoreJson({ error: "Idempotency-Key 已用于另一个问题" }, 409);
    }
    if (outcome.kind === "CAPACITY") {
      return noStoreJson({ error: "当前问题较多，请稍后再试" }, 429);
    }
    if ("item" in outcome) {
      return noStoreJson(
        outcome.item,
        outcome.kind === "CREATED" ? 202 : 200,
      );
    }
    return unavailable();
  } catch (error) {
    if (error instanceof NewsQuestionInputError) return inputError(error);
    return unavailable();
  }
}
