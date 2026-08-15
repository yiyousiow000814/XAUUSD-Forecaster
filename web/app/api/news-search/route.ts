import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import {
  parseNewsRetrievalRequest,
  retrieveNews,
} from "../_shared/news-retrieval";
import { previewBundle, previewJson } from "../_shared/preview";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const parsed = parseNewsRetrievalRequest(request);
  if (!parsed.ok) {
    return NextResponse.json(
      { error: parsed.error, code: parsed.code },
      { status: parsed.status, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }

  const outcome = await retrieveNews({
    binding: env.DB as D1Database | undefined,
    request: parsed.value,
    previewItems: previewBundle ? (previewBundle.news_index.items ?? []) : undefined,
  });
  if (!outcome.ok) {
    return NextResponse.json(
      { error: outcome.error, code: outcome.code },
      { status: outcome.status, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }

  if (previewBundle) {
    const source = outcome.payload.source_mode === "READ_ONLY_D1_ARCHIVE"
      ? "read-only-d1-archive"
      : outcome.payload.source_mode === "IMMUTABLE_PREVIEW_SNAPSHOT"
        ? "immutable-build-snapshot"
        : "not-queried";
    return previewJson(outcome.payload, 200, source);
  }
  return NextResponse.json(outcome.payload, {
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}
