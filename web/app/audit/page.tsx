"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import SystemStatePill from "../_components/SystemStatePill";
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
  content_fetch_status?: "AVAILABLE" | "PENDING" | "RETRYING" | "UNAVAILABLE";
  content_error_type?: string | null;
  summary_zh?: string | null;
  annotation_status: "READY" | "QUEUED" | "BACKING_OFF" | "DEAD_LETTER" | "WAITING_CONTENT" | "CONTENT_UNAVAILABLE" | "NOT_REQUIRED";
  annotation_reason_code?: "DUPLICATE_CONTENT" | "SEARCH_LEAD" | "HISTORICAL_MATERIAL";
  annotation_reason?: string;
  impact_status?: "PENDING_ANNOTATION" | "PENDING_IMPACT" | "ACTIVE" | "EXPIRED_ON_RECEIPT" | "EXPIRED_BEFORE_AVAILABLE" | "EXPIRED" | "DUPLICATE_REPORT" | "COMMENTARY_ONLY" | "HISTORICAL_CONTEXT" | "BACKGROUND" | "MISSING_PUBLICATION_TIME";
  impact_class?: "IMMEDIATE" | "SAME_DAY" | "DATA_RELEASE" | "POLICY_SHIFT" | "ONGOING_EVENT" | "BACKGROUND";
  impact_event_state?: "ACTIVE" | "COMPLETED" | "UNCERTAIN" | "BACKGROUND";
  impact_update_type?: "NEW_EVENT" | "MATERIAL_UPDATE" | "DUPLICATE_REPORT" | "COMMENTARY" | "HISTORICAL_CONTEXT";
  impact_assessed_at?: string | null;
  impact_available_at?: string | null;
  impact_expires_at?: string | null;
  impact_reason_zh?: string | null;
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
  source_published_time: string | null;
  collector_first_seen_time: string;
  economic_age_minutes: number | null;
  freshness_status: string;
  topics?: string[] | null;
  evidence_grade: "PRIMARY" | "CORROBORATED" | "SINGLE_RELIABLE" | "DISCOVERY_ONLY";
  broad_model_eligible: boolean;
  model_permission: "BROAD_MODEL" | "DISPLAY_ONLY";
  member_count: number;
  independent_publishers: number;
  source_names: string[];
  publisher_domains: string[];
  reason_codes: string[];
  model_seen: boolean;
  frozen_model_uses: number;
  frozen_decisions: number;
  first_model_decision_time: string | null;
  last_model_decision_time: string | null;
  model_identities?: string[] | null;
  model_versions: string[];
  model_unseen_reason_codes?: string[] | null;
};
type StoryEvent = { event_key: string; first_seen: string; headline: string;
  event_time: string; actor: string; action: string; object: string; location: string;
  claim_status: string; materiality: number; evidence_grade: string;
  independent_publishers: number; independent_organizations: number; evidence_documents: number;
  document_kinds: string[]; source_published_time: string | null;
  collector_first_seen_time: string; archival: boolean; relation: string };
type Storyline = {
  storyline_id: string; episode_key: string; title: string; state: string; event_count: number;
  evidence_document_count: number; reliable_event_count: number; latest_change: string; last_updated: string;
  model_permission: "DISPLAY_ONLY"; independent_confirmation: boolean;
  story_type: "MATERIAL_EPISODE" | "MARKET_NARRATIVE_CANDIDATE"; archival: boolean;
  coverage_template: string; coverage_count: number; coverage_total: number;
  independent_organization_count: number; source_organizations: string[];
  covered_roles: Array<{ key: string; label: string }>;
  missing_roles: Array<{ key: string; label: string }>;
  timeline: StoryEvent[];
  market_reactions: StoryEvent[];
  commentary: StoryEvent[];
  background: StoryEvent[];
};
type ThemeStream = { theme_id: string; title: string; item_count: number; last_updated: string; latest_headline: string; model_permission: "DISPLAY_ONLY" };
type EventCandidate = { candidate_id: string; episode_key: string; headline: string; first_seen: string; event_time: string; evidence_documents: number; independent_publishers: number; archival: boolean; reason: string; model_permission: "DISPLAY_ONLY" };
type MarketReactionStream = { stream_id: string; title: string; item_count: number; last_updated: string; latest_headline: string; model_permission: "DISPLAY_ONLY" };
type UnassignedStoryEvent = { event_key: string; headline: string; first_seen: string; record_kind: string; reason: string };

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
type NewsModelActivation = {
  model_identity: string;
  status: "ACTIVE" | "LEGACY_ACTIVE" | "GENERATION_WAIT" | "NOT_TRAINED" | "POLICY_MISMATCH" | "ARTIFACT_UNAVAILABLE";
  reason: string;
  model_version: string | null;
  actual_feature_version: string | null;
  actual_eligibility_version: string | null;
  expected_feature_version: string;
  expected_eligibility_version: string;
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
  cadence_metrics?: Record<EvaluationCadence, CadenceMetric>;
};
type VersionGroup = {
  model_identity: string; training_dataset_hash: string; generation: number;
  lifecycle_status: "LATEST" | "PREVIOUS" | "ARCHIVED"; created_at: string;
  latest_rebuild_at: string; training_rows: number; artifact_rebuilds: number;
  model_versions: string[]; subsequent_oos_rows: number; distinct_days: number;
  cumulative_quote_return: number; profit_factor_quote_adjusted: number | null;
  coverage_rate: number | null; average_oracle_regret: number | null;
  cadence_metrics?: Record<EvaluationCadence, CadenceMetric>;
};
type EvaluationCadence = "EVERY_5M" | "FIXED_30M";
type CadenceMetric = { oos_rows: number; distinct_days: number; cumulative_quote_return: number; profit_factor_quote_adjusted: number | null; coverage_rate: number | null };

type Payload = {
  generated_at: string;
  system: { online: boolean; market_session?: "OPEN" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE"; source_of_truth: string; sites_mirror: string; deployment?: { runtime_git_sha: string | null; expected_git_sha: string | null; runtime_dirty: boolean; status: string; storyline_policy_version: string; payload_schema_version: string; payload_generated_at: string; source_database_epoch: string | null } };
  counts: Record<string, number>;
  annotation_queue: {
    ready: number;
    queued: number;
    backing_off: number;
    dead_letter: number;
    waiting_content: number;
    unavailable_content?: number;
    configured_key_count: number;
    requests_per_minute_per_key: number;
    requests_per_minute: number;
  };
  recent_news?: News[];
  news_evidence: NewsEvidence[];
  news_evidence_summary: {
    policy_version: string;
    raw_article_revisions: number;
    distinct_articles: number;
    decision_event_exposures: number;
    total_events: number;
    displayed_events: number;
    broad_model_eligible: number;
    model_seen_events: number;
    model_unseen_events: number;
    frozen_model_uses: number;
    grades: Record<string, number>;
    topics: Record<string, number>;
  };
  storylines: Storyline[];
  market_narrative_candidates: Storyline[];
  archived_storylines: Storyline[];
  archived_story_event_candidates: EventCandidate[];
  story_event_candidates: EventCandidate[];
  market_reaction_streams: MarketReactionStream[];
  theme_streams: ThemeStream[];
  unassigned_story_events: UnassignedStoryEvent[];
  storyline_summary: { policy_version: string; legacy_policy_status: string; total: number; market_narrative_total: number; archived_total: number; candidate_total: number; market_stream_total: number; theme_total: number; unassigned_total: number; display_only: boolean };
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
    active_generation: null | { generation_id: string; training_cutoff: string; policy_version: string; weighting_version: string; member_count: number };
    news_training_evidence: {
      raw_article_revisions: number;
      distinct_articles: number;
      eligible_event_versions: number;
      distinct_eligible_events: number;
      decision_event_exposures: number;
      active_generation_weights: Record<string, { decision_event_exposures: number; effective_event_count: number; maximum_event_weight_share: number | null; total_event_budget: number }>;
    };
    commission_status: string;
    slippage_status: string;
    models: LearningModel[];
    version_groups: VersionGroup[];
    rolling_processes: RollingProcess[];
    news_model_activation: NewsModelActivation[];
    identity_curves: Array<{ model_identity: string; source_point_count?: number; chart_point_count?: number; chart_downsampled?: boolean; points: Array<{ decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number }>; source_point_count_30m?: number; chart_point_count_30m?: number; chart_downsampled_30m?: boolean; points_30m?: Array<{ decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number }> }>;
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
    status_reason?: string | null;
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

type NewsIndexResponse = {
  items: News[];
  total: number;
  all_total: number;
  readable_total?: number;
  parsed_total?: number;
  model_candidate_total?: number;
  category_counts: Record<string, number>;
  page: number;
  page_size: number;
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

const COVERAGE_STATUS_LABELS: Record<string, string> = {
  LIVE: "实时",
  COLLECTING: "监测中",
  WARMING_UP: "等待数据",
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
const EVIDENCE_REASON_LABELS: Record<string, string> = {
  CURRENT_EVENT: "当前事件",
  NOT_YET_VISIBLE: "决策时尚未收到",
  PUBLISHED_TIME_MISSING: "缺少可靠发布时间",
  PRE_FORWARD_PUBLICATION: "系统启动前的旧档案",
  PUBLISHED_AFTER_DECISION: "决策时尚未发布",
  IMPACT_NOT_ASSESSED: "等待 Gemma 判断有效期",
  IMPACT_EXPIRED: "新闻影响期已结束",
  IMPACT_DUPLICATE_REPORT: "重复报道，不延长影响期",
  STALE_EVENT: "发布时间已超过72小时",
  CATEGORY_NOT_ACTIONABLE: "非黄金方向类别",
  NEEDS_CONFIRMATION: "尚未达到模型证据门槛",
  NO_ACTION_TOPIC: "与方向主题无关",
  EVIDENCE_PRIMARY: "一手来源",
  EVIDENCE_CORROBORATED: "多源确认",
  EVIDENCE_SINGLE_RELIABLE: "单一可靠来源",
  EVIDENCE_DISCOVERY_ONLY: "线索来源",
  ELIGIBLE_AWAITING_FROZEN_PREDICTION: "已达模型门槛，等待下一次冻结预测",
  LEGACY_ANNOTATION_SCHEMA: "旧版标注，不进入当前模型",
  RECORD_KIND_NOT_ACTIONABLE: "不是可交易的现实事件",
  EVIDENCE_ROLE_NOT_ACTIONABLE: "证据角色不参与方向学习",
  LOW_MATERIALITY: "事件重要性不足",
};
const DEPLOYMENT_PRESENTATION: Record<string, { className: string; label: string }> = {
  MATCHED: { className: "matched", label: "版本正常" },
  LOCAL_CHANGES: { className: "local-changes", label: "有尚未发布的改动" },
  PROVENANCE_UNKNOWN: { className: "unknown", label: "版本暂时无法核对" },
  DEPLOYMENT_DRIFT: { className: "drift", label: "版本需要更新" },
};
const VISIBILITY_LABELS: Record<string, string> = {
  MODEL_VISIBLE: "可用于模型",
  IMPACT_PENDING: "等待 Gemma",
  IMPACT_EXPIRED: "影响已结束",
  NOT_YET_PARSED: "等待 Gemini",
  WAITING_CONTENT: "等待正文",
  DISPLAY_ONLY: "仅供查看",
  COLLECT_ONLY: "仅收集",
  MODEL_INELIGIBLE: "不可用于模型",
};
const ANNOTATION_REASON_LABELS: Record<string, string> = {
  DUPLICATE_CONTENT: "重复内容",
  SEARCH_LEAD: "搜索线索",
  HISTORICAL_MATERIAL: "历史资料",
};
const IMPACT_STATUS_LABELS: Record<string, string> = {
  PENDING_ANNOTATION: "等待 Gemini 阅读",
  PENDING_IMPACT: "等待 Gemma 判断",
  ACTIVE: "当前仍有效",
  EXPIRED_ON_RECEIPT: "收到时已过期",
  EXPIRED_BEFORE_AVAILABLE: "处理完成前已过期",
  EXPIRED: "影响期已结束",
  DUPLICATE_REPORT: "重复报道",
  COMMENTARY_ONLY: "评论内容",
  HISTORICAL_CONTEXT: "历史资料",
  BACKGROUND: "背景资料",
  MISSING_PUBLICATION_TIME: "缺少发布时间",
};
const IMPACT_CLASS_LABELS: Record<string, string> = {
  IMMEDIATE: "即时影响",
  SAME_DAY: "当日影响",
  DATA_RELEASE: "数据发布",
  POLICY_SHIFT: "政策变化",
  ONGOING_EVENT: "持续事件",
  BACKGROUND: "背景资料",
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
  const annotationStatus = row.annotation_status === "QUEUED"
    && row.model_visibility !== "NOT_YET_PARSED"
    ? "NOT_REQUIRED"
    : row.annotation_status;
  const annotationReasonLabel = ANNOTATION_REASON_LABELS[
    current.annotation_reason_code ?? ""
  ] ?? "无需 AI 解析";
  const impactLabel = IMPACT_STATUS_LABELS[current.impact_status ?? ""];
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
      <div className="news-row-stamp"><b>{row.category}</b><time title="媒体发布时间；列表按此时间排序">发布 {row.source_published_time ? time(row.source_published_time) : "未知"}</time><small title="系统第一次收到；决定模型当时能否看见">收到 {time(row.collector_first_seen_time)}</small><small className={`eligibility-badge eligibility-${row.model_visibility.toLowerCase().replaceAll("_", "-")}`}>{VISIBILITY_LABELS[row.model_visibility] ?? row.model_visibility.replaceAll("_", " ")}</small></div>
      <div className="news-row-title"><strong>{row.headline}</strong><small>{SOURCE_LABELS[row.source] ?? row.source.replaceAll("_", " ")}{translated ? " · Gemini 中文标题" : ""}{row.emerging_topic_zh ? ` · ${row.emerging_topic_zh}` : ""}</small></div>
      <div className={`news-row-state state-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
        <b>{row.content_status === "FULL_TEXT" ? `${row.content_characters.toLocaleString()} 字符` : row.content_fetch_status === "UNAVAILABLE" ? "正文不可用" : row.content_fetch_status === "RETRYING" ? "自动重试中" : row.source === "google_news_gold_geopolitics" ? "聚合标题" : "等待正文"}</b>
        <small>{annotationStatus === "READY" ? (impactLabel ?? "等待 Gemma 判断") : annotationStatus === "NOT_REQUIRED" ? annotationReasonLabel : row.content_fetch_status === "UNAVAILABLE" ? "保留标题 · 不阻塞" : row.content_fetch_status === "RETRYING" ? "备用抓取中" : annotationStatus === "QUEUED" ? "AI 等待处理中" : annotationStatus === "BACKING_OFF" ? "失败后等待重试" : annotationStatus === "DEAD_LETTER" ? "已隔离待审" : "禁止判断"}</small>
      </div>
    </summary>
    <div className="news-row-detail">
      {detailState === "loading" ? <section className="gemini-summary summary-loading"><span>正在读取新闻详情</span><p>列表与正文详情分开保存，这里只加载你点开的这一条。</p></section>
      : detailState === "error" ? <section className="gemini-summary summary-waiting"><span>详情同步中</span><p>新闻索引已经到达网页，正文摘要仍在同步；稍后重新点开即可。</p></section>
      : <>
        <div className="news-detail-top">
          <div className={`content-proof content-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
            {row.content_status === "FULL_TEXT" ? `✓ 已读取正式正文 · ${row.content_characters.toLocaleString()} 字符` : row.content_status === "SOURCE_CONTENT" ? `已读取来源内容 · ${row.content_characters.toLocaleString()} 字符` : row.content_fetch_status === "UNAVAILABLE" ? "发布网站拒绝自动读取或需要登录 · 仅保留标题，不进入模型" : row.content_fetch_status === "RETRYING" ? "首次抓取失败 · 系统将在退避结束后自动重试" : row.source === "google_news_gold_geopolitics" ? "Google News RSS 只提供聚合标题 · 未取得 publisher 正文" : "来源正文尚未抓取 · 禁止 Gemini 判断"}
          </div>
          {current.link && <a className="source-link" href={current.link} target="_blank" rel="noreferrer">阅读来源 ↗</a>}
        </div>
        {translated ? <p className="original-headline"><b>原文标题</b>{current.original_headline}</p> : null}
        {annotationStatus === "READY" ? <section className="gemini-summary">
          <span>GEMINI 中文摘要 · 完整读取 {row.content_characters.toLocaleString()} 字符</span><p>{current.summary_zh}</p>
        </section> : annotationStatus === "QUEUED" ? <section className="gemini-summary summary-queued">
          <span>FLASH-LITE 摘要排队中</span><p>正文已经完整入库，不会截断。系统正通过 {keyCount} 个 key 轮换，每分钟最多生成 {requestsPerMinute} 篇中文摘要；标题翻译会独立交给 Gemma。</p>
        </section> : annotationStatus === "BACKING_OFF" ? <section className="gemini-summary summary-queued">
          <span>暂时退避</span><p>本次模型响应未通过验证；系统已停止每分钟重试，将在退避到期后有限重试。</p>
        </section> : annotationStatus === "DEAD_LETTER" ? <section className="gemini-summary summary-waiting">
          <span>已隔离</span><p>相同永久错误重复出现，系统不会再自动消耗 Flash 配额；该新闻保留在 Ledger 中等待规则修复或人工复核。</p>
        </section> : annotationStatus === "NOT_REQUIRED" ? <section className="gemini-summary summary-queued">
          <span>{annotationReasonLabel}</span><p>{current.annotation_reason ?? "该新闻不满足当前解析条件，不会消耗 AI 配额或进入模型。"}</p>
        </section> : row.content_fetch_status === "UNAVAILABLE" ? <section className="gemini-summary summary-waiting">
          <span>来源正文不可自动读取</span><p>发布网站拒绝访问、要求登录或没有可提取正文；这类候选不会写入新闻库，也不会进入模型。</p>
        </section> : <section className="gemini-summary summary-waiting">
          <span>{row.content_fetch_status === "RETRYING" ? "正文自动重试中" : "等待来源正文"}</span><p>当前只有标题或短描述，不会进入模型，也不会假装已经理解内容。</p>
        </section>}
        {annotationStatus === "READY" && <section className={`gemini-summary ${current.impact_status === "ACTIVE" ? "" : "summary-queued"}`}>
          <span>{impactLabel ?? "等待 Gemma 判断"}{current.impact_class ? ` · ${IMPACT_CLASS_LABELS[current.impact_class] ?? current.impact_class}` : ""}</span>
          <p>{current.impact_reason_zh ?? "Gemma 将根据新闻内容判断它现在是否仍会影响市场。晚收到只影响可见时间，不会改写过去。"}</p>
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
  const [newsIndex, setNewsIndex] = useState<NewsIndexResponse>({
    items: [], total: 0, all_total: 0, category_counts: {}, page: 1, page_size: NEWS_PER_PAGE,
  });
  const [statusState, setStatusState] = useState<"loading" | "ready" | "error">("loading");
  const [learningState, setLearningState] = useState<"loading" | "ready" | "error">("loading");
  const [statusError, setStatusError] = useState<string | null>(null);
  const [learningError, setLearningError] = useState<string | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [view, setView] = useState<"news" | "evidence" | "stories" | "decisions" | "league" | "coverage">("news");
  const [newsCategory, setNewsCategory] = useState("全部");
  const [newsPage, setNewsPage] = useState(1);
  const [graphOpen, setGraphOpen] = useState(false);
  const [graphStartTab, setGraphStartTab] = useState<"curve" | "execution">("curve");
  const [summaryCadence, setSummaryCadence] = useState<EvaluationCadence>("EVERY_5M");
  const [evidenceMode, setEvidenceMode] = useState<"seen" | "unseen" | "all">("seen");
  const auditTabsRef = useRef<HTMLElement>(null);

  const refresh = useCallback(async () => {
    const [statusResult, learningResult] = await Promise.allSettled([
      fetch("/api/status", { cache: "no-store" }).then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
        return body;
      }),
      fetch("/api/learning", { cache: "no-store" }).then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
        return body;
      }),
    ]);

    if (statusResult.status === "fulfilled") {
      setStatusState("ready");
      setStatusError(null);
    } else {
      setStatusState("error");
      setStatusError(statusResult.reason instanceof Error ? statusResult.reason.message : "无法读取系统状态");
    }

    if (learningResult.status === "fulfilled") {
      setLearningState("ready");
      setLearningError(null);
    } else {
      setLearningState("error");
      setLearningError(learningResult.reason instanceof Error ? learningResult.reason.message : "无法读取学习进度");
    }

    const nextPayload = {
      ...(statusResult.status === "fulfilled" ? statusResult.value : {}),
      ...(learningResult.status === "fulfilled" ? learningResult.value : {}),
    };
    if (Object.keys(nextPayload).length > 0) {
      // Publish one coherent snapshot. Rendering either response independently can
      // expose a learning-only or status-only payload and crash another audit tab.
      setPayload(previous => ({ ...previous, ...nextPayload }) as Payload);
    }
  }, []);

  const refreshNews = useCallback(async () => {
    const query = new URLSearchParams({
      page: String(newsPage), limit: String(NEWS_PER_PAGE),
    });
    if (newsCategory !== "全部") query.set("category", newsCategory);
    const response = await fetch(`/api/news-index?${query}`, { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
    setNewsIndex(body);
    setNewsError(null);
  }, [newsCategory, newsPage]);

  useEffect(() => {
    const initial = window.setTimeout(() => {
      const requested = new URLSearchParams(window.location.search).get("view");
      if (requested === "news" || requested === "evidence" || requested === "stories" || requested === "decisions" || requested === "league" || requested === "coverage") {
        setView(requested);
      }
      refresh();
      refreshNews().catch(reason => setNewsError(
        reason instanceof Error ? reason.message : "无法读取新闻索引",
      ));
    }, 0);
    const interval = window.setInterval(() => {
      refresh();
      refreshNews().catch(reason => setNewsError(
        reason instanceof Error ? reason.message : "无法读取新闻索引",
      ));
    }, 15_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh, refreshNews]);

  const selectView = (next: "news" | "evidence" | "stories" | "decisions" | "league" | "coverage") => {
    setView(next);
    window.history.replaceState(null, "", `/audit?view=${next}`);
  };

  useEffect(() => {
    if (!window.matchMedia("(max-width: 850px)").matches) return;
    const nav = auditTabsRef.current;
    const active = nav?.querySelector<HTMLElement>("a.active");
    if (!nav || !active) return;
    nav.scrollTo({
      left: Math.max(0, active.offsetLeft - (nav.clientWidth - active.clientWidth) / 2),
      behavior: "smooth",
    });
  }, [view]);

  const progress = useMemo(() => {
    const training = payload?.training;
    if (!training) return 0;
    return Math.min(100, training.complete_rows / training.next_training_at * 100);
  }, [payload]);

  const categories = useMemo(() => [
    { name: "全部", count: newsIndex.readable_total ?? newsIndex.all_total },
    ...CATEGORY_ORDER.filter(name => newsIndex.category_counts[name]).map(name => ({ name, count: newsIndex.category_counts[name] ?? 0 })),
  ], [newsIndex]);
  const newsPageCount = Math.max(1, Math.ceil(newsIndex.total / NEWS_PER_PAGE));
  const currentNewsPage = Math.min(newsPage, newsPageCount);
  const visibleNews = newsIndex.items;
  const emptyNewsRows = Math.max(0, NEWS_PER_PAGE - visibleNews.length);
  const activeLearningModels = (payload?.learning_curves?.models ?? []).filter(
    row => row.active_rank !== null,
  );
  const activeLearningIdentities = new Set(
    activeLearningModels.map(row => row.model_identity),
  ).size;
  const archivedModelCount = (payload?.learning_curves?.models?.length ?? 0) - activeLearningModels.length;
  const latestVersionGroups = (payload?.learning_curves?.version_groups ?? []).filter(
    row => row.lifecycle_status === "LATEST",
  );
  const directionPoolRows = Math.max(0, ...latestVersionGroups
    .filter(row => !row.model_identity.endsWith("NEWS_RESIDUAL"))
    .map(row => row.training_rows));
  const readableNewsTotal = newsIndex.readable_total ?? newsIndex.all_total;
  const parsedNewsTotal = newsIndex.parsed_total ?? newsIndex.items.filter(row => Boolean(row.parsed_at)).length;
  const modelCandidateNewsTotal = newsIndex.model_candidate_total ?? newsIndex.items.filter(row => row.model_visibility === "MODEL_VISIBLE").length;
  const newsWaitingTotal = (payload?.annotation_queue?.queued ?? 0)
    + (payload?.annotation_queue?.backing_off ?? 0)
    + (payload?.annotation_queue?.dead_letter ?? 0);
  const newsNoParsingNeededTotal = Math.max(0, readableNewsTotal - parsedNewsTotal - newsWaitingTotal);
  const rowsUntilTraining = learningState === "ready" && payload?.training
    ? Math.max(0, payload.training.next_training_at - payload.training.complete_rows)
    : null;
  const combinedErrors = [
    statusError && `系统状态：${statusError}`,
    learningError && `学习进度：${learningError}`,
    newsError && `新闻索引：${newsError}`,
  ].filter(Boolean).join(" · ");
  const visibleEvidence = (payload?.news_evidence ?? []).filter(row => (
    evidenceMode === "all" || (evidenceMode === "seen" ? row.model_seen : !row.model_seen)
  ));
  const deploymentPresentation = DEPLOYMENT_PRESENTATION[
    payload?.system?.deployment?.status ?? "PROVENANCE_UNKNOWN"
  ] ?? DEPLOYMENT_PRESENTATION.PROVENANCE_UNKNOWN;

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
          <SystemStatePill loading={statusState === "loading"} error={statusState === "error"} online={Boolean(payload?.system?.online)} marketSession={payload?.system?.market_session} />
        </div>
      </header>

      <section className="audit-intro">
        <div><p className="eyebrow">IMMUTABLE FORWARD EVIDENCE</p><h1>新闻先被看见，<br />决定才被允许产生。</h1></div>
        <div
          className="training-card"
          aria-label={learningState === "ready"
            ? `学习进度：已收集 ${payload?.training?.complete_rows ?? 0} 条，目标 ${payload?.training?.next_training_at ?? 0} 条，还差 ${rowsUntilTraining ?? 0} 条`
            : "学习进度暂不可用"}
        >
          <div className="training-card-head"><span>学习进度</span></div>
          <div className="training-card-total">
            <strong>{learningState === "ready" && payload?.training
              ? <>{payload.training.complete_rows}<small> / {payload.training.next_training_at}</small></>
              : <small>{learningState === "loading" ? "读取中…" : "暂不可用"}</small>}
            </strong>
            <span>{rowsUntilTraining === null ? "等待数据" : rowsUntilTraining === 0 ? "可以开始下一轮" : `还差 ${rowsUntilTraining} 条`}</span>
          </div>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
        </div>
      </section>

      {combinedErrors && <div className="error-banner">{combinedErrors}。页面会保留上一份成功数据并自动重试。</div>}

      <nav ref={auditTabsRef} className="audit-tabs" aria-label="审计视图">
        <a href="/audit?view=news" className={view === "news" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("news"); }}>新闻 <b>{readableNewsTotal}</b></a>
        <a href="/audit?view=evidence" className={view === "evidence" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("evidence"); }}>新闻证据管理 <b>{payload?.news_evidence_summary?.model_seen_events ?? 0}</b></a>
        <a href="/audit?view=stories" className={view === "stories" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("stories"); }}>事件故事链 <b>{payload?.storyline_summary?.total ?? 0}</b></a>
        <a href="/audit?view=decisions" className={view === "decisions" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("decisions"); }}>决策与30分钟结果 <b>{payload?.counts?.decision_events ?? 0}</b></a>
        <a href="/audit?view=league" className={view === "league" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("league"); }}>Live OOS 学习曲线 <b>{learningState === "ready" ? `${activeLearningIdentities}组` : learningState === "loading" ? "读取中" : "—"}</b></a>
        <a href="/audit?view=coverage" className={view === "coverage" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("coverage"); }}>大视野覆盖 <b>{payload?.factor_coverage?.filter(row => row.status === "LIVE" || row.status === "COLLECTING").length ?? 0}/11</b></a>
      </nav>

      {view === "news" && <>
        <section className="annotation-queue" aria-label="新闻处理进度">
          <span><b>{readableNewsTotal}</b> 新闻总数</span>
          <span><b>{parsedNewsTotal}</b> 已完整解析</span>
          <span><b>{newsNoParsingNeededTotal}</b> 无需解析</span>
          <span><b>{newsWaitingTotal}</b> 等待处理</span>
          <span className="is-model-ready"><b>{modelCandidateNewsTotal}</b> 模型可用</span>
          <details>
            <summary>查看处理器技术状态</summary>
            <p>真正排队 {payload?.annotation_queue?.queued ?? 0} · 失败后等待重试 {payload?.annotation_queue?.backing_off ?? 0} · 已隔离 {payload?.annotation_queue?.dead_letter ?? 0} · 等待正文 {payload?.annotation_queue?.waiting_content ?? 0} · 正文不可用 {payload?.annotation_queue?.unavailable_content ?? 0}</p>
          </details>
        </section>
        <section className="news-browser" aria-label="新闻自动分类">
          <div><strong>自动分类</strong><span>按媒体发布时间排序 · 可读 {readableNewsTotal} 篇 · 已解析 {parsedNewsTotal} 篇 · 模型候选 {modelCandidateNewsTotal} 篇 · 每页 {NEWS_PER_PAGE} 篇</span></div>
          <nav>
            {categories.map(category => <button key={category.name} type="button" className={newsCategory === category.name ? "active" : ""} onClick={() => { setNewsCategory(category.name); setNewsPage(1); }}>
              {category.name}<b>{category.count}</b>
            </button>)}
          </nav>
        </section>
        <section className="news-table">
          <header className="news-table-head"><span>分类 / 发布时间</span><span>新闻与来源</span><span>正文 / 状态</span></header>
          {visibleNews.map(row => <NewsRow
            key={`${row.source}-${row.source_item_id}-${row.revision_number}`}
            row={row}
            keyCount={payload?.annotation_queue?.configured_key_count ?? 0}
            requestsPerMinute={payload?.annotation_queue?.requests_per_minute ?? 0}
          />)}
          {Array.from({ length: emptyNewsRows }, (_, index) => <div className="news-row-placeholder" aria-hidden="true" key={`empty-news-row-${index}`} />)}
        </section>
        {newsPageCount > 1 && <nav className="news-pagination" aria-label="新闻分页">
          <button type="button" disabled={currentNewsPage === 1} onClick={() => setNewsPage(page => Math.max(1, page - 1))}>← 上一页</button>
          <span>第 <b>{currentNewsPage}</b> / {newsPageCount} 页 · 当前分类 {newsIndex.total} 条</span>
          <button type="button" disabled={currentNewsPage === newsPageCount} onClick={() => setNewsPage(page => Math.min(newsPageCount, page + 1))}>下一页 →</button>
        </nav>}
      </>}

      {view === "evidence" && <section className="evidence-desk">
        <header className="evidence-intro evidence-intro-compact">
          <div><p className="eyebrow">NEWS USED BY MODEL</p><h2>模型真正用过哪些新闻？</h2><p>只显示实际进入过预测的独立新闻事件。</p></div>
        </header>
        <div className="evidence-summary">
          <article><span>收到多少篇新闻</span><strong>{payload?.news_evidence_summary?.distinct_articles ?? 0}</strong><small>共保存 {payload?.news_evidence_summary?.raw_article_revisions ?? 0} 个版本；文章更新不会算成新新闻</small></article>
          <article><span>历史上用过多少个事件</span><strong>{payload?.news_evidence_summary?.model_seen_events ?? 0}</strong><small>每个都确实参加过至少一次预测</small></article>
          <article><span>影响过多少次预测</span><strong>{payload?.news_evidence_summary?.decision_event_exposures ?? 0}</strong><small>同一事件可以连续影响多个 5 分钟预测</small></article>
          <article><span>模型一共读取多少次</span><strong>{payload?.news_evidence_summary?.frozen_model_uses ?? 0}</strong><small>5 套模型分别记账；这不是新闻数量</small></article>
          <article><span>从未进入预测的事件</span><strong>{payload?.news_evidence_summary?.model_unseen_events ?? 0}</strong><small>可在下方逐条查看没有使用的原因</small></article>
          <article><span>现在仍可用于预测</span><strong>{payload?.news_evidence_summary?.broad_model_eligible ?? 0}</strong><small>等待下一次预测读取；不代表历史上用过</small></article>
        </div>
        <nav className="evidence-filters" aria-label="模型新闻可见性筛选">
          <button type="button" className={evidenceMode === "seen" ? "active" : ""} onClick={() => setEvidenceMode("seen")}>历史上用过 <b>{payload?.news_evidence_summary?.model_seen_events ?? 0}</b></button>
          <button type="button" className={evidenceMode === "unseen" ? "active" : ""} onClick={() => setEvidenceMode("unseen")}>从未用过 <b>{payload?.news_evidence_summary?.model_unseen_events ?? 0}</b></button>
          <button type="button" className={evidenceMode === "all" ? "active" : ""} onClick={() => setEvidenceMode("all")}>查看全部 <b>{payload?.news_evidence_summary?.displayed_events ?? 0}</b></button>
        </nav>
        <details className="evidence-rule-note"><summary>查看统计规则</summary><p>按独立事件统计，不重复计算转载。新闻最长 72 小时有效；迟到发现只保留展示，不进入训练。</p></details>
        <div className="evidence-table-wrap"><table className="evidence-table">
          <thead><tr><th>是否用于预测</th><th>新闻事件</th><th>用了多少次 / 为什么没用</th><th>发布时间 / 收到时间</th></tr></thead>
          <tbody>{visibleEvidence.map(row => <tr key={row.event_key}>
            <td><span className={`model-seen-badge ${row.model_seen ? "is-seen" : "is-unseen"}`}>{row.model_seen ? "已用于预测" : "未用于预测"}</span><small>{EVIDENCE_LABELS[row.evidence_grade] ?? row.evidence_grade}<br />{row.model_seen ? "当时确实参与了模型输入" : row.broad_model_eligible ? "现在符合条件，等待下一次预测" : "现在也不符合使用条件"}</small></td>
            <td><strong>{row.canonical_headline}</strong><div className="evidence-topics">{(row.topics ?? []).map(topic => <span key={topic}>{TOPIC_LABELS[topic] ?? topic}</span>)}</div></td>
            <td>{row.model_seen ? <><strong>参与 {row.frozen_decisions} 次预测 · 被模型读取 {row.frozen_model_uses} 次</strong><small>{(row.model_identities ?? []).map(identity => MODEL_LABELS[identity] ?? identity).join(" · ") || "模型名称未记录"}<br />首次参与 {time(row.first_model_decision_time)} · 最近参与 {time(row.last_model_decision_time)}</small></> : <><strong>从未进入任何预测</strong><small>{(row.model_unseen_reason_codes ?? []).map(code => EVIDENCE_REASON_LABELS[code] ?? code).join(" · ") || "当时未达到时间、正文或来源要求"}</small></>}</td>
            <td><time>{row.source_published_time ? time(row.source_published_time) : "发布时间未知"}</time><small>首次收到 {time(row.collector_first_seen_time)}<br />{row.independent_publishers} 个独立来源 · {row.member_count} 篇成员新闻</small></td>
          </tr>)}</tbody>
        </table></div>
      </section>}

      {view === "stories" && <section className="story-desk">
        <header className="evidence-intro evidence-intro-compact"><div><p className="eyebrow">事件故事链</p><h2>同一事件的报道，合并显示。</h2></div></header>
        {payload?.system.deployment && <section className={`deployment-proof ${deploymentPresentation.className}`}><b>{deploymentPresentation.label}</b>{payload.system.deployment.status === "DEPLOYMENT_DRIFT" ? <span>本机 {payload.system.deployment.runtime_git_sha?.slice(0, 8) ?? "未知"} · 远端 {payload.system.deployment.expected_git_sha?.slice(0, 8) ?? "未知"}</span> : payload.system.deployment.runtime_git_sha ? <span>版本 {payload.system.deployment.runtime_git_sha.slice(0, 8)}</span> : null}</section>}
        <section className="theme-streams"><header><h3>主题流</h3><span>不声称构成单一事件</span></header><div>{(payload?.theme_streams ?? []).map(theme => <article key={theme.theme_id}><b>{theme.title}</b><strong>{theme.item_count}</strong><span>{theme.latest_headline}</span><small>{time(theme.last_updated)}</small></article>)}</div></section>
        <section className="theme-streams market-streams"><header><h3>市场反应流</h3><span>价格反应不冒充核心事实</span></header><div>{(payload?.market_reaction_streams ?? []).map(stream => <article key={stream.stream_id}><b>{stream.title}</b><strong>{stream.item_count}</strong><span>{stream.latest_headline}</span><small>{time(stream.last_updated)}</small></article>)}</div></section>
        <div className="story-grid">{(payload?.storylines ?? []).map(story => <article key={story.storyline_id}>
          <header><div><span>{({ EMERGING:"刚出现", REPORTED:"已有报道", CORROBORATED:"独立交叉确认", OFFICIALLY_CONFIRMED:"官方确认", ESCALATING:"升级中", DEESCALATING:"缓和中", CONTRADICTED:"存在冲突" } as Record<string,string>)[story.state] ?? story.state}</span><h3>{story.title}</h3><small>{story.episode_key}</small></div><strong>{story.event_count}<small> 个现实事件</small></strong></header>
          <p className="story-latest"><b>最新事实变化</b>{story.latest_change}</p>
          <div className="story-meta"><span>证据文件 {story.evidence_document_count}</span><span>独立组织 {story.independent_organization_count}</span><span>更新 {time(story.last_updated)}</span><span>{story.independent_confirmation ? "跨组织确认" : "尚未跨组织确认"}</span></div>
          <section className="story-coverage"><div><b>覆盖模板 {story.coverage_template} · {story.coverage_count}/{story.coverage_total}</b>{story.covered_roles.map(role => <span key={role.key}>{role.label}</span>)}{story.missing_roles.map(role => <em className="missing" key={role.key}>仍缺：{role.label}</em>)}</div></section>
          <ol>{story.timeline.map(item => <li key={item.event_key}><time>{time(item.event_time || item.source_published_time || item.first_seen)}</time><b>{({ STARTS:"故事开始", FOLLOWED_BY:"随后发生", CONFIRMS:"确认", CONTRADICTS:"否认/冲突", RESPONDS_TO:"作出回应", ESCALATES:"实际升级", DEESCALATES:"实际缓和", SUPERSEDES:"修订替代" } as Record<string,string>)[item.relation] ?? item.relation}</b><span>{item.headline}</span><small>{item.actor} · {item.action} · {item.evidence_documents} 份文件 · {item.independent_organizations} 个组织<br />发布 {time(item.source_published_time)} · 系统首次看到 {time(item.collector_first_seen_time)}</small></li>)}</ol>
          {story.market_reactions.length > 0 && <details className="story-attachments"><summary>市场反应 {story.market_reactions.length}</summary>{story.market_reactions.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
          {story.commentary.length > 0 && <details className="story-attachments"><summary>评论与预测 {story.commentary.length}</summary>{story.commentary.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
          {story.background.length > 0 && <details className="story-attachments"><summary>背景材料 {story.background.length}</summary>{story.background.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
        </article>)}</div>
        {(payload?.storylines?.length ?? 0) === 0 && <div className="story-empty"><b>正在建立 V5 事件链</b><span>孤立事实先留在候选区；在形成第二个不同进展前，不会冒充完整故事。</span></div>}
        <details className="unassigned-story-events" open><summary>市场叙事候选 <b>{payload?.storyline_summary?.market_narrative_total ?? 0}</b> <small>只有市场反应或评论，核心现实进展尚未确认</small></summary>{(payload?.market_narrative_candidates ?? []).map(story => <div key={story.storyline_id}><time>{time(story.last_updated)}</time><span><b>{story.title}</b><br />{story.latest_change}</span><small>{story.event_count} 个候选节点 · {story.evidence_document_count} 份文件 · 不进入活跃故事</small></div>)}</details>
        <details className="unassigned-story-events"><summary>历史档案 <b>{payload?.storyline_summary?.archived_total ?? 0}</b> <small>ARCHIVAL_BACKFILL，不显示为当前新事件</small></summary>{(payload?.archived_storylines ?? []).map(story => <div key={story.storyline_id}><time>{time(story.timeline[0]?.event_time)}</time><span><b>{story.title}</b><br />{story.latest_change}</span><small>{story.event_count} 个历史事件 · 系统首次收录 {time(story.last_updated)}</small></div>)}{(payload?.archived_story_event_candidates ?? []).map(item => <div key={item.candidate_id}><time>{time(item.event_time)}</time><span>{item.headline}</span><small>{item.evidence_documents} 份历史证据文件 · 系统首次收录 {time(item.first_seen)}</small></div>)}</details>
        <details className="unassigned-story-events" open><summary>新事件候选 <b>{payload?.storyline_summary?.candidate_total ?? 0}</b> <small>等待第二个不同进展，不会一篇新闻生成一张故事卡</small></summary>{(payload?.story_event_candidates ?? []).map(item => <div key={item.candidate_id}><time>{time(item.first_seen)}</time><span>{item.headline}</span><small>{item.evidence_documents} 篇证据 · {item.independent_publishers} 个独立来源 · {item.episode_key}</small></div>)}</details>
        <details className="unassigned-story-events"><summary>未归属事件 <b>{payload?.storyline_summary?.unassigned_total ?? 0}</b></summary>{(payload?.unassigned_story_events ?? []).map(item => <div key={item.event_key}><time>{time(item.first_seen)}</time><span>{item.headline}</span><small>{item.record_kind} · {item.reason}</small></div>)}</details>
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
            <div><dt>Collection started</dt><dd>{time(payload?.learning_curves?.collection_epoch)}</dd></div>
            <div><dt>Evaluation V2 started</dt><dd>{time(payload?.learning_curves?.evaluation_epoch_v2)}</dd></div>
            <div><dt>当前证据等级</dt><dd>{payload?.learning_curves?.learning_stage ?? "ENGINEERING"}</dd></div>
          </dl>
        </header>
        <div className="learning-summary-grid">
          <article><span>上一次学习</span><strong>{learningState === "ready" ? directionPoolRows : "—"}</strong><small>当前模型已经学到这里</small></article>
          <article><span>下一次学习</span><strong>{learningState === "ready" && payload?.training && rowsUntilTraining !== null ? `${payload.training.next_training_at} − ${payload.training.complete_rows} = ${rowsUntilTraining}` : "—"}</strong><small>{rowsUntilTraining === 0 ? "已经达到目标，可以开始新一轮" : "目标 − 目前已有 = 还差多少"}</small></article>
        </div>
        <section className="graph-launch">
          <div><h3>查看学习曲线与 K 线</h3><p>长期累计、每组成绩与决策位置</p></div>
          <button type="button" onClick={() => { setGraphStartTab("curve"); setGraphOpen(true); }}>打开交互图表 ↗</button>
        </section>
        <section className="model-score-summary"><header><div><span>LIVE OOS SCOREBOARD</span><h3>五套模型，现在表现怎样？</h3></div><small>左边是本组开始前，箭头后是连续累计，圆点后是本组独立贡献。</small></header><div className="summary-cadence"><span>统计频率</span><button type="button" className={summaryCadence === "EVERY_5M" ? "active" : ""} onClick={() => setSummaryCadence("EVERY_5M")}>每5分钟（重叠）</button><button type="button" className={summaryCadence === "FIXED_30M" ? "active" : ""} onClick={() => setSummaryCadence("FIXED_30M")}>每30分钟（:00 / :30）</button><small>预测期限始终是30分钟。</small></div>
        {(payload?.learning_curves?.models?.length ?? 0) === 0 ? <div className="league-empty">
          <strong>正在建立第一版 Preview</strong><p>达到 96 条修复或 Forward 完整样本即可训练 Market Preview，不需要等待60天。曲线只从模型创建后的新 Decision 开始，绝不回填假历史成绩。</p>
        </div> : <div className="compact-model-summary">{Object.keys(MODEL_LABELS).filter(identity => identity !== "CHAMPION_0").map(identity => {
          const process = payload?.learning_curves?.rolling_processes?.find(row => row.model_identity === identity);
          const latestGroup = payload?.learning_curves?.version_groups?.find(row => row.model_identity === identity && row.lifecycle_status === "LATEST");
          if (!process && !latestGroup) return null;
          const diagnostic = identity === "NEWS_RESIDUAL" || identity === "BROAD_NEWS_RESIDUAL";
          const processMetric = process?.cadence_metrics?.[summaryCadence] ?? process;
          const groupMetric = latestGroup?.cadence_metrics?.[summaryCadence] ?? latestGroup;
          const hasTotal = (processMetric?.oos_rows ?? 0) > 0;
          const hasGroup = (groupMetric?.oos_rows ?? 0) > 0;
          const total = hasTotal ? processMetric!.cumulative_quote_return : null;
          const group = hasGroup ? groupMetric!.cumulative_quote_return : null;
          const history = total === null ? null : total - (group ?? 0);
          const tone = group === null ? "is-pending" : group >= 0 ? "is-positive" : "is-negative";
          return <article key={identity}><b>{MODEL_LABELS[identity]}{diagnostic ? <small>新闻修正量</small> : null}</b><div className="return-flow" aria-label={`本组开始前 ${percent(history)}，加入本组后 ${percent(total)}，本组贡献 ${percent(group)}`}><span className="return-value return-history" title="本组开始前的历史累计"><small>开始前</small><span>{history === null ? "—" : percent(history)}</span></span><i className={tone} aria-hidden="true">→</i><span className="return-value return-total" title="加入本组后的连续累计"><small>当前累计</small><strong>{total === null ? "等待" : percent(total)}</strong></span><i className="return-separator" aria-hidden="true">·</i><span className={`return-value return-group ${tone}`} title="本组独立贡献"><small>本组贡献</small><strong>{group === null ? "等待" : percent(group)}</strong></span></div></article>;
        })}</div>}</section>
        <ExecutionResearch status={payload?.execution_learning} onOpenGraph={() => { setGraphStartTab("execution"); setGraphOpen(true); }} />
        <details className="model-method-note">
          <summary><span>方法与实盘边界</span><small>新闻修正量、成本与 Shadow 限制</small></summary>
          <div>
            <article><b>“大视野新闻修正量”不是“大视野新闻自身”</b><span>它先看黄金自身预测错了多少，再学习新闻应该把黄金答案往上或往下修多少。例：黄金自身 +0.10 U5，新闻修正 +0.04 U5，只有“黄金＋大视野新闻”才输出完整方向 +0.14 U5。</span></article>
            <article><b>当前还没有独立的“大视野 News-only”</b><span>真正的 News-only 会完全不读取黄金特征，只用新闻直接预测完整30分钟目标。现在名为“大视野新闻修正量”的曲线不能当作 News-only，也不能单独拿去做完整方向。</span></article>
            <article><b>成本口径</b><span>收益使用可执行 Bid/Ask，并扣除入场、退出两边各 $30 / 百万美元成交额的 commission；slippage 暂按 0。尚未包含账户真实成交偏差，所以不是实盘 PnL。</span></article>
            <article><b>做法可以实时复现；结果尚未达到实盘标准</b><span>行情和新闻都只读取决策时已经看见的内容；30分钟结果成熟后才进入下一轮训练。当前仍没有下单权限，也不会自动晋升。</span></article>
          </div>
        </details>
        <footer className="league-footer">{payload?.learning_curves?.disclaimer ?? "早期曲线用于观察学习过程，不代表已证明盈利。"} 单日和双日新闻模型明确标记 EXPERIMENTAL；达到3个新闻日期后自动进入标准证据状态。当前只运行每个 Ridge 身份的最新版和前一版；{archivedModelCount} 个旧版本的 artifact、预测和成绩已永久归档。零收益安全基准不训练、不使用 AI、不占 Ridge 版本名额。Preview 与 Shadow 都没有下单权限，也不会自动晋升。</footer>
        <LearningGraphModal key={graphStartTab} open={graphOpen} onClose={() => setGraphOpen(false)} startTab={graphStartTab} curves={payload?.learning_curves?.identity_curves ?? []} market={payload?.market_chart} versionGroups={payload?.learning_curves?.version_groups ?? []} execution={payload?.execution_learning} />
      </section>}

      {view === "coverage" && <section className="coverage-grid">
        {(payload?.factor_coverage ?? []).map(row => <article key={row.domain} className={`coverage-card status-${row.status.toLowerCase().replaceAll("_", "-")}`}>
          <div><span>{row.cadence}</span><b>{COVERAGE_STATUS_LABELS[row.status] ?? row.status}</b></div><h2>{row.domain}</h2><p>{row.source ?? "尚未连接可靠的 point-in-time 数据源"}</p>
          {row.value !== null && row.value !== undefined && <strong className="coverage-value">{number(row.value, 3)} <small>{row.unit}</small></strong>}
          <small>{row.status_reason ?? `${row.observed_at ? `观测期 ${row.observed_at} · ` : ""}${row.action_bearing ? "已进入决策Snapshot" : "Shadow特征，等待训练验证"}`}</small>
        </article>)}
      </section>}

      <footer className="audit-footer"><span>最后同步 {time(payload?.generated_at)}</span><span>SHADOW ONLY · APPEND ONLY</span></footer>
    </main>
  );
}

function ExecutionResearch({ status, onOpenGraph }: { status?: Payload["execution_learning"]; onOpenGraph: () => void }) {
  const lot = status?.models.find(row => row.model_identity === "LOT_RIDGE");
  const exit = status?.models.find(row => row.model_identity === "EXIT_RIDGE");
  const card = (title: string, model: typeof lot, hint: string) => <article>
    <div className="execution-title">
      <div><b>{title}</b><small>{hint}</small></div>
      <em className={model?.status === "RUNNING" ? "is-running" : ""}>{model?.status === "RUNNING" ? "学习中" : "收集中"}</em>
    </div>
    <dl>
      <div><dt>已学习</dt><dd>{model?.training_decisions ?? 0}</dd></div>
      <div><dt>已结算</dt><dd>{model?.scores ?? 0}</dd></div>
      <div><dt>下次训练</dt><dd>{model?.next_training_threshold ?? "—"}</dd></div>
    </dl>
  </article>;
  return <details className="model-method-note execution-research execution-research-details">
    <summary><span>仓位与退出研究</span><small>仓位倍率与提前退出</small></summary>
    <div>
      {card("仓位倍率 Ridge", lot, "0.5x / 1.0x / 2.0x")}
      {card("Exit Ridge", exit, "5 / 10 / 15 / 20 / 25 分钟")}
      <article className="execution-detail-action"><p>跟随“{status?.source_model_label ?? "黄金＋大视野新闻 Ridge"}”的 Live 方向；WAIT 不建立位置。</p><button type="button" onClick={onOpenGraph}>打开详细结果 ↗</button></article>
    </div>
  </details>;
}
