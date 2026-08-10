"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardLink from "../_components/DashboardLink";
import SystemStatePill from "../_components/SystemStatePill";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { isImmutablePreview, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";

type Decision = {
  decision_time: string;
  effective_action: string;
  research_action?: string | null;
  research_status?: string | null;
  data_health: string;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  outcome_status?: string | null;
  outcome_reason_codes?: string[];
  long_return?: number | null;
  short_return?: number | null;
  features: Record<string, number | null>;
};

type Payload = {
  generated_at: string;
  forward_epoch: string;
  system: {
    online: boolean;
    market_session?: "OPEN" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";
    quote_age_seconds: number | null;
    mode: string;
    trading_enabled: boolean;
    symbol: string;
  };
  latest: Decision & {
    source_event_time: string;
    source_received_time: string;
    u5: number | null;
    u5_status: string;
    reason_codes: string[];
  };
  research_forecast: {
    model_identity: string;
    model_version: string;
    recommended_action: "LONG" | "SHORT" | "WAIT";
    prediction_status: string;
    ev_long_u5: number | null;
    ev_short_u5: number | null;
    interval_width: number | null;
    decision_time: string;
    signal_expiry_seconds: number;
    forecast_horizon_seconds: number;
    directional_bias: "LONG" | "SHORT" | "NEUTRAL";
    frozen_record: boolean;
  } | null;
  u5_context: { percentile: number | null; samples: number; label: string };
  counts: Record<string, number>;
  outcome_summary: {
    samples: number;
    avg_long: number | null;
    avg_short: number | null;
    avg_coverage: number | null;
  };
  recent_decisions: Decision[];
  sources: Record<string, string>;
};

const fmt = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "—" : value.toFixed(digits);

const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(3)}%`;

const signedPercent = (value: number | null | undefined) => {
  if (value === null || value === undefined) return "—";
  const rendered = `${Math.abs(value * 100).toFixed(3)}%`;
  if (value > 0) return `+${rendered}`;
  if (value < 0) return `−${rendered}`;
  return `±${rendered}`;
};

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
  const [payload, setPayload] = useState<Payload | null>(() => readDashboardResource<Payload>("/api/status"));
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const immutablePreview = isImmutablePreview(payload);

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
      () => void refresh(),
      () => void refresh(true),
      5_000,
      immutablePreview,
    );
  }, [refresh, immutablePreview]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const latest = payload?.latest;
  const loading = payload === null && error === null;
  const online = Boolean(payload?.system.online && !error);
  const marketClosed = Boolean(payload?.system.market_session === "WEEKLY_CLOSED" && !error);
  const mid = latest?.bid && latest.ask ? (latest.bid + latest.ask) / 2 : null;
  const u5Percent = latest?.u5 == null ? null : Math.expm1(latest.u5) * 100;
  const u5Dollars = latest?.u5 == null || mid == null ? null : Math.expm1(latest.u5) * mid;
  const decisions = [...(payload?.recent_decisions ?? [])].reverse();
  const forecast = payload?.research_forecast;
  const forecastAction = forecast?.recommended_action ?? "WAIT";
  const riskPercentile = payload?.u5_context.percentile ?? 0;
  const forecastAge = forecast?.decision_time
    ? Math.max(0, Math.floor((now - new Date(forecast.decision_time).getTime()) / 1_000))
    : null;
  const signalExpiry = forecast?.signal_expiry_seconds ?? 20;
  const horizon = forecast?.forecast_horizon_seconds ?? 1_800;
  const signalRemaining = forecastAge === null ? 0 : Math.max(0, signalExpiry - forecastAge);
  const horizonRemaining = forecastAge === null ? 0 : Math.max(0, horizon - forecastAge);
  const horizonMinutes = Math.floor(horizonRemaining / 60);
  const forecastStatus = marketClosed
    ? null
    : loading
      ? "读取中…"
      : error
        ? "数据服务暂不可用"
        : !online
          ? "等待行情恢复"
          : forecastAge === null
            ? "等待最新预测"
            : signalRemaining > 0
              ? `可参考 · ${signalRemaining}秒`
              : horizonRemaining > 0
                ? `观察中 · 剩${horizonMinutes}分钟`
                : "本轮已结束";

  return (
    <main>
      <div className="grain" />
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">AU</span>
          <div>
            <strong>Aurum Signal Room</strong>
            <small>XAUUSD · Forward-only intelligence</small>
          </div>
        </div>
        <div className="top-actions">
          <DashboardLink className="audit-link" href="/status">系统状态</DashboardLink>
          <DashboardLink ariaLabel="新闻、决策与结果" className="audit-link" href="/audit?view=decisions">新闻 / 结果 <span aria-hidden="true">→</span></DashboardLink>
          <SystemStatePill loading={loading} error={Boolean(error)} online={Boolean(payload?.system.online)} marketSession={payload?.system.market_session} />
        </div>
      </header>

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
            <span>AGE <b>{fmt(payload?.system.quote_age_seconds, 1)}s</b></span>
          </div>
        </div>

        <div className="decision-dial">
          <span className="dial-label">30分钟预测</span>
          <div className={`action action-${forecastAction.toLowerCase()}`}>
            {forecastAction}
          </div>
          {forecastStatus && signalRemaining > 0 && online && <strong className="forecast-state is-current">{forecastStatus}</strong>}
        </div>
      </section>

      {error && <div className="error-banner">{error}。行情采集可能仍在运行，但网页数据服务已停止。</div>}

      <section className="metric-grid">
        <article>
          <span>DATA HEALTH</span>
          <strong className={latest?.data_health === "OK" ? "good" : "warn"}>
            {latest?.data_health ?? "—"}
          </strong>
          <small>最新决策 {localTime(latest?.decision_time)}</small>
        </article>
        <article>
          <span>DECISIONS</span>
          <strong>{payload?.counts.decision_events ?? 0}</strong>
          <small>从 Forward Epoch 开始</small>
        </article>
        <article>
          <span>30M OUTCOMES</span>
          <strong>{payload?.counts.outcomes ?? 0}</strong>
          <small>{payload?.outcome_summary.samples ?? 0} 个有效样本</small>
        </article>
        <article>
          <span>NEWS REVISIONS</span>
          <strong>{payload?.counts.news_revisions ?? 0}</strong>
          <small>LLM {payload?.sources.llm === "ENABLED" ? "已启用" : "Gemini 标注中"}</small>
        </article>
      </section>

      <section className="workspace-grid">
        <article className="panel timeline-panel">
          <div className="panel-head">
            <div><span>RESEARCH FORECAST LEDGER · 非下单动作</span><h2>最近90分钟</h2></div>
            <button type="button" onClick={refresh} disabled={refreshing}>
              {refreshing ? "同步中" : "刷新"}
            </button>
          </div>
          <div className="timeline">
            {decisions.map((row) => (
              <div className="tick" key={row.decision_time}>
                <span className={`health-dot ${row.data_health === "OK" ? "ok" : "bad"}`} />
                <time>{localTime(row.decision_time).slice(-8, -3)}</time>
                <b title={`安全基准动作 ${row.effective_action}`}>{row.research_action ?? row.effective_action}</b>
                <span>{fmt(row.bid)} / {fmt(row.ask)}</span>
                <em title={row.outcome_status === "VALID" ? "30分钟结果已计算" : row.outcome_status ? `样本已隔离，不进入训练：${row.outcome_reason_codes?.join(" · ") || "报价证据无效"}` : "预测已记录，等待未来30分钟走完后计算结果"}>{row.outcome_status === "VALID" ? "30分钟结果 ✓" : row.outcome_status ? "无效样本 · 已隔离" : "等待30分钟结果"}</em>
              </div>
            ))}
          </div>
        </article>

        <article className="panel factor-panel">
          <div className="panel-head"><div><span>MARKET STATE</span><h2>黄金自身确认</h2></div></div>
          <Factor label="1分钟" value={latest?.features?.return_1m} />
          <Factor label="5分钟" value={latest?.features?.return_5m} />
          <Factor label="15分钟" value={latest?.features?.return_15m} />
          <Factor label="30分钟" value={latest?.features?.return_30m} />
          <div className="factor-footer">
            <span><b>30分钟波动风险</b><small>相对本系统历史</small></span>
            <strong>{payload?.u5_context.label ?? "等待样本"}<small>{u5Percent === null ? "—" : `约 ±${u5Percent.toFixed(2)}% · ±$${u5Dollars?.toFixed(1)}`}</small></strong>
            <em>{riskPercentile.toFixed(0)} / 100</em>
            <div className="risk-scale" aria-label={`历史波动分位 ${riskPercentile.toFixed(0)} / 100`}><i style={{ left: `${Math.min(100, Math.max(0, riskPercentile))}%` }} /></div>
            <p>箭头表示它在已收集 {payload?.u5_context.samples ?? 0} 个样本中的波动分位；越靠红色，未来30分钟通常波动越剧烈。它不是亏损概率，也不代表方向。</p>
          </div>
        </article>

        <article className="panel evidence-panel">
          <div className="panel-head"><div><span>EVIDENCE</span><h2>Forward 学习状态</h2></div></div>
          <div className="evidence-stat"><span>平均 Long</span><b>{percent(payload?.outcome_summary.avg_long)}</b></div>
          <div className="evidence-stat"><span>平均 Short</span><b>{percent(payload?.outcome_summary.avg_short)}</b></div>
          <div className="evidence-stat"><span>报价覆盖率</span><b>{percent(payload?.outcome_summary.avg_coverage)}</b></div>
          <p>样本不足时不训练、不晋升，也不会把 WAIT 当作失败。</p>
        </article>

        <article className="panel source-panel">
          <div className="panel-head"><div><span>SOURCE HEALTH</span><h2>数据链路</h2></div></div>
          <Source name="cTrader XAUUSD · 本机 Algo" state={online ? "本机在线" : marketClosed ? "市场休市 · 新闻继续" : "本机中断"} good={online || marketClosed} />
          <Source name="Federal Reserve · 官方源" state="采集中" good />
          <Source name="BLS Public API · 官方源" state={payload?.sources.bls === "ONLINE" ? "采集中" : "准备中"} good={payload?.sources.bls === "ONLINE"} />
          <Source name="Gemini 3.5 Flash-Lite · API" state={payload?.sources.llm === "ENABLED" ? "标注中" : "等待标注"} good={payload?.sources.llm === "ENABLED"} />
        </article>
      </section>

      <footer>
        <span>FORWARD EPOCH {localTime(payload?.forward_epoch)}</span>
        <span>LAST SYNC {localTime(payload?.generated_at)}</span>
        <span>SHADOW / NO ORDER AUTHORITY</span>
      </footer>
    </main>
  );
}

function Factor({ label, value }: { label: string; value?: number | null }) {
  const magnitude = Math.min(100, Math.abs(value ?? 0) * 25_000);
  const direction = value === null || value === undefined ? "neutral" : value > 0 ? "up" : value < 0 ? "down" : "flat";
  const directionLabel = direction === "up" ? "▲ 上涨" : direction === "down" ? "▼ 下跌" : direction === "flat" ? "— 持平" : "— 暂无";
  return (
    <div className="factor">
      <span>{label}</span>
      <div><i className={direction} style={{ width: `${magnitude}%` }} /></div>
      <b className={direction}><small>{directionLabel}</small>{signedPercent(value)}</b>
    </div>
  );
}

function Source({ name, state, good }: { name: string; state: string; good: boolean }) {
  return <div className="source"><span className={good ? "source-good" : "source-warn"} /><b>{name}</b><em>{state}</em></div>;
}
