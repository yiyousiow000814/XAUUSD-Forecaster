"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

type StatusPayload = {
  generated_at: string;
  system: {
    online: boolean;
    source_of_truth: string;
    sites_mirror: string;
    components: Record<string, { last_success: string | null; age_seconds: number | null; status: string; last_error: string | null }>;
  };
  news_source_health: Array<{
    source: string; label: string; role: string; health: "HEALTHY" | "DEGRADED" | "ERROR" | "STALE" | "FALLBACK_ACTIVE";
    latest_poll_time: string | null; last_success: string | null;
    age_seconds: number | null; last_error_time: string | null; last_error_type: string | null; last_error: string | null;
    poll_count: number; ok_count: number; item_count: number; revision_count: number; full_text_count: number;
    recovery_mode: string | null; fallback_label: string | null; fallback_health: string | null; next_retry_time: string | null;
  }>;
};

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

function localTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
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

export default function HealthPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<StatusPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  return <main className="status-main">
    <div className="grain" />
    <header className="topbar">
      <button className="brand audit-brand brand-button" type="button" onClick={() => router.replace("/")}>
        <span className="brand-mark">AU</span><div><strong>Aurum System Health</strong><small>组件心跳 · 新闻来源</small></div>
      </button>
      <div className="top-actions">
        <button className="audit-link" type="button" onClick={() => router.push("/status")}>AI 模型用量</button>
        <button className="audit-link" type="button" onClick={() => router.push("/audit")}>新闻与决策</button>
        <button className="audit-link" type="button" onClick={() => router.replace("/")}>← 返回实时室</button>
      </div>
    </header>
    <section className="status-hero">
      <div><p className="eyebrow">OPERATIONAL HEARTBEATS / SOURCE HEALTH</p><h1>系统健康状态</h1></div>
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
        <small>最后成功 {localTime(item.last_success)}</small><small>{elapsed(item.age_seconds)}</small>
        {item.last_error ? <em>{item.last_error}</em> : null}
      </article>)}</div>
    </section>
    <section className="source-health" aria-label="新闻来源状态">
      <header><div><p className="eyebrow">NEWS INGEST / SOURCE-BY-SOURCE</p><h2>新闻来源状态</h2></div><p>发布源和正文解析器分别判断。<b>正文链路降级不会伪装成全部新闻中断</b>；错误会保留最近一次成功时间和具体原因。</p></header>
      <div className="source-health-head"><span>来源 / 角色</span><span>状态 / 最近轮询</span><span>证据</span><span>最近错误</span></div>
      {(payload?.news_source_health ?? []).map((item) => <article key={item.source}>
        <div><strong>{item.label}</strong><small>{item.role} · {item.source}</small></div>
        <div><b className={`source-health-badge health-${item.health.toLowerCase()}`}>{item.health === "FALLBACK_ACTIVE" ? "后备源接管中" : item.health}</b><small>{localTime(item.latest_poll_time)}</small><small>最近成功 {localTime(item.last_success)}</small>{item.next_retry_time ? <small>自动重试 {localTime(item.next_retry_time)}</small> : null}</div>
        <div><strong>{item.item_count || "—"} 篇</strong><small>{item.revision_count || "—"} revisions · 完整正文 {item.full_text_count || "—"}</small><small>轮询 {item.ok_count}/{item.poll_count} 完成</small></div>
        <div className="source-health-error"><strong>{item.recovery_mode === "RATE_LIMIT_BACKOFF" ? `GDELT 限流 · ${item.fallback_label} 自动接管` : item.last_error_type ? `${item.health === "HEALTHY" ? "历史异常 · 已恢复" : "当前异常"} · ${item.last_error_type}` : "无已记录异常"}</strong><small>{item.last_error_time ? localTime(item.last_error_time) : ""} {item.last_error ?? "链路轮询正常"}</small>{item.fallback_label ? <small>后备链路：{item.fallback_label} · {item.fallback_health}</small> : null}</div>
      </article>)}
    </section>
    <footer><span>每 15 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? localTime(payload.generated_at) : "—"}</span></footer>
  </main>;
}
