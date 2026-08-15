"use client";

import { useCallback, useEffect, useState } from "react";
import { CurrentDataNotice, type CurrentDataPhase } from "../_components/CurrentDataState";
import CountValue from "../_components/CountValue";
import DashboardLink from "../_components/DashboardLink";
import MobileDashboardNav from "../_components/MobileDashboardNav";
import SystemStatePill from "../_components/SystemStatePill";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";

type StatusPayload = {
  preview_status_summary?: boolean;
  generated_at: string;
  system: {
    online: boolean;
    market_session?: "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";
    source_of_truth: string;
    sites_mirror: string;
    components: Record<string, { last_success: string | null; age_seconds: number | null; status: string; last_error: string | null }>;
  };
  news_source_health: Array<{
    source: string; label: string; role: string; health: "HEALTHY" | "DEGRADED" | "ERROR" | "STALE" | "FALLBACK_ACTIVE" | "WARMING_UP";
    latest_poll_time: string | null; last_success: string | null;
    age_seconds: number | null; last_error_time: string | null; last_error_type: string | null; last_error: string | null;
    poll_count: number; ok_count: number; item_count: number; revision_count: number; full_text_count: number;
    recovery_mode: string | null; fallback_label: string | null; fallback_health: string | null; next_retry_time: string | null;
    semantic_status: string; semantic_message: string | null;
  }>;
};

const componentLabels: Record<string, string> = {
  quote_bridge: "XAUUSD 报价桥",
  system_clock: "cTrader 报价时间 / 本机接收时间",
  decision_collector: "5 分钟决策收集器",
  outcome_settler: "30 分钟结果结算器",
  news_collector: "新闻收集器",
  gemini_annotator: "Gemini 新闻分析器",
  news_semantic_pipeline: "新闻语义决策门槛",
  sites_synchronizer: "网页同步器",
  sqlite_backup: "SQLite 备份",
  integrity_check: "数据库完整性检查",
};

function localTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" }) : "—";
}

function elapsed(seconds: number | null): string {
  if (seconds === null) return "尚无记录";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `距上次成功 ${whole} 秒`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `距上次成功 ${minutes} 分 ${whole % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  return `距上次成功 ${hours} 小时 ${minutes % 60} 分`;
}

export default function HealthView() {
  const cachedStatus = readDashboardResource<StatusPayload>("/api/status");
  const [payload, setPayload] = useState<StatusPayload | null>(() => cachedStatus);
  const [error, setError] = useState<string | null>(null);
  const [syncingCurrent, setSyncingCurrent] = useState(Boolean(cachedStatus?.preview_status_summary));
  const [showHealthyComponents, setShowHealthyComponents] = useState(false);
  const [showHealthySources, setShowHealthySources] = useState(false);
  const refresh = useCallback(async (force = false, showSyncState = false) => {
    if (showSyncState) setSyncingCurrent(true);
    try {
      setPayload(await loadDashboardResource<StatusPayload>("/api/status", { force }));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "状态读取失败");
    } finally {
      if (showSyncState) setSyncingCurrent(false);
    }
  }, []);

  useEffect(() => {
    return scheduleDashboardRefresh(
      () => void refresh(Boolean(payload?.preview_status_summary), Boolean(payload?.preview_status_summary)),
      () => void refresh(true, Boolean(payload?.preview_status_summary)),
      DASHBOARD_REFRESH_INTERVALS.status,
      "current",
      "status",
    );
  }, [refresh, payload?.preview_status_summary]);

  const currentPhase: CurrentDataPhase = error
    ? "error" : !payload || syncingCurrent ? "loading" : payload.preview_status_summary ? "snapshot" : "ready";
  const components = Object.entries(payload?.system.components ?? {}).map(([name, item]) => ({
    name,
    item,
    healthy: item.status === "OK" || item.status === "MARKET_CLOSED",
  }));
  const healthyComponentCount = components.filter(component => component.healthy).length;
  const componentHasAttention = components.some(component => !component.healthy);
  const sources = payload?.news_source_health ?? [];
  const healthySourceCount = sources.filter(source => source.health === "HEALTHY").length;
  const sourceHasAttention = sources.some(source => source.health !== "HEALTHY");

  return <main className="status-main">
    <div className="grain" />
    <header className="topbar">
      <DashboardLink className="brand audit-brand brand-button" href="/" replace>
        <span className="brand-mark">AU</span><div><strong>Aurum System Health</strong><small>组件心跳 · 新闻来源</small></div>
      </DashboardLink>
      <div className="top-actions">
        <DashboardLink className="audit-link" href="/status">AI 模型用量</DashboardLink>
        <DashboardLink className="audit-link" href="/audit?view=news">新闻与决策</DashboardLink>
        <DashboardLink className="audit-link" href="/" replace>← 返回实时室</DashboardLink>
      </div>
      <MobileDashboardNav current="health" />
    </header>
    <section className="status-hero">
      <div><p className="eyebrow">OPERATIONAL HEARTBEATS / SOURCE HEALTH</p><h1>系统健康状态</h1></div>
      <SystemStatePill loading={payload === null && !error} error={Boolean(error)} online={Boolean(payload?.system.online)} marketSession={payload?.system.market_session} />
    </section>
    {error ? <div className="error-banner">状态读取失败：{error}</div> : null}
    <CurrentDataNotice phase={currentPhase} snapshotTime={payload?.generated_at ? localTime(payload.generated_at) : null} />
    <section className={`component-status ${componentHasAttention ? "has-attention" : ""} ${showHealthyComponents ? "show-healthy" : ""}`} aria-label="数据链路组件状态">
      <header><div><p className="eyebrow">EVIDENCE PIPELINE</p><h2>系统组件状态</h2></div><p><b>{payload?.system.source_of_truth ?? "Local append-only SQLite"}</b> 是不可修改的证据源；{payload?.system.sites_mirror ?? "Sites D1 read-only materialized display mirror"} 只是展示镜像。</p></header>
      {componentHasAttention && healthyComponentCount > 0 && <button className="health-reveal-button" type="button" aria-expanded={showHealthyComponents} onClick={() => setShowHealthyComponents(value => !value)}>{showHealthyComponents ? "只看异常组件" : `另有 ${healthyComponentCount} 个正常组件`}</button>}
      <div>{components.map(({ name, item, healthy }) => <article className={healthy ? "is-healthy" : "is-attention"} key={name}>
        <span className={item.status === "OK" || item.status === "MARKET_CLOSED" ? "component-ok" : "component-stale"}>{item.status === "MARKET_CLOSED" ? "市场休市" : item.status}</span>
        <strong>{componentLabels[name] ?? name.replaceAll("_", " ")}</strong>
        <small>最后成功 {localTime(item.last_success)}</small><small>{elapsed(item.age_seconds)}</small>
        {item.last_error ? <em>{item.last_error}</em> : null}
      </article>)}</div>
    </section>
    <section className={`source-health ${sourceHasAttention ? "has-attention" : ""} ${showHealthySources ? "show-healthy" : ""}`} aria-label="新闻来源状态">
      <header><div><p className="eyebrow">NEWS INGEST / SOURCE-BY-SOURCE</p><h2>新闻来源状态</h2></div><p>发布源和正文解析器分别判断。<b>正文链路降级不会伪装成全部新闻中断</b>；错误会保留最近一次成功时间和具体原因。</p></header>
      {sourceHasAttention && healthySourceCount > 0 && <button className="health-reveal-button" type="button" aria-expanded={showHealthySources} onClick={() => setShowHealthySources(value => !value)}>{showHealthySources ? "只看异常来源" : `另有 ${healthySourceCount} 个正常来源`}</button>}
      <div className="source-health-head"><span>来源 / 角色</span><span>状态 / 最近轮询</span><span>证据</span><span>最近错误</span></div>
      {sources.map((item) => <article className={item.health === "HEALTHY" ? "is-healthy" : "is-attention"} key={item.source}>
        <div><strong>{item.label}</strong><small>{item.role} · {item.source}</small></div>
        <div><b className={`source-health-badge health-${item.health.toLowerCase()}`}>{item.health === "FALLBACK_ACTIVE" ? "后备源接管中" : item.health === "WARMING_UP" ? "等待首次正式发布" : item.health}</b><small>{localTime(item.latest_poll_time)}</small><small>最近成功 {localTime(item.last_success)}</small>{item.next_retry_time ? <small>自动重试 {localTime(item.next_retry_time)}</small> : null}{item.semantic_message ? <small>{item.semantic_message}</small> : null}</div>
        <div><strong><CountValue value={item.item_count} suffix=" 篇" /></strong><small><CountValue value={item.revision_count} format="exact" suffix=" revisions" /> · 完整正文 <CountValue value={item.full_text_count} format="exact" /></small><small>轮询 <CountValue value={item.ok_count} format="exact" />/<CountValue value={item.poll_count} format="exact" /> 完成</small></div>
        <div className="source-health-error"><strong>{item.recovery_mode === "RATE_LIMIT_BACKOFF" ? `GDELT 限流 · ${item.fallback_label} 自动接管` : item.last_error_type ? `${item.health === "HEALTHY" ? "历史异常 · 已恢复" : "当前异常"} · ${item.last_error_type}` : "无已记录异常"}</strong><small>{item.last_error_time ? localTime(item.last_error_time) : ""} {item.last_error ?? "链路轮询正常"}</small>{item.fallback_label ? <small>后备链路：{item.fallback_label} · {item.fallback_health}</small> : null}</div>
      </article>)}
    </section>
    <footer><span>每 {DASHBOARD_REFRESH_INTERVALS.status / 1000} 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? localTime(payload.generated_at) : "—"}</span></footer>
  </main>;
}
