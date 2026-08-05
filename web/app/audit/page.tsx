"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

type Prediction = {
  model_identity: string;
  model_version: string;
  predicted_direction_u5: number | null;
  predicted_news_residual_u5: number | null;
  ev_long_u5: number | null;
  ev_short_u5: number | null;
  uncertainty_u5: number | null;
  recommended_action: string;
  effective_action: string;
  prediction_status: string;
};

type Decision = {
  decision_id: string;
  decision_time: string;
  effective_action: string;
  data_health: string;
  bid: number | null;
  ask: number | null;
  outcome_status: string | null;
  long_return: number | null;
  short_return: number | null;
  long_mfe: number | null;
  long_mae: number | null;
  predictions: Prediction[];
};

type News = {
  category: string;
  source: string;
  source_item_id: string;
  revision_number: number;
  source_published_time: string | null;
  collector_first_seen_time: string;
  headline: string;
  original_headline: string;
  content_characters: number;
  content_status: "FULL_TEXT" | "SOURCE_CONTENT" | "HEADLINE_ONLY";
  summary_zh: string | null;
  annotation_status: "READY" | "QUEUED" | "WAITING_CONTENT";
  link: string;
  event_type: string | null;
  entities: string[];
  hawkishness: number | null;
  inflation_impulse: number | null;
  growth_impulse: number | null;
  geopolitical_risk: number | null;
  usd_impulse: number | null;
  novelty: number | null;
  confidence: number | null;
  llm_model_version: string | null;
  prompt_version: string | null;
  parsed_at: string | null;
};

type Payload = {
  generated_at: string;
  system: { online: boolean };
  counts: Record<string, number>;
  annotation_queue: {
    ready: number;
    queued: number;
    waiting_content: number;
    configured_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute: number;
  };
  recent_news: News[];
  recent_decisions: Decision[];
  training: {
    automatic: boolean;
    minimum_rows: number;
    retrain_interval: number;
    eligible_rows: number;
    complete_rows: number;
    next_training_at: number;
    champion_auto_promotion: boolean;
    models: Array<{ model_identity: string; model_version: string; training_cutoff: string }>;
  };
  factor_coverage: Array<{
    domain: string;
    status: string;
    source: string | null;
    action_bearing: boolean;
    cadence: string;
    value?: number | null;
    observed_at?: string | null;
    unit?: string | null;
  }>;
};

const time = (value?: string | null) => value ? new Intl.DateTimeFormat("zh-CN", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
}).format(new Date(value)) : "—";
const number = (value?: number | null, digits = 2) => value === null || value === undefined ? "—" : value.toFixed(digits);
const percent = (value?: number | null) => value === null || value === undefined ? "—" : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;
const impulse = (value?: number | null) => value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
const NEWS_PER_PAGE = 12;
const CATEGORY_ORDER = ["战争/地缘", "利率/Fed", "央行购金", "通胀/就业", "增长/经济", "油价/能源", "美元/流动性", "Fed监管/其他", "其他"];
const SOURCE_LABELS: Record<string, string> = {
  federal_reserve_monetary: "Federal Reserve · 货币政策",
  federal_reserve_speeches_testimony: "Federal Reserve · 演讲证词",
  federal_reserve_press_all: "Federal Reserve · 新闻与监管",
  gdelt_gold_geopolitics: "GDELT · 战争与地缘",
  google_news_gold_geopolitics: "Google News · 战争与地缘",
  google_news_gold_context: "Google News · 黄金大视野",
  world_gold_council_central_banks: "World Gold Council · 央行购金",
  eia_today_in_energy: "U.S. EIA · 能源分析",
  eia_press_releases: "U.S. EIA · 新闻发布",
  ecb_press_releases: "European Central Bank · 官方发布",
  us_treasury_press_releases: "U.S. Treasury · 官方发布",
  bea_economic_releases: "U.S. BEA · 经济数据发布",
};

export default function AuditPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"news" | "decisions" | "coverage">("news");
  const [newsCategory, setNewsCategory] = useState("全部");
  const [newsPage, setNewsPage] = useState(1);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      setPayload(body);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取审计数据");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => {
      const requested = new URLSearchParams(window.location.search).get("view");
      if (requested === "news" || requested === "decisions" || requested === "coverage") {
        setView(requested);
      }
      refresh();
    }, 0);
    const interval = window.setInterval(refresh, 15_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh]);

  const selectView = (next: "news" | "decisions" | "coverage") => {
    setView(next);
    window.history.replaceState(null, "", `/audit?view=${next}`);
  };

  const progress = useMemo(() => {
    const training = payload?.training;
    if (!training) return 0;
    return Math.min(100, training.complete_rows / training.next_training_at * 100);
  }, [payload]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of payload?.recent_news ?? []) {
      counts.set(row.category, (counts.get(row.category) ?? 0) + 1);
    }
    return counts;
  }, [payload]);
  const categories = useMemo(() => [
    { name: "全部", count: payload?.recent_news.length ?? 0 },
    ...CATEGORY_ORDER.filter(name => categoryCounts.has(name)).map(name => ({ name, count: categoryCounts.get(name) ?? 0 })),
  ], [categoryCounts, payload]);
  const filteredNews = useMemo(() => newsCategory === "全部"
    ? payload?.recent_news ?? []
    : (payload?.recent_news ?? []).filter(row => row.category === newsCategory), [newsCategory, payload]);
  const newsPageCount = Math.max(1, Math.ceil(filteredNews.length / NEWS_PER_PAGE));
  const currentNewsPage = Math.min(newsPage, newsPageCount);
  const visibleNews = filteredNews.slice((currentNewsPage - 1) * NEWS_PER_PAGE, currentNewsPage * NEWS_PER_PAGE);
  const emptyNewsRows = Math.max(0, NEWS_PER_PAGE - visibleNews.length);

  return (
    <main className="audit-main">
      <div className="grain" />
      <header className="topbar audit-topbar">
        <button className="brand audit-brand brand-button" type="button" onClick={() => router.replace("/")}>
          <span className="brand-mark">AU</span>
          <div><strong>Aurum Evidence Desk</strong><small>新闻 · 决策 · 因子覆盖</small></div>
        </button>
        <div className="top-actions">
          <button className="audit-link" type="button" onClick={() => router.push("/status")}>系统状态</button>
          <button className="audit-link" type="button" onClick={() => router.replace("/")}>← 返回实时室</button>
          <div className={`live-pill ${payload?.system.online && !error ? "is-live" : "is-down"}`}>
            <span />{payload?.system.online && !error ? "LIVE" : "OFFLINE"}
          </div>
        </div>
      </header>

      <section className="audit-intro">
        <div><p className="eyebrow">IMMUTABLE FORWARD EVIDENCE</p><h1>新闻先被看见，<br />决定才被允许产生。</h1></div>
        <div className="training-card">
          <span>AUTO TRAINING</span>
          <strong>{payload?.training.complete_rows ?? 0}<small> / {payload?.training.next_training_at ?? 200}</small></strong>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <p>达到门槛自动生成 Challenger；Champion 仍由你批准。</p>
        </div>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="annotation-queue" aria-label="Gemini 摘要队列">
        <strong>Gemini 完整正文摘要</strong>
        <span><b>{payload?.annotation_queue.ready ?? 0}</b> 已完成</span>
        <span><b>{payload?.annotation_queue.queued ?? 0}</b> 排队中</span>
        <span><b>{payload?.annotation_queue.waiting_content ?? 0}</b> 等待正文</span>
        <small>完整正文由 Gemini 3.5 Flash-Lite 处理：本机已启用 {payload?.annotation_queue.configured_key_count ?? 0} 个 key，每个安全使用 {payload?.annotation_queue.requests_per_minute_per_key ?? 12} RPM，合计每分钟最多 {payload?.annotation_queue.requests_per_minute ?? 0} 篇；标题中文翻译由 Gemma 4 31B 分流，且不进入训练。</small>
      </section>

      <nav className="audit-tabs" aria-label="审计视图">
        <a href="/audit?view=news" className={view === "news" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("news"); }}>新闻与 Gemini <b>{payload?.counts.latest_news_items ?? 0}</b></a>
        <a href="/audit?view=decisions" className={view === "decisions" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("decisions"); }}>决策与30分钟结果 <b>{payload?.counts.decision_events ?? 0}</b></a>
        <a href="/audit?view=coverage" className={view === "coverage" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("coverage"); }}>大视野覆盖 <b>{payload?.factor_coverage.filter(row => row.status === "LIVE" || row.status === "COLLECTING").length ?? 0}/11</b></a>
      </nav>

      {view === "news" && <>
        <section className="news-browser" aria-label="新闻自动分类">
          <div><strong>自动分类</strong><span>最新版本共 {payload?.counts.latest_news_items ?? 0} 条 · 每页 {NEWS_PER_PAGE} 条</span></div>
          <nav>
            {categories.map(category => <button key={category.name} type="button" className={newsCategory === category.name ? "active" : ""} onClick={() => { setNewsCategory(category.name); setNewsPage(1); }}>
              {category.name}<b>{category.count}</b>
            </button>)}
          </nav>
        </section>
        <section className="news-table">
          <header className="news-table-head"><span>分类 / 时间</span><span>新闻与来源</span><span>正文 / Gemini</span></header>
          {visibleNews.map((row) => <details className="news-row" key={`${row.source}-${row.source_item_id}-${row.revision_number}`}>
            <summary>
              <div className="news-row-stamp"><b>{row.category}</b><time>{time(row.source_published_time)}</time></div>
              <div className="news-row-title"><strong>{row.headline}</strong><small>{SOURCE_LABELS[row.source] ?? row.source.replaceAll("_", " ")}{row.headline !== row.original_headline ? " · Gemini 中文标题" : " · 等待标题翻译"}</small></div>
              <div className={`news-row-state state-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
                <b>{row.content_status === "FULL_TEXT" ? `${row.content_characters.toLocaleString()} 字符` : row.source === "google_news_gold_geopolitics" ? "聚合标题" : "等待正文"}</b>
                <small>{row.annotation_status === "READY" ? "Gemini 已完成" : row.annotation_status === "QUEUED" ? "Gemini 排队中" : "禁止判断"}</small>
              </div>
            </summary>
            <div className="news-row-detail">
              <div className="news-detail-top">
                <div className={`content-proof content-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
                  {row.content_status === "FULL_TEXT" ? `✓ 已读取正式正文 · ${row.content_characters.toLocaleString()} 字符` : row.content_status === "SOURCE_CONTENT" ? `已读取来源内容 · ${row.content_characters.toLocaleString()} 字符` : row.source === "google_news_gold_geopolitics" ? "Google News RSS 只提供聚合标题 · 未取得 publisher 正文" : "来源正文尚未抓取 · 禁止 Gemini 判断"}
                </div>
                {row.link && <a className="source-link" href={row.link} target="_blank" rel="noreferrer">阅读来源 ↗</a>}
              </div>
              {row.headline !== row.original_headline ? <p className="original-headline"><b>原文标题</b>{row.original_headline}</p> : null}
              {row.annotation_status === "READY" ? <section className="gemini-summary">
                <span>GEMINI 中文摘要 · 完整读取 {row.content_characters.toLocaleString()} 字符</span><p>{row.summary_zh}</p>
              </section> : row.annotation_status === "QUEUED" ? <section className="gemini-summary summary-queued">
                <span>FLASH-LITE 摘要排队中</span><p>正文已经完整入库，不会截断。系统正通过 {payload?.annotation_queue.configured_key_count ?? 0} 个 key 轮换，每分钟最多生成 {payload?.annotation_queue.requests_per_minute ?? 0} 篇中文摘要；标题翻译会独立交给 Gemma。</p>
              </section> : <section className="gemini-summary summary-waiting">
                <span>等待来源正文</span><p>当前只有标题或短描述，不会进入模型，也不会假装已经理解内容。</p>
              </section>}
              {row.event_type && <div className="news-classification"><b>{row.event_type}</b><span>鹰派 {impulse(row.hawkishness)}</span><span>通胀 {impulse(row.inflation_impulse)}</span><span>增长 {impulse(row.growth_impulse)}</span><span>地缘 {impulse(row.geopolitical_risk)}</span><span>美元 {impulse(row.usd_impulse)}</span><span>新颖 {number(row.novelty)}</span><span>置信 {number(row.confidence)}</span></div>}
              <footer className="card-footer"><span>{row.entities.join(" · ") || "无实体"}</span><span>{row.llm_model_version ?? "未标注"} · 收到 {time(row.collector_first_seen_time)} · 标注 {time(row.parsed_at)}</span></footer>
            </div>
          </details>)}
          {Array.from({ length: emptyNewsRows }, (_, index) => <div className="news-row-placeholder" aria-hidden="true" key={`empty-news-row-${index}`} />)}
        </section>
        {newsPageCount > 1 && <nav className="news-pagination" aria-label="新闻分页">
          <button type="button" disabled={currentNewsPage === 1} onClick={() => setNewsPage(page => Math.max(1, page - 1))}>← 上一页</button>
          <span>第 <b>{currentNewsPage}</b> / {newsPageCount} 页 · 当前分类 {filteredNews.length} 条</span>
          <button type="button" disabled={currentNewsPage === newsPageCount} onClick={() => setNewsPage(page => Math.min(newsPageCount, page + 1))}>下一页 →</button>
        </nav>}
      </>}

      {view === "decisions" && <section className="decision-audit">
        {(payload?.recent_decisions ?? []).map((row) => {
          const full = row.predictions.find(item => item.model_identity === "CHALLENGER_FULL");
          return <details className="decision-row" key={row.decision_id}>
            <summary>
              <time>{time(row.decision_time)}</time><b>{row.effective_action}</b>
              <span>{number(row.bid)} / {number(row.ask)}</span>
              <em>Full建议 {full?.recommended_action ?? "WAIT"}</em>
              <strong className={row.outcome_status === "VALID" ? "good" : "muted"}>{row.outcome_status === "VALID" ? `Long ${percent(row.long_return)} · Short ${percent(row.short_return)}` : "等待30分钟结果"}</strong>
            </summary>
            <div className="prediction-grid">
              {row.predictions.map(model => <article key={model.model_version}>
                <span>{model.model_identity}</span><h3>{model.recommended_action}</h3>
                <p>{model.prediction_status}</p>
                <dl><div><dt>方向 U5</dt><dd>{number(model.predicted_direction_u5, 3)}</dd></div><div><dt>News residual</dt><dd>{number(model.predicted_news_residual_u5, 3)}</dd></div><div><dt>Long EV</dt><dd>{number(model.ev_long_u5, 3)}</dd></div><div><dt>Short EV</dt><dd>{number(model.ev_short_u5, 3)}</dd></div><div><dt>不确定性</dt><dd>{number(model.uncertainty_u5, 3)}</dd></div></dl>
                <small>{model.model_version}</small>
              </article>)}
            </div>
          </details>;
        })}
      </section>}

      {view === "coverage" && <section className="coverage-grid">
        {(payload?.factor_coverage ?? []).map(row => <article key={row.domain} className={`coverage-card status-${row.status.toLowerCase().replaceAll("_", "-")}`}>
          <div><span>{row.cadence}</span><b>{row.status}</b></div><h2>{row.domain}</h2><p>{row.source ?? "尚未连接可靠的 point-in-time 数据源"}</p>
          {row.value !== null && row.value !== undefined && <strong className="coverage-value">{number(row.value, 3)} <small>{row.unit}</small></strong>}
          <small>{row.observed_at ? `观测期 ${row.observed_at} · ` : ""}{row.action_bearing ? "已进入决策Snapshot" : "Shadow特征，等待训练验证"}</small>
        </article>)}
      </section>}

      <footer className="audit-footer"><span>最后同步 {time(payload?.generated_at)}</span><span>SHADOW ONLY · APPEND ONLY</span></footer>
    </main>
  );
}
