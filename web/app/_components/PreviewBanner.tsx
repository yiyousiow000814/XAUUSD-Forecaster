"use client";

import { useEffect, useState } from "react";

type PreviewInfo = {
  is_preview?: boolean;
  branch?: string;
  commit_sha?: string;
  snapshot_generated_at?: string;
};

export default function PreviewBanner() {
  const [preview, setPreview] = useState<PreviewInfo | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/status", { cache: "no-store", signal: controller.signal })
      .then(response => response.ok ? response.json() : null)
      .then(payload => setPreview(payload?.preview?.is_preview ? payload.preview : null))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  if (!preview) return null;
  return <aside className="preview-banner" role="status">
    <strong>PR 预览</strong>
    <span>分支代码 + 构建时数据，不是实时行情</span>
    <code>{preview.branch} · {preview.commit_sha?.slice(0, 8)}</code>
  </aside>;
}
