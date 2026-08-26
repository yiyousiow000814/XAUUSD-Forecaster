import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
} from "../_shared/release-validation";
import {
  d1CapabilityFailure, D1CapabilityError, requireD1Capabilities,
} from "../_shared/d1-capabilities";
import {
  abandonNewsProjection,
  activateNewsProjection,
  NEWS_GENERATION_ID,
  NEWS_INDEX_MAX_BATCH_ITEMS,
  NEWS_PROJECTION_CATEGORIES,
  NEWS_PROJECTION_CONTRACT_VERSION,
  NewsProjectionProtocolError,
  prepareNewsProjection,
  readNewsProjectionHealth,
  readNewsProjectionPage,
  stageNewsProjectionBatch,
  validateNewsProjectionManifest,
  verifyNewsProjection,
  type NewsProjectionIndexItem,
} from "../_shared/news-projection-store";
import {
  NEWS_REVIEW_STATE_INVARIANT_SQL,
  NEWS_REVIEW_STATES,
  parseNewsReviewState,
} from "../../_lib/news-review-state";
import { publicNewsRecord } from "../../_lib/public-news-copy";

export const dynamic = "force-dynamic";

const MAX_WRITE_BYTES = 120_000;

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
    return NextResponse.json({ error: "invalid news projection payload" }, { status: 400 });
  }
  return NextResponse.json({ error: "新闻档案暂时不可用" }, { status: 503 });
}

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const healthCheck = query.get("health_check") === "1";
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({
    status: "ERROR", projection_state: "RECOVERY_REQUIRED",
    verified_complete: false, error_code: "NEWS_MIRROR_HEALTH_UNAVAILABLE",
  }, { status: 503 });
  try {
    await requireD1Capabilities(binding, ["news_projection_generation"]);
    if (healthCheck) {
      const payload = await readNewsProjectionHealth(binding);
      return NextResponse.json(payload, {
        status: payload.status === "OK" ? 200 : 503,
        headers: { "Cache-Control": "no-store, max-age=0" },
      });
    }
    const reviewState = parseNewsReviewState(query.get("review_state"));
    if (reviewState === null) {
      return NextResponse.json({ error: "invalid review state" }, { status: 400 });
    }
    const page = Math.max(1, Number.parseInt(query.get("page") ?? "1", 10) || 1);
    const pageSize = Math.min(
      50, Math.max(1, Number.parseInt(query.get("limit") ?? "12", 10) || 12),
    );
    const expectedGenerationId = query.get("generation")?.trim() ?? "";
    if (expectedGenerationId && !NEWS_GENERATION_ID.test(expectedGenerationId)) {
      return NextResponse.json({ error: "invalid news generation" }, { status: 400 });
    }
    const payload = await readNewsProjectionPage(binding, {
      page, pageSize, reviewState, category: query.get("category")?.trim() ?? "",
      expectedGenerationId: expectedGenerationId || undefined,
    });
    const now = new Date().toISOString();
    payload.items = payload.items.map(raw => {
      let item = raw;
      if (
        item.model_visibility === "MODEL_VISIBLE"
        && typeof item.impact_expires_at === "string"
        && item.impact_expires_at <= now
      ) {
        item = {
          ...item,
          model_visibility: "IMPACT_EXPIRED",
          impact_status: "EXPIRED",
        };
      }
      return publicNewsRecord(item) as NewsProjectionIndexItem;
    });
    payload.review_state_counts = Object.fromEntries(NEWS_REVIEW_STATES.map(state => [
      state, payload.review_state_counts[state] ?? 0,
    ]));
    return previewBundle ? previewJson(payload, 200, "read-only-current-generation")
      : NextResponse.json(payload, {
        headers: {
          "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=120",
        },
      });
  } catch (reason) {
    return failure(reason);
  }
}

function releaseIndexValidation(
  checked: {
    action: string; generation_id: string; snapshot_id: string;
    contract_version: string; window_start: string; watermark: string;
    expected_index_count: number; expected_detail_count: number;
    withdrawal_count: number; source_digest: string; expected_receipt_digest: string;
    batch_offset: number; item_total: number; item_valid: number; review_valid: number;
  } | null,
) {
  if (!checked || !NEWS_GENERATION_ID.test(String(checked.generation_id ?? ""))) return null;
  if (checked.action === "prepare") {
    try {
      validateNewsProjectionManifest({
        generation_id: checked.generation_id, snapshot_id: checked.snapshot_id,
        contract_version: checked.contract_version, window_start: checked.window_start,
        watermark: checked.watermark, expected_index_count: checked.expected_index_count,
        expected_detail_count: checked.expected_detail_count,
        withdrawal_count: checked.withdrawal_count, source_digest: checked.source_digest,
        expected_receipt_digest: checked.expected_receipt_digest,
      });
      return { manifest: true, prepared_statements: 5 };
    } catch { return null; }
  }
  if (checked.action === "stage_index") {
    const total = Number(checked.item_total ?? 0);
    if (
      !Number.isSafeInteger(checked.batch_offset) || checked.batch_offset < 0
      || total < 1 || total > NEWS_INDEX_MAX_BATCH_ITEMS
      || Number(checked.item_valid) !== total
      || Number(checked.review_valid) !== total
    ) return null;
    return { items: total, prepared_statements: total + 3 };
  }
  if (["activate", "verify", "abandon"].includes(checked.action)) {
    return {
      generation: checked.generation_id,
      prepared_statements: checked.action === "activate" ? 4
        : checked.action === "verify" ? 2 : 4,
    };
  }
  return null;
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "news-index-write", isIngestAuthorized,
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
        `WITH input(doc) AS (SELECT ?),
              root(doc) AS (SELECT doc FROM input WHERE json_valid(doc)),
              batch(payload) AS (
                SELECT value FROM root,json_each(json_extract(doc,'$.items'))
              ),
              batch_checks AS (
                SELECT count(*) item_total,
                       coalesce(sum(CASE WHEN
                         json_type(payload)='object'
                         AND json_type(payload,'$.detail_key')='text'
                         AND length(json_extract(payload,'$.detail_key'))=64
                         AND json_extract(payload,'$.detail_key') NOT GLOB '*[^0-9a-f]*'
                         AND json_type(payload,'$.category')='text'
                         AND json_extract(payload,'$.category') IN (
                           ${NEWS_PROJECTION_CATEGORIES.map(value => `'${value}'`).join(",")}
                         )
                         AND json_type(payload,'$.collector_first_seen_time')='text'
                         AND json_type(payload,'$.cluster_id')='text'
                         AND json_type(payload,'$.mirror_contract')='text'
                         AND (json_extract(payload,'$.model_visibility')<>'MODEL_VISIBLE'
                           OR (json_type(payload,'$.impact_expires_at')='text'
                             AND length(json_extract(payload,'$.impact_expires_at'))=32
                             AND substr(json_extract(payload,'$.impact_expires_at'),27)='+00:00'))
                         THEN 1 ELSE 0 END),0) item_valid,
                       coalesce(sum(CASE WHEN
                         ${NEWS_REVIEW_STATE_INVARIANT_SQL}
                         THEN 1 ELSE 0 END),0) review_valid
                  FROM batch
              )
         SELECT json_extract(doc,'$.action') action,
                json_extract(doc,'$.generation_id') generation_id,
                json_extract(doc,'$.manifest.snapshot_id') snapshot_id,
                json_extract(doc,'$.manifest.contract_version') contract_version,
                json_extract(doc,'$.manifest.window_start') window_start,
                json_extract(doc,'$.manifest.watermark') watermark,
                json_extract(doc,'$.manifest.expected_index_count') expected_index_count,
                json_extract(doc,'$.manifest.expected_detail_count') expected_detail_count,
                json_extract(doc,'$.manifest.withdrawal_count') withdrawal_count,
                json_extract(doc,'$.manifest.source_digest') source_digest,
                json_extract(doc,'$.manifest.expected_receipt_digest') expected_receipt_digest,
                json_extract(doc,'$.offset') batch_offset,
                item_total,item_valid,review_valid
           FROM root CROSS JOIN batch_checks`,
      ).bind(bounded.serialized).first<Parameters<typeof releaseIndexValidation>[0]>();
      const work = releaseIndexValidation(checked);
      if (!work) {
        return NextResponse.json({ error: "invalid news projection payload" }, { status: 400 });
      }
      return releaseValidationResponse(validation, {
        body: "bounded-read", json: "d1-json1+json-each", transformed: work,
        mutation_boundary: `news-generation-${checked?.action}`,
      });
    }
    const body = JSON.parse(bounded.serialized) as {
      action?: unknown; generation_id?: unknown; manifest?: unknown;
      offset?: unknown; items?: unknown;
    };
    if (body.action === "prepare") {
      const manifest = validateNewsProjectionManifest(body.manifest);
      if (body.generation_id !== manifest.generation_id) {
        return NextResponse.json({ error: "generation identity mismatch" }, { status: 400 });
      }
      return NextResponse.json(await prepareNewsProjection(binding, manifest));
    }
    if (typeof body.generation_id !== "string") {
      return NextResponse.json({ error: "invalid news generation" }, { status: 400 });
    }
    if (body.action === "stage_index") {
      if (!Number.isSafeInteger(body.offset) || !Array.isArray(body.items)) {
        return NextResponse.json({ error: "invalid news index batch" }, { status: 400 });
      }
      return NextResponse.json(await stageNewsProjectionBatch(
        binding, "index", body.generation_id, Number(body.offset),
        body.items as NewsProjectionIndexItem[],
      ));
    }
    if (body.action === "activate") {
      return NextResponse.json(await activateNewsProjection(binding, body.generation_id));
    }
    if (body.action === "verify") {
      return NextResponse.json(await verifyNewsProjection(binding, body.generation_id));
    }
    if (body.action === "abandon") {
      return NextResponse.json(await abandonNewsProjection(binding, body.generation_id));
    }
    return NextResponse.json({ error: "invalid news projection action" }, { status: 400 });
  } catch (reason) {
    return failure(reason);
  }
}

export { NEWS_PROJECTION_CONTRACT_VERSION };
