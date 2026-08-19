import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { authenticateDashboardOperatorRequest } from "../_shared/dashboard-operator-auth";
import {
  AssistantChatInputError,
  cancelOwnerAssistantChatTurn,
  createAssistantChatTurn,
  getOwnerAssistantChatTurn,
  listOwnerAssistantTurnEvents,
} from "../_shared/assistant-chat";
import {
  ASSISTANT_EVENT_PROTOCOL_VERSION,
  encodeAssistantSse,
} from "../_shared/assistant-events";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isPreviewDeployment, previewJson, rejectPreviewWrite } from "../_shared/preview";
import {
  ASSISTANT_ACCEPTING_TURNS,
  ASSISTANT_UNAVAILABLE_CODE,
  ASSISTANT_UNAVAILABLE_MESSAGE,
} from "../../_lib/assistant-availability";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 身份验证失败" }, 401);
const unavailable = () => noStoreJson({ error: "Assistant 对话暂不可用" }, 503);
const notFound = () => noStoreJson({ error: "找不到这个 Assistant turn" }, 404);
const conflict = () => noStoreJson({ error: "Assistant turn 状态已改变" }, 409);
const objectId = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;

const boundedBody = async (request: Request, maximumBytes: number) => {
  const body = await readBoundedBody(request, maximumBytes);
  if (body.status === "too_large") {
    throw new AssistantChatInputError("PAYLOAD_TOO_LARGE", "内容过长");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body.serialized);
  } catch {
    throw new AssistantChatInputError("INVALID_JSON", "请求内容无效");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new AssistantChatInputError("INVALID_JSON", "请求内容无效");
  }
  return parsed as Record<string, unknown>;
};

const inputError = (error: AssistantChatInputError) => noStoreJson(
  { error: error.message, code: error.code },
  error.code === "PAYLOAD_TOO_LARGE" ? 413 : 400,
);

const previewEvents = () => new Response(": immutable Preview has no Assistant turns\n\n", {
  status: 200,
  headers: {
    "Cache-Control": "no-store, max-age=0",
    "Content-Type": "text/event-stream; charset=utf-8",
    "X-Assistant-Event-Protocol": ASSISTANT_EVENT_PROTOCOL_VERSION,
    "X-Aurum-Preview": "synthetic-empty-assistant",
  },
});

const eventCursor = (request: Request, params: URLSearchParams) => {
  const query = params.get("after");
  const header = request.headers.get("last-event-id");
  if (query !== null && header !== null && query !== header) {
    throw new AssistantChatInputError("INVALID_EVENT_CURSOR", "事件游标冲突");
  }
  const raw = query ?? header ?? "0";
  if (!/^(?:0|[1-9][0-9]{0,2})$/.test(raw)) {
    throw new AssistantChatInputError("INVALID_EVENT_CURSOR", "事件游标无效");
  }
  return Number(raw);
};

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const mode = params.get("mode");
  if (isPreviewDeployment) {
    return mode === "events"
      ? previewEvents()
      : previewJson({ item: null, preview: true }, 200, "synthetic-empty-assistant");
  }

  const actor = await authenticateDashboardOperatorRequest(request, env);
  if (!actor) return unauthorized();
  if (mode !== null && mode !== "events") {
    return inputError(new AssistantChatInputError("INVALID_MODE", "Assistant 查询模式无效"));
  }
  const binding = env.DB;
  if (!binding) return unavailable();
  const turnId = params.get("id")?.trim() ?? "";
  if (!objectId.test(turnId)) return notFound();
  try {
    if (mode === "events") {
      const requestedLimit = Number(params.get("limit") ?? 50);
      const replay = await listOwnerAssistantTurnEvents(binding, {
        ownerId: actor.actor_id,
        turnId,
        afterSequence: eventCursor(request, params),
        limit: Number.isSafeInteger(requestedLimit) ? requestedLimit : 50,
      });
      if (!replay) return notFound();
      return new Response(replay.events.map(encodeAssistantSse).join(""), {
        status: 200,
        headers: {
          "Cache-Control": "private, no-store, max-age=0",
          "Content-Type": "text/event-stream; charset=utf-8",
          "X-Accel-Buffering": "no",
          "X-Assistant-Event-Protocol": ASSISTANT_EVENT_PROTOCOL_VERSION,
          "X-Assistant-Turn-Status": replay.turn.status,
          "X-Assistant-Next-Sequence": String(replay.next_sequence),
          "X-Assistant-Has-More": String(replay.has_more),
        },
      });
    }
    const item = await getOwnerAssistantChatTurn(binding, actor.actor_id, turnId);
    return item ? noStoreJson(item) : notFound();
  } catch (error) {
    if (error instanceof AssistantChatInputError) return inputError(error);
    return unavailable();
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const mode = new URL(request.url).searchParams.get("mode");
  const actor = await authenticateDashboardOperatorRequest(request, env);
  if (!actor) return unauthorized();
  if (mode !== null) {
    return inputError(new AssistantChatInputError("INVALID_MODE", "Assistant 写入模式无效"));
  }
  const binding = env.DB;
  if (!binding) return unavailable();
  try {
    const body = await boundedBody(request, 20_000);
    const action = String(body.action ?? "SEND").trim().toUpperCase();
    if (action === "CANCEL") {
      const turnId = String(body.turn_id ?? "").trim();
      if (!objectId.test(turnId)) return notFound();
      const item = await cancelOwnerAssistantChatTurn(
        binding, actor.actor_id, turnId,
      );
      return item ? noStoreJson(item) : conflict();
    }
    if (action !== "SEND") {
      throw new AssistantChatInputError("INVALID_ACTION", "Assistant 动作无效");
    }
    if (!ASSISTANT_ACCEPTING_TURNS) {
      return noStoreJson({
        error: ASSISTANT_UNAVAILABLE_MESSAGE,
        code: ASSISTANT_UNAVAILABLE_CODE,
      }, 503);
    }
    const conversationId = body.conversation_id == null
      ? null : String(body.conversation_id).trim();
    const outcome = await createAssistantChatTurn(binding, {
      ownerId: actor.actor_id,
      idempotencyKey: request.headers.get("idempotency-key") ?? "",
      message: body.message as string,
      conversationId,
    });
    if (outcome.kind === "CONFLICT") {
      return noStoreJson({ error: "Idempotency-Key 已用于另一个 turn" }, 409);
    }
    if (outcome.kind === "CAPACITY") {
      return noStoreJson({ error: "Assistant 当前繁忙，请稍后再试" }, 429);
    }
    if (outcome.kind === "BUSY") {
      return noStoreJson({ error: "这个会话已有进行中的 turn" }, 409);
    }
    if (outcome.kind === "NOT_FOUND") return notFound();
    return noStoreJson(outcome.item, outcome.kind === "CREATED" ? 202 : 200);
  } catch (error) {
    if (error instanceof AssistantChatInputError) return inputError(error);
    return unavailable();
  }
}
