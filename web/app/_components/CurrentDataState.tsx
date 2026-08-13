import type { ReactNode } from "react";

export type CurrentDataPhase = "loading" | "ready" | "snapshot" | "error";

function SyncSignal() {
  return <span className="current-data-signal" aria-hidden="true"><i /><i /><i /></span>;
}

export function MetricValue({ phase, children }: { phase: CurrentDataPhase; children: ReactNode }) {
  if (phase === "loading") {
    return <span className="current-metric is-loading" role="status" aria-label="正在同步当前数据"><span>同步</span><SyncSignal /></span>;
  }
  if (phase === "snapshot") {
    return <span className="current-metric is-snapshot" title="实时数据暂不可用，显示构建快照">{children}<small>快照</small></span>;
  }
  if (phase === "error") return <span aria-label="当前数据暂不可用">—</span>;
  return <>{children}</>;
}

export function CurrentDataNotice({ phase, snapshotTime }: { phase: CurrentDataPhase; snapshotTime?: string | null }) {
  if (phase === "loading") {
    return <div className="current-data-notice is-loading" role="status"><SyncSignal /><b>正在同步页面当前指标</b><span>页面结构已经就绪，指标会在共享数据确认后一起更新。</span></div>;
  }
  if (phase === "snapshot") {
    return <div className="current-data-notice is-snapshot"><b>实时同步暂不可用</b><span>当前明确显示构建快照{snapshotTime ? ` · ${snapshotTime}` : ""}，不会冒充实时数字。</span></div>;
  }
  return null;
}
