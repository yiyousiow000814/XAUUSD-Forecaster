"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import LearningGraphModal from "./LearningGraphModal";

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
  annotation_status: "READY" | "QUEUED" | "BACKING_OFF" | "DEAD_LETTER" | "WAITING_CONTENT";
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
  fetched_time: string;
  collection_delay_seconds: number | null;
  processing_delay_seconds: number | null;
  source_eligibility: string;
  model_visibility: string;
  eligibility_version: string;
  primary_category: string | null;
  secondary_categories: string[];
  emerging_topic_zh: string | null;
};

type NewsEvidence = {
  event_key: string;
  canonical_headline: string;
  canonical_source: string;
  collector_first_seen_time: string;
  topics: string[];
  evidence_grade: "PRIMARY" | "CORROBORATED" | "SINGLE_RELIABLE" | "DISCOVERY_ONLY";
  broad_model_eligible: boolean;
  model_permission: "BROAD_MODEL" | "DISPLAY_ONLY";
  member_count: number;
  independent_publishers: number;
  source_names: string[];
  publisher_domains: string[];
  reason_codes: string[];
};

type LearningModel = {
  model_version: string;
  model_identity: string;
  model_stage: string;
  training_rows: number;
  training_cutoff: string;
  subsequent_oos_rows: number;
  effective_blocks: number;
  distinct_days: number;
  cumulative_quote_return: number;
  average_quote_return: number | null;
  profit_factor_quote_adjusted: number | null;
  max_drawdown_quote_return: number;
  sharpe_quote_adjusted: number | null;
  interval_width: number | null;
  calibration_status: string;
  wait_rate: number | null;
  coverage_rate: number | null;
  average_oracle_regret: number | null;
  wait_opportunity_cost: number;
  long_frequency: number;
  short_frequency: number;
  active_rank: number | null;
  lifecycle_status: "LATEST" | "PREVIOUS" | "ARCHIVED";
  news_event_days: number;
  news_evidence_status: string;
};

type RollingProcess = {
  model_identity: string;
  active_model_versions: string[];
  oos_rows: number;
  distinct_days: number;
  cumulative_quote_return: number;
  average_quote_return: number | null;
  profit_factor_quote_adjusted: number | null;
  max_drawdown_quote_return: number;
  sharpe_quote_adjusted: number | null;
  calibration_status: string;
};

type Payload = {
  generated_at: string;
  system: { online: boolean; source_of_truth: string; sites_mirror: string };
  counts: Record<string, number>;
  annotation_queue: {
    ready: number;
    queued: number;
    backing_off: number;
    dead_letter: number;
    waiting_content: number;
    configured_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute: number;
  };
  recent_news: News[];
  news_evidence: NewsEvidence[];
  news_evidence_summary: {
    policy_version: string;
    total_events: number;
    displayed_events: number;
    broad_model_eligible: number;
    grades: Record<string, number>;
    topics: Record<string, number>;
  };
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
  learning_curves: {
    collection_epoch: string | null;
    evaluation_epoch_v2: string | null;
    legacy_engineering_rows: number;
    repaired_seed_rows: number;
    live_oos_rows: number;
    raw_matured_rows: number;
    effective_30m_blocks: number;
    distinct_trading_days: number;
    news_exposed_rows: number;
    distinct_news_clusters: number;
    learning_stage: string;
    current_preview_version: string | null;
    current_shadow_version: string | null;
    next_training_threshold: number;
    commission_status: string;
    slippage_status: string;
    models: LearningModel[];
    rolling_processes: RollingProcess[];
    identity_curves: Array<{ model_identity: string; points: Array<{ decision_time: string; cumulative_quote_return: number }> }>;
    zero_return_baseline: {
      label: string;
      model_identity: string;
      cumulative_quote_return: number;
      trained: boolean;
      uses_ai: boolean;
    };
    disclaimer: string;
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
  market_chart: {
    candles: Array<{ time: string; open: number; high: number; low: number; close: number; ticks: number }>;
    decisions: Array<{
      source_decision_id: string;
      decision_time: string;
      exit_time: string;
      model_identity: string;
      recommended_action: string;
      outcome_status: string;
      predicted_direction_u5: number | null;
      ev_long_u5: number | null;
      ev_short_u5: number | null;
      lcb_long_u5: number | null;
      lcb_short_u5: number | null;
    }>;
    training_markers: Array<{ model_identity: string; model_version: string; created_at: string; training_rows: number }>;
  };
};

const time = (value?: string | null) => value ? new Intl.DateTimeFormat("zh-CN", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
}).format(new Date(value)) : "—";
const number = (value?: number | null, digits = 2) => value === null || value === undefined ? "—" : value.toFixed(digits);
const percent = (value?: number | null) => value === null || value === undefined ? "—" : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;
const impulse = (value?: number | null) => value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
const NEWS_PER_PAGE = 12;
const CATEGORY_ORDER = ["战争/地缘", "利率/Fed", "央行购金", "通胀/就业", "增长/经济", "油价/能源", "美元/流动性", "风险偏好", "监管/其他", "其他"];
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
const MODEL_LABELS: Record<string, string> = {
  CHAMPION_0: "零收益安全基准",
  MARKET_ONLY: "黄金自身 Ridge",
  NEWS_RESIDUAL: "新闻残差 Ridge",
  FULL: "黄金＋新闻 Ridge",
  BROAD_NEWS_RESIDUAL: "大视野新闻残差 Ridge",
  BROAD_FULL: "黄金＋大视野新闻 Ridge",
};
const TOPIC_LABELS: Record<string, string> = {
  rates_fed: "利率 / Fed", inflation: "通胀", employment: "就业", inflation_employment: "通胀 / 就业",
  growth_economy: "增长 / 经济", usd_liquidity: "美元 / 流动性",
  oil_energy: "油价 / 能源", war_geopolitics: "战争 / 地缘",
  central_bank_gold: "央行购金", risk_sentiment: "风险偏好", regulation_other: "监管 / 其他",
};
const EVIDENCE_LABELS: Record<string, string> = {
  PRIMARY: "一手官方证据", CORROBORATED: "多源确认",
  SINGLE_RELIABLE: "单一可靠来源", DISCOVERY_ONLY: "线索来源",
};

export default function AuditPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"news" | "evidence" | "decisions" | "league" | "coverage">("news");
  const [newsCategory, setNewsCategory] = useState("全部");
  const [newsPage, setNewsPage] = useState(1);
  const [graphOpen, setGraphOpen] = useState(false);

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
      if (requested === "news" || requested === "evidence" || requested === "decisions" || requested === "league" || requested === "coverage") {
        setView(requested);
      }
      refresh();
    }, 0);
    const interval = window.setInterval(refresh, 15_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh]);

  const selectView = (next: "news" | "evidence" | "decisions" | "league" | "coverage") => {
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
  const activeLearningModels = (payload?.learning_curves.models ?? []).filter(
    row => row.active_rank !== null,
  );
  const archivedModelCount = (payload?.learning_curves.models.length ?? 0) - activeLearningModels.length;

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
          <span>LEARNING PROGRESS</span>
          <strong>{payload?.training.complete_rows ?? 0}<small> / {payload?.training.next_training_at ?? 200}</small></strong>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <p>96 行生成 Preview，200 行生成 Shadow；Champion 永远由你批准。</p>
        </div>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="annotation-queue" aria-label="Gemini 摘要队列">
        <strong>Gemini 完整正文摘要</strong>
        <span><b>{payload?.annotation_queue.ready ?? 0}</b> 已完成</span>
        <span><b>{payload?.annotation_queue.queued ?? 0}</b> 排队中</span>
        <span><b>{payload?.annotation_queue.backing_off ?? 0}</b> 退避中</span>
        <span><b>{payload?.annotation_queue.dead_letter ?? 0}</b> 已隔离</span>
        <span><b>{payload?.annotation_queue.waiting_content ?? 0}</b> 等待正文</span>
        <small>完整正文由 Gemini 3.5 Flash-Lite 处理：本机已启用 {payload?.annotation_queue.configured_key_count ?? 0} 个 key，每个安全使用 {payload?.annotation_queue.requests_per_minute_per_key ?? 12} RPM；失败项目持久退避，相同永久错误会隔离。标题中文翻译由 Gemma 4 31B 分流，且不进入训练。</small>
      </section>

      <nav className="audit-tabs" aria-label="审计视图">
        <a href="/audit?view=news" className={view === "news" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("news"); }}>新闻与 Gemini <b>{payload?.counts.latest_news_items ?? 0}</b></a>
        <a href="/audit?view=evidence" className={view === "evidence" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("evidence"); }}>新闻证据管理 <b>{payload?.news_evidence_summary.broad_model_eligible ?? 0}</b></a>
        <a href="/audit?view=decisions" className={view === "decisions" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("decisions"); }}>决策与30分钟结果 <b>{payload?.counts.decision_events ?? 0}</b></a>
        <a href="/audit?view=league" className={view === "league" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("league"); }}>Live OOS 学习曲线 <b>{activeLearningModels.length}</b></a>
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
              <div className="news-row-stamp"><b>{row.category}</b><time>{time(row.source_published_time)}</time><small className={`eligibility-badge eligibility-${row.model_visibility.toLowerCase().replaceAll("_", "-")}`}>{row.model_visibility.replaceAll("_", " ")}</small></div>
              <div className="news-row-title"><strong>{row.headline}</strong><small>{SOURCE_LABELS[row.source] ?? row.source.replaceAll("_", " ")}{row.headline !== row.original_headline ? " · Gemini 中文标题" : " · 等待标题翻译"}{row.emerging_topic_zh ? ` · ${row.emerging_topic_zh}` : ""}</small></div>
              <div className={`news-row-state state-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
                <b>{row.content_status === "FULL_TEXT" ? `${row.content_characters.toLocaleString()} 字符` : row.source === "google_news_gold_geopolitics" ? "聚合标题" : "等待正文"}</b>
                <small>{row.annotation_status === "READY" ? "Gemini 已完成" : row.annotation_status === "QUEUED" ? "Gemini 排队中" : row.annotation_status === "BACKING_OFF" ? "失败退避中" : row.annotation_status === "DEAD_LETTER" ? "已隔离待审" : "禁止判断"}</small>
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
              </section> : row.annotation_status === "BACKING_OFF" ? <section className="gemini-summary summary-queued">
                <span>暂时退避</span><p>本次模型响应未通过验证；系统已停止每分钟重试，将在退避到期后有限重试。</p>
              </section> : row.annotation_status === "DEAD_LETTER" ? <section className="gemini-summary summary-waiting">
                <span>已隔离</span><p>相同永久错误重复出现，系统不会再自动消耗 Flash 配额；该新闻保留在 Ledger 中等待规则修复或人工复核。</p>
              </section> : <section className="gemini-summary summary-waiting">
                <span>等待来源正文</span><p>当前只有标题或短描述，不会进入模型，也不会假装已经理解内容。</p>
              </section>}
              {row.event_type && <div className="news-classification"><b>{row.event_type}</b><span>鹰派 {impulse(row.hawkishness)}</span><span>通胀 {impulse(row.inflation_impulse)}</span><span>增长 {impulse(row.growth_impulse)}</span><span>地缘 {impulse(row.geopolitical_risk)}</span><span>美元 {impulse(row.usd_impulse)}</span><span>新颖 {number(row.novelty)}</span><span>置信 {number(row.confidence)}</span></div>}
              <dl className="news-timeline"><div><dt>Publisher time</dt><dd>{time(row.source_published_time)}</dd></div><div><dt>First seen</dt><dd>{time(row.collector_first_seen_time)}</dd></div><div><dt>Parsed at</dt><dd>{time(row.parsed_at)}</dd></div><div><dt>Collection delay</dt><dd>{row.collection_delay_seconds === null ? "—" : `${number(row.collection_delay_seconds, 1)}s`}</dd></div><div><dt>Processing delay</dt><dd>{row.processing_delay_seconds === null ? "—" : `${number(row.processing_delay_seconds, 1)}s`}</dd></div><div><dt>Eligibility</dt><dd>{row.source_eligibility} · {row.model_visibility}</dd></div></dl>
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

      {view === "evidence" && <section className="evidence-desk">
        <header className="evidence-intro">
          <div><p className="eyebrow">EVENT-LEVEL NEWS EVIDENCE</p><h2>来源不是权限，<br />证据强度才是。</h2></div>
          <p>同一事件先按主题、实体和首次可见时间聚合。一手官方正文可直接进入大视野实验模型；媒体报道必须由至少两个独立可靠 publisher 相互确认。单一来源和聚合标题继续显示，但没有训练权限。</p>
        </header>
        <div className="evidence-summary">
          <article><span>事件总数</span><strong>{payload?.news_evidence_summary.total_events ?? 0}</strong><small>显示最近 {payload?.news_evidence_summary.displayed_events ?? 0} 个</small></article>
          <article><span>允许进入 Broad</span><strong>{payload?.news_evidence_summary.broad_model_eligible ?? 0}</strong></article>
          <article><span>一手官方</span><strong>{payload?.news_evidence_summary.grades.PRIMARY ?? 0}</strong></article>
          <article><span>多源确认</span><strong>{payload?.news_evidence_summary.grades.CORROBORATED ?? 0}</strong></article>
        </div>
        <div className="evidence-table-wrap"><table className="evidence-table">
          <thead><tr><th>证据等级 / 权限</th><th>事件与主题</th><th>独立来源</th><th>首次可见</th></tr></thead>
          <tbody>{(payload?.news_evidence ?? []).map(row => <tr key={row.event_key}>
            <td><span className={`evidence-grade grade-${row.evidence_grade.toLowerCase().replaceAll("_", "-")}`}>{EVIDENCE_LABELS[row.evidence_grade] ?? row.evidence_grade}</span><small>{row.broad_model_eligible ? "可进入 Broad 实验模型" : "仅显示，不进入训练"}</small></td>
            <td><strong>{row.canonical_headline}</strong><div className="evidence-topics">{row.topics.map(topic => <span key={topic}>{TOPIC_LABELS[topic] ?? topic}</span>)}</div></td>
            <td><strong>{row.independent_publishers}</strong><small>{[...row.source_names, ...row.publisher_domains].join(" · ") || "未识别 publisher"}<br />{row.member_count} 篇成员新闻</small></td>
            <td><time>{time(row.collector_first_seen_time)}</time><small>{row.reason_codes.join(" · ")}</small></td>
          </tr>)}</tbody>
        </table></div>
      </section>}

      {view === "decisions" && <section className="decision-audit">
        {(payload?.recent_decisions ?? []).map((row) => {
          const full = row.predictions.find(item => item.model_identity === "BROAD_FULL")
            ?? row.predictions.find(item => item.model_identity === "FULL");
          return <details className="decision-row" key={row.decision_id}>
            <summary>
              <time>{time(row.decision_time)}</time><b>{row.effective_action}</b>
              <span>{number(row.bid)} / {number(row.ask)}</span>
              <em>Full建议 {full?.recommended_action ?? "WAIT"}</em>
              <strong className={row.outcome_status === "VALID" ? "good" : "muted"}>{row.outcome_status === "VALID" ? `Long ${percent(row.long_return)} · Short ${percent(row.short_return)}` : "等待30分钟结果"}</strong>
            </summary>
            <div className="prediction-grid">
              {row.predictions.map(model => <article key={model.model_version}>
                <span>{MODEL_LABELS[model.model_identity] ?? model.model_identity}</span><h3>{model.recommended_action}</h3>
                <p>{model.prediction_status}</p>
                <dl><div><dt>方向 U5</dt><dd>{number(model.predicted_direction_u5, 3)}</dd></div><div><dt>News residual</dt><dd>{number(model.predicted_news_residual_u5, 3)}</dd></div><div><dt>Long EV</dt><dd>{number(model.ev_long_u5, 3)}</dd></div><div><dt>Short EV</dt><dd>{number(model.ev_short_u5, 3)}</dd></div><div><dt>不确定性</dt><dd>{number(model.uncertainty_u5, 3)}</dd></div></dl>
                <small>{model.model_version}</small>
              </article>)}
            </div>
          </details>;
        })}
      </section>}

      {view === "league" && <section className="shadow-league">
        <header className="league-intro">
          <div><p className="eyebrow">LIVE OOS LEARNING CURVES</p><h2>每次训练是一组，<br />只看它之后没见过的数据。</h2></div>
          <dl>
            <div><dt>Collection started</dt><dd>{time(payload?.learning_curves.collection_epoch)}</dd></div>
            <div><dt>Evaluation V2 started</dt><dd>{time(payload?.learning_curves.evaluation_epoch_v2)}</dd></div>
            <div><dt>当前证据等级</dt><dd>{payload?.learning_curves.learning_stage ?? "ENGINEERING"}</dd></div>
          </dl>
        </header>
        <div className="evidence-lane-grid">
          <article><span>Legacy Engineering</span><strong>{payload?.learning_curves.legacy_engineering_rows ?? 0}</strong><small>只用于修复审计</small></article>
          <article><span>Repaired Seed</span><strong>{payload?.learning_curves.repaired_seed_rows ?? 0}</strong><small>可训练，不计 Live OOS</small></article>
          <article><span>Live OOS</span><strong>{payload?.learning_curves.live_oos_rows ?? 0}</strong><small>模型上线后的真实前向结果</small></article>
          <article><span>30m Blocks / Days</span><strong>{payload?.learning_curves.effective_30m_blocks ?? 0} / {payload?.learning_curves.distinct_trading_days ?? 0}</strong><small>置信区间的独立证据</small></article>
          <article><span>News exposure</span><strong>{payload?.learning_curves.news_exposed_rows ?? 0}</strong><small>{payload?.learning_curves.distinct_news_clusters ?? 0} 个可见 cluster</small></article>
          <article><span>Next fit</span><strong>{payload?.learning_curves.next_training_threshold ?? 96}</strong><small>Preview 96 · Shadow 200 · 后续每50</small></article>
        </div>
        <div className="league-cost-note"><b>诚实成本口径</b><span>显示的是 Bid/Ask quote-cost-adjusted return；commission {payload?.learning_curves.commission_status ?? "UNCONFIGURED"}，slippage {payload?.learning_curves.slippage_status ?? "UNAVAILABLE_SHADOW"}，因此不是 net PnL。</span></div>
        <section className="graph-launch">
          <div><span>ONE TIMELINE · THREE VIEWS</span><h3>不要再对着两套数字猜。</h3><p>长期累计、最新版/前一版，以及 XAUUSD K线上的 Long / Wait / Short 与固定30分钟退出，都在同一个弹窗里按时间对齐。</p></div>
          <button type="button" onClick={() => setGraphOpen(true)}>打开交互图表 ↗</button>
        </section>
        <section className="model-scope-note">
          <article><b>新闻残差</b><span>只使用 Fed、BEA、U.S. Treasury 等冻结的核心官方正文，范围窄、来源确定。</span></article>
          <article><b>大视野新闻残差</b><span>加入 EIA、ECB、World Gold Council 及经过一手或多源确认的可靠事件；单一线索和聚合标题仍禁止训练。</span></article>
        </section>
        {(payload?.learning_curves.models.length ?? 0) === 0 ? <div className="league-empty">
          <strong>正在建立第一版 Preview</strong><p>达到 96 条修复或 Forward 完整样本即可训练 Market Preview，不需要等待60天。曲线只从模型创建后的新 Decision 开始，绝不回填假历史成绩。</p>
        </div> : <div className="compact-model-summary">{Object.keys(MODEL_LABELS).filter(identity => identity !== "CHAMPION_0").map(identity => {
          const latest = activeLearningModels.find(row => row.model_identity === identity && row.lifecycle_status === "LATEST");
          const previous = activeLearningModels.find(row => row.model_identity === identity && row.lifecycle_status === "PREVIOUS");
          if (!latest && !previous) return null;
          const score = (row?: LearningModel) => !row ? "—" : row.subsequent_oos_rows ? percent(row.cumulative_quote_return) : "等待结果";
          return <article key={identity}><b>{MODEL_LABELS[identity]}</b><span>前一版 <strong>{score(previous)}</strong></span><i aria-hidden="true">→</i><span>最新版 <strong>{score(latest)}</strong></span></article>;
        })}</div>}
        <footer className="league-footer">{payload?.learning_curves.disclaimer ?? "早期曲线用于观察学习过程，不代表已证明盈利。"} 单日和双日新闻模型明确标记 EXPERIMENTAL；达到3个新闻日期后自动进入标准证据状态。当前只运行每个 Ridge 身份的最新版和前一版；{archivedModelCount} 个旧版本的 artifact、预测和成绩已永久归档。零收益安全基准不训练、不使用 AI、不占 Ridge 版本名额。Preview 与 Shadow 都没有下单权限，也不会自动晋升。</footer>
        <LearningGraphModal open={graphOpen} onClose={() => setGraphOpen(false)} curves={payload?.learning_curves.identity_curves ?? []} models={activeLearningModels} market={payload?.market_chart} />
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
