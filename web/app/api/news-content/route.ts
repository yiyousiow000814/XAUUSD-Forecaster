import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { rejectPreviewWrite } from "../_shared/preview";
import { publicNewsRecord } from "../../_lib/public-news-copy";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
} from "../_shared/release-validation";
import {
  d1CapabilityFailure, D1CapabilityError, requireD1Capabilities,
} from "../_shared/d1-capabilities";
import {
  NEWS_GENERATION_ID,
  NewsProjectionProtocolError,
  readNewsProjectionDetails,
  stageNewsProjectionBatch,
  type NewsProjectionDetailItem,
} from "../_shared/news-projection-store";

export const dynamic = "force-dynamic";

const DETAIL_KEY_PATTERN = /^[a-f0-9]{64}$/;
const DETAIL_BATCH_LIMIT = 12;
const MAX_WRITE_BYTES = 450_000;

function failure(reason: unknown) {
  if (reason instanceof D1CapabilityError) {
    return NextResponse.json(d1CapabilityFailure(reason), { status: 503 });
  }
  if (reason instanceof NewsProjectionProtocolError) {
    return NextResponse.json({
      error: reason.message, error_code: reason.code, ...reason.details,
    }, { status: reason.status });
  }
  if (reason instanceof SyntaxError) {
    return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
  }
  return NextResponse.json({ error: "新闻详情档案暂时不可用" }, { status: 503 });
}

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const detailKeys = [...new Set(
    (query.get("keys") ?? query.get("key") ?? "")
      .split(",").map(key => key.trim()).filter(Boolean),
  )];
  if (
    !detailKeys.length || detailKeys.length > DETAIL_BATCH_LIMIT
    || detailKeys.some(key => !DETAIL_KEY_PATTERN.test(key))
  ) {
    return NextResponse.json({ error: "invalid news detail keys" }, { status: 400 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    await requireD1Capabilities(binding, ["news_projection_generation"]);
    const result = await readNewsProjectionDetails(binding, detailKeys);
    const items = Object.fromEntries(Object.entries(result.items).map(([key, value]) => [
      key, publicNewsRecord(value),
    ]));
    if (detailKeys.length === 1) {
      const item = items[detailKeys[0]];
      if (!item) {
        return NextResponse.json({
          error: "新闻详情仍在恢复", error_code: "NEWS_DETAIL_MISSING",
          generation_id: result.generation_id,
        }, { status: 404 });
      }
      return NextResponse.json(item, {
        headers: { "Cache-Control": "private, max-age=300" },
      });
    }
    return NextResponse.json({
      generation_id: result.generation_id, items, missing: result.missing,
    }, { headers: { "Cache-Control": "private, max-age=300" } });
  } catch (reason) {
    return failure(reason);
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "news-content-write", isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const bounded = await readBoundedBody(request, MAX_WRITE_BYTES);
  if (bounded.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    await requireD1Capabilities(binding, ["news_projection_generation"]);
    if (isReleaseValidationContext(validation)) {
      const checked = await binding.prepare(
        `WITH root(doc) AS (SELECT ? WHERE json_valid(?)),
              batch(row) AS (
                SELECT value FROM root,json_each(json_extract(doc,'$.items'))
              )
         SELECT json_extract(doc,'$.action') action,
                json_extract(doc,'$.generation_id') generation_id,
                json_extract(doc,'$.offset') batch_offset,
                (SELECT count(*) FROM batch) total,
                (SELECT count(*) FROM batch WHERE
                  json_type(row)='object'
                  AND json_type(row,'$.detail_key')='text'
                  AND length(json_extract(row,'$.detail_key'))=64
                  AND json_extract(row,'$.detail_key') NOT GLOB '*[^0-9a-f]*'
                  AND json_type(row,'$.detail_hash')='text'
                  AND length(json_extract(row,'$.detail_hash'))=64
                  AND json_extract(row,'$.detail_hash') NOT GLOB '*[^0-9a-f]*'
                  AND json_type(row,'$.payload') IN ('object','array')) valid
           FROM root`,
      ).bind(bounded.serialized, bounded.serialized).first<{
        action: string; generation_id: string; batch_offset: number;
        total: number; valid: number;
      }>();
      const total = Number(checked?.total ?? 0);
      if (
        !checked || checked.action !== "stage_details"
        || !NEWS_GENERATION_ID.test(String(checked.generation_id ?? ""))
        || !Number.isSafeInteger(checked.batch_offset) || checked.batch_offset < 0
        || total < 1 || total > 20 || Number(checked.valid ?? 0) !== total
      ) {
        return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
      }
      return releaseValidationResponse(validation, {
        body: "bounded-read", json: "d1-json1+json-each",
        transformed: { items: total, prepared_statements: total + 2 },
        mutation_boundary: "news-generation-detail-stage",
      });
    }
    const body = JSON.parse(bounded.serialized) as {
      action?: unknown; generation_id?: unknown; offset?: unknown; items?: unknown;
    };
    if (
      body.action !== "stage_details" || typeof body.generation_id !== "string"
      || !Number.isSafeInteger(body.offset) || !Array.isArray(body.items)
    ) {
      return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
    }
    return NextResponse.json(await stageNewsProjectionBatch(
      binding, "detail", body.generation_id, Number(body.offset),
      body.items as NewsProjectionDetailItem[],
    ));
  } catch (reason) {
    return failure(reason);
  }
}
