import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import {
  claimOperatorRetryRequest,
  finishOperatorRetryRequest,
  OperatorRetryInputError,
  syncOperatorRetryJobs,
} from "../_shared/operator-retry";
import { rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";
const json = (payload: unknown, status = 200) => NextResponse.json(payload, {
  status, headers: { "Cache-Control": "private, no-store, max-age=0" },
});

const readBody = async (request: Request) => {
  const bounded = await readBoundedBody(request, 500_000);
  if (bounded.status === "too_large") throw new OperatorRetryInputError("PAYLOAD_TOO_LARGE", "payload too large");
  const parsed: unknown = JSON.parse(bounded.serialized);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new OperatorRetryInputError("INVALID_JSON", "invalid payload");
  return parsed as Record<string, unknown>;
};

export async function GET(request: Request) {
  const preview = rejectPreviewWrite();
  if (preview) return preview;
  if (!await isIngestAuthorized(request)) return json({ error: "machine authorization failed" }, 401);
  if (!env.DB) return json({ error: "retry worker unavailable" }, 503);
  const workerId = new URL(request.url).searchParams.get("worker_id")?.trim() ?? "";
  if (!/^[A-Za-z0-9._:-]{3,96}$/.test(workerId)) return json({ error: "invalid worker identity" }, 400);
  try { return json({ item: await claimOperatorRetryRequest(env.DB, workerId) }); }
  catch { return json({ error: "retry claim failed" }, 503); }
}

export async function POST(request: Request) {
  const preview = rejectPreviewWrite();
  if (preview) return preview;
  if (!await isIngestAuthorized(request)) return json({ error: "machine authorization failed" }, 401);
  if (!env.DB) return json({ error: "retry worker unavailable" }, 503);
  try {
    const input = await readBody(request);
    const action = String(input.action ?? "").toUpperCase();
    if (action === "SYNC_JOBS") {
      if (!Array.isArray(input.items)) throw new OperatorRetryInputError("INVALID_SYNC", "items required");
      return json(await syncOperatorRetryJobs(env.DB, input.items as Array<Record<string, unknown>>));
    }
    if (action === "FINISH") {
      const item = await finishOperatorRetryRequest(env.DB, input);
      return item ? json({ item }) : json({ error: "retry lease changed" }, 409);
    }
    throw new OperatorRetryInputError("INVALID_ACTION", "invalid retry worker action");
  } catch (error) {
    if (error instanceof OperatorRetryInputError || error instanceof SyntaxError) {
      return json({ error: error.message }, 400);
    }
    return json({ error: "retry worker update failed" }, 503);
  }
}
