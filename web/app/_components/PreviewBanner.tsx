"use client";

import { useEffect, useState } from "react";
import {
  readDashboardResource, subscribeDashboardResource,
} from "../_lib/dashboard-resource";

type PreviewInfo = {
  is_preview?: boolean;
  branch?: string;
  commit_sha?: string;
  snapshot_generated_at?: string;
};

export default function PreviewBanner() {
  const readPreview = () => {
    const payload = readDashboardResource<{ preview?: PreviewInfo }>("/api/status");
    return payload?.preview?.is_preview ? payload.preview : null;
  };
  const [preview, setPreview] = useState<PreviewInfo | null>(readPreview);

  useEffect(() => subscribeDashboardResource(
    "/api/status", () => setPreview(readPreview()),
  ), []);

  if (!preview) return null;
  return <aside className="preview-banner" role="status">
    <strong>PR 预览</strong>
    <span>分支代码 · 只读不交易 · 数据可能为当前读取或构建快照</span>
    <code>{preview.branch} · {preview.commit_sha?.slice(0, 8)}</code>
  </aside>;
}
