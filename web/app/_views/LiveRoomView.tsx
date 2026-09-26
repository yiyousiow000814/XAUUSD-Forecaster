"use client";

import { useCallback, useEffect, useState } from "react";
import { CurrentDataNotice, MetricValue, type CurrentDataPhase } from "../_components/CurrentDataState";
import CountValue from "../_components/CountValue";
import RuntimeUpdateFailureBanner, { type RuntimeUpdateFailure } from "../_components/RuntimeUpdateFailureBanner";
import {
  loadDashboardResource, readDashboardResource, subscribeDashboardResource,
} from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { effectiveQuoteAgeSeconds } from "../_lib/live-broadcast";
import { quoteBridgePresentation } from "../_lib/quote-bridge-state";
import { resolveNewsMetrics, type NewsMetrics } from "../_lib/news-metrics";

type Payload = {
  preview_status_summary?: boolean;
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
  news_metrics?: NewsMetrics;
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
  const [now, setNow] = useState(() => Date.now());

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

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const latest = payload?.latest;
  const quoteAgeSeconds = effectiveQuoteAgeSeconds(
    payload as unknown as Record<string, unknown> | null, now,
  );
  const loading = (payload === null || (refreshing && payload.preview_status_summary)) && error === null;
  const currentPhase: CurrentDataPhase = error
    ? "error" : loading ? "loading" : payload?.preview_status_summary ? "snapshot" : "ready";
  const online = Boolean(payload?.system.online && !error);
  const marketClosed = Boolean(
    (payload?.system.market_session === "CLOSED" ||
      payload?.system.market_session === "WEEKLY_CLOSED") && !error
  );
  const mid = latest?.bid && latest.ask ? (latest.bid + latest.ask) / 2 : null;
  const newsMetrics = resolveNewsMetrics(payload);
  const quoteBridge = quoteBridgePresentation(
    payload?.system.components?.quote_bridge?.status,
    payload?.system.market_session,
  );
  return (
    <main>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">CURRENT MARKET / CTRADER BID—ASK</p>
          <div className="price-line">
            <span className="symbol">XAU</span>
            <strong>{fmt(mid)}</strong>
            <span className="currency">USD</span>
          </div>
          <div className="quote-strip">
            <span>BID <b>{fmt(latest?.bid)}</b></span>
            <span>ASK <b>{fmt(latest?.ask)}</b></span>
            <span>SPREAD <b>{fmt(latest?.spread, 3)}</b></span>
            <span>AGE <b>{fmt(quoteAgeSeconds, 1)}s</b></span>
          </div>
        </div>

      </section>

      {error && <div className="error-banner">{error}。行情采集可能仍在运行，但网页数据服务已停止。</div>}
      <RuntimeUpdateFailureBanner failure={payload?.system.runtime_update_failure} />
      <CurrentDataNotice phase={currentPhase} snapshotTime={payload?.generated_at ? localTime(payload.generated_at) : null} />

      <section className="metric-grid">
        <article><span>MARKET STATUS</span><strong>{marketClosed ? "休市" : online ? "行情在线" : "行情暂不可用"}</strong><small>{localTime(latest?.source_received_time)}</small></article>
        <article><span>CURRENT NEWS EVENTS</span><strong><MetricValue phase={currentPhase}><CountValue value={newsMetrics.events.currently_model_eligible} /></MetricValue></strong><a href="/audit?view=evidence">当前可用新闻事件 ↗</a></article>
        <article><span>NEWS &amp; STORIES</span><strong>新闻与脉络</strong><a href="/audit?view=stories">查看事件脉络 ↗</a></article>
        <article>
          <span>NEWS ARTICLES</span>
          <strong><MetricValue phase={currentPhase}><CountValue value={newsMetrics.articles.received} /></MetricValue></strong>
          <small className="metric-detail metric-detail-stack"><CountValue value={newsMetrics.events.independent} format="exact" suffix=" 个独立事件" /><CountValue value={newsMetrics.articles.stored_revisions} format="exact" suffix=" 个保存版本" /></small>
        </article>
      </section>

      <section className="workspace-grid">
        <article className="panel source-panel">
          <div className="panel-head"><div><span>SOURCE HEALTH</span><h2>数据链路</h2></div></div>
          <Source name="cTrader XAUUSD · 本机 Algo" state={quoteBridge.label} good={quoteBridge.good} />
          <Source name="Federal Reserve · 官方源" state="采集中" good />
          <Source name="BLS Public API · 官方源" state={payload?.sources.bls === "ONLINE" ? "采集中" : "准备中"} good={payload?.sources.bls === "ONLINE"} />
          <Source name="Gemini 3.5 Flash-Lite · API" state={payload?.sources.llm === "ENABLED" ? "标注中" : "等待标注"} good={payload?.sources.llm === "ENABLED"} />
        </article>
      </section>

      <footer>
        <span>FORWARD EPOCH {localTime(payload?.forward_epoch)}</span>
        <span>LAST SYNC {localTime(payload?.generated_at)}</span>
        <span>NEWS & MARKET OBSERVATION</span>
      </footer>
    </main>
  );
}

function Source({ name, state, good }: { name: string; state: string; good: boolean }) {
  return <div className="source"><span className={good ? "source-good" : "source-warn"} /><b>{name}</b><em>{state}</em></div>;
}
