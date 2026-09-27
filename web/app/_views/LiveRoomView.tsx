"use client";

import { useCallback, useEffect, useState } from "react";
import { CurrentDataNotice, type CurrentDataPhase } from "../_components/CurrentDataState";
import OverviewNews from "../_components/OverviewNews";
import RuntimeUpdateFailureBanner, { type RuntimeUpdateFailure } from "../_components/RuntimeUpdateFailureBanner";
import {
  loadDashboardResource, readDashboardResource, subscribeDashboardResource,
} from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";

type Payload = {
  preview_status_summary?: boolean;
  preview?: { is_preview?: boolean };
  generated_at: string;
  forward_epoch: string;
  system: {
    online: boolean;
    market_session?: "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";
    market_reopens_at?: string | null;
    quote_age_seconds: number | null;
    mode: string;
    trading_enabled: boolean;
    symbol: string;
    runtime_update_failure?: RuntimeUpdateFailure | null;
    components?: {
      quote_bridge?: { status?: string | null };
    };
  };
  operational_health?: { status: "HEALTHY" | "WARNING" | "ERROR" };
  latest: { bid: number; ask: number; spread: number; source_received_time: string } | null;
  counts: Record<string, number>;
  sources: Record<string, string>;
};

const fmt = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "—" : value.toFixed(digits);

const localTime = (value?: string) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZone: "Asia/Kuala_Lumpur",
      }).format(new Date(value))
    : "—";

export default function LiveRoomView() {
  const cachedStatus = readDashboardResource<Payload>("/api/status");
  const [payload, setPayload] = useState<Payload | null>(() => cachedStatus);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(Boolean(cachedStatus?.preview_status_summary));

  const refresh = useCallback(async (force = false) => {
    setRefreshing(true);
    try {
      setPayload(await loadDashboardResource<Payload>("/api/status", { force, maxAgeMs: 5_000 }));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取实时状态");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    return scheduleDashboardRefresh(
      () => void refresh(true),
      () => void refresh(true),
      DASHBOARD_REFRESH_INTERVALS.live,
      "current",
      "status",
    );
  }, [refresh]);

  useEffect(() => subscribeDashboardResource("/api/status", () => {
    const current = readDashboardResource<Payload>("/api/status");
    setPayload(current);
    if (current && !current.preview_status_summary) setRefreshing(false);
  }), []);

  const latest = payload?.latest;
  const loading = (payload === null || (refreshing && payload.preview_status_summary)) && error === null;
  const currentPhase: CurrentDataPhase = error
    ? "error" : loading ? "loading" : payload?.preview_status_summary ? "snapshot" : "ready";
  const online = Boolean(payload?.system.online && !error);
  const marketClosed = Boolean(
    (payload?.system.market_session === "CLOSED" ||
      payload?.system.market_session === "WEEKLY_CLOSED") && !error
  );
  const mid = latest?.bid && latest.ask ? (latest.bid + latest.ask) / 2 : null;
  return (
    <main className="overview-page">
      <section className="overview-quote" aria-label="黄金行情">
        <div>
          <p className="eyebrow">{marketClosed ? "休市 · 最近报价" : online ? "黄金实时行情" : "黄金行情 · 最近报价"}</p>
          <div className="overview-price">
            <strong>{fmt(mid)}</strong>
            <span className="currency">USD</span>
          </div>
          <div className="overview-quote-detail">
            <span>买价 <b>{fmt(latest?.bid)}</b></span>
            <span>卖价 <b>{fmt(latest?.ask)}</b></span>
            <span>报价时间 <time>{localTime(latest?.source_received_time)}</time></span>
          </div>
        </div>

      </section>

      {error && <div className="error-banner">{error}。行情采集可能仍在运行，但网页数据服务已停止。</div>}
      <RuntimeUpdateFailureBanner failure={payload?.system.runtime_update_failure} />
      <CurrentDataNotice phase={currentPhase} snapshotTime={payload?.generated_at ? localTime(payload.generated_at) : null} />

      <OverviewNews snapshot={Boolean(payload?.preview?.is_preview)} />
      <footer className="overview-footer">仅供参考</footer>
    </main>
  );
}
