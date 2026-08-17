"use client";

import { useCallback, useEffect, useState } from "react";
import { CurrentDataNotice, MetricValue, type CurrentDataPhase } from "../_components/CurrentDataState";
import CountValue from "../_components/CountValue";
import DashboardLink from "../_components/DashboardLink";
import MobileDashboardNav from "../_components/MobileDashboardNav";
import RuntimeUpdateFailureBanner, { type RuntimeUpdateFailure } from "../_components/RuntimeUpdateFailureBanner";
import SystemStatePill from "../_components/SystemStatePill";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { statusFieldPhase } from "../_lib/current-data-provenance";

type QuotaKey = {
  slot: number;
  fingerprint: string;
  sent: number;
  remaining: number;
  status: "AVAILABLE" | "DAILY_LIMIT";
};

type QuotaState = {
  quota_day_pacific: string;
  daily_limit_per_key: number;
  next_reset_at: string;
  keys: QuotaKey[];
  total_sent: number;
  total_remaining: number;
};

type StatusPayload = {
  preview_status_summary?: boolean;
  preview?: {
    branch_snapshot?: { generated_at: string | null; status_paths: string[] };
  };
  generated_at: string;
  system: {
    online: boolean; mode: string; trading_enabled: boolean; market_session?: "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";
    source_of_truth: string; sites_mirror: string;
    runtime_update_failure?: RuntimeUpdateFailure | null;
    components: Record<string, { last_success: string | null; age_seconds: number | null; status: string; last_error: string | null }>;
  };
  annotation_queue: {
    configured_key_count: number;
    configured_account_count?: number;
    available_key_count: number;
    fallback_available_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute_per_account?: number;
    requests_per_minute: number;
    input_tokens_per_minute: number;
    minute_scope: "ACCOUNT";
    backing_off: number;
    dead_letter: number;
    priority_reserve: number;
    routine_remaining: number;
  };
  gemini_quota: QuotaState;
  gemini_31_quota: QuotaState;
  gemma_quota: QuotaState;
  gemini_embedding_quota: QuotaState;
  llm_routing: {
    action_bearing: { model: string; fallback_model: string; role: string };
    display_only: {
      model: string; role: string; configured_account_count: number;
      requests_per_minute_per_account: number; requests_per_minute: number;
      input_tokens_per_minute_per_account: number; input_tokens_per_minute: number;
      provider_lanes_per_account: number; maximum_concurrent_requests: number;
      minute_scope: "ACCOUNT";
    };
    antigravity: { enabled: boolean; reason: string };
  };
  news_source_health: Array<{
    source: string; label: string; role: string; health: "HEALTHY" | "DEGRADED" | "ERROR" | "STALE";
    latest_status: string; latest_poll_time: string | null; last_success: string | null;
    freshness_reference_time: string | null; freshness_reference_status: "OK" | "PARTIAL" | null;
    age_seconds: number | null; last_error_time: string | null; last_error_type: string | null; last_error: string | null;
    poll_count: number; ok_count: number; partial_count: number; error_count: number;
    item_count: number; revision_count: number; full_text_count: number; latest_item_time: string | null;
  }>;
};

function localTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" });
}

function formatCountdown(target: string | undefined, nowMs: number): string {
  if (!target || !nowMs) return "—";
  const remaining = Math.max(0, new Date(target).getTime() - nowMs);
  const totalSeconds = Math.floor(remaining / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return remaining === 0 ? "正在重置" : `还有 ${hours} 小时 ${minutes} 分 ${seconds} 秒`;
}

function QuotaPanel({ title, eyebrow, quota, nowMs }: { title: string; eyebrow: string; quota?: QuotaState; nowMs: number }) {
  const resetAt = quota?.next_reset_at
    ? new Date(quota.next_reset_at).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" })
    : "—";
  return <section className="quota-panel">
    <div className="quota-panel-head">
      <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div>
      <div><b>Pacific 配额日 {quota?.quota_day_pacific ?? "—"}</b><span>下次自动重置：{resetAt}</span><strong className="reset-countdown">{formatCountdown(quota?.next_reset_at, nowMs)}</strong></div>
    </div>
    <div className="quota-table-head"><span>匿名 Key</span><span>今日本机已准入</span><span>剩余</span><span>状态</span></div>
    {(quota?.keys ?? []).map((key) => {
      const limit = quota?.daily_limit_per_key ?? 1;
      const used = Math.min(100, (key.sent / limit) * 100);
      return <article className="quota-row" key={key.fingerprint}>
        <div><b>KEY {key.slot}</b><small>…{key.fingerprint.slice(-6)}</small></div>
        <div className="quota-value"><small>本机已准入 / 上限</small><strong><CountValue value={key.sent} format="exact" /> / <CountValue value={limit} format="exact" /></strong></div>
        <div className="quota-value"><small>剩余</small><strong><CountValue value={key.remaining} format="exact" /></strong></div>
        <span className={key.status === "AVAILABLE" ? "quota-ok" : "quota-stop"}>{key.status === "AVAILABLE" ? "可用" : "今日已停用"}</span>
        <div className="quota-progress"><i style={{ width: `${used}%` }} /></div>
      </article>;
    })}
  </section>;
}

export default function StatusView() {
  const cachedStatus = readDashboardResource<StatusPayload>("/api/status");
  const [payload, setPayload] = useState<StatusPayload | null>(() => cachedStatus);
  const [error, setError] = useState<string | null>(null);
  const [syncingCurrent, setSyncingCurrent] = useState(Boolean(cachedStatus?.preview_status_summary));
  const [nowMs, setNowMs] = useState(0);

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

  useEffect(() => {
    const initial = window.setTimeout(() => setNowMs(Date.now()), 0);
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, []);

  const quota = payload?.gemini_quota;
  const fallbackQuota = payload?.gemini_31_quota;
  const gemmaQuota = payload?.gemma_quota;
  const embeddingQuota = payload?.gemini_embedding_quota;
  const currentPhase: CurrentDataPhase = error
    ? "error" : !payload || syncingCurrent ? "loading" : payload.preview_status_summary ? "snapshot" : "ready";
  const throughputPhase = statusFieldPhase(
    currentPhase,
    payload?.preview?.branch_snapshot?.status_paths,
    "annotation_queue.requests_per_minute",
  );
  const gemmaThroughputPhase = statusFieldPhase(
    currentPhase,
    payload?.preview?.branch_snapshot?.status_paths,
    "llm_routing.display_only.requests_per_minute",
  );
  return (
    <main className="status-main">
      <div className="grain" />
      <header className="topbar">
        <DashboardLink className="brand audit-brand brand-button" href="/" replace>
          <span className="brand-mark">AU</span>
          <div><strong>Aurum System Status</strong><small>本机进程 · 多模型配额</small></div>
        </DashboardLink>
        <div className="top-actions">
          <DashboardLink className="audit-link" href="/assistant">Assistant</DashboardLink>
          <DashboardLink className="audit-link" href="/health">组件与新闻源</DashboardLink>
          <DashboardLink className="audit-link" href="/audit?view=news">新闻与决策</DashboardLink>
          <DashboardLink className="audit-link" href="/" replace>← 返回实时室</DashboardLink>
        </div>
        <MobileDashboardNav current="status" />
      </header>

      <section className="status-hero">
        <div><p className="eyebrow">LOCAL QUOTA LEDGER / PACIFIC DAY</p><h1>AI 模型使用状态</h1></div>
        <SystemStatePill loading={payload === null && !error} error={Boolean(error)} online={Boolean(payload?.system.online)} marketSession={payload?.system.market_session} />
      </section>

      {error ? <div className="error-banner">状态读取失败：{error}</div> : null}
      <RuntimeUpdateFailureBanner failure={payload?.system.runtime_update_failure} />
      <CurrentDataNotice phase={currentPhase} snapshotTime={payload?.generated_at ? localTime(payload.generated_at) : null} />

      <section className="quota-overview" aria-labelledby="quota-overview-title">
        <header className="quota-overview-head">
          <div><p className="eyebrow">CAPACITY / ALLOCATION / QUEUE</p><h2 id="quota-overview-title">今日额度总览</h2></div>
          <p>先看还能处理多少，再看额度留给谁；异常请求单独列出。</p>
        </header>
        <div className="quota-overview-layout">
          <section className="quota-group quota-group-capacity" aria-labelledby="quota-capacity-title">
            <header><span>01</span><div><h3 id="quota-capacity-title">账户与每日额度</h3><p>{payload?.llm_routing.action_bearing.model ?? "Gemini 3.5 Flash-Lite"} → {payload?.llm_routing.action_bearing.fallback_model ?? "Gemini 3.1 Flash-Lite"} · {payload?.llm_routing.action_bearing.role ?? "普通额度用尽后接管"}</p></div></header>
            <div className="quota-metric-grid quota-capacity-grid">
              <article><span>已配置 KEY</span><strong><MetricValue phase={currentPhase}><CountValue value={payload?.annotation_queue.configured_key_count} /></MetricValue></strong><small>当前可用 <CountValue value={payload?.annotation_queue.available_key_count} format="exact" /> · 匿名编号</small></article>
              <article><span>Flash 本机已准入</span><strong><MetricValue phase={currentPhase}><CountValue value={quota?.total_sent} /></MetricValue></strong><small>保守准入账本，不等同官方成功量</small></article>
              <article><span>Flash 剩余</span><strong className="good"><MetricValue phase={currentPhase}><CountValue value={quota?.total_remaining} /></MetricValue></strong><small>主模型本机账本</small></article>
              <article><span>3.1 剩余</span><strong className="good"><MetricValue phase={currentPhase}><CountValue value={fallbackQuota?.total_remaining} /></MetricValue></strong><small>Flash 普通额度用尽后接管</small></article>
              <article><span>Embedding 剩余</span><strong className="good"><MetricValue phase={currentPhase}><CountValue value={embeddingQuota?.total_remaining} /></MetricValue></strong><small>本机准入账本 · 每账户 1K RPD</small></article>
            </div>
          </section>

          <section className="quota-group" aria-labelledby="quota-allocation-title">
            <header><span>02</span><div><h3 id="quota-allocation-title">新闻额度分配</h3><p>{payload?.llm_routing.display_only.model ?? "Gemma 4 31B"} · {payload?.llm_routing.display_only.role ?? "事件整理与中文展示"}</p></div></header>
            <div className="quota-metric-grid">
              <article><span>普通新闻可用</span><strong><MetricValue phase={currentPhase}><CountValue value={payload?.annotation_queue.routine_remaining} /></MetricValue></strong><small>不动用重要事件保留额</small></article>
              <article className="quota-metric-priority"><span>重要新闻保留</span><strong><MetricValue phase={currentPhase}><CountValue value={payload?.annotation_queue.priority_reserve} /></MetricValue></strong><small>FOMC · CPI · Payroll 专用</small></article>
            </div>
          </section>

          <section className="quota-group" aria-labelledby="quota-queue-title">
            <header><span>03</span><div><h3 id="quota-queue-title">请求异常</h3><p>只有需要处理的队列状态</p></div></header>
            <div className="quota-metric-grid">
              <article className={payload?.annotation_queue.backing_off ? "quota-metric-attention" : ""}><span>错误退避中</span><strong><MetricValue phase={currentPhase}><CountValue value={payload?.annotation_queue.backing_off} /></MetricValue></strong><small>到期前不会重复请求</small></article>
              <article className={payload?.annotation_queue.dead_letter ? "quota-metric-danger" : ""}><span>已隔离</span><strong><MetricValue phase={currentPhase}><CountValue value={payload?.annotation_queue.dead_letter} /></MetricValue></strong><small>永久错误不再消耗配额</small></article>
            </div>
          </section>
        </div>
      </section>

      <section className="throughput-section" aria-labelledby="throughput-title">
        <header><div><p className="eyebrow">RATE GUARDRAILS</p><h2 id="throughput-title">安全吞吐上限</h2></div><p>分支配置的请求入场限制，不是今日剩余额度。</p></header>
        <div className="throughput-summary">
          <article><span>Flash</span><strong><MetricValue phase={throughputPhase} snapshotLabel="分支配置" snapshotTitle="此吞吐限制来自当前 PR 分支的构建配置，不是生产实时观测"><CountValue value={payload?.annotation_queue.requests_per_minute} /></MetricValue></strong><small>总 RPM · 每账户 <CountValue value={payload?.annotation_queue.requests_per_minute_per_account} format="exact" /> · 总 TPM <CountValue value={payload?.annotation_queue.input_tokens_per_minute} /></small></article>
          <article><span>Gemma</span><strong><MetricValue phase={gemmaThroughputPhase} snapshotLabel="分支配置" snapshotTitle="每个独立账户分别执行 RPM 与 TPM 入场检查"><CountValue value={payload?.llm_routing.display_only.requests_per_minute} /></MetricValue></strong><small>总 RPM · 总 TPM <CountValue value={payload?.llm_routing.display_only.input_tokens_per_minute} /> · 并发 <CountValue value={payload?.llm_routing.display_only.maximum_concurrent_requests} format="exact" /> · <CountValue value={payload?.llm_routing.display_only.configured_account_count} format="exact" /> 个账户</small></article>
          <article><span>Embedding</span><strong>100</strong><small>每账户 RPM · 30K TPM · 1K RPD · batch 按条计数</small></article>
        </div>
      </section>

      <QuotaPanel title="Gemini 3.5 Flash-Lite · 逐 Key 配额" eyebrow="ACTION-BEARING / FULL CONTENT" quota={quota} nowMs={nowMs} />
      <QuotaPanel title="Gemini 3.1 Flash-Lite · 逐 Key 配额" eyebrow="ACTION-BEARING FALLBACK / FULL CONTENT" quota={fallbackQuota} nowMs={nowMs} />
      <QuotaPanel title="Gemma 4 31B · 逐 Key 配额" eyebrow="DISPLAY-ONLY / TITLE TRANSLATION" quota={gemmaQuota} nowMs={nowMs} />
      <QuotaPanel title="Gemini Embedding 2 · 逐账户配额" eyebrow="NEWS IDENTITY / SEMANTIC RETRIEVAL" quota={embeddingQuota} nowMs={nowMs} />

      <details className="quota-note">
        <summary><b>计数规则</b><span>查看账本与 Google 额度的区别</span></summary>
        <p>每次请求在发往模型前永久计入各自账本，包括被 Google 拒绝的请求。3.5 每 key 本机上限 500，并保留一部分给 FOMC、CPI 与 Payroll；普通额度用尽后才由 3.1 接管。数字格式和中文显示问题会在本地恢复，同一分钟的 RPM 槽位用完只会延后到下一批，不算失败。只有 Google 服务或响应故障才进入持久退避。Gemma 每 key 本机上限 15,000。Embedding 每个独立账户为 100 RPM、30K TPM、1K RPD，batch 内每条内容分别计一次请求。各账本都在 Pacific midnight 自动切换。</p>
        <p>当前配置把每个账户视为独立额度域；Flash 与 Gemma 的总 RPM/TPM 都按独立账户数汇总。每次请求仍须先通过对应账户的 RPM 与 TPM 原子检查，因此增加并发不会绕过额度。Antigravity {payload?.llm_routing.antigravity.enabled ? "已启用" : "未启用"}：{payload?.llm_routing.antigravity.reason ?? "每日额度不适合批量新闻"}。</p>
      </details>

      <footer><span>每 {DASHBOARD_REFRESH_INTERVALS.status / 1000} 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? new Date(payload.generated_at).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" }) : "—"}</span></footer>
    </main>
  );
}
