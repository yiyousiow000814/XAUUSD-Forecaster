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
  system: { online: boolean; mode: string; trading_enabled: boolean };
  annotation_queue: {
    configured_key_count: number;
    available_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute: number;
  };
  gemini_quota: QuotaState;
  gemma_quota: QuotaState;
  llm_routing: {
    action_bearing: { model: string; role: string };
    display_only: { model: string; role: string; requests_per_minute: number };
    antigravity: { enabled: boolean; reason: string };
  };
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
        <div className={`live-pill ${payload?.system.online && !error ? "is-live" : "is-down"}`}>
          <span />{payload?.system.online && !error ? "SYSTEM ONLINE" : "STATUS OFFLINE"}
        </div>
      </section>

      {error ? <div className="error-banner">状态读取失败：{error}</div> : null}

      <section className="quota-summary">
        <article><span>已配置 KEY</span><strong>{payload?.annotation_queue.configured_key_count ?? "—"}</strong><small>当前可用 {payload?.annotation_queue.available_key_count ?? "—"} · 只显示匿名编号</small></article>
        <article><span>Flash 今日已发送</span><strong>{quota?.total_sent ?? "—"}</strong><small>重要正文与训练特征</small></article>
        <article><span>Flash 今日剩余</span><strong className="good">{quota?.total_remaining ?? "—"}</strong><small>本机账本上限</small></article>
        <article><span>安全吞吐</span><strong>{payload?.annotation_queue.requests_per_minute ?? "—"}</strong><small>RPM · 每 key {payload?.annotation_queue.requests_per_minute_per_key ?? "—"}</small></article>
      </section>

      <section className="routing-grid">
        <article><span>重要 / 会进入训练</span><strong>{payload?.llm_routing.action_bearing.model ?? "Gemini 3.5 Flash-Lite"}</strong><p>{payload?.llm_routing.action_bearing.role ?? "完整正文、结构化事件与训练特征"}</p></article>
        <article><span>低重要性 / 仅展示</span><strong>{payload?.llm_routing.display_only.model ?? "Gemma 4 31B"}</strong><p>{payload?.llm_routing.display_only.role ?? "标题中文翻译，不进入模型训练"}</p></article>
        <article><span>暂不启用</span><strong>Antigravity</strong><p>{payload?.llm_routing.antigravity.reason ?? "每日额度不适合批量新闻"}</p></article>
      </section>

      <QuotaPanel title="Gemini 3.5 Flash-Lite · 逐 Key 配额" eyebrow="ACTION-BEARING / FULL CONTENT" quota={quota} nowMs={nowMs} />
      <QuotaPanel title="Gemma 4 31B · 逐 Key 配额" eyebrow="DISPLAY-ONLY / TITLE TRANSLATION" quota={gemmaQuota} nowMs={nowMs} />

      <aside className="quota-note">
        <b>计数规则</b>
        <p>每次请求在发往模型前永久计入各自账本，包括被 Google 拒绝的请求。Flash 每 key 本机上限 500；Gemma 每 key 本机上限 15,000。两个账本都在 Pacific midnight 自动切换。</p>
        <p>Google 实际额度按 project 而不是 API key 计算。如果多个 key 属于同一个 project，它们仍会共享 Google 的额度；本页显示的是本机逐模型、逐 key 的安全账本，不代表 Google 端保证额度。</p>
      </aside>

      <footer><span>每 15 秒刷新 · SHADOW ONLY</span><span>最后状态：{payload?.generated_at ? new Date(payload.generated_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</span></footer>
    </main>
  );
}
