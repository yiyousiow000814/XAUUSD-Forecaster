import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../../_shared/ingest-auth";
import {
  claimNewsQuestion,
  completeNewsQuestion,
  deferNewsQuestion,
  failNewsQuestion,
  NewsQuestionInputError,
} from "../../_shared/news-questions";
import { rejectPreviewWrite } from "../../_shared/preview";

export const dynamic = "force-dynamic";

const noStoreJson = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status,
  headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const unauthorized = () => noStoreJson({ error: "Assistant 机器身份验证失败" }, 401);
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
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) return unauthorized();
  const binding = env.DB;
  if (!binding) return unavailable();
  const params = new URL(request.url).searchParams;
  const workerId = (params.get("worker_id") ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{3,96}$/.test(workerId)) {
    return noStoreJson({ error: "invalid worker identity" }, 400);
  }
  try {
    return noStoreJson({ item: await claimNewsQuestion(binding, workerId) });
  } catch {
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
    let item;
    if (action === "COMPLETE") item = await completeNewsQuestion(binding, body);
    else if (action === "DEFER") item = await deferNewsQuestion(binding, body);
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
