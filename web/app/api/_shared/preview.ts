import { NextResponse } from "next/server";

export type PreviewBundle = {
  status: Record<string, unknown>;
  audit?: Record<string, unknown>;
  audit_briefs?: Record<string, unknown> | null;
  audit_stories?: Record<string, unknown> | null;
  audit_decisions?: Record<string, unknown> | null;
  learning_summary?: Record<string, unknown>;
  learning_history?: Array<{
    resource: string; record_key: string; sort_epoch: number;
    payload_hash: string; payload: Record<string, unknown>;
  }>;
  market_chart: Record<string, unknown>;
  news_index: {
    items?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  news_evidence?: {
    snapshot_id: string;
    contract_version: string;
    activated_at?: string | null;
    items: Array<Record<string, unknown>>;
  };
  news_details: Record<string, Record<string, unknown>>;
};

declare const __AURUM_PREVIEW_BUNDLE__: PreviewBundle | null;

export const previewBundle: PreviewBundle | null = __AURUM_PREVIEW_BUNDLE__;
export const isPreviewDeployment = previewBundle !== null;

export function previewJson(payload: unknown, status = 200, source = "immutable-build-snapshot") {
  return NextResponse.json(payload, {
    status,
    headers: {
      "Cache-Control": "no-store, max-age=0",
      "X-Aurum-Preview": source,
    },
  });
}

export function rejectPreviewWrite() {
  if (!isPreviewDeployment) return null;
  return previewJson(
    { error: "PR Preview 只读且无运行或交易权限，不接受写入" },
    403,
    "write-rejected",
  );
}
