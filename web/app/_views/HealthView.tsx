"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { CurrentDataNotice, type CurrentDataPhase } from "../_components/CurrentDataState";
import CountValue from "../_components/CountValue";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { operationalEvidenceText } from "../_lib/operational-evidence";
import { operationalEventDiagnostic, operationalIncidentActionLabels, operationalIncidentNextRetryAt, operationalIncidentsNextRetryAt, operationalScopeLabel, operationalSummaryDetails } from "../_lib/operational-incident-presentation";
import { affectedOperationalScopeCount, correlateOperationalEvents, type OperationalIncident } from "../_lib/operational-incidents";
import { normalizeOperationalEvent, schedulerTaskLabel, type AssistantOperationalHealth, type OperationalAlert, type OperationalHealth } from "../_lib/operational-health";
import { sourceHealthErrorPresentation } from "../_lib/source-health-presentation";
import { componentAggregate, operatorComponentScanState, primaryOperatorAction, sortAttentionFirst, sourceAggregate, sourceScanState, type ScanState } from "../_lib/health-scan-presentation";

export type StatusPayload = {
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
    freshness_reference_time: string | null; freshness_reference_status: "OK" | "PARTIAL" | null;
    age_seconds: number | null; last_error_time: string | null; last_error_type: string | null; last_error: string | null;
    poll_count: number; ok_count: number; item_count: number; revision_count: number; full_text_count: number;
    recovery_mode: string | null; fallback_label: string | null; fallback_health: string | null; next_retry_time: string | null;
    semantic_status: string; semantic_message: string | null;
  }>;
  operational_health?: OperationalHealth;
};

function localTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" }) : "—";
}

function compactElapsed(seconds: number | null): string {
  if (seconds === null) return "无等待";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

function compactClock(value: string | null): string {
  return value ? new Date(value).toLocaleTimeString("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kuala_Lumpur",
  }) : "—";
}

const componentRoles: Record<string, string> = {
  quote_bridge: "持续接收 cTrader XAUUSD Bid / Ask 报价",
  system_clock: "校验报价时间与本机接收时间",
  decision_collector: "每五分钟保存一次前向决策",
  outcome_settler: "结算决策后的 30 分钟结果",
  news_collector: "轮询并保存新闻来源证据",
  gemini_annotator: "生成新闻语义与影响标注",
  news_semantic_pipeline: "汇总当前新闻语义处理状态",
  sites_synchronizer: "同步只读网页展示镜像",
  sqlite_backup: "保留 append-only 证据备份",
  integrity_check: "检查本地证据数据库完整性",
  daily_news_brief: "生成每日新闻简报",
};

function componentRole(name: string): string {
  return componentRoles[name] ?? "运行与证据链路组件";
}

function componentProblem(status: string): string {
  if (status === "STALE") return "最近一次成功已超过组件的健康时限。";
  if (status === "ERROR") return "组件当前未能完成预期工作。";
  if (status === "UNAVAILABLE" || status === "UNKNOWN") return "当前无法取得可信的组件状态。";
  return "组件报告了需要关注的当前状态。";
}

function incidentForScope(incidents: OperationalIncident[], scope: string): OperationalIncident | null {
  return incidents.find(incident => (
    incident.root_event.scope === scope || incident.affected_scopes.includes(scope)
  )) ?? null;
}

export function IncidentCard({ incident }: { incident: OperationalIncident }) {
  const events = [
    incident.root_event, ...incident.related_events, ...incident.technical_events,
  ];
  const affectedScopes = [...new Set([incident.root_event.scope, ...incident.affected_scopes])];
  const nextRetryAt = operationalIncidentNextRetryAt(incident);
  const [showTechnical, setShowTechnical] = useState(false);
  const technicalId = useId();
  return <article className={`operational-incident-card is-${incident.severity.toLowerCase()}`}>
    <header>
      <div className="incident-primary">
        <div className="incident-kicker"><span>{incident.severity === "ERROR" ? "错误" : incident.severity === "WARNING" ? "警告" : "信息"}</span><b>{operationalScopeLabel(incident.root_event.scope)}</b></div>
        <h3>{incident.title_zh}</h3>
        <p>{incident.summary_zh}</p>
      </div>
      <div className={`incident-action is-${incident.action_state.toLowerCase()}`}>
        <small>当前处置</small>
        <strong>{operationalIncidentActionLabels[incident.action_state]}</strong>
        {nextRetryAt ? <time>下次尝试 {localTime(nextRetryAt)}</time> : null}
      </div>
    </header>
    {incident.summary_metrics.length ? <dl>{incident.summary_metrics.map(metric => <div key={metric.label}><dt>{metric.label}</dt><dd>{metric.value}</dd></div>)}</dl> : null}
    <p className="incident-affected"><b>受影响子系统</b><span>{affectedScopes.map(operationalScopeLabel).join(" · ")}</span><small>{affectedScopes.length} 个组件</small></p>
    <div className={`incident-technical-details${showTechnical ? " is-expanded" : ""}`}>
      <button type="button" aria-expanded={showTechnical} aria-controls={technicalId} onClick={() => setShowTechnical(value => !value)}>查看技术详情 · {incident.technical_event_count} 个事件</button>
      <div id={technicalId} hidden={!showTechnical}>
        <div className="incident-human-diagnostics">{events.map((event, index) => {
          const incidentReasons = event.code === "OPS_COMPONENT_UNHEALTHY"
            ? incident.reason_projections
              .filter(projection => projection.source_event_code === event.code
                && projection.source_scope === event.scope)
              .map(projection => projection.reason_code)
            : undefined;
          const diagnostic = operationalEventDiagnostic(event, incidentReasons);
          return <section key={`${event.code}-${event.scope}-${index}`}>
            <dl>
              <div><dt>当前状态</dt><dd>{diagnostic.status}</dd></div>
              <div><dt>组件</dt><dd>{diagnostic.component}</dd></div>
              {diagnostic.reasons.length ? <div><dt>原因</dt><dd>{diagnostic.reasons.map(reason => <span key={reason}>{reason}</span>)}</dd></div> : null}
            </dl>
          </section>;
        })}</div>
        <details className="incident-raw-evidence">
          <summary>查看原始字段</summary>
          {events.map((event, index) => <section key={`${event.code}-${event.scope}-${index}`}>
            <div><code>{event.code}</code><b>scope={event.scope}</b></div>
            <small>severity={event.severity} · blocking={String(event.blocking)}{Object.keys(event.evidence).length ? ` · ${operationalEvidenceText(event.evidence)}` : ""}</small>
          </section>)}
          {incident.reason_projections.map(projection => <section className="incident-reason-projection" key={`${projection.source_scope}-${projection.reason_code}`}>
            <div><code>{projection.reason_code}</code><b>scope={projection.source_scope}</b></div>
            <small>由结构化组件原因关联；原始组件事件仅在一个技术证据位置保留。</small>
          </section>)}
        </details>
      </div>
    </div>
  </article>;
}

type NewsSourceHealth = StatusPayload["news_source_health"][number];

function SourceHealthCard({ item }: { item: NewsSourceHealth }) {
  const state = sourceScanState(item.health);
  const errorPresentation = sourceHealthErrorPresentation(item, !state.attention);
  const problemHeading = item.health === "WARMING_UP" && !item.last_error
    ? state.label : errorPresentation.heading;
  return <article className={`${state.attention ? "is-attention" : "is-healthy"} state-${state.tone}`}>
    <header>
      <span className="health-state-mark" aria-label={state.label}>{state.symbol}</span>
      <div><strong>{item.label}</strong></div>
      <span className="health-row-meta"><span className="health-state-text">{state.label}</span><time>{compactClock(item.last_success ?? item.latest_poll_time)}</time></span>
    </header>
    {state.attention ? <div className="source-current-problem">
      <small>当前问题</small><strong>{problemHeading}</strong>
      {errorPresentation.recovery ? <b>{errorPresentation.recovery}</b> : null}
      {item.next_retry_time ? <time>下次尝试 {localTime(item.next_retry_time)}</time> : null}
      {errorPresentation.fallback ? <span>{errorPresentation.fallback}</span> : null}
    </div> : null}
    {state.attention && item.semantic_message ? <p className="source-semantic-message">{item.semantic_message}</p> : null}
    <details className="source-technical-details">
      <summary>{state.attention ? "技术详情" : "详情 ›"}</summary>
      <div className="source-evidence-counts"><strong><CountValue value={item.item_count} suffix=" 篇" /></strong><small><CountValue value={item.revision_count} format="exact" suffix=" revisions" /> · 完整正文 <CountValue value={item.full_text_count} format="exact" /></small><small>轮询 <CountValue value={item.ok_count} format="exact" />/<CountValue value={item.poll_count} format="exact" /> 完成</small></div>
      <dl>
        <div><dt>职责</dt><dd>{item.role}</dd></div>
        <div><dt>最近轮询</dt><dd>{localTime(item.latest_poll_time)}</dd></div>
        <div><dt>最近成功</dt><dd>{localTime(item.last_success)}</dd></div>
        <div><dt>来源标识</dt><dd><code>{item.source}</code></dd></div>
        <div><dt>原始状态</dt><dd><code>{item.health}</code></dd></div>
        <div><dt>错误类型</dt><dd><code>{item.last_error_type ?? "NONE"}</code></dd></div>
        <div><dt>最近错误</dt><dd>{item.last_error_time ? localTime(item.last_error_time) : "—"} · <code>{item.last_error ?? "无已记录错误"}</code></dd></div>
        {item.freshness_reference_status === "PARTIAL" ? <div><dt>新鲜度参考</dt><dd>{localTime(item.freshness_reference_time)} · 部分成功</dd></div> : null}
      </dl>
    </details>
  </article>;
}

type ComponentHealth = StatusPayload["system"]["components"][string];

function ComponentHealthCard({
  name, item, incident, state,
}: { name: string; item: ComponentHealth; incident: OperationalIncident | null; state: ScanState }) {
  const nextRetryAt = incident ? operationalIncidentNextRetryAt(incident) : null;
  return <article className={`${state.attention ? "is-attention" : "is-healthy"} state-${state.tone}`}>
    <header>
      <span className="health-state-mark" aria-label={state.label}>{state.symbol}</span>
      <h3>{operationalScopeLabel(name)}</h3>
      <span className="health-row-meta"><span className="health-state-text">{state.label}</span><time>{item.age_seconds === null ? "—" : `${compactElapsed(item.age_seconds)}前`}</time></span>
    </header>
    {state.attention ? <div className="component-current-problem">
      <strong>{incident ? operationalIncidentActionLabels[incident.action_state] : componentProblem(item.status)}</strong>
      {nextRetryAt ? <time>下次尝试 {localTime(nextRetryAt)}</time> : null}
    </div> : null}
    <details className="component-technical-details">
      <summary>{state.attention ? "技术详情" : "详情 ›"}</summary>
      <dl>
        <div><dt>职责</dt><dd>{componentRole(name)}</dd></div>
        <div><dt>最近成功</dt><dd>{localTime(item.last_success)}</dd></div>
        <div><dt>距上次成功</dt><dd>{compactElapsed(item.age_seconds)}</dd></div>
        <div><dt>组件标识</dt><dd><code>{name}</code></dd></div>
        <div><dt>原始状态</dt><dd><code>{item.status}</code></dd></div>
        <div><dt>最近错误</dt><dd><code>{item.last_error ?? "无已记录错误"}</code></dd></div>
        {incident ? <div><dt>关联问题</dt><dd>{incident.summary_zh}</dd></div> : null}
      </dl>
    </details>
  </article>;
}

export default function HealthView({ initialPayload }: { initialPayload?: StatusPayload }) {
  const cachedStatus = initialPayload ?? readDashboardResource<StatusPayload>("/api/status");
  const cachedAssistantHealth = readDashboardResource<AssistantOperationalHealth>("/api/assistant-health");
  const [payload, setPayload] = useState<StatusPayload | null>(() => cachedStatus);
  const [assistantHealth, setAssistantHealth] = useState<AssistantOperationalHealth | null>(() => cachedAssistantHealth);
  const [assistantHealthError, setAssistantHealthError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncingCurrent, setSyncingCurrent] = useState(Boolean(cachedStatus?.preview_status_summary));
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
  const assistantUnavailableEvent: OperationalAlert = normalizeOperationalEvent({
    code: "OPS_ASSISTANT_HEALTH_UNAVAILABLE", severity: "ERROR", scope: "ASSISTANT_D1",
    message_zh: "Assistant 云端运行状态无法读取。", blocking: true, evidence: {},
  });
  const operationalEvents = [
    ...(payload?.operational_health?.alerts ?? []),
    ...(assistantHealthError ? [assistantUnavailableEvent] : assistantHealth?.current ? assistantHealth.alerts : []),
  ];
  const incidents = correlateOperationalEvents(operationalEvents);
  const components = sortAttentionFirst(Object.entries(payload?.system.components ?? {}).map(([name, item]) => {
    const incident = incidentForScope(incidents, name);
    return { name, item, incident, state: operatorComponentScanState(item.status, incident) };
  }), component => component.state);
  const componentHasAttention = components.some(component => component.state.attention);
  const sources = sortAttentionFirst(payload?.news_source_health ?? [], source => sourceScanState(source.health));
  const sourceHasAttention = sources.some(source => sourceScanState(source.health).attention);
  const affectedScopeCount = affectedOperationalScopeCount(incidents);
  const incidentStatus = incidents.some(incident => incident.severity === "ERROR")
    ? "error" : incidents.length ? "warning" : "healthy";
  const operatorAction = primaryOperatorAction(incidents);
  const operatorRetryAt = operationalIncidentsNextRetryAt(incidents, operatorAction);
  const incidentStatusLabel = incidentStatus === "error" ? "运行异常" : incidentStatus === "warning" ? "运行警告" : "运行正常";
  const incidentStatusMark = incidentStatus === "error" ? "✕" : incidentStatus === "warning" ? "⚠" : "✓";
  const operatorSummaryDetails = operationalSummaryDetails(
    affectedScopeCount, operatorAction, operatorRetryAt ? localTime(operatorRetryAt) : null,
  );

  return <main className="status-main">
    <section className="status-hero">
      <div><p className="eyebrow">OPERATIONAL HEARTBEATS / SOURCE HEALTH</p><h1>系统健康状态</h1></div>
    </section>
    {error ? <div className="error-banner">状态读取失败：{error}</div> : null}
    <CurrentDataNotice phase={currentPhase} snapshotTime={payload?.generated_at ? localTime(payload.generated_at) : null} />
    <section id="operational-alerts" className={`operational-health-panel incident-summary-panel is-${incidentStatus}`} aria-label="运行问题与关联证据">
      <header><div><p className="eyebrow">CURRENT PROBLEMS</p><h2>当前问题</h2><p className="incident-operator-summary"><span aria-hidden="true">{incidentStatusMark}</span> {incidentStatusLabel} · {operatorAction ? operationalIncidentActionLabels[operatorAction] : "无需处理"}{operatorSummaryDetails.map(detail => ` · ${detail}`).join("")}</p></div></header>
      {incidents.length ? <div className="operational-incident-list">{incidents.map(incident => <IncidentCard incident={incident} key={incident.incident_key} />)}</div> : <p className="operational-all-clear">当前没有运行异常。</p>}
    </section>
    <section className={`component-status ${componentHasAttention ? "has-attention" : ""}`} aria-label="数据链路组件状态">
      <header><div><p className="eyebrow">SYSTEM COMPONENTS</p><h2>系统组件</h2></div><strong>{componentAggregate(components.map(component => component.state))}</strong></header>
      <div className="component-card-grid">{components.map(({ name, item, incident, state }) => <ComponentHealthCard name={name} item={item} incident={incident} state={state} key={name} />)}</div>
    </section>
    <section className={`source-health ${sourceHasAttention ? "has-attention" : ""}`} aria-label="新闻来源状态">
      <header><div><p className="eyebrow">NEWS SOURCES</p><h2>新闻来源</h2></div><strong>{sourceAggregate(sources.map(source => source.health))}</strong></header>
      <div className="source-health-grid">{sources.map((item) => <SourceHealthCard item={item} key={item.source} />)}</div>
    </section>
    <section className="health-technical-section" aria-label="调度器与技术状态">
      <details>
        <summary><span><small>SCHEDULER / TECHNICAL EVIDENCE</small><strong>调度器与技术状态</strong></span><b>展开技术证据</b></summary>
        <div className="technical-health-group">
          <section aria-labelledby="local-scheduler-title">
            <header><div><p className="eyebrow">LOCAL SCHEDULER</p><h3 id="local-scheduler-title">本机调度器</h3></div><p>任务数量、领取、退避、失败代码和下一次重试。</p></header>
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
          <section id="assistant-operational-alerts" aria-labelledby="assistant-queue-title">
            <header><div><p className="eyebrow">ASSISTANT D1</p><h3 id="assistant-queue-title">Assistant 云端队列</h3></div><p>与本机 SQLite 分离的队列证据。</p></header>
            <p className="technical-boundary-note">本机 SQLite 与 Assistant D1 保持独立执行面；这里只统一分类和展示。{assistantHealth?.current === false ? " PR Preview 不把生产 D1 告警伪装成分支实时状态。" : ""}</p>
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
        </div>
      </details>
    </section>
    <footer><span>每 {DASHBOARD_REFRESH_INTERVALS.status / 1000} 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? localTime(payload.generated_at) : "—"}</span></footer>
  </main>;
}
