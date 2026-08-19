import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import {
  activateNewsEvidenceSnapshot,
  cleanupNewsEvidenceSnapshots,
  evidenceMode,
  EvidenceItem,
  NEWS_EVIDENCE_CONTRACT_VERSION,
  NEWS_EVIDENCE_SNAPSHOT_ID,
  NewsEvidenceProtocolError,
  prepareNewsEvidenceSnapshot,
  readNewsEvidencePage,
  readPreviewNewsEvidencePage,
  stageNewsEvidenceBatch,
} from "../_shared/news-evidence-store";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const MAX_WRITE_BYTES = 400_000;
const MAX_WRITE_ITEMS = 20;
const MAX_PAGE_ITEMS = 50;

function protocolFailure(reason: unknown) {
  if (reason instanceof NewsEvidenceProtocolError) {
    return NextResponse.json({
      error: reason.message,
      error_code: reason.code,
      ...reason.details,
    }, { status: reason.status });
  }
  if (reason instanceof SyntaxError) {
    return NextResponse.json({ error: "invalid evidence payload" }, { status: 400 });
  }
  return NextResponse.json({
    error: "新闻证据档案暂时不可用",
  }, { status: 503 });
}

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const mode = evidenceMode(query.get("mode"));
  if (mode === null) {
    return NextResponse.json({ error: "invalid evidence mode" }, { status: 400 });
  }
  const page = Math.max(1, Number.parseInt(query.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(
    MAX_PAGE_ITEMS,
    Math.max(1, Number.parseInt(query.get("limit") ?? "20", 10) || 20),
  );
  if (previewBundle?.news_evidence) {
    try {
      return previewJson(readPreviewNewsEvidencePage(
        previewBundle.news_evidence,
        { mode, rawCursor: query.get("cursor"), page, pageSize },
      ), 200, "immutable-build-snapshot-evidence");
    } catch (reason) {
      return protocolFailure(reason);
    }
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "新闻证据档案暂时不可用" }, { status: 503 });
  }
  try {
    const payload = await readNewsEvidencePage(binding, {
      mode, rawCursor: query.get("cursor"), page, pageSize,
    });
    return previewBundle ? previewJson(payload, 200, "read-only-d1-archive")
      : NextResponse.json(payload, {
        headers: {
          "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=120",
        },
      });
  } catch (reason) {
    return protocolFailure(reason);
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const bounded = await readBoundedBody(request, MAX_WRITE_BYTES);
  if (bounded.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  try {
    const body = JSON.parse(bounded.serialized) as {
      contract_version?: unknown;
      prepare_snapshot?: unknown;
      snapshot_id?: unknown;
      items?: unknown;
      offset?: unknown;
      activate_snapshot?: unknown;
      expected_count?: unknown;
      cleanup_active_snapshot?: unknown;
    };
    if (body.contract_version !== NEWS_EVIDENCE_CONTRACT_VERSION) {
      return NextResponse.json({ error: "invalid evidence contract" }, { status: 400 });
    }
    if (typeof body.cleanup_active_snapshot === "string") {
      return NextResponse.json(await cleanupNewsEvidenceSnapshots(
        binding, body.cleanup_active_snapshot,
      ));
    }
    if (typeof body.prepare_snapshot === "string") {
      if (
        !NEWS_EVIDENCE_SNAPSHOT_ID.test(body.prepare_snapshot)
        || !Number.isSafeInteger(body.expected_count)
        || Number(body.expected_count) < 0
      ) {
        return NextResponse.json({ error: "invalid evidence manifest" }, { status: 400 });
      }
      return NextResponse.json(await prepareNewsEvidenceSnapshot(
        binding, body.prepare_snapshot, Number(body.expected_count),
      ));
    }
    if (typeof body.activate_snapshot === "string") {
      if (
        !NEWS_EVIDENCE_SNAPSHOT_ID.test(body.activate_snapshot)
        || !Number.isSafeInteger(body.expected_count)
        || Number(body.expected_count) < 0
      ) {
        return NextResponse.json({ error: "invalid evidence activation" }, { status: 400 });
      }
      return NextResponse.json(await activateNewsEvidenceSnapshot(
        binding, body.activate_snapshot, Number(body.expected_count),
      ));
    }
    if (
      typeof body.snapshot_id !== "string"
      || !NEWS_EVIDENCE_SNAPSHOT_ID.test(body.snapshot_id)
      || !Number.isSafeInteger(body.offset) || Number(body.offset) < 0
      || !Array.isArray(body.items) || body.items.length < 1
      || body.items.length > MAX_WRITE_ITEMS
    ) {
      return NextResponse.json({ error: "invalid evidence batch" }, { status: 400 });
    }
    return NextResponse.json(await stageNewsEvidenceBatch(
      binding, body.snapshot_id, Number(body.offset), body.items as EvidenceItem[],
    ));
  } catch (reason) {
    return protocolFailure(reason);
  }
}
