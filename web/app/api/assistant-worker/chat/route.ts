import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import {
  appendAssistantChatEvents,
  AssistantChatInputError,
  claimAssistantChatTurn,
  completeAssistantChatTurn,
  deferAssistantChatTurn,
  failAssistantChatTurn,
  renewAssistantChatTurn,
} from "../../_shared/assistant-chat";
import { readBoundedBody } from "../../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../../_shared/ingest-auth";
import { rejectPreviewWrite } from "../../_shared/preview";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 机器身份验证失败" }, 401);
const unavailable = () => noStoreJson({ error: "Assistant 对话暂不可用" }, 503);
const conflict = () => noStoreJson({ error: "Assistant turn 状态已改变" }, 409);

const boundedBody = async (request: Request) => {
  const body = await readBoundedBody(request, 384_000);
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

export async function GET(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  try {
    const params = new URL(request.url).searchParams;
    const item = await claimAssistantChatTurn(
      binding, params.get("worker_id")?.trim() ?? "",
    );
    return noStoreJson({ item });
  } catch (error) {
    if (error instanceof AssistantChatInputError) return inputError(error);
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
    if (action === "RENEW") {
      item = await renewAssistantChatTurn(binding, {
        id: body.id,
        lease_token: body.lease_token,
      });
    } else if (action === "EVENTS") {
      item = await appendAssistantChatEvents(binding, {
        id: body.id,
        lease_token: body.lease_token,
        events: body.events,
      });
    } else if (action === "COMPLETE") {
      item = await completeAssistantChatTurn(binding, body);
    } else if (action === "FAIL") {
      item = await failAssistantChatTurn(binding, body);
    } else if (action === "DEFER") {
      item = await deferAssistantChatTurn(binding, body);
    } else {
      throw new AssistantChatInputError("INVALID_ACTION", "机器动作无效");
    }
    return item
      ? noStoreJson({ status: "OK", item })
      : conflict();
  } catch (error) {
    if (error instanceof AssistantChatInputError) return inputError(error);
    return unavailable();
  }
}
