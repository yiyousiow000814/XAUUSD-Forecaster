import type { ReactNode } from "react";

export type CurrentDataPhase = "loading" | "ready" | "snapshot" | "error";

function MetricLoadingSignal() {
  return <span className="current-metric-placeholder" aria-hidden="true"><i /><i /><i /></span>;
}

export function MetricValue({
  phase,
  children,
  snapshotLabel = "快照",
  snapshotTitle = "实时数据暂不可用，显示构建快照",
}: {
  phase: CurrentDataPhase;
  children: ReactNode;
  snapshotLabel?: string;
  snapshotTitle?: string;
}) {
  if (phase === "loading") {
    return <span className="current-metric is-loading" role="progressbar" aria-label="当前数字读取中"><MetricLoadingSignal /></span>;
  }
  if (phase === "snapshot") {
    return <span className="current-metric is-snapshot" title={snapshotTitle}>{children}<small>{snapshotLabel}</small></span>;
  }
  if (phase === "error") return <span aria-label="当前数据暂不可用">—</span>;
  return <>{children}</>;
}

export function CurrentDataNotice({ phase, snapshotTime, snapshotKind = "fallback" }: {
  phase: CurrentDataPhase;
  snapshotTime?: string | null;
  snapshotKind?: "fallback" | "branch";
}) {
  if (phase === "loading") {
    return null;
  }
  if (phase === "snapshot") {
    if (snapshotKind === "branch") {
      return <div className="current-data-notice is-snapshot"><b>分支构建快照</b><span>此页使用分支重新计算的构建快照{snapshotTime ? ` · ${snapshotTime}` : ""}，不代表当前生产结果。</span></div>;
    }
    return <div className="current-data-notice is-snapshot"><b>实时同步暂不可用</b><span>当前明确显示构建快照{snapshotTime ? ` · ${snapshotTime}` : ""}，不会冒充实时数字。</span></div>;
  }
  return null;
}
