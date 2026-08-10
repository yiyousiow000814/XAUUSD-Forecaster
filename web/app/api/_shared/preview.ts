import { NextResponse } from "next/server";

export type PreviewBundle = {
  status: Record<string, unknown>;
  // The complete structured ledger stays in D1; this is first-paint only.
  learning_summary?: Record<string, unknown>;
  news_index: {
    items?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  news_details: Record<string, Record<string, unknown>>;
};

declare const __AURUM_PREVIEW_BUNDLE__: PreviewBundle | null;

export const previewBundle: PreviewBundle | null = __AURUM_PREVIEW_BUNDLE__;
export const isPreviewDeployment = previewBundle !== null;

export function previewJson(payload: unknown, status = 200) {
  return NextResponse.json(payload, {
    status,
    headers: {
      "Cache-Control": "no-store, max-age=0",
      "X-Aurum-Preview": "immutable-build-snapshot",
    },
  });
}

export function rejectPreviewWrite() {
  if (!isPreviewDeployment) return null;
  return previewJson({ error: "PR Preview 是只读快照，不接受同步写入" }, 403);
}
