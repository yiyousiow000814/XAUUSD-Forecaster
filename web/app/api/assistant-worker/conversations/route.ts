import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import {
  AssistantConversationInputError,
  claimAssistantTitleJob,
  completeAssistantTitleJob,
  deferAssistantTitleJob,
  failAssistantTitleJob,
  parseAssistantIdempotencyKey,
} from "../../_shared/assistant-conversations";
import { readBoundedBody } from "../../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../../_shared/ingest-auth";
import {
  buildAssistantContext,
  claimAssistantCompactionJob,
  completeAssistantCompactionJob,
  createAssistantPinnedEntry,
  deferAssistantCompactionJob,
  failAssistantCompactionJob,
  scheduleAssistantCompaction,
} from "../../_shared/assistant-memory";
import {
  claimAssistantMemoryIndexJob,
  completeAssistantMemoryIndexJob,
  failAssistantMemoryIndexJob,
} from "../../_shared/assistant-memory-index";
import { rejectPreviewWrite } from "../../_shared/preview";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 机器身份验证失败" }, 401);
const unavailable = () => noStoreJson({ error: "Assistant 会话暂不可用" }, 503);
const notFound = () => noStoreJson({ error: "找不到这个会话" }, 404);

const boundedBody = async (request: Request) => {
  const body = await readBoundedBody(request, 64_000);
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
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  const params = new URL(request.url).searchParams;
  const workerId = (params.get("worker_id") ?? "").trim();
  if (!validWorkerId(workerId)) {
    return noStoreJson({ error: "invalid worker identity" }, 400);
  }
  try {
    const queue = params.get("queue");
    const item = queue === "title"
      ? await claimAssistantTitleJob(binding, workerId)
      : queue === "compaction"
        ? await claimAssistantCompactionJob(binding, workerId)
        : queue === "memory-index"
          ? await claimAssistantMemoryIndexJob(binding, workerId)
          : null;
    if (queue !== "title" && queue !== "compaction" && queue !== "memory-index") {
      throw new AssistantConversationInputError("INVALID_QUEUE", "Assistant 机器队列无效");
    }
    return noStoreJson({ item });
  } catch (error) {
    if (error instanceof AssistantConversationInputError) return inputError(error);
    return unavailable();
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  try {
    const body = await boundedBody(request);
    const action = String(body.action ?? "").trim().toUpperCase();
    let item: unknown = null;
    if (action === "COMPLETE_TITLE") item = await completeAssistantTitleJob(binding, body);
    else if (action === "DEFER_TITLE") item = await deferAssistantTitleJob(binding, body);
    else if (action === "FAIL_TITLE") item = await failAssistantTitleJob(binding, body);
    else if (action === "COMPLETE_COMPACTION") {
      item = await completeAssistantCompactionJob(binding, body);
    } else if (action === "FAIL_COMPACTION") {
      item = await failAssistantCompactionJob(binding, body);
    } else if (action === "DEFER_COMPACTION") {
      item = await deferAssistantCompactionJob(binding, body);
    } else if (action === "COMPLETE_MEMORY_INDEX") {
      item = await completeAssistantMemoryIndexJob(binding, body);
    } else if (action === "FAIL_MEMORY_INDEX") {
      item = await failAssistantMemoryIndexJob(binding, body);
    } else if (action === "SCHEDULE_COMPACTION") {
      const conversationId = String(body.conversation_id ?? "").trim();
      if (!validObjectId(conversationId)) {
        throw new AssistantConversationInputError("INVALID_CONVERSATION_ID", "会话编号无效");
      }
      item = await scheduleAssistantCompaction(binding, conversationId);
    } else if (action === "PIN_STATE" || action === "BUILD_CONTEXT") {
      const conversationId = String(body.conversation_id ?? "").trim();
      if (!validObjectId(conversationId)) {
        throw new AssistantConversationInputError("INVALID_CONVERSATION_ID", "会话编号无效");
      }
      const conversation = await binding.prepare(
        "SELECT owner_id FROM assistant_conversations WHERE id=?",
      ).bind(conversationId).first<{ owner_id: string }>();
      if (!conversation) return notFound();
      if (action === "PIN_STATE") {
        item = await createAssistantPinnedEntry(binding, {
          ownerId: conversation.owner_id,
          conversationId,
          idempotencyKey: parseAssistantIdempotencyKey(
            request.headers.get("idempotency-key"),
          ),
          entry: body.entry,
        });
      } else {
        item = await buildAssistantContext(binding, {
          ownerId: conversation.owner_id,
          conversationId,
          currentUserMessageId: String(body.current_user_message_id ?? ""),
          toolEvidence: Array.isArray(body.tool_evidence)
            ? body.tool_evidence as Array<{ evidence_id: string; content: unknown }>
            : [],
        });
      }
    } else {
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
