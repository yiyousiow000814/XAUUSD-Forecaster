import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { authenticateAssistantRequest } from "../_shared/assistant-auth";
import {
  AssistantConversationInputError,
  claimAssistantTitleJob,
  completeAssistantTitleJob,
  failAssistantTitleJob,
  getOwnerAssistantConversation,
  listOwnerAssistantConversations,
  listOwnerAssistantMessages,
  parseAssistantIdempotencyKey,
  parseAssistantTitle,
  renameOwnerAssistantConversation,
  requestAssistantTitleRegeneration,
  setOwnerAssistantConversationArchived,
} from "../_shared/assistant-conversations";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { isPreviewDeployment, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 身份验证失败" }, 401);
const unavailable = () => noStoreJson({ error: "Assistant 会话暂不可用" }, 503);
const notFound = () => noStoreJson({ error: "找不到这个会话" }, 404);

const boundedBody = async (request: Request) => {
  const body = await readBoundedBody(request, 10_000);
  if (body.status === "too_large") {
    throw new AssistantConversationInputError("PAYLOAD_TOO_LARGE", "内容过长");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body.serialized);
  } catch {
    throw new AssistantConversationInputError("INVALID_JSON", "请求内容无效");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new AssistantConversationInputError("INVALID_JSON", "请求内容无效");
  }
  return parsed as Record<string, unknown>;
};

const inputError = (error: AssistantConversationInputError) => noStoreJson(
  { error: error.message, code: error.code },
  error.code === "PAYLOAD_TOO_LARGE" ? 413 : 400,
);

const validWorkerId = (value: string) => /^[A-Za-z0-9._:-]{3,96}$/.test(value);
const validObjectId = (value: string) => /^[A-Za-z0-9:._-]{1,128}$/.test(value);

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  if (isPreviewDeployment) {
    if (params.get("mode") === "title-claim") {
      return rejectPreviewWrite() ?? previewJson({ error: "Preview 只读" }, 403, "write-rejected");
    }
    return previewJson({ items: [], preview: true }, 200, "synthetic-empty-assistant");
  }
  if (params.get("mode") === "title-claim") {
    if (!await isIngestAuthorized(request)) return unauthorized();
    const binding = env.DB;
    if (!binding) return unavailable();
    const workerId = (params.get("worker_id") ?? "").trim();
    if (!validWorkerId(workerId)) {
      return noStoreJson({ error: "invalid worker identity" }, 400);
    }
    try {
      return noStoreJson({ item: await claimAssistantTitleJob(binding, workerId) });
    } catch {
      return unavailable();
    }
  }

  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  const conversationId = params.get("id")?.trim() ?? "";
  try {
    if (conversationId) {
      if (!validObjectId(conversationId)) return notFound();
      const conversation = await getOwnerAssistantConversation(
        binding, actor.actor_id, conversationId,
      );
      if (!conversation) return notFound();
      const requestedLimit = Number(params.get("message_limit") ?? 30);
      const messages = await listOwnerAssistantMessages(
        binding,
        actor.actor_id,
        conversationId,
        {
          beforeCreatedAt: params.get("before_created_at") ?? undefined,
          beforeId: params.get("before_id") ?? undefined,
          limit: Number.isSafeInteger(requestedLimit) ? requestedLimit : 30,
        },
      );
      return noStoreJson({ conversation, ...messages });
    }
    const requestedLimit = Number(params.get("limit") ?? 20);
    const items = await listOwnerAssistantConversations(binding, actor.actor_id, {
      archived: params.get("archived") === "true",
      limit: Number.isSafeInteger(requestedLimit) ? requestedLimit : 20,
    });
    return noStoreJson({ items });
  } catch (error) {
    if (error instanceof AssistantConversationInputError) return inputError(error);
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
      const item = action === "COMPLETE_TITLE"
        ? await completeAssistantTitleJob(binding, body)
        : action === "FAIL_TITLE"
          ? await failAssistantTitleJob(binding, body)
          : null;
      if (!item && action !== "COMPLETE_TITLE" && action !== "FAIL_TITLE") {
        throw new AssistantConversationInputError("INVALID_ACTION", "机器动作无效");
      }
      return item
        ? noStoreJson({ status: "OK", item })
        : noStoreJson({ error: "租约已失效" }, 409);
    } catch (error) {
      if (error instanceof AssistantConversationInputError) return inputError(error);
      return unavailable();
    }
  }

  const actor = await authenticateAssistantRequest(request, env);
  if (!actor) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  try {
    const body = await boundedBody(request);
    const action = String(body.action ?? "").trim().toUpperCase();
    const conversationId = String(body.conversation_id ?? "").trim();
    if (!validObjectId(conversationId)) return notFound();
    if (action === "RENAME") {
      const item = await renameOwnerAssistantConversation(
        binding, actor.actor_id, conversationId, parseAssistantTitle(body.title),
      );
      return item ? noStoreJson(item) : notFound();
    }
    if (action === "ARCHIVE" || action === "UNARCHIVE") {
      const item = await setOwnerAssistantConversationArchived(
        binding, actor.actor_id, conversationId, action === "ARCHIVE",
      );
      return item ? noStoreJson(item) : notFound();
    }
    if (action === "REGENERATE_TITLE") {
      const idempotencyKey = parseAssistantIdempotencyKey(
        request.headers.get("idempotency-key"),
      );
      const outcome = await requestAssistantTitleRegeneration(binding, {
        ownerId: actor.actor_id,
        conversationId,
        idempotencyKey,
      });
      if (outcome.kind === "NOT_FOUND") return notFound();
      if (outcome.kind === "NO_ASSISTANT_MESSAGE") {
        return noStoreJson({ error: "会话还没有可用于标题的回答" }, 409);
      }
      if (outcome.kind === "ALREADY_PENDING") {
        return noStoreJson({ error: "标题已在生成中" }, 409);
      }
      return noStoreJson(outcome, outcome.kind === "CREATED" ? 202 : 200);
    }
    throw new AssistantConversationInputError("INVALID_ACTION", "会话动作无效");
  } catch (error) {
    if (error instanceof AssistantConversationInputError) return inputError(error);
    return unavailable();
  }
}
