"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import CountValue from "../_components/CountValue";
import type { AuditViewName } from "../_components/DashboardNavigation";
import { CurrentDataNotice, MetricValue, type CurrentDataPhase } from "../_components/CurrentDataState";
import {
  DashboardResourceError, loadDashboardResource, readDashboardResource,
} from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { statusFieldPhase } from "../_lib/current-data-provenance";
import { PREVIEW_NEWS_PAGE_SIZE } from "../_lib/preview-manifest";
import { resolveNewsMetrics, type NewsMetrics } from "../_lib/news-metrics";
import { authoritativeNewsTotals, type NewsTotalsScope } from "../_lib/news-index-contract";
import { settleResponsiveScroll } from "../_lib/responsive-scroll";
import type { NewsReviewState } from "../_lib/news-review-state";
import { formatExactCount, progressCountPresentation } from "../_lib/count-format";
import { publicImpactReason } from "../_lib/public-news-copy";
import { sortNewsEvidenceByTime } from "../_lib/news-evidence-order";
import type { VersionEvaluationStatus } from "../_lib/version-result-state";
import LearningGraphModal from "../audit/LearningGraphModal";

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

type AuditDeskView = AuditViewName;
type AuditDetailView = "briefs" | "stories" | "decisions";

const AUDIT_DETAIL_RESOURCES: Record<AuditDetailView, string> = {
  briefs: "/api/audit-briefs",
  stories: "/api/audit-stories",
  decisions: "/api/audit-decisions",
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
  annotation_reason_code?: "DUPLICATE_CONTENT" | "SEARCH_LEAD" | "HISTORICAL_MATERIAL" | "STALE_AT_INTAKE" | "INVALID_PUBLISHED_TIME" | "QUEUE_INVARIANT_MISMATCH" | "INTAKE_REJECTED" | "MODEL_OUTPUT_CONTRACT_FAILED" | "MODEL_OUTPUT_INVALID" | "PROVIDER_HTTP_ERROR" | "MODEL_REQUEST_FAILED";
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
  source_organizations?: string[];
  source_identity_organizations?: string[];
  publisher_domains: string[];
  reason_codes: string[];
  model_seen: boolean;
  frozen_model_uses: number;
  frozen_decisions: number;
  frozen_versions?: number;
  first_model_decision_time: string | null;
  last_model_decision_time: string | null;
  model_identities?: string[] | null;
  model_versions: string[];
  model_unseen_reason_codes?: string[] | null;
};
type NewsEvidenceResponse = {
  items: NewsEvidence[];
  page: number;
  page_size: number;
  mode: "all" | "eligible" | "seen" | "unseen";
  has_more?: boolean;
  next_cursor?: string | null;
  snapshot_id?: string;
  source_mode?: string;
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
  subsequent_prediction_rows?: number; unscored_oos_rows?: number; overdue_oos_rows?: number;
  evaluation_status?: VersionEvaluationStatus;
  cumulative_quote_return: number; profit_factor_quote_adjusted: number | null;
  coverage_rate: number | null; average_oracle_regret: number | null;
  cadence_metrics?: Record<EvaluationCadence, CadenceMetric>;
};
type EvaluationCadence = "EVERY_5M" | "FIXED_30M";
type CadenceMetric = { oos_rows: number; distinct_days: number; cumulative_quote_return: number; profit_factor_quote_adjusted: number | null; coverage_rate: number | null; prediction_rows?: number; unscored_oos_rows?: number; overdue_oos_rows?: number; evaluation_status?: VersionEvaluationStatus };
type DailyBriefPhase = "WAITING" | "UPDATING" | "DEFERRED" | "FINAL" | "DEGRADED" | "EMPTY";
type DailyNewsBrief = { brief_date: string; revision_number: number; cutoff_at: string; generated_at: string; model_version: string; prompt_version: string; phase?: DailyBriefPhase; received_items?: number; reviewed_items?: number; pending_items?: number; terminal_failure_items?: number; next_retry_at?: string | null; finalized_at?: string | null; brief: { title: string; overview?: string; drivers?: string[]; watch_next?: string; items: Array<{ headline: string; summary: string; evidence_ids: string[] }> } };
type DailyNewsBriefSummary = { brief_date: string; phase: DailyBriefPhase; received_items: number | null; reviewed_items: number | null; pending_items: number | null; terminal_failure_items: number | null; latest_revision: number | null; last_generated_at: string | null; next_retry_at: string | null; is_final: boolean; total_brief_days: number | null; observation_scope?: "BUILD_SNAPSHOT_COMPATIBILITY" };
type NewsSearchResponse = {
  items: News[];
  total: number;
  page: number;
  page_size: number;
  query: string;
  filters: {
    published_from: string | null; published_to: string | null;
    received_from: string | null; received_to: string | null;
    evidence_id: string | null; source: string | null; category: string | null;
  };
  source_mode: "D1_ARCHIVE" | "READ_ONLY_D1_ARCHIVE" | "IMMUTABLE_PREVIEW_SNAPSHOT" | "NOT_QUERIED";
  archive_complete: boolean | null;
};

const emptyNewsSearch = (): NewsSearchResponse => ({
  items: [], total: 0, page: 1, page_size: 10, query: "",
  filters: {
    published_from: null, published_to: null, received_from: null, received_to: null,
    evidence_id: null, source: null, category: null,
  },
  source_mode: "NOT_QUERIED", archive_complete: null,
});

type Payload = {
  preview_status_summary?: boolean;
  preview?: {
    is_preview?: boolean;
    branch_snapshot?: { generated_at: string | null; status_paths: string[] };
  };
  learning_preview_summary?: boolean;
  learning_history_resource?: string;
  learning_history_manifest?: {
    contract_version: string; model_total: number;
    version_group_total: number; record_total: number;
  };
  generated_at: string;
  system: { online: boolean; market_session?: "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE"; source_of_truth: string; sites_mirror: string; deployment?: { runtime_git_sha: string | null; expected_git_sha: string | null; runtime_dirty: boolean; status: string; storyline_policy_version: string; payload_schema_version: string; payload_generated_at: string; source_database_epoch: string | null } };
  operational_health?: { status: "HEALTHY" | "WARNING" | "ERROR" };
  counts: Record<string, number>;
  news_metrics?: NewsMetrics;
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
  daily_news_briefs?: DailyNewsBrief[];
  daily_news_brief_summary?: DailyNewsBriefSummary;
  news_evidence?: NewsEvidence[];
  audit_resource?: string;
  audit_briefs_resource?: string;
  audit_stories_resource?: string;
  audit_decisions_resource?: string;
  news_evidence_resource?: string;
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
    current_contract_exposed_rows: number;
    current_contract_distinct_events: number;
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
    news_contract_transition?: {
      current_contract_exposed_rows: number;
      current_contract_distinct_events: number;
      minimum_exposed_rows: number;
      missing_exposed_rows: number;
    };
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
    candles: Array<{ time: string; open: number; high: number; low: number; close: number; ticks?: number }>;
    overview_candles?: Array<{ time: string; open: number; high: number; low: number; close: number; ticks?: number }>;
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
    history_resource?: string;
    history_start?: string | null;
    history_end?: string | null;
    detail_start?: string | null;
    source_candle_count?: number;
    overview_downsampled?: boolean;
    prediction_history_start?: Record<string, string>;
    source_decision_count?: number;
    decision_downsampled?: boolean;
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
  review_state?: NewsReviewState;
  review_state_counts?: Partial<Record<NewsReviewState, number>>;
  page: number;
  page_size: number;
  window_days?: number;
  totals_scope?: NewsTotalsScope;
};

type NewsDetailResponse = { payload: Partial<News> };
type NewsDetailBatchResponse = {
  items: Record<string, NewsDetailResponse>;
  missing: string[];
};

const time = (value?: string | null) => value ? new Intl.DateTimeFormat("zh-CN", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false, timeZone: "Asia/Kuala_Lumpur",
}).format(new Date(value)) : "—";
const dailyBriefPhaseLabel = (phase?: DailyBriefPhase, isToday = false) => ({
  WAITING: isToday ? "今日" : "待生成",
  UPDATING: isToday ? "今日" : "处理中",
  DEFERRED: "待重试",
  FINAL: "已完成",
  DEGRADED: "已完成",
  EMPTY: "无资料",
}[phase ?? "WAITING"]);
const dailyBriefDateLabel = (phase?: DailyBriefPhase, isToday = false) => phase === "DEGRADED"
  ? "需注意"
  : dailyBriefPhaseLabel(phase, isToday);
const shortBriefDate = (value: string) => value.slice(5).replace("-", "/");
const DAILY_BRIEF_VISIBLE_DATES = 4;
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
const NEWS_PER_PAGE = PREVIEW_NEWS_PAGE_SIZE;
const EVIDENCE_PER_PAGE = 20;
const NEWS_REVIEW_PRESENTATION: Record<NewsReviewState, {
  label: string; description: string;
}> = {
  COMPLETED: {
    label: "已完成",
    description: "已通过语义复核，或已明确无需 AI 复核",
  },
  PROCESSING: {
    label: "处理中",
    description: "仍在排队、有限重试或等待必要证据",
  },
  ISOLATED: {
    label: "已隔离",
    description: "自动处理已停止，保留有限证据等待检查",
  },
};
const CATEGORY_ORDER = ["战争/地缘", "利率/Fed", "央行购金", "通胀/就业", "增长/经济", "油价/能源", "美元/流动性", "风险情绪 / 避险", "监管/其他", "其他"];
const SOURCE_LABELS: Record<string, string> = {
  federal_reserve_monetary: "Federal Reserve · 货币政策",
  federal_reserve_speeches_testimony: "Federal Reserve · 演讲证词",
  federal_reserve_press_all: "Federal Reserve · 新闻与监管",
  google_news_gold_geopolitics: "Google News · 战争与地缘",
  google_news_gold_context: "Google News · 黄金大视野",
  world_gold_council_central_banks: "World Gold Council · 央行购金",
  eia_today_in_energy: "U.S. EIA · 能源分析",
  eia_press_releases: "U.S. EIA · 新闻发布",
  ecb_press_releases: "European Central Bank · 官方发布",
  us_treasury_press_releases: "U.S. Treasury · 官方发布",
  bea_economic_releases: "U.S. BEA · 经济数据发布",
};
function newsSourceLabel(row: Pick<News, "source" | "category">): string {
  if (row.source === "gdelt_gold_geopolitics") return `GDELT · ${row.category}`;
  return SOURCE_LABELS[row.source] ?? row.source.replaceAll("_", " ");
}

const COVERAGE_STATUS_LABELS: Record<string, string> = {
  LIVE: "实时",
  COLLECTING: "监测中",
  WARMING_UP: "等待数据",
};
const MODEL_LABELS: Record<string, string> = {
  CHAMPION_0: "零收益安全基准",
  MARKET_ONLY: "黄金自身 Ridge",
  NEWS_RESIDUAL: "核心新闻修正 Ridge",
  FULL: "黄金＋核心新闻 Ridge",
  BROAD_NEWS_RESIDUAL: "大视野新闻修正量 Ridge",
  BROAD_FULL: "黄金＋大视野新闻 Ridge",
  NEWS_ONLY: "纯新闻方向 Ridge",
};
function predictionStatusLabel(status: string): string {
  if (status === "RESEARCH_RESIDUAL_DIRECTION") return "修正量自己的30分钟方向研究";
  if (status === "DIAGNOSTIC_RESIDUAL_ONLY") return "历史版本仅保存修正值";
  if (status === "RESEARCH_NEWS_ONLY") return "只看新闻的30分钟方向研究";
  if (status === "NO_ELIGIBLE_NEWS") return "当前没有合格新闻";
  return status;
}
const TOPIC_LABELS: Record<string, string> = {
  rates_fed: "利率 / Fed", inflation: "通胀", employment: "就业", inflation_employment: "通胀 / 就业",
  growth_economy: "增长 / 经济", usd_liquidity: "美元 / 流动性",
  oil_energy: "油价 / 能源", war_geopolitics: "战争 / 地缘",
  central_bank_gold: "央行购金", risk_sentiment: "风险情绪 / 避险", regulation_other: "监管 / 其他",
};
const EVIDENCE_LABELS: Record<string, string> = {
  PRIMARY: "一手完整证据", CORROBORATED: "多源确认",
  SINGLE_RELIABLE: "单一可靠来源 · 35%权重", DISCOVERY_ONLY: "线索来源",
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
  STALE_EVENT: "按有效交易时间计算，影响期已结束",
  CATEGORY_NOT_ACTIONABLE: "非黄金方向类别",
  NEEDS_CONFIRMATION: "尚未达到模型证据门槛",
  NO_ACTION_TOPIC: "与方向主题无关",
  EVIDENCE_PRIMARY: "一手来源",
  EVIDENCE_CORROBORATED: "多源确认",
  EVIDENCE_SINGLE_RELIABLE: "单一可靠来源",
  EVIDENCE_DISCOVERY_ONLY: "线索来源",
  RELIABLE_SINGLE_SOURCE_PROVISIONAL: "可靠单一来源，已降低权重",
  RELIABLE_PUBLISHER_TIME_PROXY: "以媒体发布时间作为公开时间",
  EDITORIAL_OR_INVESTMENT_GUIDE: "投资建议或评论，不进入模型",
  ELIGIBLE_AWAITING_FROZEN_PREDICTION: "已达模型门槛，等待下一次冻结预测",
  LEGACY_ANNOTATION_SCHEMA: "旧版标注，不进入当前模型",
  RECORD_KIND_NOT_ACTIONABLE: "行情报道，不是新的事实事件",
  EVIDENCE_ROLE_NOT_ACTIONABLE: "证据角色不参与方向学习",
  LOW_MATERIALITY: "事件重要性不足",
};
const EVIDENCE_REASON_PRIORITY = [
  "RECORD_KIND_NOT_ACTIONABLE", "EDITORIAL_OR_INVESTMENT_GUIDE",
  "IMPACT_DUPLICATE_REPORT", "LOW_MATERIALITY", "IMPACT_EXPIRED",
  "STALE_EVENT", "IMPACT_NOT_ASSESSED", "NEEDS_CONFIRMATION",
  "NO_ACTION_TOPIC", "CATEGORY_NOT_ACTIONABLE",
];
function evidenceReason(row: NewsEvidence): string {
  const codes = row.model_unseen_reason_codes ?? [];
  const code = EVIDENCE_REASON_PRIORITY.find(candidate => codes.includes(candidate))
    ?? codes.find(candidate => EVIDENCE_REASON_LABELS[candidate]);
  return code ? (EVIDENCE_REASON_LABELS[code] ?? code) : "当时未达到使用条件";
}
function mergeUnique(values: Array<string[] | null | undefined>): string[] {
  return Array.from(new Set(values.flatMap(value => value ?? []).filter(Boolean))).sort();
}
function mergeNewsEvidenceByEvent(rows: NewsEvidence[]): NewsEvidence[] {
  const merged = new Map<string, NewsEvidence>();
  for (const row of rows) {
    const previous = merged.get(row.event_key);
    if (!previous) {
      merged.set(row.event_key, row);
      continue;
    }
    const latest = row.collector_first_seen_time >= previous.collector_first_seen_time ? row : previous;
    merged.set(row.event_key, {
      ...latest,
      event_key: row.event_key,
      broad_model_eligible: previous.broad_model_eligible || row.broad_model_eligible,
      model_permission: previous.model_permission === "BROAD_MODEL" || row.model_permission === "BROAD_MODEL"
        ? "BROAD_MODEL"
        : "DISPLAY_ONLY",
      member_count: Math.max(previous.member_count, row.member_count),
      independent_publishers: Math.max(previous.independent_publishers, row.independent_publishers),
      source_names: mergeUnique([previous.source_names, row.source_names]),
      source_organizations: mergeUnique([previous.source_organizations, row.source_organizations]),
      source_identity_organizations: mergeUnique([
        previous.source_identity_organizations, row.source_identity_organizations,
      ]),
      publisher_domains: mergeUnique([previous.publisher_domains, row.publisher_domains]),
      topics: mergeUnique([previous.topics, row.topics]),
      reason_codes: mergeUnique([previous.reason_codes, row.reason_codes]),
      model_seen: previous.model_seen || row.model_seen,
      frozen_model_uses: previous.frozen_model_uses + row.frozen_model_uses,
      frozen_decisions: previous.frozen_decisions + row.frozen_decisions,
      frozen_versions: (previous.frozen_versions ?? 1) + (row.frozen_versions ?? 1),
      first_model_decision_time: [previous.first_model_decision_time, row.first_model_decision_time]
        .filter((value): value is string => Boolean(value)).sort()[0] ?? null,
      last_model_decision_time: [previous.last_model_decision_time, row.last_model_decision_time]
        .filter((value): value is string => Boolean(value)).sort().at(-1) ?? null,
      model_identities: mergeUnique([previous.model_identities, row.model_identities]),
      model_versions: mergeUnique([previous.model_versions, row.model_versions]),
      model_unseen_reason_codes: mergeUnique([
        previous.model_unseen_reason_codes, row.model_unseen_reason_codes,
      ]),
    });
  }
  return sortNewsEvidenceByTime(merged.values());
}
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
  DUPLICATE_CONTENT: "旧版重复内容",
  SEARCH_LEAD: "旧版搜索线索",
  HISTORICAL_MATERIAL: "历史资料",
  STALE_AT_INTAKE: "收到时已过期",
  INVALID_PUBLISHED_TIME: "发布时间无效",
  QUEUE_INVARIANT_MISMATCH: "队列异常",
  INTAKE_REJECTED: "采集条件未通过",
  MODEL_OUTPUT_CONTRACT_FAILED: "模型输出未通过验证",
  MODEL_OUTPUT_INVALID: "模型输出无法读取",
  PROVIDER_HTTP_ERROR: "模型服务暂时失败",
  MODEL_REQUEST_FAILED: "模型请求失败",
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
  BACKGROUND: "非当前影响",
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

function NewsRow({
  row, prefetchedDetail,
}: {
  row: News;
  prefetchedDetail?: Partial<News>;
}) {
  const [detail, setDetail] = useState<Partial<News> | null>(
    row.summary_zh !== undefined ? row : (prefetchedDetail ?? null),
  );
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">(
    row.summary_zh !== undefined || prefetchedDetail ? "ready" : "idle",
  );
  const detailElement = useRef<HTMLDetailsElement>(null);
  const [detailRetryCount, setDetailRetryCount] = useState(0);
  const [showSlowLoading, setShowSlowLoading] = useState(false);
  const [showSupportingEvidence, setShowSupportingEvidence] = useState(false);
  const resolvedDetailState = prefetchedDetail ? "ready" : detailState;
  const current = { ...row, ...(detail ?? prefetchedDetail ?? {}) };
  const annotationStatus = row.annotation_status === "QUEUED"
    && row.model_visibility !== "NOT_YET_PARSED"
    ? "NOT_REQUIRED"
    : row.annotation_status;
  const annotationReasonLabel = ANNOTATION_REASON_LABELS[
    current.annotation_reason_code ?? ""
  ] ?? "无需 AI 解析";
  const impactLabel = IMPACT_STATUS_LABELS[current.impact_status ?? ""];
  const impactClassLabel = current.impact_class
    ? IMPACT_CLASS_LABELS[current.impact_class] ?? current.impact_class
    : null;
  const impactLabels = [...new Set([
    impactLabel ?? "等待 Gemma 判断", impactClassLabel,
  ].filter((label): label is string => Boolean(label)))];
  const translated = Boolean(
    current.original_headline && current.headline !== current.original_headline,
  );
  const fetchDetail = useCallback(async () => {
    if (!row.detail_key) {
      setDetailState("error");
      return;
    }
    setShowSlowLoading(false);
    setDetailState("loading");
    try {
      const body = await loadDashboardResource<NewsDetailResponse>(
        `/api/news-content?key=${encodeURIComponent(row.detail_key)}`,
        { maxAgeMs: Number.POSITIVE_INFINITY },
      );
      setDetail(body.payload);
      setDetailState("ready");
      setDetailRetryCount(0);
    } catch {
      setDetailState("error");
    }
  }, [row.detail_key]);
  const loadDetail = (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || resolvedDetailState === "loading" || resolvedDetailState === "ready") return;
    void fetchDetail();
  };
  const retryDetail = () => {
    setDetailRetryCount(0);
    void fetchDetail();
  };
  useEffect(() => {
    if (resolvedDetailState !== "error" || detailRetryCount >= 3) return;
    const timer = window.setTimeout(() => {
      if (!detailElement.current?.open) return;
      setDetailRetryCount(count => count + 1);
      void fetchDetail();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [detailRetryCount, resolvedDetailState, fetchDetail]);
  useEffect(() => {
    if (resolvedDetailState !== "loading" || !detailElement.current?.open) return;
    const timer = window.setTimeout(() => setShowSlowLoading(true), 180);
    return () => window.clearTimeout(timer);
  }, [resolvedDetailState]);
  return <details ref={detailElement} className="news-row" onToggle={loadDetail} aria-busy={resolvedDetailState === "loading"}>
    <summary>
      <div className="news-row-stamp"><b>{row.category}</b><time title="媒体发布时间；列表按此时间排序">发布 {row.source_published_time ? time(row.source_published_time) : "未知"}</time><small title="系统第一次收到；决定模型当时能否看见">收到 {time(row.collector_first_seen_time)}</small><small className={`eligibility-badge eligibility-${row.model_visibility.toLowerCase().replaceAll("_", "-")}`}>{VISIBILITY_LABELS[row.model_visibility] ?? row.model_visibility.replaceAll("_", " ")}</small></div>
      <div className="news-row-title"><strong>{row.headline}</strong><small>{newsSourceLabel(row)}{row.emerging_topic_zh ? ` · ${row.emerging_topic_zh}` : ""}</small></div>
      <div className={`news-row-state state-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
        <b>{row.content_status === "FULL_TEXT" ? `${formatExactCount(row.content_characters)} 字符` : row.content_fetch_status === "UNAVAILABLE" ? "正文不可用" : row.content_fetch_status === "RETRYING" ? "自动重试中" : row.source === "google_news_gold_geopolitics" ? "聚合标题" : "等待正文"}</b>
        <small>{annotationStatus === "READY" ? (impactLabel ?? "等待 Gemma 判断") : annotationStatus === "NOT_REQUIRED" ? annotationReasonLabel : row.content_fetch_status === "UNAVAILABLE" ? "保留标题 · 不阻塞" : row.content_fetch_status === "RETRYING" ? "备用抓取中" : annotationStatus === "QUEUED" ? "AI 等待处理中" : annotationStatus === "BACKING_OFF" ? "失败后等待重试" : annotationStatus === "DEAD_LETTER" ? "已隔离待审" : "禁止判断"}</small>
      </div>
    </summary>
    <div className="news-row-detail">
      {resolvedDetailState === "loading" ? <section className={`news-detail-skeleton ${showSlowLoading ? "is-visible" : ""}`} aria-hidden="true"><i /><i /><i /></section>
      : resolvedDetailState === "error" ? <section className="gemini-summary summary-waiting"><span>详情暂未到达</span><p>{detailRetryCount >= 3 ? "自动重试已停止。" : "系统正在自动重试。"}<button type="button" onClick={retryDetail}>立即重试</button></p></section>
      : <>
        <div className="news-detail-top">
          <div className={`content-proof content-${row.content_status.toLowerCase().replaceAll("_", "-")}`}>
            {row.content_status === "FULL_TEXT" ? `✓ 已读取正式正文 · ${formatExactCount(row.content_characters)} 字符` : row.content_status === "SOURCE_CONTENT" ? `已读取来源内容 · ${formatExactCount(row.content_characters)} 字符` : row.content_fetch_status === "UNAVAILABLE" ? "发布网站拒绝自动读取或需要登录 · 仅保留标题，不进入模型" : row.content_fetch_status === "RETRYING" ? "首次抓取失败 · 系统将在退避结束后自动重试" : row.source === "google_news_gold_geopolitics" ? "Google News RSS 只提供聚合标题 · 未取得 publisher 正文" : "来源正文尚未抓取 · 禁止 Gemini 判断"}
          </div>
          {current.link && <a className="source-link" href={current.link} target="_blank" rel="noreferrer">阅读来源 ↗</a>}
        </div>
        {translated ? <p className="original-headline"><b>原文标题</b>{current.original_headline}</p> : null}
        {annotationStatus === "READY" ? <section className="gemini-summary">
          <span>GEMINI 中文摘要 · 完整读取 {formatExactCount(row.content_characters)} 字符</span><p>{current.summary_zh}</p>
        </section> : annotationStatus === "QUEUED" ? <section className="gemini-summary summary-queued">
          <span>中文摘要排队中</span><p>正文已经完整入库，不会截断；系统会依序生成中文摘要，标题翻译独立处理。</p>
        </section> : annotationStatus === "BACKING_OFF" ? <section className="gemini-summary summary-queued">
          <span>暂时退避</span><p>{current.annotation_reason ?? "本次模型响应未通过验证；系统已停止每分钟重试，将在退避到期后有限重试。"}</p>
        </section> : annotationStatus === "DEAD_LETTER" ? <section className="gemini-summary summary-waiting">
          <span>{annotationReasonLabel}</span><p>{current.annotation_reason ?? "相同永久错误重复出现，系统不会再自动消耗 Flash 配额；该新闻保留在 Ledger 中等待规则修复或人工复核。"}</p>
        </section> : annotationStatus === "NOT_REQUIRED" ? <section className="gemini-summary summary-queued">
          <span>{annotationReasonLabel}</span><p>{current.annotation_reason ?? "该新闻不满足当前解析条件，不会消耗 AI 配额或进入模型。"}</p>
        </section> : row.content_fetch_status === "UNAVAILABLE" ? <section className="gemini-summary summary-waiting">
          <span>来源正文不可自动读取</span><p>发布网站拒绝访问、要求登录或没有可提取正文；这类候选不会写入新闻库，也不会进入模型。</p>
        </section> : <section className="gemini-summary summary-waiting">
          <span>{row.content_fetch_status === "RETRYING" ? "正文自动重试中" : "等待来源正文"}</span><p>当前只有标题或短描述，不会进入模型，也不会假装已经理解内容。</p>
        </section>}
        <button className="news-secondary-toggle" type="button" aria-expanded={showSupportingEvidence} onClick={() => setShowSupportingEvidence(value => !value)}>{showSupportingEvidence ? "收起证据与时间线" : "查看证据、分类与时间线"}</button>
        <div className={`news-secondary-evidence ${showSupportingEvidence ? "is-open" : ""}`}>
          {annotationStatus === "READY" && <section className={`gemini-summary ${current.impact_status === "ACTIVE" ? "" : "summary-queued"}`}>
            <span>{impactLabels.join(" · ")}</span>
            <p>{publicImpactReason(current.impact_reason_zh) || "Gemma 将根据新闻内容判断它现在是否仍会影响市场。晚收到只影响可见时间，不会改写过去。"}</p>
          </section>}
          {current.event_type && <div className="news-classification"><b>{current.event_type}</b><span>鹰派 {impulse(current.hawkishness)}</span><span>通胀 {impulse(current.inflation_impulse)}</span><span>增长 {impulse(current.growth_impulse)}</span><span>地缘 {impulse(current.geopolitical_risk)}</span><span>美元 {impulse(current.usd_impulse)}</span><span>新颖 {number(current.novelty)}</span><span>置信 {number(current.confidence)}</span></div>}
          <dl className="news-timeline"><div><dt>媒体发布时间</dt><dd>{time(row.source_published_time)}</dd></div><div><dt>系统首次收到</dt><dd>{time(row.collector_first_seen_time)}</dd></div><div><dt>Gemini 完成时间</dt><dd>{time(current.parsed_at)}</dd></div><div><dt>采集延迟</dt><dd>{current.collection_delay_seconds == null ? "—" : `${number(current.collection_delay_seconds, 1)} 秒`}</dd></div><div><dt>处理延迟</dt><dd>{current.processing_delay_seconds == null ? "—" : `${number(current.processing_delay_seconds, 1)} 秒`}</dd></div><div><dt>模型权限</dt><dd>{current.source_eligibility ?? "—"} · {row.model_visibility}</dd></div></dl>
          <footer className="card-footer"><span>{current.entities?.join(" · ") || "无实体"}</span><span>{current.llm_model_version ?? "未标注"} · 收到 {time(row.collector_first_seen_time)} · 标注 {time(current.parsed_at)}</span></footer>
        </div>
      </>}
    </div>
  </details>;
}

export default function AuditView({ initialView }: { initialView: AuditDeskView }) {
  const cachedStatus = readDashboardResource<Payload>("/api/status");
  const cachedAudit = readDashboardResource<Partial<Payload>>("/api/audit");
  const cachedAuditBriefs = readDashboardResource<Partial<Payload>>(AUDIT_DETAIL_RESOURCES.briefs);
  const cachedAuditStories = readDashboardResource<Partial<Payload>>(AUDIT_DETAIL_RESOURCES.stories);
  const cachedAuditDecisions = readDashboardResource<Partial<Payload>>(AUDIT_DETAIL_RESOURCES.decisions);
  const cachedLearning = readDashboardResource<Partial<Payload>>("/api/learning");
  const cachedNewsIndex = readDashboardResource<NewsIndexResponse>(`/api/news-index?page=1&limit=${NEWS_PER_PAGE}&review_state=COMPLETED`);
  const [payload, setPayload] = useState<Payload | null>(() => cachedStatus
    ? ({
        ...cachedStatus, ...cachedAudit, ...cachedLearning,
        ...cachedAuditBriefs, ...cachedAuditStories, ...cachedAuditDecisions,
      } as Payload)
    : null);
  const [newsIndex, setNewsIndex] = useState<NewsIndexResponse>(() => (
    cachedNewsIndex ?? {
      items: [], total: 0, all_total: 0, category_counts: {}, page: 1,
      page_size: NEWS_PER_PAGE, totals_scope: "LOADING",
      review_state: "COMPLETED",
      review_state_counts: { COMPLETED: 0, PROCESSING: 0, ISOLATED: 0 },
    }
  ));
  const [statusState, setStatusState] = useState<CurrentDataPhase>(
    cachedStatus?.preview_status_summary ? "loading" : cachedStatus ? "ready" : "loading",
  );
  const [learningState, setLearningState] = useState<CurrentDataPhase | "idle">(
    cachedLearning?.learning_preview_summary ? "loading" : cachedLearning ? "ready" : "idle",
  );
  const [auditState, setAuditState] = useState<CurrentDataPhase | "idle">(
    cachedAudit ? "ready" : "idle",
  );
  const [auditDetailState, setAuditDetailState] = useState<Record<AuditDetailView, CurrentDataPhase | "idle">>({
    briefs: cachedAuditBriefs ? "ready" : "idle",
    stories: cachedAuditStories ? "ready" : "idle",
    decisions: cachedAuditDecisions ? "ready" : "idle",
  });
  const [auditDetailError, setAuditDetailError] = useState<Record<AuditDetailView, string | null>>({
    briefs: null, stories: null, decisions: null,
  });
  const [statusError, setStatusError] = useState<string | null>(null);
  const [learningError, setLearningError] = useState<string | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [newsDetails, setNewsDetails] = useState<Record<string, Partial<News>>>({});
  const [view, setView] = useState<AuditDeskView>(initialView);
  const pendingScrollTop = useRef<number | null>(null);
  const [briefDate, setBriefDate] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [searchTimeField, setSearchTimeField] = useState<"published" | "received">("published");
  const [searchDateFrom, setSearchDateFrom] = useState("");
  const [searchDateTo, setSearchDateTo] = useState("");
  const [searchResults, setSearchResults] = useState<NewsSearchResponse>(emptyNewsSearch);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [newsCategory, setNewsCategory] = useState("全部");
  const [newsPage, setNewsPage] = useState(1);
  const [newsReviewState, setNewsReviewState] = useState<NewsReviewState>("COMPLETED");
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const [showEvidenceMetrics, setShowEvidenceMetrics] = useState(false);
  const [showAllStoryEvents, setShowAllStoryEvents] = useState(false);
  const [showAllStorylines, setShowAllStorylines] = useState(false);
  const [expandedStorylines, setExpandedStorylines] = useState<Set<string>>(() => new Set());
  const pageDetailKeys = newsIndex.items
    .map(row => row.detail_key)
    .filter((key): key is string => Boolean(key))
    .join(",");
  useEffect(() => {
    if (view !== "news" || !pageDetailKeys) return;
    let cancelled = false;
    void loadDashboardResource<NewsDetailBatchResponse>(
      `/api/news-content?keys=${encodeURIComponent(pageDetailKeys)}`,
      { maxAgeMs: Number.POSITIVE_INFINITY },
    ).then(body => {
      if (cancelled) return;
      setNewsDetails(currentDetails => {
        const next = { ...currentDetails };
        for (const [key, item] of Object.entries(body.items)) next[key] = item.payload;
        return next;
      });
    }).catch(() => {
      // Opening one row still uses the existing single-detail retry path.
    });
    return () => { cancelled = true; };
  }, [pageDetailKeys, view]);
  const [graphOpen, setGraphOpen] = useState(false);
  const [graphStartTab, setGraphStartTab] = useState<"curve" | "execution">("curve");
  const fullStatusReadyRef = useRef(Boolean(cachedStatus && !cachedStatus.preview_status_summary));
  const fullLearningReadyRef = useRef(Boolean(cachedLearning && !cachedLearning.learning_preview_summary));
  const fullNewsIndexReadyRef = useRef(cachedNewsIndex?.totals_scope === "D1_ARCHIVE");
  const learningDataAvailableRef = useRef(Boolean(cachedLearning));
  const learningFailureCountRef = useRef(0);
  const [summaryCadence, setSummaryCadence] = useState<EvaluationCadence>("EVERY_5M");
  const [evidenceMode, setEvidenceMode] = useState<"eligible" | "seen" | "unseen">("eligible");
  const [evidencePage, setEvidencePage] = useState(1);
  const [evidencePageCursors, setEvidencePageCursors] = useState<Record<number, string | null>>({ 1: null });
  const evidenceCursor = evidencePageCursors[evidencePage] ?? null;
  const evidenceUrl = `/api/news-evidence?mode=${evidenceMode}&page=${evidencePage}&limit=${EVIDENCE_PER_PAGE}${evidenceCursor ? `&cursor=${encodeURIComponent(evidenceCursor)}` : ""}`;
  const [evidenceArchive, setEvidenceArchive] = useState<NewsEvidenceResponse>(() => (
    readDashboardResource<NewsEvidenceResponse>(evidenceUrl) ?? {
      items: [], page: 1, page_size: EVIDENCE_PER_PAGE, mode: "eligible",
    }
  ));

  const refreshStatus = useCallback(async (force = false) => {
    try {
      const body = await loadDashboardResource<Payload>("/api/status", { force });
      setPayload(previous => ({ ...previous, ...body }) as Payload);
      if (!body.preview_status_summary) fullStatusReadyRef.current = true;
      setStatusState(body.preview_status_summary ? "snapshot" : "ready");
      setStatusError(null);
    } catch (reason) {
      setStatusState("error");
      setStatusError(reason instanceof Error ? reason.message : "无法读取系统状态");
    }
  }, []);

  const refreshLearning = useCallback(async (force = false) => {
    setLearningState(previous => previous === "ready" ? previous : "loading");
    try {
      const body = await loadDashboardResource<Partial<Payload>>("/api/learning", { force });
      setPayload(previous => ({ ...previous, ...body }) as Payload);
      learningDataAvailableRef.current = true;
      learningFailureCountRef.current = 0;
      if (!body.learning_preview_summary) fullLearningReadyRef.current = true;
      setLearningState(body.learning_preview_summary ? "snapshot" : "ready");
      setLearningError(null);
    } catch (reason) {
      learningFailureCountRef.current += 1;
      setLearningState(learningDataAvailableRef.current ? "ready" : "error");
      setLearningError(
        !learningDataAvailableRef.current || learningFailureCountRef.current >= 2
          ? (reason instanceof Error ? reason.message : "无法读取学习进度")
          : null,
      );
    }
  }, []);

  const refreshAudit = useCallback(async (force = false) => {
    setAuditState(previous => previous === "ready" ? previous : "loading");
    try {
      const body = await loadDashboardResource<Partial<Payload>>("/api/audit", { force });
      setPayload(previous => ({ ...previous, ...body }) as Payload);
      setAuditState("ready");
      setAuditError(null);
    } catch (reason) {
      setAuditState("error");
      setAuditError(reason instanceof Error ? reason.message : "无法读取审计首屏");
    }
  }, []);

  const refreshAuditDetail = useCallback(async (
    detailView: AuditDetailView, force = false,
  ) => {
    setAuditDetailState(previous => ({
      ...previous,
      [detailView]: previous[detailView] === "ready" ? "ready" : "loading",
    }));
    try {
      const body = await loadDashboardResource<Partial<Payload>>(
        AUDIT_DETAIL_RESOURCES[detailView], { force },
      );
      setPayload(previous => ({ ...previous, ...body }) as Payload);
      setAuditDetailState(previous => ({ ...previous, [detailView]: "ready" }));
      setAuditDetailError(previous => ({ ...previous, [detailView]: null }));
    } catch (reason) {
      setAuditDetailState(previous => ({
        ...previous,
        [detailView]: previous[detailView] === "ready" ? "ready" : "error",
      }));
      setAuditDetailError(previous => ({
        ...previous,
        [detailView]: reason instanceof Error ? reason.message : "无法读取审计详情",
      }));
    }
  }, []);

  const refreshEvidence = useCallback(async (force = false) => {
    try {
      const body = await loadDashboardResource<NewsEvidenceResponse>(evidenceUrl, { force });
      setEvidenceArchive(body);
      if (body.page === 1) {
        setEvidencePageCursors({
          1: null, ...(body.next_cursor ? { 2: body.next_cursor } : {}),
        });
      }
      if (body.next_cursor) {
        setEvidencePageCursors(previous => ({
          ...previous, [body.page + 1]: body.next_cursor,
        }));
      }
      setEvidenceError(null);
    } catch (reason) {
      if (
        reason instanceof DashboardResourceError
        && reason.code === "NEWS_EVIDENCE_CURSOR_STALE"
      ) {
        const firstUrl = `/api/news-evidence?mode=${evidenceMode}&page=1&limit=${EVIDENCE_PER_PAGE}`;
        setEvidencePage(1);
        setEvidencePageCursors({ 1: null });
        setEvidenceArchive({
          items: [], page: 1, page_size: EVIDENCE_PER_PAGE, mode: evidenceMode,
        });
        try {
          const first = await loadDashboardResource<NewsEvidenceResponse>(
            firstUrl, { force: true },
          );
          setEvidenceArchive(first);
          setEvidencePageCursors({
            1: null, ...(first.next_cursor ? { 2: first.next_cursor } : {}),
          });
          setEvidenceError(null);
          return;
        } catch (reloadReason) {
          setEvidenceError(
            reloadReason instanceof Error
              ? reloadReason.message : "新闻证据代次变化后重新读取失败",
          );
          return;
        }
      }
      setEvidenceError(reason instanceof Error ? reason.message : "无法读取新闻证据档案");
    }
  }, [evidenceMode, evidenceUrl]);

  const refreshNews = useCallback(async (force = false) => {
    const query = new URLSearchParams({
      page: String(newsPage), limit: String(NEWS_PER_PAGE),
      review_state: newsReviewState,
    });
    if (newsCategory !== "全部") query.set("category", newsCategory);
    const body = await loadDashboardResource<NewsIndexResponse>(`/api/news-index?${query}`, { force });
    setNewsIndex(body);
    if (body.totals_scope === "D1_ARCHIVE") fullNewsIndexReadyRef.current = true;
    setNewsError(null);
  }, [newsCategory, newsPage, newsReviewState]);

  useEffect(() => {
    return scheduleDashboardRefresh(
      () => void refreshStatus(!fullStatusReadyRef.current),
      () => void refreshStatus(true),
      DASHBOARD_REFRESH_INTERVALS.status,
      "current",
      "status",
    );
  }, [refreshStatus]);

  useEffect(() => {
    if (view !== "news") {
      // The archive total is a headline metric, so start its bounded D1 read
      // even when another audit view is selected.  Do not poll off-screen;
      // the first news page is reused if the viewer opens the news desk.
      if (fullNewsIndexReadyRef.current) return;
      const initial = window.setTimeout(() => {
        void refreshNews(true).catch(reason => setNewsError(
          reason instanceof Error ? reason.message : "无法读取新闻索引",
        ));
      }, 0);
      return () => window.clearTimeout(initial);
    }
    return scheduleDashboardRefresh(
      () => void refreshNews(!fullNewsIndexReadyRef.current).catch(reason => setNewsError(
        reason instanceof Error ? reason.message : "无法读取新闻索引",
      )),
      () => void refreshNews(true).catch(reason => setNewsError(
        reason instanceof Error ? reason.message : "无法读取新闻索引",
      )),
      DASHBOARD_REFRESH_INTERVALS.news,
      "current",
      `news-index:${newsReviewState}:${newsCategory}:${newsPage}`,
    );
  }, [refreshNews, view, newsCategory, newsPage, newsReviewState]);

  useEffect(() => {
    if (view !== "league") return;
    // The embedded Preview summary keeps first paint small.  Fetch the complete
    // D1 ledger as soon as the league is visible so the modal never waits for
    // the next polling interval.
    return scheduleDashboardRefresh(
      () => void refreshLearning(!fullLearningReadyRef.current),
      () => void refreshLearning(true),
      DASHBOARD_REFRESH_INTERVALS.learning,
      "current",
      "learning",
    );
  }, [refreshLearning, view]);

  const selectedAuditDetailState = view in AUDIT_DETAIL_RESOURCES
    ? auditDetailState[view as AuditDetailView] : null;

  useEffect(() => {
    return scheduleDashboardRefresh(
      () => void refreshAudit(auditState !== "ready"),
      () => void refreshAudit(true),
      DASHBOARD_REFRESH_INTERVALS.status,
      "current",
      "audit",
    );
  }, [auditState, refreshAudit]);

  useEffect(() => {
    if (!(view in AUDIT_DETAIL_RESOURCES)) return;
    const detailView = view as AuditDetailView;
    return scheduleDashboardRefresh(
      () => void refreshAuditDetail(
        detailView, selectedAuditDetailState !== "ready",
      ),
      () => void refreshAuditDetail(detailView, true),
      DASHBOARD_REFRESH_INTERVALS.status,
      "current",
      `audit-detail:${detailView}`,
    );
  }, [refreshAuditDetail, selectedAuditDetailState, view]);

  useEffect(() => {
    if (view !== "evidence") return;
    return scheduleDashboardRefresh(
      () => void refreshEvidence(false),
      () => void refreshEvidence(true),
      DASHBOARD_REFRESH_INTERVALS.news,
      "current",
      `news-evidence:${evidenceMode}:${evidencePage}`,
    );
  }, [evidenceMode, evidencePage, refreshEvidence, view]);

  const openLearningGraph = (tab: "curve" | "execution") => {
    setGraphStartTab(tab);
    setGraphOpen(true);
    if (!fullLearningReadyRef.current) void refreshLearning(true);
  };

  const selectView = (next: AuditDeskView) => {
    pendingScrollTop.current = window.scrollY;
    setView(next);
    window.history.replaceState(null, "", `/audit?view=${next}`);
  };

  useLayoutEffect(() => {
    if (pendingScrollTop.current === null) return;
    const cancel = settleResponsiveScroll(options => window.scrollTo(options), () => window.scrollY, pendingScrollTop.current!);
    pendingScrollTop.current = null;
    return cancel;
  }, [view]);

  const runNewsSearch = async (page = 1, applied?: NewsSearchResponse) => {
    const query = applied?.query ?? searchInput.trim();
    const appliedFilters = applied?.filters;
    if (!query && !searchDateFrom && !searchDateTo && !appliedFilters) {
      setSearchResults(emptyNewsSearch());
      setSearchError(null);
      return;
    }
    setSearchBusy(true);
    try {
      const params = new URLSearchParams({ page: String(page), limit: "10" });
      if (query) params.set("q", query);
      if (appliedFilters) {
        for (const [name, value] of Object.entries(appliedFilters)) {
          if (value) params.set(name, value);
        }
      } else {
        if (searchDateFrom) params.set(`${searchTimeField}_from`, searchDateFrom);
        if (searchDateTo) params.set(`${searchTimeField}_to`, searchDateTo);
      }
      setSearchResults(await loadDashboardResource<NewsSearchResponse>(`/api/news-search?${params}`, { force: true }));
      setSearchError(null);
    } catch (reason) {
      setSearchError(reason instanceof Error ? reason.message : "新闻搜索暂不可用");
    } finally {
      setSearchBusy(false);
    }
  };

  const progress = useMemo(() => {
    const training = payload?.training;
    if (!training) return 0;
    return Math.min(100, training.complete_rows / training.next_training_at * 100);
  }, [payload]);

  const archiveTotals = authoritativeNewsTotals(newsIndex);
  const newsPhase: CurrentDataPhase = archiveTotals
    ? "ready" : newsError ? "error" : "loading";
  const branchSnapshotStatusPaths = payload?.preview?.branch_snapshot?.status_paths;
  const coveragePhase = statusFieldPhase(
    statusState, branchSnapshotStatusPaths, "factor_coverage",
  );
  const pageUsesBranchSnapshot = view === "coverage"
    && statusState === "ready" && coveragePhase === "snapshot";
  const currentPagePhase: CurrentDataPhase = statusState === "error"
    || (view === "news" && newsPhase === "error")
    || (view === "league" && learningState === "error")
    ? "error"
    : statusState === "snapshot" || pageUsesBranchSnapshot
      || (view === "league" && learningState === "snapshot")
      ? "snapshot"
      : statusState === "loading"
        || (view === "news" && newsPhase === "loading")
        || (view === "league" && learningState !== "ready")
        ? "loading" : "ready";
  const categories = useMemo(() => [
    {
      name: "全部",
      count: archiveTotals
        ? newsIndex.review_state_counts?.[newsReviewState] ?? 0
        : null,
    },
    ...CATEGORY_ORDER.filter(name => newsIndex.category_counts[name]).map(name => ({
      name, count: archiveTotals ? newsIndex.category_counts[name] ?? 0 : null,
    })),
  ], [archiveTotals, newsIndex.category_counts, newsIndex.review_state_counts, newsReviewState]);
  const newsPageCount = archiveTotals
    ? Math.max(1, Math.ceil(archiveTotals.category / NEWS_PER_PAGE))
    : 1;
  const currentNewsPage = Math.min(newsPage, newsPageCount);
  const visibleNews = (newsIndex.review_state ?? "COMPLETED") === newsReviewState
    ? newsIndex.items
    : [];
  const emptyNewsRows = Math.max(0, NEWS_PER_PAGE - visibleNews.length);
  const activeLearningModels = (payload?.learning_curves?.models ?? []).filter(
    row => row.active_rank !== null,
  );
  const activeLearningIdentities = new Set(
    activeLearningModels.map(row => row.model_identity),
  ).size;
  const liveOosModelGroups = learningState === "ready"
    ? activeLearningIdentities
    : payload?.counts?.live_oos_model_groups;
  const liveOosPhase: CurrentDataPhase = learningState === "ready"
    ? "ready"
    : payload?.counts?.live_oos_model_groups !== undefined
      ? statusState
      : learningState === "idle" ? "loading" : learningState;
  const liveOosHeadlinePhase: CurrentDataPhase =
    liveOosModelGroups === undefined && view !== "league" ? "ready" : liveOosPhase;
  const liveOosHeadline = liveOosModelGroups === undefined
    ? "点击查看" : `${liveOosModelGroups}组`;
  const latestVersionGroups = (payload?.learning_curves?.version_groups ?? []).filter(
    row => row.lifecycle_status === "LATEST",
  );
  const directionPoolRows = Math.max(0, ...latestVersionGroups
    .filter(row => !row.model_identity.endsWith("NEWS_RESIDUAL"))
    .map(row => row.training_rows));
  const newsMetrics = resolveNewsMetrics(payload);
  const readableNewsTotal = archiveTotals?.readable ?? null;
  const parsedNewsTotal = archiveTotals?.parsed ?? null;
  const newsWaitingTotal = archiveTotals
    ? newsIndex.review_state_counts?.PROCESSING ?? 0
    : null;
  const isolatedNewsTotal = archiveTotals
    ? newsIndex.review_state_counts?.ISOLATED ?? 0
    : null;
  const rowsUntilTraining = statusState === "ready" && payload?.training
    ? Math.max(0, payload.training.next_training_at - payload.training.complete_rows)
    : null;
  const trainingProgress = progressCountPresentation(
    payload?.training?.complete_rows,
    payload?.training?.next_training_at,
  );
  const combinedErrors = [
    statusError && `系统状态：${statusError}`,
    auditError && `审计首屏：${auditError}`,
    view === "evidence" && evidenceError && `新闻证据：${evidenceError}`,
    view === "league" && learningError && `学习进度：${learningError}`,
    view === "news" && newsError && `新闻索引：${newsError}`,
    view in AUDIT_DETAIL_RESOURCES
      && auditDetailError[view as AuditDetailView]
      && `审计详情：${auditDetailError[view as AuditDetailView]}`,
  ].filter(Boolean).join(" · ");
  const legacyEvidence = useMemo(
    () => mergeNewsEvidenceByEvent(payload?.news_evidence ?? []),
    [payload?.news_evidence],
  );
  const evidenceArchiveReady = Boolean(
    evidenceArchive.snapshot_id && evidenceArchive.mode === evidenceMode,
  );
  const canonicalEvidence = useMemo(
    () => evidenceArchiveReady
      ? mergeNewsEvidenceByEvent(evidenceArchive.items) : legacyEvidence,
    [evidenceArchive.items, evidenceArchiveReady, legacyEvidence],
  );
  const evidencePayloadHasDuplicates = (
    legacyEvidence.length !== (payload?.news_evidence ?? []).length
  );
  const repairLegacyDuplicateSummary = (
    !evidenceArchiveReady && evidencePayloadHasDuplicates
  );
  const seenEvidenceCount = canonicalEvidence.filter(row => row.model_seen).length;
  const unseenEvidenceCount = canonicalEvidence.length - seenEvidenceCount;
  const eligibleEvidenceCount = canonicalEvidence.filter(row => row.broad_model_eligible).length;
  const evidenceDecisionExposures = canonicalEvidence.reduce(
    (total, row) => total + row.frozen_decisions, 0,
  );
  const evidenceModelUses = canonicalEvidence.reduce(
    (total, row) => total + row.frozen_model_uses, 0,
  );
  const evidenceSummarySeenCount = repairLegacyDuplicateSummary ? seenEvidenceCount : newsMetrics.events.used_in_predictions;
  const evidenceSummaryUnseenCount = repairLegacyDuplicateSummary ? unseenEvidenceCount : newsMetrics.events.never_used;
  const evidenceSummaryEligibleCount = repairLegacyDuplicateSummary ? eligibleEvidenceCount : newsMetrics.events.currently_model_eligible;
  const evidenceSummaryDecisionExposures = repairLegacyDuplicateSummary ? evidenceDecisionExposures : newsMetrics.prediction_usage.decision_event_exposures;
  const evidenceSummaryModelUses = repairLegacyDuplicateSummary ? evidenceModelUses : newsMetrics.prediction_usage.frozen_model_uses;
  const visibleEvidence = canonicalEvidence.filter(row => (
    evidenceMode === "eligible" ? row.broad_model_eligible
      : evidenceMode === "seen" ? row.model_seen : !row.model_seen
  ));
  const evidenceModeTotal = evidenceMode === "eligible" ? evidenceSummaryEligibleCount
    : evidenceMode === "seen" ? evidenceSummarySeenCount : evidenceSummaryUnseenCount;
  const evidenceWindowPartial = visibleEvidence.length < evidenceModeTotal;
  const selectEvidenceMode = (mode: "eligible" | "seen" | "unseen") => {
    setEvidenceMode(mode);
    setEvidencePage(1);
    setEvidencePageCursors({ 1: null });
    setShowAllEvidence(false);
  };
  const deploymentPresentation = DEPLOYMENT_PRESENTATION[
    payload?.system?.deployment?.status ?? "PROVENANCE_UNKNOWN"
  ] ?? DEPLOYMENT_PRESENTATION.PROVENANCE_UNKNOWN;
  const continuedEventTotal = payload?.storyline_summary?.total ?? 0;
  const singleEventTotal = payload?.storyline_summary?.candidate_total ?? 0;
  const activeEventTotal = continuedEventTotal + singleEventTotal;
  return (
    <main className={`audit-main audit-view-${view}`}>
      <section className="audit-intro">
        <div><p className="eyebrow">IMMUTABLE FORWARD EVIDENCE</p><h1>新闻先被看见，<br />决定随后产生。</h1></div>
        <div
          className="training-card"
          aria-label={statusState === "ready"
            ? `学习进度：已收集 ${formatExactCount(payload?.training?.complete_rows)} 条，目标 ${formatExactCount(payload?.training?.next_training_at)} 条，还差 ${formatExactCount(rowsUntilTraining)} 条`
            : "学习进度暂不可用"}
        >
          <div className="training-card-head"><span>学习进度</span></div>
          <div className="training-card-total">
            <strong>{statusState === "ready" && payload?.training
              ? <span className="training-progress-pair" aria-hidden="true">
                <span>{trainingProgress.current.main}{trainingProgress.current.remainder && <span className="training-progress-tail">+{trainingProgress.current.remainder}</span>}</span>
                <i>/</i>
                <span>{trainingProgress.target.main}{trainingProgress.target.remainder && <span className="training-progress-tail">+{trainingProgress.target.remainder}</span>}</span>
              </span>
              : <small>{statusState === "loading" ? "读取中…" : "暂不可用"}</small>}
            </strong>
            <span>{rowsUntilTraining === null ? "等待数据" : rowsUntilTraining === 0 ? "可以开始下一轮" : `还差 ${formatExactCount(rowsUntilTraining)} 条`}</span>
          </div>
          {statusState === "ready" && trainingProgress.showExactDetail && (
            <small className="training-card-exact">
              当前 {trainingProgress.current.exact} · 目标 {trainingProgress.target.exact}
            </small>
          )}
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
        </div>
      </section>

      {combinedErrors && <div className="error-banner">{combinedErrors}。页面会保留上一份成功数据并自动重试。</div>}
      <CurrentDataNotice
        phase={currentPagePhase}
        snapshotKind={pageUsesBranchSnapshot ? "branch" : "fallback"}
        snapshotTime={pageUsesBranchSnapshot
          ? payload?.preview?.branch_snapshot?.generated_at
            ? time(payload.preview.branch_snapshot.generated_at) : null
          : payload?.generated_at ? time(payload.generated_at) : null}
      />

      <div className="audit-tabs-shell">
      <nav className="audit-tabs" aria-label="审计视图">
        <a href="/audit?view=briefs" className={view === "briefs" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("briefs"); }}>每日简报 <b><MetricValue phase={statusState}>{payload?.daily_news_brief_summary?.brief_date ? shortBriefDate(payload.daily_news_brief_summary.brief_date) : "—"}</MetricValue></b></a>
        <a href="/audit?view=search" className={view === "search" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("search"); }}>搜索 <b aria-hidden="true">⌕</b></a>
        <a href="/audit?view=news" className={view === "news" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("news"); }}>新闻 <b><MetricValue phase={newsPhase}><CountValue value={readableNewsTotal} /></MetricValue></b></a>
        <a href="/audit?view=evidence" className={view === "evidence" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("evidence"); }}>当前可用新闻事件 <b><MetricValue phase={statusState}><CountValue value={newsMetrics.events.currently_model_eligible} /></MetricValue></b></a>
        <a href="/audit?view=stories" className={view === "stories" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("stories"); }}>事件脉络 <b><MetricValue phase={statusState}><CountValue value={activeEventTotal} /></MetricValue></b></a>
        <a href="/audit?view=decisions" className={view === "decisions" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("decisions"); }}>决策与30分钟结果 <b><MetricValue phase={statusState}><CountValue value={payload?.counts?.decision_events} /></MetricValue></b></a>
        <a href="/audit?view=league" className={view === "league" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("league"); }}>Live OOS 学习曲线 <b><MetricValue phase={liveOosHeadlinePhase}>{liveOosHeadline}</MetricValue></b></a>
        <a href="/audit?view=coverage" className={view === "coverage" ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView("coverage"); }}>大视野覆盖 <b><MetricValue phase={coveragePhase} snapshotLabel="分支快照" snapshotTitle="此覆盖结果由当前 PR 分支在构建时重新计算，不是生产实时观测">{payload?.factor_coverage?.filter(row => row.status === "LIVE" || row.status === "COLLECTING").length ?? 0}/11</MetricValue></b></a>
      </nav>
      </div>

      <label className="audit-view-picker">
        <span>证据台页面</span>
        <select aria-label="切换证据台页面" value={view} onChange={event => selectView(event.currentTarget.value as AuditDeskView)}>
          <option value="briefs">每日简报{payload?.daily_news_brief_summary?.brief_date ? ` · ${shortBriefDate(payload.daily_news_brief_summary.brief_date)}` : ""}</option>
          <option value="search">搜索新闻</option>
          <option value="news">新闻 · {formatExactCount(readableNewsTotal)}</option>
          <option value="evidence">当前可用新闻事件 · {formatExactCount(newsMetrics.events.currently_model_eligible)}</option>
          <option value="stories">事件脉络 · {formatExactCount(activeEventTotal)}</option>
          <option value="decisions">决策与30分钟结果 · {formatExactCount(payload?.counts?.decision_events)}</option>
          <option value="league">Live OOS 学习曲线 · {liveOosModelGroups === undefined ? "点击查看" : `${formatExactCount(liveOosModelGroups)}组`}</option>
          <option value="coverage">大视野覆盖 · {formatExactCount(payload?.factor_coverage?.filter(row => row.status === "LIVE" || row.status === "COLLECTING").length)}/11</option>
        </select>
      </label>

      {selectedAuditDetailState === "loading" && <div className="current-data-notice is-loading" role="status"><b>审计详情读取中</b><span>当前页面尚未加载，不会显示为零或空资料。</span></div>}
      {selectedAuditDetailState === "error" && <div className="current-data-notice is-error" role="alert"><b>审计详情暂不可用</b><span>页面会自动重试，不会把缺失资料解释为空。</span></div>}

      {view === "briefs" && selectedAuditDetailState === "ready" && (() => {
        const briefs = payload?.daily_news_briefs ?? [];
        const summary = payload?.daily_news_brief_summary;
        const dates = Array.from(new Set([summary?.brief_date, ...briefs.map(row => row.brief_date)].filter((value): value is string => Boolean(value))));
        const selectedDate = dates.includes(briefDate) ? briefDate : (dates[0] ?? "");
        const recentDates = dates.slice(0, DAILY_BRIEF_VISIBLE_DATES);
        const historicalDates = dates.slice(DAILY_BRIEF_VISIBLE_DATES);
        const selected = briefs.find(row => row.brief_date === selectedDate);
        const isCurrent = selectedDate === summary?.brief_date;
        const phase = isCurrent ? summary?.phase : selected?.phase;
        const reviewed = isCurrent ? summary?.reviewed_items : selected?.reviewed_items;
        const pending = isCurrent ? summary?.pending_items : selected?.pending_items;
        const terminal = isCurrent ? summary?.terminal_failure_items : selected?.terminal_failure_items;
        const lastGenerated = isCurrent ? summary?.last_generated_at : selected?.generated_at;
        const generatedByGemma = selected && !selected.model_version.startsWith("system-");
        const qualityNote = phase === "DEGRADED"
          ? generatedByGemma
            ? `${formatExactCount(terminal)} 条资料未纳入：正文缺失或复核失败。`
            : terminal
              ? `Gemma 汇总未生成，当前为系统整理版；另有 ${formatExactCount(terminal)} 条资料因正文缺失或复核失败未纳入。`
              : "Gemma 汇总未生成，当前为系统整理版。"
          : null;
        const overview = selected?.brief.overview?.trim() || null;
        const drivers = selected?.brief.drivers?.map(driver => driver.trim()).filter(Boolean) ?? [];
        const watchNext = selected?.brief.watch_next?.trim() || null;
        const visibleEvidence = selected?.brief.items.slice(0, 2) ?? [];
        const remainingEvidence = selected?.brief.items.slice(2) ?? [];
        const renderBriefItem = (item: DailyNewsBrief["brief"]["items"][number], index: number) => <li key={`${selectedDate}-${index}`}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <h3>{item.headline}</h3>
            <p>{item.summary}</p>
            <small>{formatExactCount(item.evidence_ids.length)} 份来源证据</small>
          </div>
        </li>;
        return <section className="daily-brief-desk">
          <header><div><p className="eyebrow">{selectedDate ? `${shortBriefDate(selectedDate)} · DAILY BRIEF · ASIA/KUALA_LUMPUR` : "DAILY BRIEF · ASIA/KUALA_LUMPUR"}</p><h2>{selected?.brief.title ?? (selectedDate ? `${shortBriefDate(selectedDate)} 每日简报` : "每日简报")}</h2><p className={`brief-phase phase-${(phase ?? "WAITING").toLowerCase()}`}>{dailyBriefPhaseLabel(phase, isCurrent)}</p></div>
            <div className="brief-date-switcher">
              <nav aria-label="最近简报日期">{recentDates.map(date => { const row = briefs.find(item => item.brief_date === date); const isToday = date === summary?.brief_date; const datePhase = isToday ? summary.phase : row?.phase; return <button type="button" key={date} className={selectedDate === date ? "active" : ""} onClick={() => setBriefDate(date)}><span>{shortBriefDate(date)}</span><small>{dailyBriefDateLabel(datePhase, isToday)}</small></button>; })}</nav>
              {historicalDates.length > 0 && <label className="brief-history-picker">
                <span>历史简报</span>
                <select aria-label="选择更早的每日简报" value={historicalDates.includes(selectedDate) ? selectedDate : ""} onChange={event => event.currentTarget.value && setBriefDate(event.currentTarget.value)}>
                  <option value="">更早日期</option>
                  {historicalDates.map(date => <option key={date} value={date}>{shortBriefDate(date)} · {dailyBriefDateLabel(briefs.find(item => item.brief_date === date)?.phase, false)}</option>)}
                </select>
              </label>}
            </div>
          </header>
          <div className="brief-progress">
            <strong>本版依据 {formatExactCount(reviewed)} 条已复核资料</strong>
            <span>{pending === null || pending === undefined ? "资料范围确认中" : pending > 0 ? "新资料会纳入下一版" : "资料整理完成"}</span>
            {lastGenerated && <small>更新于 {time(lastGenerated)}</small>}
            {qualityNote && <p className="brief-quality-note">{qualityNote}</p>}
          </div>
          {selected ? <>
            <div className={`brief-overview ${generatedByGemma ? "is-gemma" : "is-fallback"} ${overview ? "" : "is-missing"}`}>
              <div className="brief-overview-head">
                <strong>{isCurrent ? "今日黄金脉络" : "当日黄金脉络"}</strong>
              </div>
              {overview
                ? <p className="brief-overview-lead">{overview}</p>
                : <p className="brief-overview-missing">这版没有保存总摘要，可展开查看重点依据。</p>}
              {(drivers.length > 0 || watchNext) && <div className="brief-overview-points">
                {drivers.length > 0 && <section>
                  <span>关键驱动</span>
                  <ul>{drivers.map((driver, index) => <li key={`${selectedDate}-driver-${index}`}>{driver}</li>)}</ul>
                </section>}
                {watchNext && <section>
                  <span>接下来关注</span>
                  <p>{watchNext}</p>
                </section>}
              </div>}
            </div>
            {visibleEvidence.length > 0 && <section className="brief-evidence">
              <header className="brief-evidence-head"><strong>重点依据</strong><span>先看最关键的新闻脉络</span></header>
              <ol>{visibleEvidence.map(renderBriefItem)}</ol>
              {remainingEvidence.length > 0 && <details key={selectedDate} className="brief-evidence-stories">
                <summary><span>再看 {formatExactCount(remainingEvidence.length)} 个依据</span><small>标题、摘要与来源证据</small></summary>
                <ol>{remainingEvidence.map((item, index) => renderBriefItem(item, index + visibleEvidence.length))}</ol>
              </details>}
            </section>}
            <footer>{phase === "FINAL" || phase === "DEGRADED" ? "该日期已完成" : "随已复核资料滚动更新"} · 第 {selected.revision_number} 版 · 仅供阅读，不进入模型训练</footer>
          </> : <p className="brief-empty">{phase === "EMPTY" ? `${shortBriefDate(selectedDate)} 没有符合简报范围的新闻。` : `等待 ${shortBriefDate(selectedDate)} 首批已复核新闻。系统会在有足够资料后生成，并随新资料持续更新。`}</p>}
        </section>;
      })()}

      {view === "search" && <section className="news-search-desk">
        <form onSubmit={event => { event.preventDefault(); void runNewsSearch(); }}>
          <label htmlFor="news-search">搜索新闻</label>
          <div className="search-query-row"><input id="news-search" value={searchInput} maxLength={80} onChange={event => setSearchInput(event.target.value)} placeholder="标题、来源或主题" /><button type="submit" disabled={searchBusy}>{searchBusy ? "搜索中" : "搜索"}</button></div>
          <fieldset className="search-filter-grid">
            <legend>可选日期范围</legend>
            <label htmlFor="news-search-time-field">时间字段<select id="news-search-time-field" value={searchTimeField} onChange={event => setSearchTimeField(event.target.value as "published" | "received")}><option value="published">媒体发布</option><option value="received">系统收到</option></select></label>
            <label htmlFor="news-search-from">从<input id="news-search-from" type="date" value={searchDateFrom} onChange={event => setSearchDateFrom(event.target.value)} /></label>
            <label htmlFor="news-search-to">到<input id="news-search-to" type="date" value={searchDateTo} onChange={event => setSearchDateTo(event.target.value)} /></label>
          </fieldset>
        </form>
        {searchError && <p className="search-error" role="alert">{searchError}</p>}
        {searchResults.source_mode !== "NOT_QUERIED" && <p className="search-count">{searchResults.query ? `“${searchResults.query}”` : "所选日期范围"} 找到 <CountValue value={searchResults.total} format="exact" /> 条 · {searchResults.source_mode === "IMMUTABLE_PREVIEW_SNAPSHOT" ? "Preview 构建快照（非完整档案）" : "当前新闻档案"}</p>}
        <div className="search-results">{searchResults.items.map(row => <article key={row.detail_key}><time>{time(row.source_published_time ?? row.collector_first_seen_time)}</time><h3>{row.headline}</h3><p>{row.emerging_topic_zh || publicImpactReason(row.impact_reason_zh) || row.source}</p><small>{row.source} · {row.category} · 证据 {row.detail_key.slice(0, 12)}…</small></article>)}</div>
        {searchResults.source_mode !== "NOT_QUERIED" && searchResults.total === 0 && <p className="search-empty">没有符合条件的新闻证据。</p>}
        {searchResults.total > searchResults.page_size && <nav className="search-pages" aria-label="搜索结果分页"><button type="button" aria-label="上一页搜索结果" disabled={searchResults.page <= 1 || searchBusy} onClick={() => void runNewsSearch(searchResults.page - 1, searchResults)}>←</button><span>{formatExactCount(searchResults.page)} / {formatExactCount(Math.ceil(searchResults.total / searchResults.page_size))}</span><button type="button" aria-label="下一页搜索结果" disabled={searchResults.page >= Math.ceil(searchResults.total / searchResults.page_size) || searchBusy} onClick={() => void runNewsSearch(searchResults.page + 1, searchResults)}>→</button></nav>}
      </section>}
      {view === "news" && <>
        <section className="annotation-queue" aria-label="新闻处理进度">
          <span><b><CountValue value={readableNewsTotal} /></b> {readableNewsTotal === null ? "正在读取近60天新闻总量" : "条近60天可读新闻"}</span>
          <span><b><CountValue value={parsedNewsTotal} /></b> 条语义复核完成</span>
          <span><b><CountValue value={newsWaitingTotal} /></b> 条等待处理</span>
          <span><b><CountValue value={isolatedNewsTotal} /></b> 条已隔离待查</span>
          <span className="is-model-ready"><b><CountValue value={newsMetrics.events.currently_model_eligible} /></b> 个当前可用事件</span>
          <details>
            <summary>查看处理器技术状态</summary>
            <p>真正排队 {formatExactCount(payload?.annotation_queue?.queued)} · 失败后等待重试 {formatExactCount(payload?.annotation_queue?.backing_off)} · 已隔离 {formatExactCount(payload?.annotation_queue?.dead_letter)} · 等待正文 {formatExactCount(payload?.annotation_queue?.waiting_content)} · 正文不可用 {formatExactCount(payload?.annotation_queue?.unavailable_content)}</p>
          </details>
        </section>
        <nav className="news-review-zones" aria-label="新闻审核区域">
          {(Object.keys(NEWS_REVIEW_PRESENTATION) as NewsReviewState[]).map(state => {
            const presentation = NEWS_REVIEW_PRESENTATION[state];
            return <button
              key={state}
              type="button"
              className={newsReviewState === state ? "active" : ""}
              aria-pressed={newsReviewState === state}
              onClick={() => {
                setNewsReviewState(state);
                setNewsCategory("全部");
                setNewsPage(1);
              }}
            >
              <span>{presentation.label}</span>
              <b><CountValue value={archiveTotals ? newsIndex.review_state_counts?.[state] ?? 0 : null} /></b>
              <small>{presentation.description}</small>
            </button>;
          })}
        </nav>
        <section className="news-browser" aria-label="新闻自动分类">
          <div><strong>{NEWS_REVIEW_PRESENTATION[newsReviewState].label}新闻</strong><span>{archiveTotals ? `${NEWS_REVIEW_PRESENTATION[newsReviewState].description} · 按媒体发布时间排序 · 每页 ${formatExactCount(NEWS_PER_PAGE)} 条` : `正在读取近60天新闻总量 · 每页 ${formatExactCount(NEWS_PER_PAGE)} 条`}</span></div>
          <nav>
            {categories.map(category => <button key={category.name} type="button" className={newsCategory === category.name ? "active" : ""} onClick={() => { setNewsCategory(category.name); setNewsPage(1); }}>
              {category.name}{category.count !== null && <b><CountValue value={category.count} /></b>}
            </button>)}
          </nav>
          <label className="news-category-picker">
            <span>新闻分类</span>
            <select value={newsCategory} onChange={(event) => { setNewsCategory(event.target.value); setNewsPage(1); }}>
              {categories.map(category => <option key={category.name} value={category.name}>{category.name}{category.count !== null ? ` · ${formatExactCount(category.count)}` : ""}</option>)}
            </select>
          </label>
        </section>
        <section className="news-table">
          <header className="news-table-head"><span>分类 / 发布时间</span><span>新闻与来源</span><span>正文 / 状态</span></header>
          {visibleNews.map(row => <NewsRow
            key={`${row.source}-${row.source_item_id}-${row.revision_number}`}
            row={row}
            prefetchedDetail={newsDetails[row.detail_key]}
          />)}
          {Array.from({ length: emptyNewsRows }, (_, index) => <div className="news-row-placeholder" aria-hidden="true" key={`empty-news-row-${index}`} />)}
        </section>
        {newsPageCount > 1 && <nav className="news-pagination" aria-label="新闻分页">
          <button type="button" disabled={currentNewsPage === 1} onClick={() => setNewsPage(page => Math.max(1, page - 1))}>← 上一页</button>
          <span>第 <b>{formatExactCount(currentNewsPage)}</b> / {formatExactCount(newsPageCount)} 页 · {NEWS_REVIEW_PRESENTATION[newsReviewState].label} · 当前分类 {formatExactCount(newsIndex.total)} 条</span>
          <button type="button" disabled={currentNewsPage === newsPageCount} onClick={() => setNewsPage(page => Math.min(newsPageCount, page + 1))}>下一页 →</button>
        </nav>}
      </>}

      {view === "evidence" && <section className="evidence-desk">
        <header className="evidence-intro evidence-intro-compact">
          <div><p className="eyebrow">NEWS USED BY MODEL</p><h2>模型真正用过哪些新闻？</h2><p>按独立事件说明模型用过什么、没用什么。</p></div>
        </header>
        <button className="evidence-metrics-toggle" type="button" aria-expanded={showEvidenceMetrics} onClick={() => setShowEvidenceMetrics(value => !value)}>{showEvidenceMetrics ? "收起统计口径" : "查看统计口径"}<span>{formatExactCount(evidenceSummarySeenCount)} 个事件历史上用过 · {formatExactCount(evidenceSummaryEligibleCount)} 个现在可用</span></button>
        <div className={`evidence-metrics-block ${showEvidenceMetrics ? "is-open" : ""}`}>
          <div className="evidence-summary">
            <article><span>收到多少篇文章</span><strong><CountValue value={newsMetrics.articles.received} /></strong><small>共保存 {formatExactCount(newsMetrics.articles.stored_revisions)} 个版本；文章更新不会算成新文章</small></article>
            <article><span>历史上用过多少个事件</span><strong><CountValue value={evidenceSummarySeenCount} /></strong><small>每个都确实参加过至少一次预测</small></article>
            <article><span>影响过多少次预测</span><strong><CountValue value={evidenceSummaryDecisionExposures} /></strong><small>同一事件可以连续影响多个 5 分钟预测</small></article>
            <article><span>模型一共读取多少次</span><strong><CountValue value={evidenceSummaryModelUses} /></strong><small>5 套模型分别记账；这不是新闻数量</small></article>
            <article><span>从未进入预测的事件</span><strong><CountValue value={evidenceSummaryUnseenCount} /></strong><small>可在下方逐条查看没有使用的原因</small></article>
            <article><span>现在仍可用于预测</span><strong><CountValue value={evidenceSummaryEligibleCount} /></strong><small>等待下一次预测读取；不代表历史上用过</small></article>
          </div>
          <p className="evidence-count-note"><b>{formatExactCount(newsMetrics.training.current_contract_rows)} 条训练记录</b> 来自 <b>{formatExactCount(newsMetrics.training.distinct_events)} 个当前契约事件</b>；文章、独立事件、预测读取和训练记录是四种不同口径。</p>
        </div>
        <nav className="evidence-filters" aria-label="模型新闻可见性筛选">
          <button type="button" className={evidenceMode === "eligible" ? "active" : ""} onClick={() => selectEvidenceMode("eligible")}>当前可用 <b><CountValue value={evidenceSummaryEligibleCount} /></b></button>
          <button type="button" className={evidenceMode === "seen" ? "active" : ""} onClick={() => selectEvidenceMode("seen")}>历史上用过 <b><CountValue value={evidenceSummarySeenCount} /></b></button>
          <button type="button" className={evidenceMode === "unseen" ? "active" : ""} onClick={() => selectEvidenceMode("unseen")}>从未用过 <b><CountValue value={evidenceSummaryUnseenCount} /></b></button>
        </nav>
        <p className="evidence-window-note">
          {evidenceWindowPartial
            ? <>本筛选已载入 <b>{formatExactCount(visibleEvidence.length)}</b> / {formatExactCount(evidenceModeTotal)} 个；完整总数保留在审计账本。</>
            : <>已显示全部 <b>{formatExactCount(evidenceModeTotal)}</b> 个。</>}
        </p>
        <details className="evidence-rule-note"><summary>查看统计规则</summary><p>核心新闻要求一手完整证据或至少两个独立可靠来源确认；大视野新闻还纳入单一可靠来源并降低权重。新闻只从首次收到后生效，按事件类型和有效交易时间逐步衰减。Gemini 与 Gemma 负责理解事件语义，版本化证据规则负责时间、身份、去重与准入；每个事件下方可核对统一身份和原始发布域名。</p></details>
        <div className={`evidence-table-wrap ${showAllEvidence ? "show-all-mobile-items" : ""}`}><table className="evidence-table">
          <thead><tr><th>是否用于预测</th><th>新闻事件</th><th>用了多少次 / 为什么没用</th><th>发布时间 / 收到时间</th></tr></thead>
          <tbody>{visibleEvidence.length === 0 && evidenceModeTotal > 0 && <tr className="evidence-unavailable-row"><td colSpan={4}>这个分类有记录，但本页尚未载入明细。总数不会被当成空结果。</td></tr>}{visibleEvidence.map(row => <tr key={`${evidenceMode}:${row.event_key}`}>
            <td className="evidence-status-cell"><span className={`model-seen-badge ${row.model_seen ? "is-seen" : "is-unseen"}`}>{row.model_seen ? "已用于预测" : "未用于预测"}</span><small><span className="evidence-grade-label">{EVIDENCE_LABELS[row.evidence_grade] ?? row.evidence_grade}</span><span className="evidence-status-copy">{row.model_seen ? "当时确实参与了模型输入" : row.broad_model_eligible ? "现在符合条件，等待下一次预测" : "现在也不符合使用条件"}</span></small></td>
            <td className="evidence-event-cell"><strong>{row.canonical_headline}</strong><div className="evidence-topics">{(row.topics ?? []).map(topic => <span key={topic}>{TOPIC_LABELS[topic] ?? topic}</span>)}</div><small className="evidence-source-identity"><span>统一来源身份：{(row.source_identity_organizations ?? []).join(" · ") || "未确认"}</span><span>原始发布域名：{row.publisher_domains.join(" · ") || "未记录"}</span></small></td>
            <td className="evidence-usage-cell">{row.model_seen ? <><strong>参与 {formatExactCount(row.frozen_decisions)} 次预测 · 模型读取 {formatExactCount(row.frozen_model_uses)} 次</strong><small><span className="evidence-model-list">{(row.model_identities ?? []).map(identity => MODEL_LABELS[identity] ?? identity).join(" · ") || "模型名称未记录"}</span><span className="evidence-use-window">首次 {time(row.first_model_decision_time)} · 最近 {time(row.last_model_decision_time)}</span></small></> : <><strong>从未进入任何预测</strong><small>{evidenceReason(row)}</small></>}</td>
            <td className="evidence-time-cell"><time><span>发布</span>{row.source_published_time ? time(row.source_published_time) : "时间未知"}</time><small><span>收到 {time(row.collector_first_seen_time)}</span><span>{formatExactCount(row.independent_publishers)} 个独立来源 · {formatExactCount(row.member_count)} 篇新闻</span></small></td>
          </tr>)}</tbody>
        </table></div>
        <nav className="market-history-nav" aria-label="新闻证据翻页">
          <button type="button" disabled={evidencePage <= 1} onClick={() => setEvidencePage(page => Math.max(1, page - 1))}>上一页</button>
          <span>第 {formatExactCount(evidencePage)} 页</span>
          <button type="button" disabled={!evidenceArchiveReady || !evidenceArchive.has_more || !evidenceArchive.next_cursor} onClick={() => setEvidencePage(page => page + 1)}>下一页</button>
        </nav>
        {visibleEvidence.length > 8 && <button className="mobile-reveal-button" type="button" aria-expanded={showAllEvidence} onClick={() => setShowAllEvidence(value => !value)}>{showAllEvidence ? "收起证据" : `显示本页其余 ${formatExactCount(visibleEvidence.length - 8)} 个事件`}</button>}
      </section>}

      {view === "stories" && selectedAuditDetailState === "ready" && <section className="story-desk">
        <header className="evidence-intro evidence-intro-compact"><div><p className="eyebrow">事件脉络</p><h2>第一次进展立即显示，后续变化接在一起。</h2></div></header>
        {payload?.system.deployment && <section className={`deployment-proof ${deploymentPresentation.className}`}><b>{deploymentPresentation.label}</b>{payload.system.deployment.status === "DEPLOYMENT_DRIFT" ? <span>本机 {payload.system.deployment.runtime_git_sha?.slice(0, 8) ?? "未知"} · 远端 {payload.system.deployment.expected_git_sha?.slice(0, 8) ?? "未知"}</span> : payload.system.deployment.runtime_git_sha ? <span>版本 {payload.system.deployment.runtime_git_sha.slice(0, 8)}</span> : null}</section>}
        <div className="event-thread-summary" aria-label="事件脉络统计"><span><b><CountValue value={activeEventTotal} /></b> 个独立事件</span><span><b><CountValue value={continuedEventTotal} /></b> 个已有后续</span><span><b><CountValue value={singleEventTotal} /></b> 个暂无后续</span></div>
        {(payload?.storylines ?? []).length > 0 && <><div className={`story-grid ${showAllStorylines ? "show-all-mobile-items" : ""}`}>{(payload?.storylines ?? []).map(story => <article key={story.storyline_id}>
          <header><div><span>{({ EMERGING:"刚出现", REPORTED:"已有报道", CORROBORATED:"独立交叉确认", OFFICIALLY_CONFIRMED:"官方确认", ESCALATING:"升级中", DEESCALATING:"缓和中", CONTRADICTED:"存在冲突" } as Record<string,string>)[story.state] ?? story.state}</span><h3>{story.title}</h3></div><strong><CountValue value={story.event_count} /><small> 个进展</small></strong></header>
          <p className="story-latest"><b>最新事实变化</b>{story.latest_change}</p>
          <div className="story-meta"><span>证据文件 {formatExactCount(story.evidence_document_count)}</span><span>独立组织 {formatExactCount(story.independent_organization_count)}</span><span>更新 {time(story.last_updated)}</span><span>{story.independent_confirmation ? "跨组织确认" : "尚未跨组织确认"}</span></div>
          <section className="story-coverage"><div><b>证据覆盖 {formatExactCount(story.coverage_count)}/{formatExactCount(story.coverage_total)}</b>{story.covered_roles.map(role => <span key={role.key}>{role.label}</span>)}{story.missing_roles.map(role => <em className="missing" key={role.key}>仍缺：{role.label}</em>)}</div></section>
          <button className="story-timeline-toggle" type="button" aria-expanded={expandedStorylines.has(story.storyline_id)} onClick={() => setExpandedStorylines(current => { const next = new Set(current); if (next.has(story.storyline_id)) next.delete(story.storyline_id); else next.add(story.storyline_id); return next; })}>{expandedStorylines.has(story.storyline_id) ? "收起进展时间线" : `查看 ${formatExactCount(story.timeline.length)} 个进展`}</button>
          <ol className={`story-timeline ${expandedStorylines.has(story.storyline_id) ? "is-open" : ""}`}>{story.timeline.map(item => <li key={item.event_key}><time>{time(item.event_time || item.source_published_time || item.first_seen)}</time><b>{({ STARTS:"首次进展", FOLLOWED_BY:"随后发生", CONFIRMS:"确认", CONTRADICTS:"否认/冲突", RESPONDS_TO:"作出回应", ESCALATES:"实际升级", DEESCALATES:"实际缓和", SUPERSEDES:"修订替代" } as Record<string,string>)[item.relation] ?? item.relation}</b><span>{item.headline}</span><small>{item.actor} · {item.action} · {formatExactCount(item.evidence_documents)} 份文件 · {formatExactCount(item.independent_organizations)} 个组织<br />发布 {time(item.source_published_time)} · 系统首次看到 {time(item.collector_first_seen_time)}</small></li>)}</ol>
          {story.market_reactions.length > 0 && <details className="story-attachments"><summary>市场反应 {formatExactCount(story.market_reactions.length)}</summary>{story.market_reactions.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
          {story.commentary.length > 0 && <details className="story-attachments"><summary>评论与预测 {formatExactCount(story.commentary.length)}</summary>{story.commentary.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
          {story.background.length > 0 && <details className="story-attachments"><summary>背景材料 {formatExactCount(story.background.length)}</summary>{story.background.map(item => <p key={item.event_key}>{item.headline}</p>)}</details>}
        </article>)}</div>{(payload?.storylines ?? []).length > 4 && <button className="mobile-reveal-button storylines-reveal-button" type="button" aria-expanded={showAllStorylines} onClick={() => setShowAllStorylines(value => !value)}>{showAllStorylines ? "收起较早脉络" : `显示其余 ${formatExactCount((payload?.storylines ?? []).length - 4)} 条脉络`}</button>}</>}
        {(payload?.story_event_candidates ?? []).length > 0 && <section className="single-event-index">
          <header><div><h3>新发生</h3><span>有后续时会自动接成一条脉络</span></div><strong><CountValue value={singleEventTotal} /></strong></header>
          <div className={showAllStoryEvents ? "show-all-mobile-items" : ""}>{(payload?.story_event_candidates ?? []).map(item => <article key={item.candidate_id}>
            <time>{time(item.event_time || item.first_seen)}</time>
            <h3>{item.headline}</h3>
            <span>1 个进展</span>
            <small>{formatExactCount(item.evidence_documents)} 篇证据 · {formatExactCount(item.independent_publishers)} 个独立来源</small>
          </article>)}</div>
          {(payload?.story_event_candidates ?? []).length > 8 && <button className="mobile-reveal-button" type="button" aria-expanded={showAllStoryEvents} onClick={() => setShowAllStoryEvents(value => !value)}>{showAllStoryEvents ? "收起新事件" : `显示其余 ${formatExactCount((payload?.story_event_candidates ?? []).length - 8)} 个新事件`}</button>}
        </section>}
        {activeEventTotal === 0 && <div className="story-empty"><b>还没有收到可确认的独立事件</b><span>新事件出现后会直接显示在这里。</span></div>}
        <section className="theme-streams"><header><h3>主题流</h3><span>不声称构成单一事件</span></header><div>{(payload?.theme_streams ?? []).map(theme => <article key={theme.theme_id}><b>{theme.title}</b><strong><CountValue value={theme.item_count} /></strong><span>{theme.latest_headline}</span><small>{time(theme.last_updated)}</small></article>)}</div></section>
        <section className="theme-streams market-streams"><header><h3>市场反应流</h3><span>价格反应不冒充核心事实</span></header><div>{(payload?.market_reaction_streams ?? []).map(stream => <article key={stream.stream_id}><b>{stream.title}</b><strong><CountValue value={stream.item_count} /></strong><span>{stream.latest_headline}</span><small>{time(stream.last_updated)}</small></article>)}</div></section>
        <details className="unassigned-story-events" open><summary>市场叙事候选 <b><CountValue value={payload?.storyline_summary?.market_narrative_total} /></b> <small>只有市场反应或评论，核心现实进展尚未确认</small></summary>{(payload?.market_narrative_candidates ?? []).map(story => <div key={story.storyline_id}><time>{time(story.last_updated)}</time><span><b>{story.title}</b><br />{story.latest_change}</span><small>{formatExactCount(story.event_count)} 个候选节点 · {formatExactCount(story.evidence_document_count)} 份文件 · 不进入活跃故事</small></div>)}</details>
        <details className="unassigned-story-events"><summary>历史档案 <b><CountValue value={payload?.storyline_summary?.archived_total} /></b> <small>ARCHIVAL_BACKFILL，不显示为当前新事件</small></summary>{(payload?.archived_storylines ?? []).map(story => <div key={story.storyline_id}><time>{time(story.timeline[0]?.event_time)}</time><span><b>{story.title}</b><br />{story.latest_change}</span><small>{formatExactCount(story.event_count)} 个历史事件 · 系统首次收录 {time(story.last_updated)}</small></div>)}{(payload?.archived_story_event_candidates ?? []).map(item => <div key={item.candidate_id}><time>{time(item.event_time)}</time><span>{item.headline}</span><small>{formatExactCount(item.evidence_documents)} 份历史证据文件 · 系统首次收录 {time(item.first_seen)}</small></div>)}</details>
        <details className="unassigned-story-events"><summary>未归属事件 <b><CountValue value={payload?.storyline_summary?.unassigned_total} /></b></summary>{(payload?.unassigned_story_events ?? []).map(item => <div key={item.event_key}><time>{time(item.first_seen)}</time><span>{item.headline}</span><small>{item.record_kind} · {item.reason}</small></div>)}</details>
      </section>}

      {view === "decisions" && selectedAuditDetailState === "ready" && <section className="decision-audit">
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
                <p>{predictionStatusLabel(model.prediction_status)}</p>
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
          <article><span>上一次学习</span><strong>{learningState === "ready" ? <CountValue value={directionPoolRows} /> : "—"}</strong><small>当前模型已经学到这里</small></article>
          <article><span>下一次学习</span><strong>{learningState === "ready" && rowsUntilTraining !== null ? (rowsUntilTraining === 0 ? "已经就绪" : `还差 ${formatExactCount(rowsUntilTraining)} 条`) : "—"}</strong><small>{rowsUntilTraining === 0 ? "已经达到目标，可以开始新一轮" : `目标 ${formatExactCount(payload?.training?.next_training_at)} 条`}</small></article>
        </div>
        <section className="graph-launch">
          <div><h3>查看学习曲线与 K 线</h3><p>长期累计、每组成绩与决策位置</p></div>
          <button type="button" onClick={() => openLearningGraph("curve")}>打开交互图表 ↗</button>
        </section>
        <section className="model-score-summary"><header><div><span>LIVE OOS SCOREBOARD</span><h3>六套模型，现在表现怎样？</h3></div><small>左为历史累计，箭头后为当前累计，圆点后为本组贡献。</small></header><div className="summary-cadence"><span>统计频率</span><button type="button" className={summaryCadence === "EVERY_5M" ? "active" : ""} onClick={() => setSummaryCadence("EVERY_5M")}>每5分钟（重叠）</button><button type="button" className={summaryCadence === "FIXED_30M" ? "active" : ""} onClick={() => setSummaryCadence("FIXED_30M")}>每30分钟（:00 / :30）</button><small>预测期限始终是30分钟。</small></div>
        {(payload?.learning_curves?.models?.length ?? 0) === 0 ? <div className="league-empty">
          <strong>正在建立第一版 Preview</strong><p>达到 96 条修复或 Forward 完整样本即可训练 Market Preview，不需要等待60天。曲线只从模型创建后的新 Decision 开始，绝不回填假历史成绩。</p>
        </div> : <div className="compact-model-summary">{Object.keys(MODEL_LABELS).filter(identity => identity !== "CHAMPION_0").map(identity => {
          const process = payload?.learning_curves?.rolling_processes?.find(row => row.model_identity === identity);
          const latestGroup = payload?.learning_curves?.version_groups?.find(row => row.model_identity === identity && row.lifecycle_status === "LATEST");
          if (!process && !latestGroup && identity !== "NEWS_ONLY") return null;
          const diagnostic = identity === "NEWS_RESIDUAL" || identity === "BROAD_NEWS_RESIDUAL";
          const newsOnlyPending = identity === "NEWS_ONLY" && !process && !latestGroup;
          const processMetric = process?.cadence_metrics?.[summaryCadence] ?? process;
          const groupMetric = latestGroup?.cadence_metrics?.[summaryCadence] ?? latestGroup;
          const hasTotal = (processMetric?.oos_rows ?? 0) > 0;
          const hasGroup = (groupMetric?.oos_rows ?? 0) > 0;
          const total = hasTotal ? processMetric!.cumulative_quote_return : null;
          const group = hasGroup ? groupMetric!.cumulative_quote_return : null;
          const history = total === null ? null : total - (group ?? 0);
          const tone = group === null ? "is-pending" : group >= 0 ? "is-positive" : "is-negative";
          return <article key={identity}><b>{MODEL_LABELS[identity]}{diagnostic ? <small>新闻修正量</small> : newsOnlyPending ? <small>等待新版生成</small> : null}</b><div className="return-flow" aria-label={`本组开始前 ${percent(history)}，加入本组后 ${percent(total)}，本组贡献 ${percent(group)}`}><span className="return-value return-history" title="本组开始前的历史累计"><small>开始前</small><span>{history === null ? "—" : percent(history)}</span></span><i className={tone} aria-hidden="true">→</i><span className="return-value return-total" title="加入本组后的连续累计"><small>当前累计</small><strong>{total === null ? "等待" : percent(total)}</strong></span><i className="return-separator" aria-hidden="true">·</i><span className={`return-value return-group ${tone}`} title="本组独立贡献"><small>本组贡献</small><strong>{group === null ? "等待" : percent(group)}</strong></span></div></article>;
        })}</div>}</section>
        <ExecutionResearch status={payload?.execution_learning} onOpenGraph={() => openLearningGraph("execution")} />
        <details className="model-method-note">
          <summary><span>方法与实盘边界</span><small>新闻修正量、成本与 Shadow 限制</small></summary>
          <div>
            <article><b>新闻修正量也显示自己的方向</b><span>正修正显示 LONG，负修正显示 SHORT；它表示新闻把黄金基线往上或往下推，不等于“黄金＋新闻”的完整方向。例：黄金自身 +0.10 U5，新闻修正 +0.04 U5，修正量是 LONG，完整模型为 +0.14 U5。</span></article>
            <article><b>“纯新闻方向”单独回答新闻看涨还是看跌</b><span>它完全不读取黄金行情特征，只用决策时已经看见的合格新闻，直接预测未来30分钟黄金方向；没有合格新闻时显示 WAIT。它与新闻修正量分开评分。</span></article>
            <article><b>成本口径</b><span>收益使用可执行 Bid/Ask，并扣除入场、退出两边各 $30 / 百万美元成交额的 commission；slippage 暂按 0。尚未包含账户真实成交偏差，所以不是实盘 PnL。</span></article>
            <article><b>做法可以实时复现；结果尚未达到实盘标准</b><span>行情和新闻都只读取决策时已经看见的内容；30分钟结果成熟后才进入下一轮训练。当前仍没有下单权限，也不会自动晋升。</span></article>
          </div>
        </details>
        <footer className="league-footer">仅供研究观察，不代表盈利，也不会自动下单。</footer>
        <LearningGraphModal key={graphStartTab} open={graphOpen} onClose={() => setGraphOpen(false)} startTab={graphStartTab} curves={payload?.learning_curves?.identity_curves ?? []} market={payload?.market_chart} versionGroups={payload?.learning_curves?.version_groups ?? []} execution={payload?.execution_learning} historyResource={payload?.learning_history_resource} />
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
      <div><dt>已学习</dt><dd><CountValue value={model?.training_decisions} /></dd></div>
      <div><dt>已结算</dt><dd><CountValue value={model?.scores} /></dd></div>
      <div><dt>下次训练</dt><dd><CountValue value={model?.next_training_threshold} /></dd></div>
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
