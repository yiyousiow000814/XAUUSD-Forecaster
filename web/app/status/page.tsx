"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

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
  generated_at: string;
  system: {
    online: boolean; mode: string; trading_enabled: boolean;
    source_of_truth: string; sites_mirror: string;
    components: Record<string, { last_success: string | null; age_seconds: number | null; status: string; last_error: string | null }>;
  };
  annotation_queue: {
    configured_key_count: number;
    available_key_count: number;
    fallback_available_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute: number;
    backing_off: number;
    dead_letter: number;
    priority_reserve: number;
    routine_remaining: number;
  };
  gemini_quota: QuotaState;
  gemini_31_quota: QuotaState;
  gemma_quota: QuotaState;
  llm_routing: {
    action_bearing: { model: string; fallback_model: string; role: string };
    display_only: { model: string; role: string; requests_per_minute: number };
    antigravity: { enabled: boolean; reason: string };
  };
  news_source_health: Array<{
    source: string; label: string; role: string; health: "HEALTHY" | "DEGRADED" | "ERROR" | "STALE";
    latest_status: string; latest_poll_time: string | null; last_success: string | null;
    age_seconds: number | null; last_error_time: string | null; last_error_type: string | null; last_error: string | null;
    poll_count: number; ok_count: number; partial_count: number; error_count: number;
    item_count: number; revision_count: number; full_text_count: number; latest_item_time: string | null;
  }>;
};

function localTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function elapsed(seconds: number | null): string {
  if (seconds === null) return "尚无记录";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `距今 ${whole} 秒`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `距今 ${minutes} 分 ${whole % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  return `距今 ${hours} 小时 ${minutes % 60} 分`;
}

const componentLabels: Record<string, string> = {
  quote_bridge: "XAUUSD 报价桥",
  decision_collector: "5 分钟决策收集器",
  outcome_settler: "30 分钟结果结算器",
  news_collector: "新闻收集器",
  gemini_annotator: "Gemini 新闻分析器",
  sites_synchronizer: "网页同步器",
  sqlite_backup: "SQLite 备份",
  integrity_check: "数据库完整性检查",
};

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
    ? new Date(quota.next_reset_at).toLocaleString("zh-CN", { hour12: false })
    : "—";
  return <section className="quota-panel">
    <div className="quota-panel-head">
      <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div>
      <div><b>Pacific 配额日 {quota?.quota_day_pacific ?? "—"}</b><span>下次自动重置：{resetAt}</span><strong className="reset-countdown">{formatCountdown(quota?.next_reset_at, nowMs)}</strong></div>
    </div>
    <div className="quota-table-head"><span>匿名 Key</span><span>今日已发送</span><span>剩余</span><span>状态</span></div>
    {(quota?.keys ?? []).map((key) => {
      const limit = quota?.daily_limit_per_key ?? 1;
      const used = Math.min(100, (key.sent / limit) * 100);
      return <article className="quota-row" key={key.fingerprint}>
        <div><b>KEY {key.slot}</b><small>…{key.fingerprint.slice(-6)}</small></div>
        <strong>{key.sent} / {limit}</strong>
        <strong>{key.remaining}</strong>
        <span className={key.status === "AVAILABLE" ? "quota-ok" : "quota-stop"}>{key.status === "AVAILABLE" ? "可用" : "今日已停用"}</span>
        <div className="quota-progress"><i style={{ width: `${used}%` }} /></div>
      </article>;
    })}
  </section>;
}

export default function StatusPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<StatusPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setPayload(await response.json());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "状态读取失败");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

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

  return (
    <main className="status-main">
      <div className="grain" />
      <header className="topbar">
        <button className="brand audit-brand brand-button" type="button" onClick={() => router.replace("/")}>
          <span className="brand-mark">AU</span>
          <div><strong>Aurum System Status</strong><small>本机进程 · 多模型配额</small></div>
        </button>
        <div className="top-actions">
          <button className="audit-link" type="button" onClick={() => router.push("/audit")}>新闻与决策</button>
          <button className="audit-link" type="button" onClick={() => router.replace("/")}>← 返回实时室</button>
        </div>
      </header>

      <section className="status-hero">
        <div><p className="eyebrow">LOCAL QUOTA LEDGER / PACIFIC DAY</p><h1>AI 模型使用状态</h1></div>
        <div className={`live-pill ${payload === null && !error ? "is-pending" : payload?.system.online && !error ? "is-live" : "is-down"}`}>
          <span />{payload === null && !error ? "正在读取状态" : payload?.system.online && !error ? "系统在线" : "状态离线"}
        </div>
      </section>

      {error ? <div className="error-banner">状态读取失败：{error}</div> : null}

      <section className="component-status" aria-label="数据链路组件状态">
        <header><div><p className="eyebrow">EVIDENCE PIPELINE</p><h2>系统组件状态</h2></div><p><b>{payload?.system.source_of_truth ?? "Local append-only SQLite"}</b> 是不可修改的证据源；{payload?.system.sites_mirror ?? "Sites D1 read-only materialized display mirror"} 只是展示镜像。</p></header>
        <div>{Object.entries(payload?.system.components ?? {}).map(([name, item]) => <article key={name}>
          <span className={item.status === "OK" ? "component-ok" : "component-stale"}>{item.status}</span>
          <strong>{componentLabels[name] ?? name.replaceAll("_", " ")}</strong>
          <small>最后成功 {item.last_success ? new Date(item.last_success).toLocaleString("zh-CN", { hour12: false }) : "—"}</small>
          <small>{elapsed(item.age_seconds)}</small>
          {item.last_error ? <em>{item.last_error}</em> : null}
        </article>)}</div>
      </section>

      <section className="source-health" aria-label="新闻来源状态">
        <header>
          <div><p className="eyebrow">NEWS INGEST / SOURCE-BY-SOURCE</p><h2>新闻来源状态</h2></div>
          <p>发布源和正文解析器分别判断。<b>正文链路降级不会伪装成全部新闻中断</b>；错误会保留最近一次成功时间和具体原因。</p>
        </header>
        <div className="source-health-head"><span>来源 / 角色</span><span>状态 / 最近轮询</span><span>证据</span><span>最近错误</span></div>
        {(payload?.news_source_health ?? []).map((item) => <article key={item.source}>
          <div><strong>{item.label}</strong><small>{item.role} · {item.source}</small></div>
          <div><b className={`source-health-badge health-${item.health.toLowerCase()}`}>{item.health}</b><small>{localTime(item.latest_poll_time)}</small><small>最近成功 {localTime(item.last_success)}</small></div>
          <div><strong>{item.item_count || "—"} 篇</strong><small>{item.revision_count || "—"} revisions · 完整正文 {item.full_text_count || "—"}</small><small>轮询 {item.ok_count}/{item.poll_count} 成功</small></div>
          <div className="source-health-error"><strong>{item.last_error_type ? `${item.health === "HEALTHY" ? "历史异常 · 已恢复" : "当前异常"} · ${item.last_error_type}` : "无已记录异常"}</strong><small>{item.last_error_time ? localTime(item.last_error_time) : ""} {item.last_error ?? "链路轮询正常"}</small></div>
        </article>)}
      </section>

      <section className="quota-summary">
        <article><span>已配置 KEY</span><strong>{payload?.annotation_queue.configured_key_count ?? "—"}</strong><small>当前可用 {payload?.annotation_queue.available_key_count ?? "—"} · 只显示匿名编号</small></article>
        <article><span>Flash 今日已发送</span><strong>{quota?.total_sent ?? "—"}</strong><small>重要正文与训练特征</small></article>
        <article><span>Flash 今日剩余</span><strong className="good">{quota?.total_remaining ?? "—"}</strong><small>本机账本上限</small></article>
        <article><span>3.1 今日剩余</span><strong className="good">{fallbackQuota?.total_remaining ?? "—"}</strong><small>3.5 普通额度用尽后接管</small></article>
        <article><span>普通新闻可用</span><strong>{payload?.annotation_queue.routine_remaining ?? "—"}</strong><small>不会动用重要新闻保留额</small></article>
        <article><span>重要新闻保留</span><strong className="good">{payload?.annotation_queue.priority_reserve ?? "—"}</strong><small>FOMC、CPI、Payroll 专用</small></article>
        <article><span>错误退避中</span><strong>{payload?.annotation_queue.backing_off ?? "—"}</strong><small>到期前不会重复请求</small></article>
        <article><span>已隔离</span><strong>{payload?.annotation_queue.dead_letter ?? "—"}</strong><small>相同永久错误不再消耗配额</small></article>
        <article><span>安全吞吐</span><strong>{payload?.annotation_queue.requests_per_minute ?? "—"}</strong><small>RPM · 每 key {payload?.annotation_queue.requests_per_minute_per_key ?? "—"}</small></article>
      </section>

      <section className="routing-grid">
        <article><span>重要 / 会进入训练</span><strong>{payload?.llm_routing.action_bearing.model ?? "Gemini 3.5 Flash-Lite"} → {payload?.llm_routing.action_bearing.fallback_model ?? "Gemini 3.1 Flash-Lite"}</strong><p>{payload?.llm_routing.action_bearing.role ?? "3.5 优先，普通额度用尽后由 3.1 接管"}</p></article>
        <article><span>低重要性 / 仅展示</span><strong>{payload?.llm_routing.display_only.model ?? "Gemma 4 31B"}</strong><p>{payload?.llm_routing.display_only.role ?? "标题中文翻译，不进入模型训练"}</p></article>
        <article><span>暂不启用</span><strong>Antigravity</strong><p>{payload?.llm_routing.antigravity.reason ?? "每日额度不适合批量新闻"}</p></article>
      </section>

      <QuotaPanel title="Gemini 3.5 Flash-Lite · 逐 Key 配额" eyebrow="ACTION-BEARING / FULL CONTENT" quota={quota} nowMs={nowMs} />
      <QuotaPanel title="Gemini 3.1 Flash-Lite · 逐 Key 配额" eyebrow="ACTION-BEARING FALLBACK / FULL CONTENT" quota={fallbackQuota} nowMs={nowMs} />
      <QuotaPanel title="Gemma 4 31B · 逐 Key 配额" eyebrow="DISPLAY-ONLY / TITLE TRANSLATION" quota={gemmaQuota} nowMs={nowMs} />

      <aside className="quota-note">
        <b>计数规则</b>
        <p>每次请求在发往模型前永久计入各自账本，包括被 Google 拒绝的请求。3.5 每 key 本机上限 500，并保留一部分给 FOMC、CPI 与 Payroll；普通额度用尽后才由 3.1 接管。数字格式和中文显示问题会在本地恢复，同一分钟的 RPM 槽位用完只会延后到下一批，不算失败。只有 Google 服务或响应故障才进入持久退避。Gemma 每 key 本机上限 15,000。三个账本都在 Pacific midnight 自动切换。</p>
        <p>Google 实际额度按 project 而不是 API key 计算。如果多个 key 属于同一个 project，它们仍会共享 Google 的额度；本页显示的是本机逐模型、逐 key 的安全账本，不代表 Google 端保证额度。</p>
      </aside>

      <footer><span>每 15 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? new Date(payload.generated_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</span></footer>
    </main>
  );
}
