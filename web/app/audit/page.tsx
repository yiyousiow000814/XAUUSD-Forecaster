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
  research_action?: string | null;
  research_status?: string | null;
  data_health: string;
  bid: number | null;
  ask: number | null;
  outcome_status: string | null;
  outcome_reason_codes: string[];
  long_return: number | null;
  short_return: number | null;
  long_mfe: number | null;
  long_mae: number | null;
  predictions: Prediction[];
};

type News = {
  detail_key: string;
  category: string;
  source: string;
  source_item_id: string;
  revision_number: number;
  source_published_time: string | null;
  collector_first_seen_time: string;
  headline: string;
  original_headline?: string;
  content_characters: number;
  content_status: "FULL_TEXT" | "SOURCE_CONTENT" | "HEADLINE_ONLY";
  summary_zh?: string | null;
  annotation_status: "READY" | "QUEUED" | "BACKING_OFF" | "DEAD_LETTER" | "WAITING_CONTENT";
  link?: string;
  event_type?: string | null;
  entities?: string[];
  hawkishness?: number | null;
  inflation_impulse?: number | null;
  growth_impulse?: number | null;
  geopolitical_risk?: number | null;
  usd_impulse?: number | null;
  novelty?: number | null;
  confidence?: number | null;
  llm_model_version?: string | null;
  prompt_version?: string | null;
  parsed_at?: string | null;
  fetched_time?: string;
  collection_delay_seconds?: number | null;
  processing_delay_seconds?: number | null;
  source_eligibility?: string;
  model_visibility: string;
  eligibility_version?: string;
  primary_category?: string | null;
  secondary_categories?: string[];
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
type Storyline = {
  storyline_id: string; title: string; family: string; family_label: string; state: string; event_count: number;
  reliable_event_count: number; latest_change: string; last_updated: string;
  topics: string[]; model_permission: "DISPLAY_ONLY";
  covered_roles: Array<{ key: string; label: string }>;
  missing_roles: Array<{ key: string; label: string }>;
  coverage_count: number; coverage_total: number;
  candidate_sources: Array<{ candidate: string; suggested_role: string; reason: string; status: string; adapter: string }>;
  state_deltas: Record<string, number>;
  timeline: Array<{ event_key: string; first_seen: string; headline: string;
    evidence_grade: string; independent_publishers: number; relation: string; topics: string[] }>;
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
  history_cutoff?: string | null;
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
type VersionGroup = {
  model_identity: string; training_dataset_hash: string; generation: number;
  lifecycle_status: "LATEST" | "PREVIOUS" | "ARCHIVED"; created_at: string;
  latest_rebuild_at: string; training_rows: number; artifact_rebuilds: number;
  model_versions: string[]; subsequent_oos_rows: number; distinct_days: number;
  cumulative_quote_return: number; profit_factor_quote_adjusted: number | null;
  coverage_rate: number | null; average_oracle_regret: number | null;
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
  storylines: Storyline[];
  storyline_summary: { policy_version: string; total: number; display_only: boolean };
  news_feature_policy: {
    maximum_current_age_hours: number;
    freshness_half_life_hours: number;
    historical_training_rows_retained: boolean;
    point_in_time_cutoff: boolean;
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
    outcome_quality: { valid: number; invalid: number; reason_counts: Record<string, number> };
    news_exposed_rows: number;
    distinct_news_clusters: number;
    learning_stage: string;
    current_preview_version: string | null;
    current_shadow_version: string | null;
    next_training_threshold: number;
    training_generation_count: number;
    training_run_count: number;
    recovery_rebuild_count: number;
    commission_status: string;
    slippage_status: string;
    models: LearningModel[];
    version_groups: VersionGroup[];
    rolling_processes: RollingProcess[];
    identity_curves: Array<{ model_identity: string; points: Array<{ decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number }> }>;
    zero_return_baseline: {
      label: string;
      model_identity: string;
      cumulative_quote_return: number;
      trained: boolean;
      uses_ai: boolean;
    };
    disclaimer: string;
  };
  execution_learning: {
      shadow_only: boolean;
      source_model_identity: string;
      source_model_label: string;
      training_contract: string;
    lot_candidates: number[];
    exit_checkpoints_minutes: number[];
      models: Array<{ model_identity: string; status: string; training_rows: number;
        training_decisions?: number; training_observations?: number;
      available_examples: number; next_training_threshold: number;
      model_version: string | null; predictions: number; scores: number;
      evaluation: {
          score_count: number; selected_cumulative_return?: number;
          baseline_cumulative_return?: number; delta_cumulative_return?: number; unit: string;
          chart_source_count?: number; chart_point_count?: number; chart_downsampled?: boolean;
          points: Array<Record<string, string | number>>;
          results?: Array<Record<string, string | number>>;
      } }>;
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
    training_markers: Array<{ model_identity: string; training_dataset_hash: string; created_at: string; training_rows: number; artifact_count: number }>;
    decision_resource?: string;
  };
};

const time = (value?: string | null) => value ? new Intl.DateTimeFormat("zh-CN", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
}).format(new Date(value)) : "—";
const number = (value?: number | null, digits = 2) => value === null || value === undefined ? "—" : value.toFixed(digits);
const percent = (value?: number | null) => value === null || value === undefined ? "—" : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;
const outcomeReason = (codes: string[]) => codes.some(code => code.includes("CLOCK_AHEAD"))
  ? "服务器报价时钟与本机接收钟偏差过大，样本已隔离"
  : codes.includes("NO_ENTRY_RECEIVED_WITHIN_EXPIRY")
    ? "20秒有效期内没有收到可执行报价，样本已隔离"
    : codes.includes("NO_EXIT_RECEIVED_AFTER_HORIZON")
      ? "30分钟后没有收到退出报价，样本已隔离"
      : "报价证据不完整，样本已隔离且不进入训练";
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
  NEWS_RESIDUAL: "新闻修正量 Ridge",
  FULL: "黄金＋新闻 Ridge",
  BROAD_NEWS_RESIDUAL: "大视野新闻修正量 Ridge",
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

function NewsRow({ row, keyCount, requestsPerMinute }: {
  row: News; keyCount: number; requestsPerMinute: number;
}) {
  const [detail, setDetail] = useState<Partial<News> | null>(
    row.summary_zh !== undefined ? row : null,
  );
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">(
    row.summary_zh !== undefined ? "ready" : "idle",
  );
  const current = { ...row, ...(detail ?? {}) };
  const translated = Boolean(
    current.original_headline && current.headline !== current.original_headline,
  );
  const loadDetail = async (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || detailState === "loading" || detailState === "ready") return;
    if (!row.detail_key) {
      setDetailState("error");
      return;
    }
    setDetailState("loading");
    try {
      const response = await fetch(`/api/news-content?key=${encodeURIComponent(row.detail_key)}`, {
        cache: "no-store",
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      setDetail(body.payload);
      setDetailState("ready");
    } catch {
      setDetailState("error");
    }
  };
  return <details className="news-row" onToggle={loadDetail}>
    <summary>
      <div className="news-row-stamp"><b>{row.category}</b><time title="系统第一次收到这条新闻的时间">收到 {time(row.collector_first_seen_time)}</time><small className={`eligibility-badge eligibility-${row.model_visibility.toLowerCase().replaceAll("_", "-")}`}>{row.model_visibility.replaceAll("_", " ")}</small></div>
      <div className="news-row-title"><strong>{row.headline}</strong><small>{SOURCE_LABELS[row.source] ?? row.source.replaceAll("_", " ")}{translated ? " · Gemini 中文标题" : ""}{row.emerging_topic_zh ? ` · ${row.emerging_topic_zh}` : ""}</small></div>
      <div className={`news-row-state state-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
        <b>{row.content_status === "FULL_TEXT" ? `${row.content_characters.toLocaleString()} 字符` : row.source === "google_news_gold_geopolitics" ? "聚合标题" : "等待正文"}</b>
        <small>{row.annotation_status === "READY" ? "Gemini 已完成" : row.annotation_status === "QUEUED" ? "Gemini 排队中" : row.annotation_status === "BACKING_OFF" ? "失败退避中" : row.annotation_status === "DEAD_LETTER" ? "已隔离待审" : "禁止判断"}</small>
      </div>
    </summary>
    <div className="news-row-detail">
      {detailState === "loading" ? <section className="gemini-summary summary-loading"><span>正在读取新闻详情</span><p>列表与正文详情分开保存，这里只加载你点开的这一条。</p></section>
      : detailState === "error" ? <section className="gemini-summary summary-waiting"><span>详情同步中</span><p>新闻索引已经到达网页，正文摘要仍在同步；稍后重新点开即可。</p></section>
      : <>
        <div className="news-detail-top">
          <div className={`content-proof content-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
            {row.content_status === "FULL_TEXT" ? `✓ 已读取正式正文 · ${row.content_characters.toLocaleString()} 字符` : row.content_status === "SOURCE_CONTENT" ? `已读取来源内容 · ${row.content_characters.toLocaleString()} 字符` : row.source === "google_news_gold_geopolitics" ? "Google News RSS 只提供聚合标题 · 未取得 publisher 正文" : "来源正文尚未抓取 · 禁止 Gemini 判断"}
          </div>
          {current.link && <a className="source-link" href={current.link} target="_blank" rel="noreferrer">阅读来源 ↗</a>}
        </div>
        {translated ? <p className="original-headline"><b>原文标题</b>{current.original_headline}</p> : null}
        {row.annotation_status === "READY" ? <section className="gemini-summary">
          <span>GEMINI 中文摘要 · 完整读取 {row.content_characters.toLocaleString()} 字符</span><p>{current.summary_zh}</p>
        </section> : row.annotation_status === "QUEUED" ? <section className="gemini-summary summary-queued">
          <span>FLASH-LITE 摘要排队中</span><p>正文已经完整入库，不会截断。系统正通过 {keyCount} 个 key 轮换，每分钟最多生成 {requestsPerMinute} 篇中文摘要；标题翻译会独立交给 Gemma。</p>
        </section> : row.annotation_status === "BACKING_OFF" ? <section className="gemini-summary summary-queued">
          <span>暂时退避</span><p>本次模型响应未通过验证；系统已停止每分钟重试，将在退避到期后有限重试。</p>
        </section> : row.annotation_status === "DEAD_LETTER" ? <section className="gemini-summary summary-waiting">
          <span>已隔离</span><p>相同永久错误重复出现，系统不会再自动消耗 Flash 配额；该新闻保留在 Ledger 中等待规则修复或人工复核。</p>
        </section> : <section className="gemini-summary summary-waiting">
          <span>等待来源正文</span><p>当前只有标题或短描述，不会进入模型，也不会假装已经理解内容。</p>
        </section>}
        {current.event_type && <div className="news-classification"><b>{current.event_type}</b><span>鹰派 {impulse(current.hawkishness)}</span><span>通胀 {impulse(current.inflation_impulse)}</span><span>增长 {impulse(current.growth_impulse)}</span><span>地缘 {impulse(current.geopolitical_risk)}</span><span>美元 {impulse(current.usd_impulse)}</span><span>新颖 {number(current.novelty)}</span><span>置信 {number(current.confidence)}</span></div>}
        <dl className="news-timeline"><div><dt>媒体发布时间</dt><dd>{time(row.source_published_time)}</dd></div><div><dt>系统首次收到</dt><dd>{time(row.collector_first_seen_time)}</dd></div><div><dt>Gemini 完成时间</dt><dd>{time(current.parsed_at)}</dd></div><div><dt>采集延迟</dt><dd>{current.collection_delay_seconds == null ? "—" : `${number(current.collection_delay_seconds, 1)} 秒`}</dd></div><div><dt>处理延迟</dt><dd>{current.processing_delay_seconds == null ? "—" : `${number(current.processing_delay_seconds, 1)} 秒`}</dd></div><div><dt>模型权限</dt><dd>{current.source_eligibility ?? "—"} · {row.model_visibility}</dd></div></dl>
        <footer className="card-footer"><span>{current.entities?.join(" · ") || "无实体"}</span><span>{current.llm_model_version ?? "未标注"} · 收到 {time(row.collector_first_seen_time)} · 标注 {time(current.parsed_at)}</span></footer>
      </>}
    </div>
  </details>;
}

export default function AuditPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"news" | "evidence" | "stories" | "decisions" | "league" | "coverage">("news");
  const [newsCategory, setNewsCategory] = useState("全部");
  const [newsPage, setNewsPage] = useState(1);
  const [graphOpen, setGraphOpen] = useState(false);
  const [graphStartTab, setGraphStartTab] = useState<"curve" | "execution">("curve");

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
      if (requested === "news" || requested === "evidence" || requested === "stories" || requested === "decisions" || requested === "league" || requested === "coverage") {
        setView(requested);
      }
      refresh();
    }, 0);
    const interval = window.setInterval(refresh, 15_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh]);

  const selectView = (next: "news" | "evidence" | "stories" | "decisions" | "league" | "coverage") => {
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
  const activeLearningIdentities = new Set(
    activeLearningModels.map(row => row.model_identity),
  ).size;
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
        <a href="/audit?view=stories" className={view === "stories" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("stories"); }}>事件故事链 <b>{payload?.storyline_summary.total ?? 0}</b></a>
        <a href="/audit?view=decisions" className={view === "decisions" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("decisions"); }}>决策与30分钟结果 <b>{payload?.counts.decision_events ?? 0}</b></a>
        <a href="/audit?view=league" className={view === "league" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("league"); }}>Live OOS 学习曲线 <b>{activeLearningIdentities}组</b></a>
        <a href="/audit?view=coverage" className={view === "coverage" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("coverage"); }}>大视野覆盖 <b>{payload?.factor_coverage.filter(row => row.status === "LIVE" || row.status === "COLLECTING").length ?? 0}/11</b></a>
      </nav>

      {view === "news" && <>
        <section className="news-browser" aria-label="新闻自动分类">
          <div><strong>自动分类</strong><span>按系统首次收到排序 · 最新版本共 {payload?.counts.latest_news_items ?? 0} 条 · 每页 {NEWS_PER_PAGE} 条</span></div>
          <nav>
            {categories.map(category => <button key={category.name} type="button" className={newsCategory === category.name ? "active" : ""} onClick={() => { setNewsCategory(category.name); setNewsPage(1); }}>
              {category.name}<b>{category.count}</b>
            </button>)}
          </nav>
        </section>
        <section className="news-table">
          <header className="news-table-head"><span>分类 / 首次收到</span><span>新闻与来源</span><span>正文 / Gemini</span></header>
          {visibleNews.map(row => <NewsRow
            key={`${row.source}-${row.source_item_id}-${row.revision_number}`}
            row={row}
            keyCount={payload?.annotation_queue.configured_key_count ?? 0}
            requestsPerMinute={payload?.annotation_queue.requests_per_minute ?? 0}
          />)}
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
          <p>同一事件先按主题、实体和首次可见时间聚合。一手官方正文可直接进入大视野实验模型；媒体报道必须由至少两个独立可靠 publisher 相互确认。当前决策最多回看 <b>{payload?.news_feature_policy.maximum_current_age_hours ?? 72} 小时</b>，每 {payload?.news_feature_policy.freshness_half_life_hours ?? 6} 小时权重减半；更旧新闻仍保留为当时历史样本，但不会伪装成今天的信号。</p>
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

      {view === "stories" && <section className="story-desk">
        <header className="evidence-intro"><div><p className="eyebrow">TEMPORAL EVENT GRAPH · RESEARCH ONLY</p><h2>不再数新闻，<br />而是看故事发生了什么变化。</h2></div><p>系统按<b>事件家族、实体和首次可见时间</b>组装故事，再按来源角色模板检查官方确认、现场影响、独立确认与市场反应。缺少角色时只进入候选来源队列，<b>不会自动授权来源，也不进入 Ridge</b>。</p></header>
        <div className="story-grid">{(payload?.storylines ?? []).map(story => <article key={story.storyline_id}>
          <header><div><span>{story.family_label ? `${story.family_label} · ` : ""}{({ EMERGING:"刚出现", REPORTED:"已有报道", CORROBORATED:"多源确认", OFFICIALLY_CONFIRMED:"官方确认", PHYSICAL_IMPACT_CONFIRMED:"实物影响确认", ESCALATING:"升级中", DEESCALATING:"缓和中", CONTRADICTED:"存在冲突", RESOLVED:"已结束" } as Record<string,string>)[story.state] ?? story.state}</span><h3>{story.title}</h3></div><strong>{story.event_count}<small> 个事件</small></strong></header>
          <p className="story-latest"><b>最新变化</b>{story.latest_change}</p>
          <div className="story-meta"><span>可靠证据 {story.reliable_event_count}</span><span>更新 {time(story.last_updated)}</span><span>来源角色 {story.coverage_count ?? 0}/{story.coverage_total ?? 0}</span></div>
          <section className="story-coverage"><div><b>已覆盖</b>{(story.covered_roles ?? []).length ? story.covered_roles.map(role => <span key={role.key}>{role.label}</span>) : <em>等待 v2 来源角色</em>}</div><div><b>仍缺少</b>{(story.missing_roles ?? []).length ? story.missing_roles.map(role => <span className="missing" key={role.key}>{role.label}</span>) : <em>{story.coverage_total ? "覆盖完整" : "后台同步中"}</em>}</div></section>
          <ol>{story.timeline.slice().reverse().map(item => <li key={item.event_key}><time>{time(item.first_seen)}</time><b>{({ STARTS:"故事开始", FOLLOWED_BY:"随后发生", CONFIRMS:"可靠来源确认", ESCALATES:"风险升级", DEESCALATES:"风险缓和" } as Record<string,string>)[item.relation] ?? item.relation}</b><span>{item.headline}</span><small>{item.evidence_grade} · {item.independent_publishers} 个独立来源</small></li>)}</ol>
          {(story.candidate_sources ?? []).length > 0 && <details className="source-candidate-queue"><summary>候选来源与缺口 <b>{story.candidate_sources.length}</b></summary>{story.candidate_sources.map((item, index) => <div key={`${item.candidate}-${index}`}><strong>{item.candidate}</strong><span>{item.reason}</span><small>{item.status} · {item.adapter}</small></div>)}</details>}
        </article>)}</div>
      </section>}

      {view === "decisions" && <section className="decision-audit">
        {(payload?.recent_decisions ?? []).map((row) => {
          const full = row.predictions.find(item => item.model_identity === "BROAD_FULL")
            ?? row.predictions.find(item => item.model_identity === "FULL");
          return <details className="decision-row" key={row.decision_id}>
            <summary>
              <time>{time(row.decision_time)}</time><b>{row.research_action ?? full?.recommended_action ?? "WAIT"}</b>
              <span>{number(row.bid)} / {number(row.ask)}</span>
              <em>大视野研究预测 {full?.recommended_action ?? row.research_action ?? "WAIT"}</em>
              <strong className={row.outcome_status === "VALID" ? "good" : row.outcome_status ? "bad" : "muted"}>{row.outcome_status === "VALID" ? `Long ${percent(row.long_return)} · Short ${percent(row.short_return)}` : row.outcome_status ? `无效样本 · ${outcomeReason(row.outcome_reason_codes)}` : "等待30分钟结果"}</strong>
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
          <article><span>有效 / 隔离样本</span><strong>{payload?.learning_curves.outcome_quality.valid ?? 0} / {payload?.learning_curves.outcome_quality.invalid ?? 0}</strong><small>隔离样本不评分、不训练；点开 K 线可看具体原因</small></article>
          <article><span>News exposure</span><strong>{payload?.learning_curves.news_exposed_rows ?? 0}</strong><small>{payload?.learning_curves.distinct_news_clusters ?? 0} 个可见 cluster</small></article>
          <article><span>训练数据代 / 运行</span><strong>{payload?.learning_curves.training_generation_count ?? 0} / {payload?.learning_curves.training_run_count ?? 0}</strong><small>{payload?.learning_curves.recovery_rebuild_count ?? 0} 次只是恢复重建，不算新一组</small></article>
          <article><span>Next fit</span><strong>{payload?.learning_curves.next_training_threshold ?? 96}</strong><small>再有 {(payload?.learning_curves.next_training_threshold ?? 96) - (payload?.training.complete_rows ?? 0)} 条成熟数据训练下一组</small></article>
        </div>
        <div className="league-cost-note"><b>诚实成本口径</b><span>显示的是 Bid/Ask quote-cost-adjusted return；commission {payload?.learning_curves.commission_status ?? "UNCONFIGURED"}，slippage {payload?.learning_curves.slippage_status ?? "UNAVAILABLE_SHADOW"}，因此不是 net PnL。</span></div>
        <section className="graph-launch">
          <div><span>ONE TIMELINE · THREE VIEWS</span><h3>曲线、每组成绩与 K 线放在同一弹窗。</h3><p>主页面保持紧凑；点开后可切换长期累计、每个训练组的独立成绩，以及 XAUUSD K线决策。</p></div>
          <button type="button" onClick={() => { setGraphStartTab("curve"); setGraphOpen(true); }}>打开交互图表 ↗</button>
        </section>
        <ExecutionResearch status={payload?.execution_learning} onOpenGraph={() => { setGraphStartTab("execution"); setGraphOpen(true); }} />
        <section className="model-scope-note">
          <article><b>“大视野新闻修正量”不是“大视野新闻自身”</b><span>它先看黄金自身预测错了多少，再学习新闻应该把黄金答案往上或往下修多少。例：黄金自身 +0.10 U5，新闻修正 +0.04 U5，只有“黄金＋大视野新闻”才输出完整方向 +0.14 U5。</span></article>
          <article><b>当前还没有独立的“大视野 News-only”</b><span>真正的 News-only 会完全不读取黄金特征，只用新闻直接预测完整30分钟目标。现在名为“大视野新闻修正量”的曲线不能当作 News-only，也不能单独拿去做完整方向。</span></article>
          <article className="live-method"><b>做法可以实时复现；结果尚未达到实盘标准</b><span>行情只读取决策时已经收到的 Bid/Ask，新闻只读取当时已经首次看见且已完成解析的内容；30分钟结果成熟后才进入下一轮训练，所以方法本身不依赖未来数据。当前仍缺真实 commission、slippage 与下单接口验证，因此这里只能证明“计算方法可在线运行”，不能宣称已有可实盘收益。</span></article>
        </section>
        {(payload?.learning_curves.models.length ?? 0) === 0 ? <div className="league-empty">
          <strong>正在建立第一版 Preview</strong><p>达到 96 条修复或 Forward 完整样本即可训练 Market Preview，不需要等待60天。曲线只从模型创建后的新 Decision 开始，绝不回填假历史成绩。</p>
        </div> : <div className="compact-model-summary">{Object.keys(MODEL_LABELS).filter(identity => identity !== "CHAMPION_0").map(identity => {
          const process = payload?.learning_curves.rolling_processes.find(row => row.model_identity === identity);
          const latestGroup = payload?.learning_curves.version_groups.find(row => row.model_identity === identity && row.lifecycle_status === "LATEST");
          if (!process && !latestGroup) return null;
          const diagnostic = identity === "NEWS_RESIDUAL" || identity === "BROAD_NEWS_RESIDUAL";
          const hasTotal = (process?.oos_rows ?? 0) > 0;
          const hasGroup = (latestGroup?.subsequent_oos_rows ?? 0) > 0;
          const total = hasTotal ? process!.cumulative_quote_return : null;
          const group = hasGroup ? latestGroup!.cumulative_quote_return : null;
          const history = total === null ? null : total - (group ?? 0);
          const tone = group === null ? "is-pending" : group >= 0 ? "is-positive" : "is-negative";
          return <article key={identity}><b>{MODEL_LABELS[identity]}{diagnostic ? <small>新闻修正量</small> : null}</b><div className="return-flow" aria-label={`本组开始前 ${percent(history)}，加入本组后 ${percent(total)}，本组贡献 ${percent(group)}`}><span title="本组开始前的历史累计">{history === null ? "—" : percent(history)}</span><i className={tone} aria-hidden="true">→</i><strong title="加入本组后的连续累计">{total === null ? "等待结果" : percent(total)}</strong><i className="return-separator" aria-hidden="true">·</i><strong className={`group-return ${tone}`} title="本组独立贡献">{group === null ? "等待" : percent(group)}</strong></div></article>;
        })}</div>}
        <footer className="league-footer">{payload?.learning_curves.disclaimer ?? "早期曲线用于观察学习过程，不代表已证明盈利。"} 单日和双日新闻模型明确标记 EXPERIMENTAL；达到3个新闻日期后自动进入标准证据状态。当前只运行每个 Ridge 身份的最新版和前一版；{archivedModelCount} 个旧版本的 artifact、预测和成绩已永久归档。零收益安全基准不训练、不使用 AI、不占 Ridge 版本名额。Preview 与 Shadow 都没有下单权限，也不会自动晋升。</footer>
        <LearningGraphModal key={graphStartTab} open={graphOpen} onClose={() => setGraphOpen(false)} startTab={graphStartTab} curves={payload?.learning_curves.identity_curves ?? []} market={payload?.market_chart} versionGroups={payload?.learning_curves.version_groups ?? []} execution={payload?.execution_learning} />
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

function ExecutionResearch({ status, onOpenGraph }: { status?: Payload["execution_learning"]; onOpenGraph: () => void }) {
  const lot = status?.models.find(row => row.model_identity === "LOT_RIDGE");
  const exit = status?.models.find(row => row.model_identity === "EXIT_RIDGE");
    const card = (title: string, model: typeof lot, detail: string, contract: string) => <article>
    <div className="execution-title"><b>{title}</b><em className={model?.status === "RUNNING" ? "is-running" : ""}>{model?.status === "RUNNING" ? "学习中" : "收集中"}</em></div>
      <strong>{model?.training_decisions ? `已学习 ${model.training_decisions} 个历史方向` : `${model?.available_examples ?? 0} / ${model?.next_training_threshold ?? 0}`}</strong>
      <p>{detail}</p><dl><div><dt>可用方向决策</dt><dd>{model?.available_examples ?? 0}</dd></div><div><dt>未来位置 / 已结算</dt><dd>{model?.predictions ?? 0} / {model?.scores ?? 0}</dd></div><div><dt>下一次训练</dt><dd>{model?.next_training_threshold ?? "—"} 个方向</dd></div></dl><small>{contract}</small>
  </article>;
    return <section className="execution-research"><header><div><span>EXECUTION FOLLOWS LIVE DIRECTION</span><h3>仓位与退出跟随同一套方向。</h3><p>方向固定来自“{status?.source_model_label ?? "黄金＋大视野新闻 Ridge"}”。它当时 LONG 就只研究这笔 LONG；SHORT 同理；WAIT 不创建仓位。</p></div><button type="button" onClick={onOpenGraph}>查看逐笔未来结果 ↗</button></header><div>
      {card("仓位倍率 Ridge", lot, "每个冻结方向只产生一个位置，在 0.5x / 1.0x / 2.0x 中选择倍率。三种倍率分别学习未来效用，不再同时虚构 Long 和 Short。", "Shadow multiplier · 一个方向=一个位置 · 非账户 lot")}
      {card("Exit Ridge", exit, "同一位置按 5 / 10 / 15 / 20 / 25 分钟顺序检查。模型一旦选择 EXIT，这笔位置结束，不会继续制造后续 HOLD / EXIT 评分。", `历史路径检查点 ${exit?.training_observations ?? 0} 个 · 未来评分按位置计数`) }
  </div></section>;
}
