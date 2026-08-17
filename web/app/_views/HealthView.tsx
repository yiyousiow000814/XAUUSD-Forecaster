"use client";

import { useCallback, useEffect, useState } from "react";
import { CurrentDataNotice, type CurrentDataPhase } from "../_components/CurrentDataState";
import CountValue from "../_components/CountValue";
import DashboardLink from "../_components/DashboardLink";
import MobileDashboardNav from "../_components/MobileDashboardNav";
import SystemStatePill from "../_components/SystemStatePill";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { operationalEvidenceText } from "../_lib/operational-evidence";
import { schedulerTaskLabel, type AssistantOperationalHealth, type OperationalHealth } from "../_lib/operational-health";

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
  operational_health?: OperationalHealth;
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

function compactElapsed(seconds: number | null): string {
  if (seconds === null) return "无等待";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

type NewsSourceHealth = StatusPayload["news_source_health"][number];

function SourceHealthCard({ item }: { item: NewsSourceHealth }) {
  const healthy = item.health === "HEALTHY";
  const [showDetails, setShowDetails] = useState(false);
  return <article className={`${healthy ? "is-healthy" : "is-attention"} ${showDetails ? "is-detail-open" : ""}`}>
    <div><strong>{item.label}</strong><small>{item.role} · {item.source}</small></div>
    <div><b className={`source-health-badge health-${item.health.toLowerCase()}`}>{item.health === "FALLBACK_ACTIVE" ? "后备源接管中" : item.health === "WARMING_UP" ? "等待首次正式发布" : item.health}</b><small>{localTime(item.latest_poll_time)}</small><small>最近成功 {localTime(item.last_success)}</small>{item.next_retry_time ? <small>自动重试 {localTime(item.next_retry_time)}</small> : null}{item.semantic_message ? <small>{item.semantic_message}</small> : null}</div>
    {healthy && <button className="source-detail-toggle" type="button" aria-expanded={showDetails} onClick={() => setShowDetails(value => !value)}>{showDetails ? "收起来源证据" : "查看来源证据"}</button>}
    <div className="news-source-details">
      <div><strong><CountValue value={item.item_count} suffix=" 篇" /></strong><small><CountValue value={item.revision_count} format="exact" suffix=" revisions" /> · 完整正文 <CountValue value={item.full_text_count} format="exact" /></small><small>轮询 <CountValue value={item.ok_count} format="exact" />/<CountValue value={item.poll_count} format="exact" /> 完成</small></div>
      <div className="source-health-error"><strong>{item.recovery_mode === "RATE_LIMIT_BACKOFF" ? `GDELT 限流 · ${item.fallback_label} 自动接管` : item.last_error_type ? `${healthy ? "历史异常 · 已恢复" : "当前异常"} · ${item.last_error_type}` : "无已记录异常"}</strong><small>{item.last_error_time ? localTime(item.last_error_time) : ""} {item.last_error ?? "链路轮询正常"}</small>{item.fallback_label ? <small>后备链路：{item.fallback_label} · {item.fallback_health}</small> : null}</div>
    </div>
  </article>;
}

export default function HealthView() {
  const cachedStatus = readDashboardResource<StatusPayload>("/api/status");
  const cachedAssistantHealth = readDashboardResource<AssistantOperationalHealth>("/api/assistant-health");
  const [payload, setPayload] = useState<StatusPayload | null>(() => cachedStatus);
  const [assistantHealth, setAssistantHealth] = useState<AssistantOperationalHealth | null>(() => cachedAssistantHealth);
  const [assistantHealthError, setAssistantHealthError] = useState(false);
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
  const refreshAssistantHealth = useCallback(async (force = false) => {
    try {
      setAssistantHealth(await loadDashboardResource<AssistantOperationalHealth>("/api/assistant-health", { force }));
      setAssistantHealthError(false);
    } catch {
      setAssistantHealthError(true);
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
  useEffect(() => scheduleDashboardRefresh(
    () => void refreshAssistantHealth(false),
    () => void refreshAssistantHealth(true),
    DASHBOARD_REFRESH_INTERVALS.status,
    "current",
    "assistant-health",
  ), [refreshAssistantHealth]);

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
        <DashboardLink className="audit-link" href="/assistant">Assistant</DashboardLink>
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
    <section id="operational-alerts" className={`operational-health-panel is-${(payload?.operational_health?.status ?? "HEALTHY").toLowerCase()}`} aria-label="运行异常与错误码">
      <header><div><p className="eyebrow">OPERATIONAL ERROR CODES</p><h2>运行异常与定位证据</h2></div><p>覆盖本机预测与新闻数据链路、新闻 AI 队列、来源、同步和 Daily Brief；Assistant 云端任务另有独立运行面。正常运行不等于正在产生进展。</p></header>
      {(payload?.operational_health?.alerts ?? []).length ? <div className="operational-alert-list">
        {payload?.operational_health?.alerts.map((alert, index) => <article key={`${alert.code}-${alert.scope}-${index}`} className={`is-${alert.severity.toLowerCase()}`}>
          <div><code>{alert.code}</code><b>{alert.scope}</b><span>{alert.severity === "ERROR" ? "需要处理" : "需要留意"}</span></div>
          <p>{alert.message_zh}</p>
          <small>{operationalEvidenceText(alert.evidence)}</small>
        </article>)}
      </div> : <p className="operational-all-clear">当前没有达到告警阈值的运行异常。</p>}
      <div className="scheduler-health-grid">
        {(payload?.operational_health?.scheduler.tasks ?? []).map(task => <article key={task.task_type}>
          <header><strong>{schedulerTaskLabel[task.task_type] ?? task.task_type}</strong><code>{task.task_type}</code></header>
          <dl>
            <div><dt>当前工作</dt><dd>{task.queued + task.leased + task.backing_off}</dd></div>
            <div><dt>可立即处理</dt><dd>{task.claimable}</dd></div>
            <div><dt>定时重试</dt><dd>{task.scheduled_retry}</dd></div>
            <div><dt>15分钟完成</dt><dd>{task.completed_15m}</dd></div>
            <div><dt>容量延后</dt><dd>{task.deferred_15m}</dd></div>
            <div><dt>服务商调度等待</dt><dd>{task.provider_dispatch_deferred_15m ?? 0}</dd></div>
            <div><dt>失败</dt><dd>{task.errors_15m}</dd></div>
            <div><dt>最旧可处理</dt><dd>{compactElapsed(task.oldest_age_seconds)}</dd></div>
            <div><dt>下次重试</dt><dd>{task.earliest_retry_at ? localTime(task.earliest_retry_at) : "—"}</dd></div>
            <div><dt>最高领取</dt><dd>{task.max_claim_count}{task.max_claim_job_ref ? ` · ${task.max_claim_job_ref}` : ""}</dd></div>
          </dl>
          {task.failure_codes_15m.length ? <p className="scheduler-failure-codes">{task.failure_codes_15m.map(item => <code key={item.code}>{item.code} × {item.count}</code>)}</p> : null}
          {(task.capacity_dimensions_15m ?? []).length ? <p className="scheduler-failure-codes">{task.capacity_dimensions_15m?.map(item => <code key={item.dimension}>LOCAL_{item.dimension}_LIMIT × {item.count}</code>)}</p> : null}
        </article>)}
      </div>
    </section>
    <section id="assistant-operational-alerts" className={`operational-health-panel is-${assistantHealthError ? "error" : (assistantHealth?.status ?? "healthy").toLowerCase()}`} aria-label="Assistant 云端运行异常与错误码">
      <header><div><p className="eyebrow">ASSISTANT D1 ERROR CODES</p><h2>Assistant 云端任务</h2></div><p>覆盖对话、新闻问答、标题、上下文压缩与历史记忆索引。这里直接读取 Cloudflare D1 队列，不拿本机新闻队列代替。</p></header>
      {assistantHealthError ? <div className="operational-alert-list"><article className="is-error"><div><code>OPS_ASSISTANT_HEALTH_UNAVAILABLE</code><b>ASSISTANT_D1</b><span>需要处理</span></div><p>Assistant 云端运行状态无法读取。</p></article></div>
        : assistantHealth === null ? <p className="operational-all-clear">正在读取 Assistant 云端任务状态…</p>
          : assistantHealth.current === false ? <p className="operational-all-clear">PR Preview 不把生产 D1 告警伪装成分支实时状态；合并后由生产页面显示。</p>
          : assistantHealth?.alerts.length ? <div className="operational-alert-list">{assistantHealth.alerts.map((alert, index) => <article key={`${alert.code}-${alert.scope}-${index}`} className={`is-${alert.severity.toLowerCase()}`}><div><code>{alert.code}</code><b>{alert.scope}</b><span>{alert.severity === "ERROR" ? "需要处理" : "需要留意"}</span></div><p>{alert.message_zh}</p><small>{operationalEvidenceText(alert.evidence)}</small></article>)}</div>
            : <p className="operational-all-clear">当前没有达到告警阈值的 Assistant 云端任务异常。</p>}
      <div className="scheduler-health-grid">
        {(assistantHealth?.queues ?? []).map(queue => <article key={queue.queue}>
          <header><strong>{queue.label}</strong><code>{queue.queue}</code></header>
          <dl>
            <div><dt>排队</dt><dd>{queue.queued}</dd></div>
            <div><dt>处理中</dt><dd>{queue.processing}</dd></div>
            <div><dt>可立即处理</dt><dd>{queue.claimable}</dd></div>
            <div><dt>定时重试</dt><dd>{queue.scheduled_retry}</dd></div>
            <div><dt>15分钟完成</dt><dd>{queue.completed_15m}</dd></div>
            <div><dt>终止失败</dt><dd>{queue.failed_15m}</dd></div>
            <div><dt>容量等待</dt><dd>{queue.capacity_deferred}</dd></div>
            <div><dt>最旧可处理</dt><dd>{compactElapsed(queue.oldest_age_seconds)}</dd></div>
            <div><dt>最高尝试</dt><dd>{queue.max_attempt_count}</dd></div>
          </dl>
          {queue.failure_codes.length ? <p className="scheduler-failure-codes">{queue.failure_codes.map(item => <code key={item.code}>{item.code} × {item.count}</code>)}</p> : null}
        </article>)}
      </div>
    </section>
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
      {sources.map((item) => <SourceHealthCard item={item} key={item.source} />)}
    </section>
    <footer><span>每 {DASHBOARD_REFRESH_INTERVALS.status / 1000} 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? localTime(payload.generated_at) : "—"}</span></footer>
  </main>;
}
