import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { unstable_splitSqlQuery } from "wrangler";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);
const { applyFreshness } = await import("../app/api/status/freshness.js");
const { runtimeUpdateFailurePresentation } = await import("../app/_lib/runtime-update-failure.js");
const { countPresentation, formatCompactCount, formatExactCount, progressCountPresentation } = await import("../app/_lib/count-format.ts");
const { versionResultLabel } = await import("../app/_lib/version-result-state.ts");
const { modelVersionMarkers } = await import("../app/_lib/model-version-markers.ts");
const { buildTrainingCutoffChart } = await import("../app/_lib/training-cutoff-chart.ts");
const { statusFieldPhase } = await import("../app/_lib/current-data-provenance.ts");
const { shouldPollDashboardResource } = await import("../app/_lib/dashboard-refresh-policy.ts");
const { quoteBridgePresentation } = await import("../app/_lib/quote-bridge-state.ts");
const { systemStateAxes, systemStatePresentation } = await import("../app/_lib/system-state.ts");
const { withPreviewIdentity } = await import("../app/api/_shared/preview-status.ts");
const {
  NEWS_REVIEW_STATE_CASE_SQL,
  NEWS_REVIEW_STATE_SQL,
  newsReviewStateInvariantHolds,
  newsReviewStateOf,
  parseNewsReviewState,
} = await import("../app/_lib/news-review-state.ts");
const { publicImpactReason, publicNewsRecord } = await import("../app/_lib/public-news-copy.ts");
const { sortNewsEvidenceByTime } = await import("../app/_lib/news-evidence-order.ts");
const { assistantQueueOperationalAlerts, summarizeAssistantQueue } = await import("../app/api/_shared/assistant-operational-health.ts");
const { normalizeOperationalEvent } = await import("../app/_lib/operational-health.ts");
const { correlateOperationalEvents, globalOperationalIncidents } = await import("../app/_lib/operational-incidents.ts");
const { operationalEventDiagnostic, operationalIncidentActionLabels, operationalIncidentNextRetryAt, operationalIncidentsNextRetryAt, operationalSummaryDetails } = await import("../app/_lib/operational-incident-presentation.ts");
const { operationalEvidenceText } = await import("../app/_lib/operational-evidence.ts");
const { sourceHealthErrorPresentation } = await import("../app/_lib/source-health-presentation.ts");
const {
  componentAggregate,
  componentScanState,
  operatorComponentScanState,
  primaryOperatorAction,
  sortAttentionFirst,
  sourceAggregate,
  sourceScanState,
} = await import("../app/_lib/health-scan-presentation.ts");
const { compactPreviewStatus } = await import("../build/preview-learning.ts");

test("reserves the global shell alert for blocking operational faults", () => {
  const warning = { code: "OPS_AI_BACKLOG_OVERDUE", severity: "WARNING", scope: "ACTIVE_IMPACT", message_zh: "积压", blocking: false, evidence: {} };
  const blocking = { code: "OPS_RUNTIME_UPDATE_FAILED", severity: "ERROR", scope: "DEPLOYMENT", message_zh: "更新失败", blocking: true, evidence: {} };
  assert.deepEqual(globalOperationalIncidents(correlateOperationalEvents([warning])), []);
  const incidents = globalOperationalIncidents(correlateOperationalEvents([warning, blocking]));
  assert.equal(incidents.length, 1);
  assert.equal(incidents[0].root_event.code, blocking.code);
});

test("current Web operational emitters use catalog-allowed severities", () => {
  const definition = {
    queue: "CHAT_TURN", label: "Assistant 对话", table: "unused",
    createdColumn: "created_at", completedExpression: "completed_at",
    successStatuses: ["ANSWERED"], failureStatuses: ["FAILED"], slaSeconds: 300,
  };
  const base = {
    queue: "CHAT_TURN", label: "Assistant 对话", queued: 1, processing: 0,
    claimable: 1, scheduled_retry: 0, oldest_active_at: "2026-08-18T00:00:00Z",
    oldest_age_seconds: 600, max_attempt_count: 3, completed_15m: 0,
    failed_15m: 1, capacity_deferred: 0, failure_codes: [{ code: "FAILED", count: 1 }],
  };
  const emitted = [
    ...assistantQueueOperationalAlerts(base, definition),
    ...assistantQueueOperationalAlerts({ ...base, max_attempt_count: 0, completed_15m: 1, failed_15m: 0 }, definition),
  ];
  assert.deepEqual(
    new Set(emitted.map(item => item.code)),
    new Set([
      "OPS_ASSISTANT_JOB_RETRY_LOOP", "OPS_ASSISTANT_PIPELINE_STALLED",
      "OPS_ASSISTANT_BACKLOG_OVERDUE", "OPS_ASSISTANT_NEW_TERMINAL_FAILURE",
    ]),
  );
  assert.ok(emitted.every(item => !("taxonomy_error" in item.evidence)));

  const mismatch = normalizeOperationalEvent({
    code: "OPS_AI_ROUTE_CAPACITY_SATURATED", severity: "ERROR", scope: "ACTIVE_IMPACT",
    message_zh: "容量异常", blocking: true, evidence: {},
  });
  assert.equal(
    mismatch.evidence.taxonomy_error,
    "SEVERITY_NOT_ALLOWED:OPS_AI_ROUTE_CAPACITY_SATURATED:ERROR",
  );
});

test("renders operational evidence timestamps for the UTC+8 operator surface", () => {
  assert.equal(
    operationalEvidenceText({
      failed_at: "2026-08-16T19:33:00.7001669+00:00",
      status: "ROLLED_BACK",
    }),
    "failed_at=2026-08-17 03:33:00 UTC+8 · status=ROLLED_BACK",
  );
  assert.equal(
    operationalEvidenceText({ earliest_retry_at: null, active_jobs: 3 }),
    "earliest_retry_at=— · active_jobs=3",
  );
});

test("renders human incident diagnostics before nested raw machine evidence", () => {
  const [incident] = correlateOperationalEvents([{
    code: "OPS_COMPONENT_UNHEALTHY",
    severity: "WARNING",
    scope: "news_semantic_pipeline",
    message_zh: "组件 news_semantic_pipeline 当前状态为 WARN。",
    blocking: false,
    evidence: {
      status: "WARN",
      age_seconds: 213.730543,
      last_error: "ACTIONABLE_NEWS_IMPACT_PENDING,ACTIONABLE_NEWS_IMPACT_RECOVERING",
      reason_codes: [
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_RECOVERING",
      ],
    },
  }]);
  const diagnostic = operationalEventDiagnostic(incident.root_event);
  assert.equal(incident.title_zh, "新闻影响复核等待中");
  assert.equal(operationalIncidentActionLabels[incident.action_state], "自动重试中");
  assert.equal(incident.summary_zh, "有新闻影响复核正在等待计划重试。系统会自动再次尝试处理，目前无需手动操作。");
  assert.deepEqual(diagnostic, {
    status: "WARN · 已持续 3 分 34 秒",
    component: "新闻语义决策门槛",
    reasons: ["新闻影响复核等待中", "新闻影响复核自动重试中"],
  });
  assert.deepEqual(operationalEventDiagnostic(incident.root_event, [
    "ACTIONABLE_NEWS_IMPACT_RECOVERING",
  ]).reasons, ["新闻影响复核自动重试中"]);
  assert.equal(operationalEventDiagnostic({
    ...incident.root_event, evidence: { age_seconds: 56 },
  }).status, "WARNING · 已持续 56 秒");
  assert.equal(operationalEventDiagnostic({
    ...incident.root_event, evidence: { age_seconds: 4200 },
  }).status, "WARNING · 已持续 1 小时 10 分");

  const view = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  const humanLayer = view.indexOf("incident-human-diagnostics");
  const rawLayer = view.indexOf("incident-raw-evidence");
  assert.ok(humanLayer >= 0 && rawLayer > humanLayer);
  assert.match(view, /<details className="incident-raw-evidence">/);
  assert.match(view, /<summary>查看原始字段<\/summary>/);
  assert.match(view, /<code>\{event\.code\}<\/code>/);
  assert.match(view, /operationalEvidenceText\(event\.evidence\)/);
  assert.doesNotMatch(view.slice(humanLayer, rawLayer), /event\.message_zh|<code>|operationalEvidenceText/);
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.incident-raw-evidence > summary \{[^}]*min-height:44px/);
  assert.match(css, /\.incident-raw-evidence code,.incident-raw-evidence small \{[^}]*overflow-wrap:anywhere/);
});

test("presents decision output stalls separately from collector liveness", () => {
  const [incident] = correlateOperationalEvents([{
    code: "OPS_DECISION_OUTPUT_STALLED",
    severity: "ERROR",
    scope: "decision_output",
    message_zh: "市场与报价正常，但 5 分钟决策输出已超过容许节奏。",
    blocking: true,
    evidence: { status: "STALLED", age_seconds: 1200 },
  }]);

  assert.equal(incident.title_zh, "决策输出停滞");
  assert.equal(incident.root_event.scope, "decision_output");
  assert.equal(incident.action_state, "ACTION_REQUIRED");
  assert.equal(operationalIncidentActionLabels[incident.action_state], "需要人工处理");
  assert.deepEqual(operationalEventDiagnostic(incident.root_event), {
    status: "STALLED · 已持续 20 分 0 秒",
    component: "5 分钟决策输出",
    reasons: [],
  });
});

test("retains bounded operational incidents for deterministic Preview hydration", () => {
  const operationalHealth = {
    schema_version: "operational-health.v1",
    status: "WARNING",
    alerts: [{ code: "OPS_COMPONENT_UNHEALTHY", scope: "news_semantic_pipeline" }],
  };
  const compact = compactPreviewStatus({ operational_health: operationalHealth });
  assert.deepEqual(compact.operational_health, operationalHealth);
  const manifest = JSON.parse(readFileSync(new URL("../preview-manifest.json", import.meta.url), "utf8"));
  assert.ok(manifest.statusInlineKeys.includes("operational_health"));
});

test("summarizes Assistant queue evidence without exposing job content", () => {
  const now = new Date("2026-08-16T12:15:00.000Z");
  const summary = summarizeAssistantQueue({
    queue: "CHAT_TURN", label: "Assistant 对话", table: "assistant_turn_jobs",
    createdColumn: "created_at", completedExpression: "completed_at",
    successStatuses: ["ANSWERED"], failureStatuses: ["FAILED"], slaSeconds: 300,
  }, {
    queued: 4, processing: 1, claimable: 3, scheduled_retry: 2,
    oldest_active_at: "2026-08-16T12:05:00.000Z", max_attempt_count: 3,
    completed_15m: 6, failed_15m: 1, capacity_deferred: 2,
  }, [
    { failure_code: "NO_MODEL_CAPACITY", total: 2 },
    { failure_code: "WORKER_FAILURE", total: 1 },
  ], now);

  assert.equal(summary.oldest_age_seconds, 600);
  assert.equal(summary.claimable, 3);
  assert.deepEqual(summary.failure_codes, [
    { code: "NO_MODEL_CAPACITY", count: 2 },
    { code: "WORKER_FAILURE", count: 1 },
  ]);
  assert.equal(JSON.stringify(summary).includes("job_id"), false);
});

test("renders canonical source rate-limit fallback and generic failures", () => {
  assert.deepEqual(sourceHealthErrorPresentation({
    recovery_mode: "RATE_LIMITED",
    fallback_label: "Google News Context",
    fallback_health: "HEALTHY",
    last_error_type: "RateLimited",
  }, false), {
    heading: "GDELT 限流 · Google News Context 自动接管",
    recovery: "后备来源正在接管",
    fallback: "后备链路：Google News Context · HEALTHY",
  });
  assert.deepEqual(sourceHealthErrorPresentation({
    recovery_mode: "AUTO_RECOVERING",
    fallback_label: null,
    fallback_health: null,
    last_error_type: "TimeoutError",
  }, false), {
    heading: "Provider 响应超时",
    recovery: "正在自动重试",
    fallback: null,
  });
});

test("presents health as an action-first scanning contract", () => {
  assert.equal(primaryOperatorAction([]), null);
  assert.equal(primaryOperatorAction([{ action_state: "MONITORING" }]), "MONITORING");
  assert.equal(primaryOperatorAction([
    { action_state: "MONITORING" }, { action_state: "AUTO_RECOVERING" },
  ]), "AUTO_RECOVERING");
  assert.equal(primaryOperatorAction([
    { action_state: "AUTO_RECOVERING" }, { action_state: "ACTION_REQUIRED" },
  ]), "ACTION_REQUIRED");

  assert.deepEqual(componentScanState("OK"), {
    tone: "healthy", symbol: "✓", label: "正常", attention: false,
  });
  assert.deepEqual(componentScanState("UNKNOWN"), {
    tone: "neutral", symbol: "—", label: "状态未知", attention: true,
  });
  assert.deepEqual(componentScanState("WARN"), {
    tone: "warning", symbol: "⚠", label: "警告", attention: true,
  });
  const staleWithActionRequiredIncident = operatorComponentScanState("STALE", {
    severity: "ERROR", action_state: "ACTION_REQUIRED",
  });
  assert.deepEqual(staleWithActionRequiredIncident, {
    tone: "error", symbol: "✕", label: "需要人工处理", attention: true,
  });
  assert.equal(componentScanState("STALE").tone, "warning");
  assert.deepEqual(sourceScanState("ERROR"), {
    tone: "error", symbol: "✕", label: "错误", attention: true,
  });
  assert.deepEqual(
    sortAttentionFirst(["OK", "ERROR", "STALE"], componentScanState),
    ["ERROR", "STALE", "OK"],
  );
  assert.deepEqual(
    sortAttentionFirst(["HEALTHY", "WARMING_UP"], sourceScanState),
    ["WARMING_UP", "HEALTHY"],
  );
  assert.equal(componentAggregate([
    componentScanState("OK"), componentScanState("OK"),
    componentScanState("WARN"), staleWithActionRequiredIncident,
  ]), "2 正常 · 1 警告 · 1 错误");
  assert.equal(sourceAggregate(["HEALTHY", "HEALTHY", "WARMING_UP"]), "2 正常 · 1 等待发布");
});

test("presents structured retry timing without parsing human error copy", () => {
  const [incident] = correlateOperationalEvents([{
    code: "OPS_AI_JOB_RETRY_LOOP", severity: "WARNING", scope: "ACTIVE_IMPACT",
    message_zh: "任意人类说明", blocking: false,
    evidence: { latest_failure_code: "PROVIDER_HTTP_ERROR", claimable: false, next_retry_at: "2026-08-19T04:00:00Z" },
  }]);
  assert.equal(operationalIncidentActionLabels[incident.action_state], "自动重试中");
  assert.equal(operationalIncidentNextRetryAt(incident), "2026-08-19T04:00:00Z");
});

test("uses the earliest structured retry across every incident with the primary action", () => {
  const incident = (action_state, retryTimes) => ({
    action_state,
    root_event: { evidence: { next_retry_at: retryTimes[0] } },
    related_events: retryTimes.slice(1).map(next_retry_at => ({ evidence: { next_retry_at } })),
    technical_events: [],
  });
  const incidents = [
    incident("AUTO_RECOVERING", ["not-a-time", "2026-08-19T08:55:00Z"]),
    incident("MONITORING", ["2026-08-19T08:00:00Z"]),
    incident("AUTO_RECOVERING", ["2026-08-19T09:00:00Z"]),
  ];
  assert.equal(operationalIncidentsNextRetryAt(incidents, "AUTO_RECOVERING"), "2026-08-19T08:55:00Z");
  assert.equal(operationalIncidentsNextRetryAt(incidents, null), null);
  assert.deepEqual(operationalSummaryDetails(3, "AUTO_RECOVERING", "16:55"), [
    "3 个子系统受影响", "下次尝试 16:55",
  ]);
  assert.deepEqual(operationalSummaryDetails(1, "ACTION_REQUIRED", null), ["1 个子系统受影响"]);
  assert.deepEqual(operationalSummaryDetails(0, null, null), []);
});

test("keeps internal matched-news identifiers out of user-facing prose", () => {
  const internalId = "f63eb3e5-9370-5278-9509-8f917efa04c1";
  assert.equal(
    publicImpactReason(`正文与候选${internalId}的核心事实完全一致。`),
    "正文与系统中已有的一篇报道的核心事实完全一致。",
  );
  assert.equal(
    publicImpactReason("matched_candidate_id 指向同一事件。"),
    "系统中已有的一篇报道 指向同一事件。",
  );
  assert.equal(
    publicImpactReason("与已有报道记录02b87ba0-e4f9-556a-820b-0332553f6b完全一致。"),
    "与系统中已有的一篇报道完全一致。",
  );
  assert.equal(
    publicImpactReason("与已有报道1d181c31完全一致。"),
    "与系统中已有的一篇报道完全一致。",
  );
  assert.deepEqual(
    publicNewsRecord({
      detail_hash: "a".repeat(64),
      payload: {
        impact_reason_zh: "与已有报道1d181c31完全一致。",
        matched_candidate_id: "1d181c31",
      },
    }),
    {
      detail_hash: "a".repeat(64),
      payload: {
        impact_reason_zh: "与系统中已有的一篇报道完全一致。",
        matched_candidate_id: "1d181c31",
      },
    },
  );
});

test("renders Daily Brief from authoritative date lifecycle state", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const manifest = JSON.parse(readFileSync(new URL("../preview-manifest.json", import.meta.url), "utf8"));
  assert.match(source, /daily_news_brief_summary/);
  assert.match(source, /本版依据 \{formatExactCount\(reviewed\)\} 条已复核资料/);
  assert.match(source, /新资料会纳入下一版/);
  assert.match(source, /ASIA\/KUALA_LUMPUR/);
  assert.doesNotMatch(source, /payload\?\.daily_news_briefs\?\.length/);
  assert.doesNotMatch(source, /今日还没有简报/);
  assert.ok(manifest.auditInlineKeys.includes("daily_news_brief_summary"));
  assert.ok(
    css.indexOf(".brief-progress { grid-template-columns:1fr") >
      css.indexOf(".brief-progress { display:grid; grid-template-columns:minmax"),
    "Daily Brief mobile grid must override its desktop grid",
  );
});

test("labels version results from their durable evaluation state", () => {
  assert.equal(versionResultLabel({ oos_rows: 12, evaluation_status: "HAS_RESULTS" }, "+1.250%"), "+1.250%");
  assert.equal(versionResultLabel({ oos_rows: 0, evaluation_status: "AWAITING_OUTCOME" }, "+0.000%"), "等待结果");
  assert.equal(versionResultLabel({ oos_rows: 0, evaluation_status: "OUTCOME_UNAVAILABLE" }, "+0.000%"), "无结果");
  assert.equal(versionResultLabel({ oos_rows: 0, evaluation_status: "AWAITING_FIRST_PREDICTION" }, "+0.000%"), "没行动");
  assert.equal(versionResultLabel({ oos_rows: 0, evaluation_status: "NO_PREDICTIONS" }, "+0.000%"), "没行动");
  assert.equal(versionResultLabel({ oos_rows: 0 }, "+0.000%"), "状态未知");
});

test("derives model handovers from the predictions actually shown", () => {
  assert.deepEqual(modelVersionMarkers([
    { decision_time: "2026-08-14T01:00:00Z", model_version: "version-a" },
    { decision_time: "2026-08-14T01:05:00Z", model_version: "version-a" },
    { decision_time: "2026-08-14T01:10:00Z", model_version: "version-b" },
    { decision_time: "2026-08-14T01:15:00Z", model_version: "version-b" },
    { decision_time: "2026-08-14T01:20:00Z", model_version: "version-c" },
  ]), [
    {
      decision_time: "2026-08-14T01:10:00Z",
      previous_model_version: "version-a",
      model_version: "version-b",
    },
    {
      decision_time: "2026-08-14T01:20:00Z",
      previous_model_version: "version-b",
      model_version: "version-c",
    },
  ]);
  assert.deepEqual(modelVersionMarkers([
    { decision_time: "2026-08-14T01:00:00Z", model_version: "version-a" },
  ]), []);
});

test("aligns models by comparable training cutoff without inventing history", () => {
  const chart = buildTrainingCutoffChart([
    { model_identity: "MARKET_ONLY", created_at: "2026-08-14T01:00:00Z", generation: 1, training_rows: 1000 },
    { model_identity: "MARKET_ONLY", created_at: "2026-08-14T02:00:00Z", generation: 2, training_rows: 1050 },
    { model_identity: "MARKET_ONLY", created_at: "2026-08-14T03:00:00Z", generation: 3, training_rows: 1100 },
    { model_identity: "NEWS_ONLY", created_at: "2026-08-14T02:00:00Z", generation: 1, training_rows: 1050 },
    { model_identity: "NEWS_ONLY", created_at: "2026-08-14T03:00:00Z", generation: 2, training_rows: 1100 },
    { model_identity: "NEWS_ONLY", created_at: "2026-08-14T03:00:00Z", generation: 3, training_rows: 1100 },
  ], row => row.training_rows);

  assert.deepEqual(chart.cutoffs, [1000, 1050, 1100]);
  const market = chart.series.find(series => series.modelIdentity === "MARKET_ONLY");
  const news = chart.series.find(series => series.modelIdentity === "NEWS_ONLY");
  assert.deepEqual(market.points.map(point => point.cutoffIndex), [0, 1, 2]);
  assert.deepEqual(news.points.map(point => point.cutoffIndex), [1, 2, 2]);
  assert.equal(news.points[0].row.training_rows, 1050);
  assert.deepEqual(news.points.slice(1).map(point => point.row.generation), [2, 3]);
  assert.equal(chart.cutoffs.at(-1), 1100);
});

test("keeps branch throughput limits while refreshing Preview metrics from D1", () => {
  const merged = withPreviewIdentity({
    annotation_queue: { ready: 9, requests_per_minute: 48 },
    system: { online: true },
  }, {
    annotation_queue: {
      requests_per_minute_per_key: 12,
      requests_per_minute_per_account: 12,
      requests_per_minute: 12,
      input_tokens_per_minute: 225_000,
      minute_scope: "ACCOUNT",
    },
    llm_routing: { display_only: {
      configured_account_count: 1,
      requests_per_minute_per_account: 20,
      requests_per_minute: 20,
      input_tokens_per_minute_per_account: 15_000,
      input_tokens_per_minute: 15_000,
      provider_lanes_per_account: 2,
      maximum_concurrent_requests: 2,
      minute_scope: "ACCOUNT",
    } },
    system: {},
  });

  assert.deepEqual(merged.annotation_queue, {
    ready: 9,
    requests_per_minute_per_key: 12,
    requests_per_minute_per_account: 12,
    requests_per_minute: 12,
    input_tokens_per_minute: 225_000,
    minute_scope: "ACCOUNT",
  });
  assert.equal(merged.llm_routing.display_only.requests_per_minute, 20);
  assert.deepEqual(merged.preview.branch_snapshot.status_paths, [
    "annotation_queue.requests_per_minute_per_key",
    "annotation_queue.requests_per_minute_per_account",
    "annotation_queue.requests_per_minute",
    "annotation_queue.input_tokens_per_minute",
    "annotation_queue.minute_scope",
    "llm_routing.display_only.configured_account_count",
    "llm_routing.display_only.requests_per_minute_per_account",
    "llm_routing.display_only.requests_per_minute",
    "llm_routing.display_only.input_tokens_per_minute_per_account",
    "llm_routing.display_only.input_tokens_per_minute",
    "llm_routing.display_only.provider_lanes_per_account",
    "llm_routing.display_only.maximum_concurrent_requests",
    "llm_routing.display_only.minute_scope",
  ]);
});

test("runtime update success stays silent while failures have stable presentation", () => {
  assert.equal(runtimeUpdateFailurePresentation(null), null);
  assert.deepEqual(runtimeUpdateFailurePresentation({
    status: "ROLLED_BACK",
    message: "observation failed",
    failed_at: "2026-08-13T03:00:00Z",
  }), {
    label: "新版运行验证失败，已自动恢复上一版。",
    failedAt: "2026-08-13T03:00:00Z",
  });
  assert.equal(runtimeUpdateFailurePresentation({
    status: "SWITCH_FAILED", message: "switch failed", failed_at: "now",
  }).label, "新版切换失败，当前版本继续运行。");
  assert.equal(runtimeUpdateFailurePresentation({
    status: "ROLLBACK_FAILED", message: "rollback failed", failed_at: "now",
  }).label, "新版运行验证失败，自动恢复也失败，请检查本机服务。");
  assert.equal(runtimeUpdateFailurePresentation({
    status: "PREFLIGHT_FAILED", message: "preflight failed", failed_at: "now",
  }).label, "新版预检失败，当前版本继续运行。");
});

test("formats growing counts through one compact and exact display contract", () => {
  const cases = [
    [0, "0", "0"],
    [999, "999", "999"],
    [1_000, "1K", "1,000"],
    [1_250, "1.3K", "1,250"],
    [1_000_000, "1M", "1,000,000"],
    [1_000_000_000, "1B", "1,000,000,000"],
  ];

  for (const [value, compact, exact] of cases) {
    assert.equal(formatCompactCount(value), compact);
    assert.equal(formatExactCount(value), exact);
  }
  assert.equal(formatCompactCount(null), "—");
  assert.equal(formatCompactCount(Number.NaN), "—");

  assert.deepEqual(countPresentation(1_250, "compact", " 条"), {
    accessibleValue: "1,250 条",
    display: "1.3K",
    exact: "1,250",
    title: "1,250 条",
  });
  assert.deepEqual(countPresentation(1_250, "exact", " 条"), {
    accessibleValue: "1,250 条",
    display: "1,250",
    exact: "1,250",
    title: undefined,
  });
  assert.deepEqual(countPresentation(null), {
    accessibleValue: "暂无数据",
    display: "—",
    exact: "—",
    title: undefined,
  });

  assert.deepEqual(progressCountPresentation(15_030, 15_050), {
    current: { exact: "15,030", main: "15K", remainder: "30" },
    isAbbreviated: true,
    showExactDetail: false,
    target: { exact: "15,050", main: "15K", remainder: "50" },
  });
  assert.deepEqual(progressCountPresentation(12_449_999, 12_450_000), {
    current: { exact: "12,449,999", main: "12.4M" },
    isAbbreviated: true,
    showExactDetail: true,
    target: { exact: "12,450,000", main: "12.4M" },
  });
  assert.deepEqual(progressCountPresentation(1_200_000_000, 1_500_000_000), {
    current: { exact: "1,200,000,000", main: "1.2B" },
    isAbbreviated: true,
    showExactDetail: false,
    target: { exact: "1,500,000,000", main: "1.5B" },
  });
  assert.equal(progressCountPresentation(1_234_567_890, 1_500_000_000).showExactDetail, true);
  assert.equal(progressCountPresentation(1_200_000_000_000, 1_500_000_000_000).current.main, "1.2T");
  assert.equal(progressCountPresentation(null, 1_450).current.main, "—");
});

test("keeps nested compact counts in each dashboard headline hierarchy", () => {
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  for (const [selector, size] of [
    ["metric-grid strong", "44px"],
    ["quota-metric-grid strong", "40px"],
    ["evidence-summary strong", "40px"],
    ["learning-summary-grid strong", "42px"],
    ["event-thread-summary b", "25px"],
    ["theme-streams article strong", "25px"],
    ["story-grid header>strong", "34px"],
    ["chart-caption>strong", "24px"],
    ["execution-scorecards strong", "25px"],
  ]) {
    assert.match(css, new RegExp(`\\.${selector.replaceAll(".", "\\.")} \\{[^}]*font-size:${size}`));
  }
  assert.match(css, /\.metric-grid strong \.count-value \{[^}]*font-size:inherit/);
  for (const unsafeSelector of [
    /\.metric-grid span,\.metric-grid small/,
    /\.quota-metric-grid span,\.quota-metric-grid small/,
    /\.evidence-summary span/,
    /\.learning-summary-grid span,\.learning-summary-grid small/,
    /\.event-thread-summary span/,
    /\.theme-streams article span/,
    /\.story-grid header span/,
    /\.chart-caption span/,
    /\.execution-scorecards small,\.execution-scorecards span/,
    /\.annotation-queue span \{/,
  ]) {
    assert.doesNotMatch(css, unsafeSelector);
  }
});

test("keeps every remaining audit destination in one balanced desktop grid", () => {
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const auditGrid = [...css.matchAll(/\.audit-tabs\s*\{([^}]*)\}/g)]
    .map((match) => match[1])
    .find((rule) => /grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/.test(rule)) ?? "";
  assert.match(auditGrid, /grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(auditGrid, /gap:0/);
  assert.match(auditGrid, /padding:0/);
  assert.match(auditGrid, /background:var\(--paper\)/);
  assert.match(css, /\.audit-tabs a \{ border:0; background:var\(--paper\); \}/);
  assert.match(css, /\.audit-tabs a:not\(:nth-child\(4n\+1\)\) \{ border-left:1px solid var\(--ink\); \}/);
  assert.match(css, /\.audit-tabs a:nth-child\(n\+5\) \{ border-top:1px solid var\(--ink\); \}/);
  assert.doesNotMatch(view, /audit-tab-primary/);
  assert.equal(view.match(/<a href="\/audit\?view=/g)?.length, 8);
  assert.match(css, /\.annotation-queue \{ grid-template-columns:repeat\(5,1fr\); gap:0;[^}]*background:var\(--paper\); \}/);
  assert.match(css, /\.annotation-queue>span\+span \{ border-left:1px solid var\(--ink\); \}/);
  assert.match(css, /\.annotation-queue>details \{ grid-column:1\/-1; border-top:1px solid var\(--ink\); padding:0; \}/);
  assert.match(css, /\.news-timeline \{[^}]*gap:0/);
  assert.match(css, /\.news-timeline div:not\(:nth-child\(3n\+1\)\) \{ border-left:1px solid/);
  assert.match(css, /\.news-timeline div:nth-child\(n\+4\) \{ border-top:1px solid/);
  assert.match(css, /\.news-timeline div:not\(:nth-child\(3n\+1\)\) \{ border-left:0/);
  assert.match(css, /\.news-timeline div:nth-child\(n\+2\) \{ border-top:1px solid/);
});

async function render(path) {
  return worker.fetch(
    new Request(`http://localhost${path}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

async function renderSettled(path, marker) {
  let response = await render(path);
  let html = await response.text();
  if (!marker.test(html) && html.includes("app-view-loading")) {
    await new Promise(resolve => setTimeout(resolve, 25));
    response = await render(path);
    html = await response.text();
  }
  return { response, html };
}

test("renders the live room inside the canonical product shell", async () => {
  const response = await render("/");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Signal Room/);
  assert.match(html, /XAUUSD · Forward-only intelligence/);
  assert.match(html, /新闻与决策/);
  assert.match(html, /<a[^>]*aria-current="page"[^>]*>总览<\/a>/);
  assert.doesNotMatch(html, /返回实时室|新闻 \/ 结果/);
  assert.doesNotMatch(html, /next\/link|rel="prefetch"/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("keeps global shell ownership centralized and prevents view-level design drift", () => {
  const shell = readFileSync(new URL("../app/_components/DashboardShell.tsx", import.meta.url), "utf8");
  const navigation = readFileSync(new URL("../app/_components/DashboardNavigation.tsx", import.meta.url), "utf8");
  const mobile = readFileSync(new URL("../app/_components/MobileDashboardNav.tsx", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const views = [
    "../app/_views/LiveRoomView.tsx",
    "../app/_views/AssistantView.tsx",
    "../app/_views/AuditView.tsx",
    "../app/_views/HealthView.tsx",
    "../app/_views/AdminOverviewView.tsx",
    "../app/_views/RetryView.tsx",
    "../app/_views/StatusView.tsx",
  ];

  assert.match(app, /<DashboardShell location=\{location\}>/);
  assert.match(shell, /<header className="dashboard-header topbar">/);
  assert.match(shell, /Aurum Signal Room/);
  assert.match(shell, /XAUUSD · Forward-only intelligence/);
  assert.match(shell, /DASHBOARD_GLOBAL_DESTINATIONS\.map/);
  assert.match(shell, /DASHBOARD_ADMIN_DESTINATIONS\.map/);
  assert.match(mobile, /DASHBOARD_GLOBAL_DESTINATIONS\.map/);
  assert.doesNotMatch(mobile, /const SECTIONS|MobileDashboardSection/);
  assert.equal(navigation.match(/label: "(?:总览|新闻与决策|系统|管理员登录)"/g)?.length, 4);
  assert.match(navigation, /href: "\/audit\?view=news"/);
  assert.match(navigation, /rooms: \["health"\]/);
  assert.match(navigation, /DASHBOARD_ADMIN_DESTINATIONS/);
  assert.match(navigation, /概览[\s\S]*Assistant[\s\S]*重试任务[\s\S]*AI 模型用量/);

  for (const path of views) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(source, /<header className="topbar|MobileDashboardNav|SystemStatePill/);
    assert.doesNotMatch(source, /Aurum Signal Room|XAUUSD · Forward-only intelligence/);
    assert.doesNotMatch(source, /Aurum System Status|Aurum System Health|Aurum Evidence Desk|Aurum Assistant/);
    assert.doesNotMatch(source, /返回实时室/);
  }
});

test("renders static public shell and path-specific admin shells with one invariant header", async () => {
  const routes = [
    ["/", "总览"],
    ["/audit?view=news", "新闻与决策"],
    ["/audit?view=league", "新闻与决策"],
    ["/health", "系统"],
    ["/admin", "管理员登录"],
    ["/admin/assistant", "管理员登录"],
    ["/admin/retry-jobs", "管理员登录"],
    ["/admin/ai-usage", "管理员登录"],
  ];
  const publicLabels = ["总览", "新闻与决策", "系统"];

  for (const [path, activeLabel] of routes) {
    const { response, html } = await renderSettled(path, /dashboard-header topbar/);
    assert.equal(response.status, 200, path);
    assert.equal(html.match(/class="dashboard-header topbar"/g)?.length, 1, path);
    const header = html.match(/<header class="dashboard-header topbar">[\s\S]*?<\/header>/)?.[0];
    assert.ok(header, path);
    assert.match(header, /<span class="brand-mark">AU<\/span>/, path);
    assert.match(header, /<strong>Aurum Signal Room<\/strong>/, path);
    assert.match(header, /<small>XAUUSD · Forward-only intelligence<\/small>/, path);
    assert.equal(header.match(/aria-current="page"/g)?.length, 1, path);
    assert.match(header, new RegExp(`aria-current="page"[^>]*>(?:<span[^>]*></span>)?${activeLabel}</(?:a|button)>`), path);
    assert.equal(header.match(/class="dashboard-global-state"/g)?.length, 1, path);
    assert.doesNotMatch(header, /返回实时室|学习曲线|AI 模型用量|系统健康|重试任务/, path);

    const globalNav = header.match(/<nav class="dashboard-global-nav"[\s\S]*?<\/nav>/)?.[0];
    const mobileNav = header.match(/<select aria-label="切换主要区域"[\s\S]*?<\/select>/)?.[0];
    assert.ok(globalNav && mobileNav, path);
    let previousGlobal = -1;
    let previousMobile = -1;
    for (const label of publicLabels) {
      const globalIndex = globalNav.indexOf(`>${label}</a>`);
      const mobileIndex = mobileNav.indexOf(`>${label}</option>`);
      assert.ok(globalIndex > previousGlobal, `${path}: desktop ${label}`);
      assert.ok(mobileIndex > previousMobile, `${path}: mobile ${label}`);
      previousGlobal = globalIndex;
      previousMobile = mobileIndex;
    }
    assert.match(globalNav, />管理员登录<\/button>/);
    assert.match(mobileNav, />管理员登录<\/option>/);
  }
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  assert.match(app, /useLayoutEffect/);
  assert.match(app, /parseDashboardUrl\(new URL\(window\.location\.href\)\)/);
  assert.match(app, /room === "audit"/);
  assert.match(app, /room === "health"/);
});

test("keeps Admin login intent local until the explicit Access handoff", () => {
  const shell = readFileSync(new URL("../app/_components/DashboardShell.tsx", import.meta.url), "utf8");
  const mobile = readFileSync(new URL("../app/_components/MobileDashboardNav.tsx", import.meta.url), "utf8");
  const shellCss = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(shell, /dashboard-admin-login-trigger/);
  assert.match(shell, /onClick=\{openAdminLogin\}/);
  assert.match(shell, /dialogRef\.current\?\.showModal\(\)/);
  assert.match(shell, /<h2>管理员登录<\/h2>/);
  assert.match(shell, /仅系统管理员可访问 Assistant、重试任务和 AI 模型用量。/);
  assert.match(shell, /登录后进入私有管理后台。/);
  assert.match(shell, /className="admin-login-primary"[\s\S]*onClick=\{beginAdminLogin\}/);
  assert.match(shell, /openAdminAuthPopup[\s\S]*window\.location\.assign\("\/admin"\)/);
  assert.match(shell, /isTrustedAdminAuthMessage[\s\S]*revalidateAdminSession/);
  assert.match(shell, /adminAuthState === "AUTHENTICATED"/);
  assert.match(shell, /<button type="button" onClick=\{closeAdminLogin\}>取消<\/button>/);
  assert.doesNotMatch(shell, /<ul>|其他私有运维工具|登录后可以访问|PRIVATE ADMIN WORKSPACE/);
  assert.doesNotMatch(shell, /DashboardLink[^\n]*使用 Google 登录/);
  assert.match(shellCss, /\.dashboard-admin-login-trigger \{[^}]*border-style:dashed/);
  assert.match(shellCss, /\.admin-login-dialog \{ width:min\(420px/);
  assert.match(shellCss, /\.admin-login-dialog footer \{[^}]*flex-direction:row/);
  assert.match(mobile, /destination\?\.private[\s\S]*openAdminLogin\(\)/);
});

test("renders one canonical Admin navigation with direct child active state", async () => {
  for (const [path, label, marker] of [
    ["/admin", "概览", /OWNER OPERATIONS/],
    ["/admin/assistant", "Assistant", /ASSISTANT/],
    ["/admin/retry-jobs", "重试任务", /PRIVATE OPERATOR QUEUE/],
    ["/admin/ai-usage", "AI 模型用量", /AI 模型使用状态/],
  ]) {
    const page = await renderSettled(path, marker);
    assert.equal(page.response.status, 200, path);
    const navigation = page.html.match(/<nav class="dashboard-section-nav admin-section-nav"[\s\S]*?<\/nav>/)?.[0] ?? "";
    assert.match(navigation, /概览[\s\S]*Assistant[\s\S]*重试任务[\s\S]*AI 模型用量/, path);
    assert.match(navigation, new RegExp(`aria-current="page"[^>]*>${label}</a>`), path);
  }
});

test("keeps branch Preview identity and blocks writes", async () => {
  const source = readFileSync(new URL("../app/api/_shared/preview.ts", import.meta.url), "utf8");
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(source, /PR Preview 只读且无运行或交易权限/);
  assert.match(source, /X-Aurum-Preview/);
  assert.match(layout, /<PreviewBanner \/>/);

  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") return;
  const builtPreview = readdirSync(new URL("../dist/server/", import.meta.url), {
    recursive: true,
    withFileTypes: true,
  }).filter(entry => entry.isFile() && entry.name.endsWith(".js"))
    .map(entry => readFileSync(`${entry.parentPath}/${entry.name}`, "utf8"))
    .join("\n");
  assert.match(builtPreview, /PREVIEW_SNAPSHOT/);
  const escapedBranch = process.env.WORKERS_CI_BRANCH.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  assert.match(builtPreview, new RegExp(escapedBranch));

  for (const path of [
    "/api/ingest", "/api/audit", "/api/audit-briefs",
    "/api/audit-stories", "/api/audit-decisions",
    "/api/learning", "/api/learning-history",
    "/api/news-index", "/api/news-content", "/api/news-evidence", "/api/market-chart",
    "/api/market-history", "/api/news-questions", "/api/assistant-chat",
    "/api/assistant-worker/chat", "/api/assistant-worker/conversations",
    "/api/assistant-worker/news-questions",
  ]) {
    const forbiddenD1 = new Proxy({}, {
      get() { throw new Error(`${path} touched D1 before Preview rejection`); },
    });
    const response = await worker.fetch(
      new Request(`http://localhost${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{not-valid-json",
      }),
      { DB: forbiddenD1, ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 403, `${path} must reject Preview writes`);
    assert.equal(response.headers.get("X-Aurum-Preview"), "write-rejected");
  }
});

test("hydrates Preview first paint from its immutable build snapshot", () => {
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  const previewResources = readFileSync(new URL("../app/_lib/preview-resources.ts", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const resources = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  assert.match(page, /previewResources\(\)/);
  assert.match(previewResources, /function previewResources/);
  assert.match(previewResources, /review_state=COMPLETED/);
  assert.match(previewResources, /previewBundle\.status/);
  assert.match(previewResources, /previewBundle\.audit/);
  assert.match(app, /const initialStatus = initialResources\["\/api\/status"\]/);
  assert.match(app, /<StatusView \/>/);
  assert.match(app, /<HealthView initialPayload=\{initialStatus\}/);
  const health = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  const status = readFileSync(new URL("../app/_views/StatusView.tsx", import.meta.url), "utf8");
  assert.match(health, /initialPayload \?\? readDashboardResource<StatusPayload>\("\/api\/status"\)/);
  assert.match(status, /const statusUrl = `\$\{ADMIN_API_PREFIX\}\/admin-status`/);
  assert.match(status, /readDashboardResource<StatusPayload>\(statusUrl\)/);
  assert.match(previewResources, /previewBundle\.learning_summary/);
  const vite = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  const learning = readFileSync(new URL("../build/preview-learning.ts", import.meta.url), "utf8");
  const manifest = JSON.parse(readFileSync(new URL("../preview-manifest.json", import.meta.url), "utf8"));
  const previewBuilder = readFileSync(new URL("../../scripts/build_preview_bundle.py", import.meta.url), "utf8");
  assert.match(vite, /compactPreviewLearning/);
  assert.match(vite, /compactPreviewStatus/);
  assert.match(vite, /compactPreviewAudit/);
  assert.match(vite, /compactPreviewNewsIndex/);
  assert.match(vite, /delete bundle\.learning/);
  assert.match(learning, /daily_news_briefs: 2/);
  assert.match(learning, /recent_decisions: 12/);
  assert.match(learning, /value\.slice\(0, limit\)/);
  assert.match(learning, /items\.slice\(0, PREVIEW_NEWS_PAGE_SIZE\)/);
  assert.match(learning, /totals_scope: "BUILD_SNAPSHOT"/);
  assert.match(learning, /history_resource: market\.history_resource \?\? PREVIEW_RESOURCES\.marketHistory/);
  assert.match(learning, /training_markers: market\.training_markers \?\? \[\]/);
  for (const key of ["story_event_candidates", "recent_decisions"]) {
    assert.ok(manifest.auditInlineKeys.includes(key), key);
  }
  assert.ok(!manifest.statusInlineKeys.includes("news_evidence"));
  assert.equal(manifest.resources.newsEvidence, "/api/news-evidence");
  assert.equal(manifest.resources.marketHistory, "/api/market-history");
  assert.doesNotMatch(page, /function previewRoomResources/);
  assert.match(learning, /models\.filter/);
  assert.match(learning, /lifecycle_status === "LATEST"/);
  assert.match(learning, /identity_curves: \[\]/);
  assert.match(learning, /execution_learning:/);
  assert.match(learning, /points: points\.slice\(-48\)/);
  assert.match(learning, /results: results\.slice\(-20\)/);
  assert.match(previewBuilder, /resource=execution-point/);
  assert.match(previewBuilder, /for identity in \("LOT_RIDGE", "EXIT_RIDGE"\)/);
  assert.match(previewBuilder, /resource=curve-overview&cadence=\{cadence\}/);
  assert.match(previewBuilder, /for cadence in \("5m", "30m"\)/);
  assert.match(previewBuilder, /resource=version-overview/);
  assert.match(previewBuilder, /\*version_history/);
  assert.match(previewBuilder, /"news_evidence": news_evidence/);
  assert.doesNotMatch(page, /auditView === "league"/);
  assert.match(previewResources, /\[PREVIEW_RESOURCES\.status\]: publicDashboardStatus\(previewBundle\.status\)/);
  assert.match(app, /primeDashboardResources\(initialResources\);\s*const \[location/);
  assert.match(resources, /DEFAULT_TIMEOUT_MS = 10_000/);
  assert.match(resources, /数据读取超时，页面会自动重试/);
});

test("uses one current-data contract across every dashboard surface", () => {
  const component = readFileSync(new URL("../app/_components/CurrentDataState.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const statusRoute = readFileSync(new URL("../app/api/status/route.ts", import.meta.url), "utf8");
  const statusReader = readFileSync(new URL("../app/api/_shared/dashboard-status.ts", import.meta.url), "utf8");
  const statusContract = readFileSync(new URL("../app/api/_shared/dashboard-snapshot.ts", import.meta.url), "utf8");
  const learningRoute = readFileSync(new URL("../app/api/learning/route.ts", import.meta.url), "utf8");

  assert.doesNotMatch(component, /正在同步页面当前指标/);
  assert.doesNotMatch(component, />同步</);
  assert.match(component, /current-metric-placeholder/);
  assert.match(component, /role="progressbar"/);
  assert.match(component, /显示构建快照/);
  assert.match(css, /@keyframes current-data-pulse/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);

  for (const view of ["AuditView", "LiveRoomView", "StatusView", "HealthView"]) {
    const source = readFileSync(new URL(`../app/_views/${view}.tsx`, import.meta.url), "utf8");
    assert.match(source, /CurrentDataNotice/, `${view} must expose current-data state`);
  }
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(audit, /live_oos_model_groups !== undefined\s*\? statusState/);

  const statusD1 = statusReader.indexOf("dashboard_snapshots WHERE id = ?");
  const statusPreviewFallback = statusRoute.indexOf("if (previewBundle) return previewJson(publicDashboardStatus(previewBundle.status))");
  const learningD1 = learningRoute.indexOf("dashboard_snapshots WHERE id = ?");
  const learningPreviewFallback = learningRoute.indexOf("if (previewBundle?.learning_summary)");
  assert.ok(statusD1 >= 0 && statusPreviewFallback > statusD1, "Preview status must prefer current D1 data");
  assert.ok(learningD1 >= 0 && learningPreviewFallback > learningD1, "Preview learning must prefer current D1 data");
  assert.match(statusRoute, /withPreviewIdentity\(current\.payload, previewBundle\.status\)/);
  assert.match(statusRoute, /publicDashboardStatus\(payload\)/);
  assert.match(statusReader, /PUBLIC_STATUS_PRIVATE_FIELDS/);
  assert.match(statusContract, /"annotation_queue"[\s\S]*"llm_routing"/);
  assert.match(learningRoute, /"X-Aurum-Preview": "read-only-d1-snapshot"/);
});

test("preserves field-level provenance while overlaying current read-only status", async () => {
  const { withPreviewIdentity } = await import("../app/api/_shared/preview-status.ts");
  const result = withPreviewIdentity({
    counts: { decision_events: 20 },
    factor_coverage: ["production-precomputed"],
    storylines: ["production-precomputed"],
    storyline_summary: { policy_version: "production-policy" },
    market_narrative_candidates: ["production-precomputed"],
    story_event_candidates: ["production-precomputed"],
    annotation_queue: { ready: 9, requests_per_minute: 48 },
    system: { online: true, market_session: "OPEN" },
  }, {
    generated_at: "2026-08-13T10:42:03Z",
    preview: {
      is_preview: true, branch: "feature/test", commit_sha: "abc123",
      snapshot_generated_at: "2026-08-13T10:42:03Z",
    },
    factor_coverage: ["branch-recomputed"],
    storyline_summary: { policy_version: "branch-policy" },
    storylines: ["branch-recomputed"],
    market_narrative_candidates: ["branch-recomputed"],
    story_event_candidates: ["branch-recomputed"],
    annotation_queue: {
      requests_per_minute_per_key: 12,
      requests_per_minute_per_account: 12,
      requests_per_minute: 12,
      input_tokens_per_minute: 225_000,
      minute_scope: "ACCOUNT",
    },
    system: { deployment: { runtime_git_sha: "abc123" } },
  });

  assert.deepEqual(result.counts, { decision_events: 20 });
  assert.deepEqual(result.factor_coverage, ["branch-recomputed"]);
  assert.deepEqual(result.storylines, ["production-precomputed"]);
  assert.deepEqual(result.storyline_summary, { policy_version: "production-policy" });
  assert.deepEqual(result.market_narrative_candidates, ["production-precomputed"]);
  assert.deepEqual(result.story_event_candidates, ["production-precomputed"]);
  assert.deepEqual(result.annotation_queue, {
    ready: 9,
    requests_per_minute_per_key: 12,
    requests_per_minute_per_account: 12,
    requests_per_minute: 12,
    input_tokens_per_minute: 225_000,
    minute_scope: "ACCOUNT",
  });
  assert.equal(result.preview.branch, "feature/test");
  assert.deepEqual(result.preview.branch_snapshot, {
    generated_at: "2026-08-13T10:42:03Z",
    status_paths: [
      "factor_coverage",
      "annotation_queue.requests_per_minute_per_key",
      "annotation_queue.requests_per_minute_per_account",
      "annotation_queue.requests_per_minute",
      "annotation_queue.input_tokens_per_minute",
      "annotation_queue.minute_scope",
    ],
  });
  assert.equal(result.preview_status_summary, false);
  assert.equal(result.system.online, false);
  assert.equal(result.system.market_session, "DATA_UNAVAILABLE");
  assert.equal(result.system.source_of_truth, "生产 D1 当前只读数据");
});

test("marks only declared branch snapshot fields as snapshots", () => {
  const paths = ["factor_coverage", "annotation_queue.requests_per_minute"];
  assert.equal(statusFieldPhase("ready", paths, "factor_coverage"), "snapshot");
  assert.equal(statusFieldPhase("ready", paths, "annotation_queue.requests_per_minute"), "snapshot");
  assert.equal(statusFieldPhase("ready", paths, "annotation_queue.ready"), "ready");
  assert.equal(statusFieldPhase("ready", paths, "storylines"), "ready");
  assert.equal(statusFieldPhase("loading", paths, "factor_coverage"), "loading");
  assert.equal(statusFieldPhase("error", paths, "factor_coverage"), "error");
});

test("only a current D1 archive may publish the 60-day news total", async () => {
  const { authoritativeNewsTotals } = await import("../app/_lib/news-index-contract.ts");
  const frozen = {
    total: 200, all_total: 200, readable_total: 200,
    parsed_total: 195, model_candidate_total: 14,
    totals_scope: "BUILD_SNAPSHOT",
  };
  assert.equal(authoritativeNewsTotals(frozen), null);
  assert.deepEqual(authoritativeNewsTotals({
    ...frozen,
    total: 1138,
    all_total: 1138,
    readable_total: 1138,
    parsed_total: 1100,
    model_candidate_total: 31,
    totals_scope: "D1_ARCHIVE",
  }), { category: 1138, readable: 1138, parsed: 1100, modelCandidates: 31 });
  assert.equal(authoritativeNewsTotals({
    ...frozen,
    totals_scope: "RECENT_WINDOW",
  }), null);
});

test("keeps every audit collection in the compact Preview manifest", () => {
  const manifest = JSON.parse(readFileSync(new URL("../preview-manifest.json", import.meta.url), "utf8"));
  for (const key of [
    "storylines", "story_event_candidates", "theme_streams",
    "market_reaction_streams", "recent_decisions",
  ]) {
    assert.ok(manifest.auditInlineKeys.includes(key), key);
  }
  assert.ok(manifest.statusInlineKeys.includes("preview"));
  assert.ok(!manifest.statusInlineKeys.includes("news_evidence"));
  assert.equal(manifest.resources.audit, "/api/audit");
  assert.equal(manifest.resources.auditBriefs, "/api/audit-briefs");
  assert.equal(manifest.resources.auditStories, "/api/audit-stories");
  assert.equal(manifest.resources.auditDecisions, "/api/audit-decisions");
  assert.equal(manifest.resources.newsEvidence, "/api/news-evidence");
  assert.deepEqual(manifest.branchSnapshotStatusPaths, [
    "factor_coverage",
    "annotation_queue.requests_per_minute_per_key",
    "annotation_queue.requests_per_minute_per_account",
    "annotation_queue.requests_per_minute",
    "annotation_queue.input_tokens_per_minute",
    "annotation_queue.minute_scope",
    "llm_routing.display_only.configured_account_count",
    "llm_routing.display_only.requests_per_minute_per_account",
    "llm_routing.display_only.requests_per_minute",
    "llm_routing.display_only.input_tokens_per_minute_per_account",
    "llm_routing.display_only.input_tokens_per_minute",
    "llm_routing.display_only.provider_lanes_per_account",
    "llm_routing.display_only.maximum_concurrent_requests",
    "llm_routing.display_only.minute_scope",
  ]);
  assert.equal(manifest.resources.marketHistory, "/api/market-history");
});

test("falls through to read-only D1 for later Preview news and details", () => {
  const index = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  const detail = readFileSync(new URL("../app/api/news-content/route.ts", import.meta.url), "utf8");
  assert.doesNotMatch(index, /inlinePreviewItems/);
  assert.match(index, /D1 is the source of truth even on the first Preview page/);
  assert.match(index, /"read-only-d1-archive"/);
  assert.match(index, /"current-read-unavailable"/);
  assert.match(detail, /if \(detail\) return previewJson\(publicNewsRecord\(detail\)\)/);
  assert.match(detail, /payload: publicNewsRecord|const payload = publicNewsRecord/);
  assert.match(index, /return publicNewsRecord\(item\) as NewsIndexItem/);
  assert.match(detail, /"read-only-d1-detail"/);
  assert.doesNotMatch(detail, /该新闻详情不在本次 Preview 快照中/);
});

test("separates completed, processing, and isolated news by durable review state", () => {
  assert.equal(parseNewsReviewState(null), "COMPLETED");
  assert.equal(parseNewsReviewState("COMPLETED"), "COMPLETED");
  assert.equal(parseNewsReviewState("PROCESSING"), "PROCESSING");
  assert.equal(parseNewsReviewState("ISOLATED"), "ISOLATED");
  assert.equal(parseNewsReviewState("UNKNOWN"), null);

  for (const status of ["READY", "NOT_REQUIRED"]) {
    assert.equal(newsReviewStateOf({ annotation_status: status }), "COMPLETED");
  }
  for (const status of ["QUEUED", "BACKING_OFF", "WAITING_CONTENT", undefined]) {
    assert.equal(newsReviewStateOf({ annotation_status: status }), "PROCESSING");
  }
  for (const status of ["DEAD_LETTER", "CONTENT_UNAVAILABLE"]) {
    assert.equal(newsReviewStateOf({ annotation_status: status }), "ISOLATED");
  }
  for (const status of ["READY", "NOT_REQUIRED"]) {
    assert.match(NEWS_REVIEW_STATE_SQL.COMPLETED, new RegExp(`'${status}'`));
    assert.match(NEWS_REVIEW_STATE_SQL.PROCESSING, new RegExp(`'${status}'`));
    assert.match(NEWS_REVIEW_STATE_CASE_SQL, new RegExp(`'${status}'`));
  }
  for (const status of ["DEAD_LETTER", "CONTENT_UNAVAILABLE"]) {
    assert.match(NEWS_REVIEW_STATE_SQL.ISOLATED, new RegExp(`'${status}'`));
    assert.match(NEWS_REVIEW_STATE_SQL.PROCESSING, new RegExp(`'${status}'`));
    assert.match(NEWS_REVIEW_STATE_CASE_SQL, new RegExp(`'${status}'`));
  }
  assert.equal(newsReviewStateInvariantHolds({
    annotation_status: "NOT_REQUIRED",
    model_visibility: "MODEL_INELIGIBLE",
    parsed_at: null,
  }), true);
  assert.equal(newsReviewStateInvariantHolds({
    annotation_status: "NOT_REQUIRED",
    model_visibility: "NOT_YET_PARSED",
    parsed_at: null,
  }), false);
  assert.equal(newsReviewStateInvariantHolds({
    annotation_status: "BACKING_OFF",
    model_visibility: "BACKING_OFF",
    parsed_at: null,
  }), true);
  assert.equal(newsReviewStateInvariantHolds({
    annotation_status: "REPAIRING_DISPLAY",
    model_visibility: "REPAIRING_DISPLAY",
    parsed_at: null,
  }), true);
  assert.equal(newsReviewStateInvariantHolds({
    annotation_status: "BACKING_OFF",
    model_visibility: "MODEL_VISIBLE",
    parsed_at: "2026-08-17T00:00:00Z",
  }), false);

  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(view, /className="news-review-zones"/);
  assert.match(view, /review_state: newsReviewState/);
  assert.match(view, /setNewsCategory\("全部"\)/);
  assert.match(route, /invalid review state/);
  assert.match(route, /review_state_counts/);
  assert.match(route, /NEWS_REVIEW_STATE_CASE_SQL/);
  assert.doesNotMatch(route, /annotation_status'\) IN/);
  assert.match(css, /\.news-review-zones button \{[^}]*min-height:104px/);
  assert.match(view, /className="news-category-picker"/);
  assert.match(css, /\.news-review-zones \{ grid-template-columns:repeat\(3,minmax\(0,1fr\)\);[^}]*overflow:visible/);
  assert.match(css, /\.news-browser nav \{ display:none; \}/);
  assert.match(css, /\.news-category-picker select \{[^}]*min-height:48px/);
  assert.match(css, /\.news-row>summary \{ grid-template-columns:1fr;/);
  assert.match(css, /\.annotation-queue>span:nth-of-type\(4\),\.annotation-queue>span:nth-of-type\(5\) \{ display:flex/);
  assert.match(view, /newsIndex\.review_state_counts\?\.PROCESSING/);
  assert.match(view, /newsIndex\.review_state_counts\?\.ISOLATED/);
  assert.match(css, /\.news-row-title \{ order:1; \}/);
  assert.match(css, /\.news-table \{ display:grid; gap:15px; border:0/);
});

test("keeps the 60-day news archive inside bounded D1 work", () => {
  const index = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  const reviewState = readFileSync(new URL("../app/_lib/news-review-state.ts", import.meta.url), "utf8");
  const detail = readFileSync(new URL("../app/api/news-content/route.ts", import.meta.url), "utf8");
  const migration = readFileSync(new URL("../drizzle/0007_bounded_news_archive.sql", import.meta.url), "utf8");
  assert.match(index, /body\.items\.length > 20/);
  assert.match(detail, /body\.items\.length > 20/);
  assert.match(detail, /DETAIL_BATCH_LIMIT = 12/);
  assert.match(detail, /WHERE detail_key IN \(\$\{placeholders\}\)/);
  assert.match(index, /ORDER BY published_time DESC/);
  assert.match(index, /impact_expires_at>\?/);
  assert.match(index, /item\.model_visibility = "IMPACT_EXPIRED"/);
  assert.match(index, /DELETE FROM news_index WHERE mirror_contract <> \?/);
  assert.match(index, /neutralize_operational_state_for_contract/);
  assert.match(index, /CONTRACT_HANDOVER_PENDING/);
  assert.match(reviewState, /annotation_status'\)='NOT_REQUIRED'/);
  assert.match(reviewState, /model_visibility'\)='NOT_YET_PARSED'/);
  assert.match(index, /health_check/);
  assert.match(index, /NEWS_DETAIL_MISSING/);
  assert.match(index, /NEWS_PARSED_FLAG_MISMATCH/);
  assert.match(index, /NEWS_CANDIDATE_FLAG_MISMATCH/);
  assert.match(index, /current_contract/);
  assert.match(index, /mirror_contract=\?/);
  assert.match(index, /NEWS_DUPLICATE_ACTIVE_CLUSTER/);
  assert.match(index, /NEWS_MIRROR_CONTRACT_STALE/);
  assert.match(index, /NEWS_MIRROR_HEALTH_UNAVAILABLE/);
  assert.match(index, /news review state invariant violation/);
  assert.match(index, /SET model_candidate=0 WHERE mirror_contract <> \?/);
  assert.match(index, /SET parsed=0,/);
  assert.match(index, /body\.withdraw_detail_keys\.length > 20/);
  assert.match(index, /DELETE FROM news_index WHERE detail_key = \?/);
  assert.match(index, /DELETE FROM news_details WHERE detail_key = \?/);
  assert.match(index, /s-maxage=30/);
  assert.match(migration, /news_index_published_idx/);
  assert.match(migration, /news_index_category_published_idx/);
});

test("keeps every D1 migration compatible with remote compound-statement parsing", () => {
  const attributes = readFileSync(new URL("../../.gitattributes", import.meta.url), "utf8");
  assert.match(attributes, /^web\/drizzle\/\*\.sql text eol=lf$/m);
  const migrations = readdirSync(new URL("../drizzle/", import.meta.url))
    .filter(name => name.endsWith(".sql"));
  assert.ok(migrations.length > 0);
  for (const migration of migrations) {
    const sql = readFileSync(new URL(`../drizzle/${migration}`, import.meta.url), "utf8");
    assert.doesNotMatch(sql, /\r\n/, `${migration} must remain LF-only`);
    const triggers = unstable_splitSqlQuery(sql)
      .filter(statement => /^CREATE TRIGGER\b/i.test(statement));
    for (const trigger of triggers) {
      assert.doesNotMatch(
        trigger,
        /\bSELECT\s+CASE\b/i,
        `${migration} trigger bodies must avoid SELECT CASE for remote D1 query parsing`,
      );
    }
  }
});

test("prefetches bounded news details and avoids a fast loading-label flash", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /api\/news-content\?keys=/);
  assert.match(source, /setShowSlowLoading\(true\), 180/);
  assert.doesNotMatch(source, /正在读取新闻详情/);
  assert.match(css, /\.news-detail-skeleton\.is-visible/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(source, /BACKGROUND: "非当前影响"/);
  assert.match(source, /new Set\(\[/);
  assert.doesNotMatch(source, /Gemini 中文标题/);
});

test("refreshes current resources without polling build-snapshot-only resources", () => {
  const helper = readFileSync(new URL("../app/_lib/dashboard-refresh.ts", import.meta.url), "utf8");
  assert.match(helper, /live:\s*15_000/);
  assert.match(helper, /status:\s*60_000/);
  assert.match(helper, /news:\s*30_000/);
  assert.match(helper, /learning:\s*300_000/);
  assert.match(helper, /deployment:\s*120_000/);
  assert.match(helper, /DashboardResourceMode = "current" \| "build-snapshot"/);
  assert.match(helper, /resourceMode === "current"/);
  assert.match(helper, /mayRefresh\s*\?\s*window\.setInterval\(pollWhenEligible, intervalMs\)\s*:\s*null/);
  assert.match(helper, /visibilitychange/);
  const statusView = readFileSync(new URL("../app/_views/StatusView.tsx", import.meta.url), "utf8");
  const healthView = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  assert.match(statusView, /DASHBOARD_REFRESH_INTERVALS\.status \/ 1000/);
  assert.match(healthView, /DASHBOARD_REFRESH_INTERVALS\.status \/ 1000/);
  for (const path of [
    "../app/_views/LiveRoomView.tsx",
    "../app/_views/StatusView.tsx",
    "../app/_views/HealthView.tsx",
    "../app/_views/AuditView.tsx",
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, /scheduleDashboardRefresh/);
    assert.match(source, /DASHBOARD_REFRESH_INTERVALS\.[a-z]+,[\s\S]*?"current"/);
    assert.doesNotMatch(source, /isImmutablePreview|immutablePreview/);
  }
});

test("a shared polling lease cannot leave another visible tab permanently stale", () => {
  const intervalMs = 15_000;
  const common = {
    visible: true,
    automated: false,
    intervalMs,
    lastLocalPollAt: 100_000,
  };

  assert.equal(shouldPollDashboardResource({
    ...common,
    now: 115_000,
    lastSharedPollAt: 114_500,
  }), false, "a recent poll by another tab should suppress duplicate work");

  assert.equal(shouldPollDashboardResource({
    ...common,
    now: 130_000,
    lastSharedPollAt: 129_500,
  }), true, "each visible tab must refresh after two local intervals");

  assert.equal(shouldPollDashboardResource({
    ...common,
    visible: false,
    now: 145_000,
    lastSharedPollAt: 144_500,
  }), false, "hidden tabs must not bypass the request budget");

  assert.equal(shouldPollDashboardResource({
    ...common,
    automated: true,
    now: 145_000,
    lastSharedPollAt: 0,
  }), false, "browser automation must not create background polling");
});

test("renders static Preview shells with embedded resources for client-side rooms", async () => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") return;
  for (const [path, marker] of [
    ["/", /Aurum Signal Room/],
    ["/health", /系统健康状态/],
    ["/audit?view=news", /证据台页面/],
    ["/admin", /管理后台/],
    ["/admin/ai-usage", /AI 模型使用状态/],
    ["/admin/retry-jobs", /PRIVATE OPERATOR QUEUE/],
  ]) {
    const { response, html } = await renderSettled(path, marker);
    assert.equal(response.status, 200, path);
    assert.match(html, marker, path);
  }
  for (const view of ["news", "evidence", "stories", "decisions", "league", "coverage"]) {
    const response = await render(`/audit?view=${view}`);
    assert.equal(response.status, 200, view);
    const html = await response.text();
    assert.doesNotMatch(html, /正在同步页面当前指标/, view);
    assert.match(html, /<title>证据台页面 \| Aurum Signal Room<\/title>/, view);
    assert.match(html, /<noscript><main><h1>证据台页面<\/h1>/, view);
  }
  for (const view of ["briefs", "search"]) {
    const response = await render(`/audit?view=${view}`);
    assert.equal(response.status, 200, view);
    assert.match(await response.text(), /证据台页面/, view);
  }
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  assert.match(app, /parseDashboardUrl\(new URL\(window\.location\.href\)\)/);
  assert.match(app, /setLocation\(current =>/);
  assert.match(app, /<AuditView key=\{location\.auditView\} initialView=\{location\.auditView\}/);
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(audit, /function AuditView\(\{ initialView \}/);
  assert.doesNotMatch(audit, /useSearchParams/);
  assert.doesNotMatch(audit, /requestedView/);
});

test("formats server-rendered preview times in one deterministic timezone", () => {
  for (const path of ["../app/_views/AuditView.tsx", "../app/_views/LiveRoomView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx"]) {
    assert.match(readFileSync(new URL(path, import.meta.url), "utf8"), /timeZone:\s*"Asia\/Kuala_Lumpur"/, path);
  }
});

test("returns a verified main revision through the deployment status endpoint", () => {
  const ingest = readFileSync(new URL("../app/api/ingest/route.ts", import.meta.url), "utf8");
  const snapshot = readFileSync(new URL("../app/api/_shared/dashboard-snapshot.ts", import.meta.url), "utf8");
  const vite = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  assert.match(vite, /__AURUM_DEPLOYMENT__/);
  assert.match(vite, /WORKERS_CI_COMMIT_SHA/);
  assert.match(ingest, /deployment\.branch === "main"/);
  assert.match(ingest, /\^\[0-9a-f\]\{40\}\$/);
  assert.match(ingest, /main_revision/);
  assert.match(ingest, /export async function GET/);
  assert.match(ingest, /Cache-Control.*no-store/);
  assert.match(ingest, /writeDashboardStatusSnapshots\(body\.serialized, binding\)/);
  assert.doesNotMatch(ingest, /request\.json\(\)|JSON\.stringify\(|TextEncoder/);
  assert.match(snapshot, /json_valid\(payload\)/);
  assert.match(snapshot, /content-length/);
  assert.match(snapshot, /MAX_DASHBOARD_SNAPSHOT_BYTES/);
});

test("replaces the forecast state with the broker reopening countdown", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /const forecastStatus = marketClosed/);
  assert.match(source, /距离重开/);
  assert.match(source, /marketClosed \|\| marketUnavailable \|\| \(signalRemaining > 0 && online\)/);
  assert.match(source, /等待行情恢复/);
  assert.match(source, /等待最新预测/);
  assert.doesNotMatch(source, /当前不可参考/);
});

test("renders the Gemini quota status route", async () => {
  const source = readFileSync(new URL("../app/_views/StatusView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const { response, html } = await renderSettled("/admin/ai-usage", /AI 模型使用状态/);
  assert.equal(response.status, 200);
  assert.match(html, /AI 模型使用状态/);
  assert.match(html, /Gemini 3.5 Flash-Lite/);
  assert.match(html, /Gemini 3.1 Flash-Lite/);
  assert.match(html, /Gemma 4 31B/);
  assert.match(html, /reset-countdown/);
  assert.match(html, /逐 Key 配额/);
  assert.match(source, /本机已准入 \/ 上限/);
  assert.match(source, /className="quota-value"/);
  assert.match(source, /className="quota-overview" aria-labelledby="quota-overview-title"/);
  assert.match(source, /id="quota-capacity-title">账户与每日额度/);
  assert.match(source, /id="quota-allocation-title">新闻额度分配/);
  assert.match(source, /id="quota-queue-title">请求异常/);
  assert.match(source, /annotation_queue\.backing_off \? "quota-metric-attention"/);
  assert.match(source, /annotation_queue\.dead_letter \? "quota-metric-danger"/);
  assert.match(source, /className="throughput-section" aria-labelledby="throughput-title"/);
  assert.doesNotMatch(html, /class="routing-grid"/);
  assert.match(html, /账户与每日额度[\s\S]*?Gemini 3\.5 Flash-Lite[\s\S]*?Gemini 3\.1 Flash-Lite/);
  assert.match(html, /新闻额度分配[\s\S]*?Gemma 4 31B/);
  assert.match(html, /Antigravity[\s\S]*?未启用/);
  assert.doesNotMatch(css, /\.routing-grid/);
  assert.match(css, /\.quota-overview-layout \{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.quota-capacity-grid \{[^}]*grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/);
  assert.match(css, /\.throughput-summary \{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(css, /\.quota-metric-grid article\+article \{[^}]*border-left:1px solid var\(--ink\)/);
  assert.match(css, /\.quota-metric-grid article:before \{[^}]*width:46px/);
  for (const [state, color] of [["priority", "green"], ["attention", "gold"], ["danger", "red"]]) {
    const rule = css.match(new RegExp(`\\.quota-metric-grid \\.quota-metric-${state}:before \\{[^}]*\\}`))?.[0] ?? "";
    assert.match(rule, new RegExp(`background:var\\(--${color}\\)`));
    assert.doesNotMatch(rule, /width:/);
  }
  assert.match(source, /<details className="quota-note">/);
  assert.match(source, /查看账本与 Google 额度的区别/);
  assert.match(html, /分支配置/);
  assert.match(html, /Pacific midnight/);
  assert.match(html, /AI 模型用量/);
  assert.match(html, /aria-current="page"[^>]*>AI 模型用量<\/a>/);
  assert.match(html, /data-read-state="(?:CURRENT|REFRESHING)"/);
  assert.match(html, /data-live-market-state="MARKET_DATA_UNAVAILABLE"/);
  assert.match(html, /data-operational-state="(?:HEALTHY|WARNING|ERROR)"/);
  assert.match(html, /连接中|运行警告|运行异常|实时链路不可用/);
});

test("keeps System Health separate from the dedicated retry workspace", async () => {
  const healthPage = await renderSettled("/health", /系统健康状态/);
  assert.equal(healthPage.response.status, 200);
  const html = healthPage.html;
  assert.match(html, /系统健康状态/);
  assert.doesNotMatch(html, /AI 模型用量|重试任务|管理后台区域/);
  assert.doesNotMatch(html, /PRIVATE OPERATOR QUEUE|class="retry-queue"/);
  const view = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(view, /RetryQueue|operator-retry|assistant-health/);
  const retryView = readFileSync(new URL("../app/_views/RetryView.tsx", import.meta.url), "utf8");
  assert.match(retryView, /<RetryQueue \/>/);
  const retryPage = await renderSettled("/admin/retry-jobs", /PRIVATE OPERATOR QUEUE/);
  assert.equal(retryPage.response.status, 200);
  assert.match(retryPage.html, /PRIVATE OPERATOR QUEUE/);
  assert.match(retryPage.html, /<h1>重试任务<\/h1>/);
  assert.match(retryPage.html, /aria-current="page"[^>]*>[\s\S]*?管理员登录<\/button>/);
  assert.match(retryPage.html, /aria-current="page"[^>]*>重试任务<\/a>/);
  const retryNavigation = retryPage.html.match(/<nav class="dashboard-section-nav admin-section-nav"[\s\S]*?<\/nav>/)?.[0] ?? "";
  assert.match(retryNavigation, /概览[\s\S]*Assistant[\s\S]*重试任务[\s\S]*AI 模型用量/);
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const retryQueue = readFileSync(new URL("../app/_components/RetryQueue.tsx", import.meta.url), "utf8");
  const retryRoute = readFileSync(new URL("../app/api/operator-retry/route.ts", import.meta.url), "utf8");
  const retryGet = retryRoute.match(/export async function GET[\s\S]*?\n\}/)?.[0] ?? "";
  assert.match(retryGet, /authenticateDashboardOperatorRequest\(request, env\)/);
  assert.match(retryGet, /authorization\.state !== "AUTHORIZED"[\s\S]*dashboardOperatorAuthFailure\(authorization\)/);
  assert.ok(retryGet.indexOf("authenticateDashboardOperatorRequest") < retryGet.indexOf("env.DB"));
  assert.ok(retryGet.indexOf("env.DB") < retryGet.indexOf("listOperatorRetryJobs"));
  assert.match(retryQueue, /立即可领取/);
  assert.match(retryQueue, /云端已接受/);
  assert.match(retryQueue, /尚不代表 Windows scheduler 已应用/);
  assert.match(retryQueue, /PR Preview 使用合成演示任务/);
  assert.match(retryQueue, /调整计划/);
  assert.match(retryQueue, /恢复自动计划/);
  assert.match(retryQueue, /action.mode !== "KEEP_ORIGINAL" \|\| overridden/);
  assert.doesNotMatch(retryQueue, /自动重试中|系统空闲时|空的演示队列/);
  assert.match(retryQueue, /timeZone: "Asia\/Kuala_Lumpur"/);
  assert.match(retryQueue, /type="datetime-local"/);
  assert.match(retryQueue, /PRIVATE OPERATOR QUEUE/);
  assert.match(retryQueue, /className="retry-queue-summary"/);
  assert.match(retryQueue, /summary\.total/);
  assert.match(retryQueue, /selected\.size \? `已选 \$\{selected\.size\} 个` : "选择任务后可批量调整"/);
  assert.match(retryQueue, /selected\.size \? <div className="retry-bulk-action">/);
  assert.match(retryQueue, /原自动计划/);
  assert.match(retryQueue, /retry-checkbox-target/);
  assert.match(retryQueue, /className=\{`retry-job-row \$\{expanded \? "is-expanded" : ""\}`\}/);
  assert.match(retryQueue, /expandedJobId === job\.job_id/);
  assert.match(retryQueue, /setExpandedJobId\(expanded \? null : job\.job_id\)/);
  assert.match(retryQueue, /expanded \? <div className="retry-job-plan"/);
  assert.match(retryQueue, /command\.shortLabel/);
  assert.doesNotMatch(retryQueue, /retry-job-card|retry-job-main/);
  assert.match(css, /\.retry-checkbox-target \{[^}]*min-width:44px;[^}]*min-height:44px/);
  assert.match(css, /\.retry-job-row \{[^}]*grid-template-columns:44px minmax\(220px,1fr\)[^}]*padding:8px 12px/);
  assert.match(css, /\.retry-job-plan \{[^}]*grid-column:2 \/ -1/);
  assert.match(css, /\.retry-job-plan > div \{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.retry-job-row \{ grid-template-columns:44px minmax\(0,1fr\)/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.retry-job-control \{ position:absolute; top:8px; right:10px; width:96px/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.retry-job-plan > div \{ grid-template-columns:1fr 1fr/);
  assert.match(layout, /<OperationalAlertBanner \/>/);
  const banner = readFileSync(new URL("../app/_components/OperationalAlertBanner.tsx", import.meta.url), "utf8");
  assert.match(banner, /globalOperationalIncidents/);
  assert.match(banner, /className="operational-alert-toggle"/);
  assert.match(banner, /aria-expanded=\{expanded\}/);
  assert.match(view, /CURRENT PROBLEMS/);
  assert.doesNotMatch(view, /health-at-a-glance|当前结论/);
  assert.match(view, /className="incident-operator-summary"/);
  assert.match(view, /incidentStatusLabel/);
  assert.match(view, /incidentStatusMark/);
  assert.match(view, /operatorSummaryDetails/);
  assert.match(view, /operationalSummaryDetails\(/);
  assert.match(view, /operatorSummaryDetails\.map\(detail => ` · \$\{detail\}`\)\.join\(""\)/);
  assert.match(view, /primaryOperatorAction\(incidents\)/);
  assert.match(view, /operationalIncidentsNextRetryAt\(incidents, operatorAction\)/);
  assert.match(view, /operatorComponentScanState\(item\.status, incident\)/);
  assert.match(view, /operationalIncidentActionLabels\[operatorAction\]/);
  assert.match(view, /无需处理/);
  assert.doesNotMatch(view, /现在需要人工处理|当前无需人工处理/);
  assert.doesNotMatch(view, /<small>自动恢复<\/small>|<small>人工处理<\/small>/);
  assert.match(view, /incident\.root_event/);
  assert.match(view, /affectedOperationalScopeCount/);
  assert.match(view, /受影响子系统/);
  assert.doesNotMatch(view, /个问题 · 异常优先|当前没有运行问题/);
  assert.match(view, /当前没有运行异常。/);
  assert.doesNotMatch(view, /个下游影响|related_events\.length, 0/);
  assert.match(view, /查看技术详情/);
  assert.match(view, /aria-expanded=\{showTechnical\}/);
  assert.match(view, /hidden=\{!showTechnical\}/);
  assert.match(view, /operationalIncidentNextRetryAt/);
  assert.match(view, /下次尝试 \{localTime\(nextRetryAt\)\}/);
  assert.match(view, /completed_15m/);
  assert.match(view, /deferred_15m/);
  assert.match(view, /provider_dispatch_deferred_15m/);
  assert.match(view, /LOCAL_\{item\.dimension\}_LIMIT/);
  assert.match(view, /componentHasAttention/);
  assert.match(view, /sourceHasAttention/);
  assert.match(view, /function ComponentHealthCard/);
  assert.match(view, /function SourceHealthCard/);
  assert.match(view, /className="component-card-grid"/);
  assert.match(view, /className="source-health-grid"/);
  assert.match(view, /<dt>最近成功<\/dt><dd>\{localTime\(item\.last_success\)\}<\/dd>/);
  assert.match(view, /item\.freshness_reference_status === "PARTIAL"/);
  assert.match(view, /className="component-technical-details"/);
  assert.match(view, /className="source-technical-details"/);
  assert.match(view, /state\.attention \? "技术详情" : "详情 ›"/);
  assert.match(view, /item\.health === "WARMING_UP" && !item\.last_error/);
  assert.match(view, /item\.last_error \?\? "无已记录错误"/);
  assert.match(view, /<dt>原始状态<\/dt><dd><code>\{item\.status\}<\/code><\/dd>/);
  assert.match(view, /incident\.severity === "ERROR" \? "错误"/);
  assert.match(view, /severity=\{event\.severity\}/);
  assert.match(readFileSync(new URL("../app/_lib/operational-incident-presentation.ts", import.meta.url), "utf8"), /daily_news_brief: "每日新闻简报"/);
  assert.match(view, /<dt>关联问题<\/dt><dd>\{incident\.summary_zh\}<\/dd>/);
  assert.match(view, /projection\.reason_code/);
  assert.match(view, /className="health-technical-section"/);
  assert.match(view, /调度器与技术状态/);
  const incidentIndex = view.indexOf('<section id="operational-alerts"');
  const componentIndex = view.indexOf('<section className={`component-status');
  const sourceIndex = view.indexOf('<section className={`source-health');
  const technicalIndex = view.indexOf('<section className="health-technical-section"');
  assert.ok(incidentIndex < componentIndex && componentIndex < sourceIndex && sourceIndex < technicalIndex);
  assert.match(view, /sortAttentionFirst\(Object\.entries/);
  assert.match(view, /sortAttentionFirst\(payload\?\.news_source_health/);
  assert.match(view, /className="health-state-mark" aria-label=\{state\.label\}>\{state\.symbol\}/);
  assert.match(view, /className="health-state-text">\{state\.label\}/);
  assert.match(view, /className="health-row-meta"/);
  assert.match(css, /\.component-status article\.is-healthy,\.source-health article\.is-healthy \{[^}]*position:relative; display:block/);
  assert.match(css, /\.component-card-grid>article\.is-healthy:has\(>details\[open\]\),\.source-health-grid>article\.is-healthy:has\(>details\[open\]\) \{[^}]*grid-column:1\/-1/);
  assert.match(css, /article\.is-healthy>header,\.source-health article\.is-healthy>header \{[^}]*padding-right:72px/);
  assert.match(css, /article\.is-healthy>\.component-technical-details,\.source-health article\.is-healthy>\.source-technical-details \{[^}]*display:block; height:0/);
  assert.match(css, /article\.is-healthy>\.component-technical-details\[open\],\.source-health article\.is-healthy>\.source-technical-details\[open\] \{[^}]*height:auto/);
  assert.match(css, /article\.is-healthy>\.component-technical-details>summary,\.source-health article\.is-healthy>\.source-technical-details>summary \{[^}]*position:absolute; top:0; right:0; width:60px; justify-content:flex-end/);
  assert.match(css, /article\.is-healthy>\.component-technical-details>:not\(summary\),\.source-health article\.is-healthy>\.source-technical-details>:not\(summary\) \{[^}]*width:100%/);
  assert.match(css, /\.component-status article\.is-attention,\.source-health article\.is-attention \{[^}]*grid-column:1\/-1/);
  assert.match(css, /\.component-current-problem \{[^}]*min-height:46px/);
  assert.match(css, /\.component-card-grid \{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.source-health-grid \{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.component-card-grid \{[^}]*align-items:start/);
  assert.match(css, /\.source-health-grid \{[^}]*align-items:start/);
  assert.doesNotMatch(css, /\.component-status>div \{[^}]*grid-template-columns:repeat\(6/);
  assert.doesNotMatch(css, /\.component-status article:not\(:nth-child\(3n\)\)/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.component-card-grid,\.source-health-grid \{ grid-template-columns:1fr; \}/);
  assert.match(css, /\.operational-alert-banner a \{[^}]*min-height: 44px/);
  assert.match(css, /\.incident-technical-details > button[^}]*min-height:44px/);
  assert.match(css, /\.component-technical-details>summary,\.source-technical-details>summary \{[^}]*min-height:44px/);
  assert.match(css, /article\.is-healthy summary,\.source-health article\.is-healthy summary \{[^}]*min-height:48px/);
  assert.match(css, /article\.is-healthy,\.source-health article\.is-healthy \{[^}]*border-bottom:1px solid rgba\(17,17,15,\.16\)/);
  assert.match(css, /\.component-status article h3,\.source-health article>header strong \{[^}]*font-family:var\(--font-sans\)/);
  assert.match(css, /\.incident-summary-panel,\.component-status,\.source-health \{[^}]*font-family:var\(--font-sans\)/);
  assert.match(css, /\.health-row-meta \{[^}]*display:flex[^}]*white-space:nowrap/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*article\.is-healthy>header[^}]*grid-template-columns:26px minmax\(0,1fr\)[^}]*min-height:64px/);
  assert.match(css, /article\.is-healthy>header \.health-row-meta[^}]*grid-column:2[^}]*grid-row:2/);
  assert.match(css, /article\.is-healthy summary,\.source-health article\.is-healthy summary \{[^}]*min-height:64px/);
  assert.doesNotMatch(css, /\.health-at-a-glance|\.health-conclusion-mark|\.health-current-conclusion/);
  assert.match(css, /\.incident-summary-panel time,\.component-status time,\.source-health time,\.incident-raw-evidence \{[^}]*font-family:var\(--font-mono\)/);
  assert.match(css, /\.operational-incident-card \{[^}]*min-width:0/);
  assert.match(css, /\.operational-alert-toggle \{ display:none; cursor:pointer; \}/);
  assert.match(css, /\.operational-alert-banner\.is-expanded \.operational-alert-detail \{ display:flex/);
  assert.match(css, /@media \(max-width: 640px\)[\s\S]*\.scheduler-health-grid \{ grid-template-columns: 1fr; \}/);
});

test("separates anonymous health data from owner-only Admin evidence", async () => {
  const publicStatus = readFileSync(new URL("../app/api/status/route.ts", import.meta.url), "utf8");
  const statusProjection = readFileSync(new URL("../app/api/_shared/dashboard-status.ts", import.meta.url), "utf8");
  const statusContract = readFileSync(new URL("../app/api/_shared/dashboard-snapshot.ts", import.meta.url), "utf8");
  const adminStatus = readFileSync(new URL("../app/api/admin-status/route.ts", import.meta.url), "utf8");
  const assistantHealth = readFileSync(new URL("../app/api/assistant-health/route.ts", import.meta.url), "utf8");
  const healthView = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  const alertBanner = readFileSync(new URL("../app/_components/OperationalAlertBanner.tsx", import.meta.url), "utf8");
  const adminOverview = readFileSync(new URL("../app/_views/AdminOverviewView.tsx", import.meta.url), "utf8");
  const adminClient = readFileSync(new URL("../app/_lib/admin-client.ts", import.meta.url), "utf8");
  const statusView = readFileSync(new URL("../app/_views/StatusView.tsx", import.meta.url), "utf8");
  const retryQueue = readFileSync(new URL("../app/_components/RetryQueue.tsx", import.meta.url), "utf8");
  const assistantView = readFileSync(new URL("../app/_views/AssistantView.tsx", import.meta.url), "utf8");
  const assistantTranscript = readFileSync(new URL("../app/_components/AssistantTranscript.tsx", import.meta.url), "utf8");

  assert.match(publicStatus, /publicDashboardStatus\(payload\)/);
  for (const field of [
    "annotation_queue", "gemini_quota", "gemini_31_quota", "gemma_quota",
    "gemini_embedding_quota", "llm_routing",
  ]) assert.match(statusContract, new RegExp(`"${field}"`));
  assert.match(statusProjection, /PUBLIC_STATUS_PRIVATE_FIELDS/);
  assert.doesNotMatch(healthView, /operator-retry|assistant-health|AdminOverview/);
  assert.doesNotMatch(alertBanner, /assistant-health|AssistantOperationalHealth/);
  assert.match(adminOverview, /fetch\(`\$\{ADMIN_API_PREFIX\}\/operator-retry`/);
  assert.match(adminOverview, /fetch\(`\$\{ADMIN_API_PREFIX\}\/assistant-health`/);
  assert.match(adminOverview, /assistantHealthPresentation\(assistantHealth\)/);
  assert.match(adminOverview, /需要管理员登录/);
  assert.match(adminOverview, /href="\/admin">管理员登录/);
  assert.match(adminClient, /ADMIN_AUTH_REQUIRED_MESSAGE = "需要管理员登录。"/);
  assert.doesNotMatch(adminClient, /会话已过期/);
  assert.match(statusView, /adminErrorPresentation[\s\S]*href="\/admin">管理员登录/);
  assert.match(statusView, /presentation\.kind === "AUTH_REQUIRED"[\s\S]*clearDashboardResource\(statusUrl\)[\s\S]*setPayload\(null\)/);
  const dashboardResource = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  assert.match(dashboardResource, /export function clearDashboardResource\(url: string\)/);
  assert.doesNotMatch(dashboardResource, /resources\.clear\(\)/);
  assert.match(retryQueue, /adminErrorPresentation[\s\S]*href="\/admin">管理员登录/);
  assert.match(assistantView, /ADMIN_AUTH_REQUIRED_MESSAGE/);
  assert.doesNotMatch(assistantView, /会话已过期/);
  assert.match(assistantTranscript, /href="\/admin">管理员登录/);
  assert.doesNotMatch(statusProjection, /fetch\(|STATUS_RELAY_URL/);
  assert.doesNotMatch(adminStatus, /STATUS_RELAY_URL/);

  for (const source of [adminStatus, assistantHealth]) {
    const get = source.match(/export async function GET[\s\S]*?\n\}/)?.[0] ?? "";
    assert.match(get, /authenticateDashboardOperatorRequest\(request, env\)/);
    assert.ok(get.indexOf("authenticateDashboardOperatorRequest") < get.indexOf("env.DB"));
  }
  assert.match(adminStatus, /previewBundle[\s\S]*synthetic-admin-status/);
  assert.match(assistantHealth, /status: "HEALTHY"[\s\S]*current: false/);

  const overview = await renderSettled("/admin", /OWNER OPERATIONS/);
  assert.equal(overview.response.status, 200);
  assert.match(overview.html, /概览[\s\S]*Assistant[\s\S]*重试任务[\s\S]*AI 模型用量/);
  assert.match(overview.html, /总任务[\s\S]*等待应用[\s\S]*冲突/);
  assert.doesNotMatch(adminOverview, /进入私有对话|查看 Windows 应用进度|查看模型额度/);
  assert.match(adminOverview, /className="admin-overview-health"/);
  const adminCss = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(adminCss, /\.admin-overview-card \{[^}]*min-height:198px/);
  assert.match(overview.html, /aria-current="page"[^>]*>概览<\/a>/);
});

test("uses one Chinese system-state presentation across every dashboard page", () => {
  const component = readFileSync(new URL("../app/_components/SystemStatePill.tsx", import.meta.url), "utf8");
  const shell = readFileSync(new URL("../app/_components/DashboardShell.tsx", import.meta.url), "utf8");
  const contract = readFileSync(new URL("../app/_lib/system-state.ts", import.meta.url), "utf8");
  const freshness = readFileSync(new URL("../app/_components/CurrentDataState.tsx", import.meta.url), "utf8");
  assert.match(component, /systemStatePresentation/);
  assert.match(component, /data-read-state/);
  assert.match(contract, /实时链路正常/);
  assert.match(contract, /实时链路不可用/);
  assert.match(contract, /市场休市/);
  assert.match(contract, /状态不可用/);
  assert.doesNotMatch(contract, /状态离线|系统在线/);
  assert.match(freshness, /状态更新失败，正在重试/);
  assert.match(freshness, /最近状态/);
  assert.match(shell, /className="dashboard-global-state"/);
  assert.match(shell, /<SystemStatePill/);
  assert.match(shell, /subscribeDashboardResource\("\/api\/status"/);
  assert.match(shell, /readDashboardResourceState<ShellStatusPayload>/);
  assert.match(shell, /hasSnapshot=/);
  assert.match(shell, /operationalStatus=/);
  for (const path of ["../app/_views/LiveRoomView.tsx", "../app/_views/AuditView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx", "../app/_views/AssistantView.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(source, /SystemStatePill/);
    assert.doesNotMatch(source, /MARKET CLOSED|CONNECTING|市场休市 · 新闻运行中/);
  }
});

test("keeps read, live-market, and operational status axes independent", () => {
  const cachedRefreshFailure = systemStatePresentation({
    loading: false, error: true, hasSnapshot: true, online: true,
    marketSession: "OPEN", operationalStatus: "HEALTHY",
  });
  assert.equal(cachedRefreshFailure.readState, "STALE_SNAPSHOT");
  assert.equal(cachedRefreshFailure.label, "状态更新失败");

  assert.equal(systemStatePresentation({
    loading: false, error: true, hasSnapshot: false, online: false,
  }).label, "状态不可用");

  const closed = systemStateAxes({
    loading: false, error: false, hasSnapshot: true, online: false,
    marketSession: "CLOSED", operationalStatus: "HEALTHY",
  });
  assert.equal(closed.liveMarketState, "MARKET_CLOSED");
  assert.equal(closed.operationalState, "HEALTHY");

  assert.equal(systemStatePresentation({
    loading: false, error: false, hasSnapshot: true, online: false,
    marketSession: "DATA_UNAVAILABLE", operationalStatus: "HEALTHY",
  }).label, "实时链路不可用");

  assert.equal(systemStatePresentation({
    loading: false, error: false, hasSnapshot: true, online: true,
    marketSession: "OPEN", operationalStatus: "ERROR",
  }).label, "运行异常");
});

test("reports cTrader health independently from downstream decision lag", () => {
  assert.deepEqual(quoteBridgePresentation("OK", "DATA_UNAVAILABLE"), {
    label: "本机在线",
    good: true,
  });
  assert.deepEqual(quoteBridgePresentation("STALE", "DATA_UNAVAILABLE"), {
    label: "本机中断",
    good: false,
  });
  assert.deepEqual(quoteBridgePresentation("MARKET_CLOSED", "CLOSED"), {
    label: "市场休市 · 新闻继续",
    good: true,
  });
});

test("live room presents broker-confirmed closure instead of a WAIT prediction", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /距离重开/);
  assert.doesNotMatch(source, /cTrader 已确认 XAUUSD 休市/);
  assert.match(source, /const dialAction = marketClosed/);
  assert.match(source, /marketUnavailable\s*\? "无行情"/);
});

test("live room hides a stale forecast when broker status is unavailable", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /const marketUnavailable = Boolean\(payload && !online && !marketClosed\)/);
  assert.match(source, /marketUnavailable\s*\? "unavailable"/);
  assert.match(source, /marketUnavailable\s*\? "无行情"/);
  assert.match(source, /marketClosed \|\| marketUnavailable \|\|/);
});

test("renders the news and decision audit route", async () => {
  const response = await render("/audit?view=news");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Signal Room/);
  assert.match(html, /XAUUSD · Forward-only intelligence/);
  assert.match(html, /新闻与决策/);
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, />新闻 <b>/);
  assert.match(source, /当前可用新闻事件/);
  assert.match(source, /模型真正用过哪些新闻/);
  assert.match(source, /按独立事件说明模型用过什么、没用什么/);
  assert.match(source, /evidence-intro evidence-intro-compact/);
  assert.match(source, /查看统计规则/);
  assert.match(source, /收到多少篇文章/);
  assert.match(source, /历史上用过多少个事件/);
  assert.match(source, /影响过多少次预测/);
  assert.match(source, /模型一共读取多少次/);
  assert.match(source, /现在仍可用于预测/);
  assert.match(source, /本筛选已载入/);
  assert.match(source, /完整总数保留在审计账本/);
  assert.match(source, /这个分类有记录，但本页尚未载入明细/);
  assert.match(source, /这不是新闻数量/);
  assert.doesNotMatch(source, /文章 \/ Revision/);
  assert.doesNotMatch(source, /当前达到 Broad 门槛/);
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.evidence-summary \{[^}]*grid-template-columns:repeat\(3,1fr\)/);
  assert.match(css, /\.evidence-filters button \{[^}]*min-height:44px/);
  assert.match(css, /\.evidence-rule-note summary \{[^}]*min-height:44px/);
  assert.match(source, /多源确认/);
  assert.match(source, /核心新闻要求一手完整证据或至少两个独立可靠来源确认/);
  assert.match(source, /大视野新闻还纳入单一可靠来源并降低权重/);
  assert.match(source, /api\/news-content\?key=/);
  assert.match(source, /api\/news-index\?/);
  assert.match(source, /api\/learning/);
  assert.match(source, /briefs: "\/api\/audit-briefs"/);
  assert.match(source, /stories: "\/api\/audit-stories"/);
  assert.match(source, /decisions: "\/api\/audit-decisions"/);
  assert.match(source, /当前页面尚未加载，不会显示为零或空资料/);
  assert.match(source, /页面会自动重试，不会把缺失资料解释为空/);
  assert.match(source, /if \(view !== "news"\) \{[\s\S]*?fullNewsIndexReadyRef\.current[\s\S]*?refreshNews\(true\)/);
  assert.match(source, /Do not poll off-screen/);
  assert.match(source, /view !== "league"/);
  assert.match(source, /loadDashboardResource<Payload>\("\/api\/status"/);
  assert.doesNotMatch(source, /Promise\.allSettled/);
  assert.doesNotMatch(source, /row\.topics\.map/);
  assert.doesNotMatch(source, /row\.model_identities\.map/);
  assert.doesNotMatch(source, /row\.model_unseen_reason_codes\.map/);
  assert.doesNotMatch(source, /IDENTITY_LABELS/);
  assert.match(source, /MODEL_LABELS\[identity\] \?\? identity/);
  assert.match(source, /读取中…/);
  assert.match(source, /学习数据暂不可用|暂不可用/);
  assert.match(source, /页面会保留上一份成功数据并自动重试/);
  assert.doesNotMatch(source, /payload\?\.system\.online && !error/);
  assert.match(source, /api\/news-content\?keys=/);
  assert.doesNotMatch(source, /这些新闻处理到哪里了/);
  assert.match(source, /条近60天可读新闻/);
  assert.match(source, /条已隔离待查/);
  assert.match(source, /GDELT · \$\{row\.category\}/);
  assert.doesNotMatch(source, /GDELT · 新闻发现/);
  assert.match(source, /row\.model_visibility !== "NOT_YET_PARSED"/);
  assert.match(source, /个当前可用事件/);
  const newsIndexRoute = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  assert.match(newsIndexRoute, /SUPERSEDED_CONTRACT/);
  assert.match(newsIndexRoute, /CASE WHEN \$\{ACTIVE_NEWS_SQL\} THEN parsed ELSE 0 END/);
  assert.match(newsIndexRoute, /FROM news_index WHERE \$\{ACTIVE_NEWS_SQL\} GROUP BY review_state/);
  assert.match(newsIndexRoute, /neutralize_operational_state_for_contract/);
  assert.match(newsIndexRoute, /SET model_candidate=0 WHERE mirror_contract <> \?/);
  assert.match(newsIndexRoute, /NEWS_REVIEW_STATE_SQL\[reviewState\]/);
  assert.match(source, /evidenceMode === "eligible"/);
  assert.match(source, />当前可用 <b>/);
  assert.match(source, />历史上用过 <b>/);
  assert.match(source, />从未用过 <b>/);
  assert.doesNotMatch(source, /查看全部/);
  assert.doesNotMatch(source, /个 key 轮换|每分钟最多生成/);
  assert.ok(source.indexOf('<nav className="audit-tabs"') < source.indexOf('<section className="annotation-queue"'));
  assert.doesNotMatch(source, /已经积累多少结果|真实上线后结果|当前模型学到哪里|距离下次学习/);
  assert.match(source, /上一次学习/);
  assert.match(source, /下一次学习/);
  assert.match(source, /还差 \$\{formatExactCount\(rowsUntilTraining\)\} 条/);
  assert.match(source, /目标 \$\{formatExactCount\(payload\?\.training\?\.next_training_at\)\} 条/);
  assert.doesNotMatch(source, /next_training_at\)} − \$\{formatExactCount/);
  assert.doesNotMatch(source, /查看技术审计明细/);
  assert.doesNotMatch(source, /旧工程数据|修复后的训练种子|上线后前向结果/);
  assert.doesNotMatch(source, /Legacy Engineering|Repaired Seed|Next fit/);
  assert.match(source, /大视野新闻还纳入单一可靠来源并降低权重/);
  assert.match(source, /按事件类型和有效交易时间逐步衰减/);
  assert.match(source, /无效样本/);
  assert.match(source, /activeLearningIdentities/);
  assert.match(source, /counts\?\.live_oos_model_groups/);
  assert.match(source, /className="news-table"/);
});

test("switches dashboard rooms locally and reuses client data between views", () => {
  const cache = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const link = readFileSync(new URL("../app/_components/DashboardLink.tsx", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  for (const path of ["../app/_views/LiveRoomView.tsx", "../app/_views/AuditView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(source, /DashboardLink/);
    assert.match(source, /readDashboardResource/);
    assert.match(source, /loadDashboardResource/);
    assert.doesNotMatch(source, /useRouter/);
  }
  assert.match(link, /navigation\?\.preload\(href\)/);
  assert.match(link, /navigation\.navigate\(href, replace\)/);
  assert.match(link, /href=\{href\}/);
  assert.match(link, /setAttribute\("aria-busy", "true"\)/);
  assert.match(app, /window\.history\.pushState/);
  assert.match(app, /window\.history\.replaceState/);
  assert.match(app, /window\.addEventListener\("popstate"/);
  assert.match(app, /lazy\(loadAuditView\)/);
  assert.match(app, /url\.pathname === "\/admin\/retry-jobs"/);
  assert.match(app, /room === "retry"/);
  assert.match(app, /lazy\(loadRetryView\)/);
  assert.match(app, /<RetryView \/>/);
  assert.match(app, /<DashboardShell location=\{location\}>/);
  assert.match(css, /\.dashboard-global-link\.is-navigating::after/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(cache, /const resources = new Map/);
  assert.match(cache, /if \(!options\.force && isFresh\)/);
  assert.match(cache, /if \(entry\.pending\)/);
  assert.match(cache, /cache: "no-store"/);
});

test("redirects only admin legacy URLs and preserves canonical public paths", async () => {
  for (const [path, location] of [
    ["/status", "/admin/ai-usage"],
    ["/assistant", "/admin/assistant"],
    ["/retry-jobs", "/admin/retry-jobs"],
  ]) {
    const response = await render(path);
    assert.equal(response.status, 307);
    const redirected = new URL(response.headers.get("location"), "http://localhost");
    assert.equal(redirected.pathname + redirected.search, location);
  }
  for (const path of ["/?room=assistant", "/?room=retry", "/?room=status"]) {
    const response = await render(path);
    assert.equal(response.status, 200);
  }
  for (const path of ["/health", "/audit?view=league"]) {
    const response = await render(path);
    assert.equal(response.status, 200);
  }
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  assert.match(app, /window\.location\.replace\(canonicalHref\(destination\)\)/);
});

test("reloads an already-open dashboard after a deployment changes its client bundle", () => {
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const refresh = readFileSync(new URL("../app/_components/DeploymentRefresh.tsx", import.meta.url), "utf8");
  assert.match(layout, /<DeploymentRefresh\s*\/>/);
  assert.match(refresh, /\/_next\/static\//);
  assert.match(refresh, /cache:\s*"no-store"/);
  assert.match(refresh, /window\.location\.reload\(\)/);
  assert.match(refresh, /document\.visibilityState !== "visible"/);
  assert.match(refresh, /navigator\.webdriver/);
  assert.match(refresh, /DASHBOARD_REFRESH_INTERVALS\.deployment/);
});

test("keeps large chart snapshots off the Worker JSON serialization path", () => {
  const route = readFileSync(new URL("../app/api/market-chart/route.ts", import.meta.url), "utf8");
  assert.match(route, /return new Response\(row\.payload/);
  assert.doesNotMatch(route, /NextResponse\.json\(JSON\.parse\(row\.payload\)/);
});

test("reads the bounded learning first page before the compact live relay", () => {
  const source = readFileSync(new URL("../app/api/learning/route.ts", import.meta.url), "utf8");
  const d1Read = source.indexOf("dashboard_snapshots WHERE id = ?");
  const relayRead = source.indexOf("process.env.STATUS_RELAY_URL");
  assert.ok(d1Read >= 0, "learning route must read the dedicated D1 snapshot");
  assert.ok(relayRead > d1Read, "the compact relay must remain a fallback");
  assert.match(source, /append-only learning records stored in D1/);
  assert.match(source, /return new Response\(row\.payload/);
  assert.doesNotMatch(source, /NextResponse\.json\(JSON\.parse\(row\.payload\)/);
  assert.match(source, /previewBundle\?\.learning_summary/);
  assert.match(source, /writeDashboardSnapshot\(request, binding, 3,/);
  assert.doesNotMatch(source, /JSON\.parse\(serialized\)|TextEncoder/);
});

test("stores growing learning history as bounded idempotent D1 records", () => {
  const route = readFileSync(new URL("../app/api/learning-history/route.ts", import.meta.url), "utf8");
  const sync = readFileSync(new URL("../../scripts/run_dashboard_sync.py", import.meta.url), "utf8");
  assert.match(route, /MAX_INGEST_BYTES = 350_000/);
  assert.match(route, /readBoundedBody\(request, MAX_INGEST_BYTES\)/);
  assert.match(route, /json_each\(json_extract\(doc,'\$\.records'\)\)/);
  assert.match(route, /ON CONFLICT\(resource,record_key\) DO UPDATE/);
  assert.doesNotMatch(route, /JSON\.parse\(body\.serialized\)/);
  assert.match(route, /MAX_RESPONSE_BYTES = 400_000/);
  assert.match(route, /json_group_array\(json\(payload\)\)/);
  assert.match(route, /length\(CAST\(payload AS BLOB\)\)/);
  assert.match(route, /running_bytes<=\?/);
  assert.doesNotMatch(route, /results\.map\(row => JSON\.parse\(row\.payload\)\)/);
  assert.match(route, /next_cursor/);
  assert.match(sync, /LEARNING_HISTORY_CONTRACT_VERSION = "learning-history-d1-v2"/);
  assert.match(route, /resource='curve-overview'/);
  assert.match(route, /resource='version-overview'/);
  assert.doesNotMatch(route, /row_number\(\) OVER \(PARTITION BY model_identity/);
  assert.match(sync, /learning_history_records/);
  assert.match(sync, /LEARNING_SUMMARY_GROUPS_PER_IDENTITY = 6/);
  assert.match(sync, /LEARNING_SUMMARY_CURVE_POINTS = 48/);
});

test("uses one D1-validated writer for every large dashboard snapshot", () => {
  for (const [path, id] of [
    ["../app/api/market-chart/route.ts", 2],
    ["../app/api/learning/route.ts", 3],
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, new RegExp(`writeDashboardSnapshot\\(request, binding, ${id},`), path);
    assert.doesNotMatch(source, /INSERT INTO dashboard_snapshots/, path);
  }
  const auditRoute = readFileSync(
    new URL("../app/api/audit/route.ts", import.meta.url), "utf8",
  );
  assert.match(
    auditRoute,
    /writeDashboardSnapshot\(\s*request,\s*binding,\s*AUDIT_SNAPSHOT_IDS\.summary,\s*\{[\s\S]*maxBytes:\s*AUDIT_SUMMARY_SNAPSHOT_BYTES/,
  );
  assert.doesNotMatch(auditRoute, /INSERT INTO dashboard_snapshots/);
  const ingest = readFileSync(new URL("../app/api/ingest/route.ts", import.meta.url), "utf8");
  const snapshot = readFileSync(new URL("../app/api/_shared/dashboard-snapshot.ts", import.meta.url), "utf8");
  assert.match(ingest, /writeDashboardStatusSnapshots\(body\.serialized, binding\)/);
  assert.match(snapshot, /writeDashboardStatusSnapshots/);
  assert.match(snapshot, /summary:\s*9/);
  assert.match(snapshot, /AUDIT_SUMMARY_SNAPSHOT_BYTES = 16_000/);
  assert.match(snapshot, /AUDIT_DETAIL_SNAPSHOT_BYTES = 120_000/);
  assert.match(snapshot, /json_valid\(payload\)/);
  assert.match(snapshot, /json_remove/);
});

test("streams byte bounds before parsing every history-sensitive large write", () => {
  for (const path of [
    "../app/api/market-history/route.ts",
    "../app/api/news-index/route.ts",
    "../app/api/news-content/route.ts",
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, /readBoundedBody\(request,/i, path);
    assert.doesNotMatch(source, /request\.text\(\)|request\.json\(\)/, path);
    assert.doesNotMatch(source, /new TextEncoder\(\)\.encode\(serialized\)/, path);
  }
});

test("activates only complete paged news-evidence generations outside status", () => {
  const route = readFileSync(new URL("../app/api/news-evidence/route.ts", import.meta.url), "utf8");
  const migration = readFileSync(
    new URL("../drizzle/0021_paged_news_evidence.sql", import.meta.url), "utf8",
  );
  const store = readFileSync(
    new URL("../app/api/_shared/news-evidence-store.ts", import.meta.url), "utf8",
  );
  const sync = readFileSync(new URL("../../scripts/run_dashboard_sync.py", import.meta.url), "utf8");
  const manifest = JSON.parse(readFileSync(new URL("../preview-manifest.json", import.meta.url), "utf8"));
  assert.match(migration, /PRIMARY KEY\(`snapshot_id`, `event_key`\)/);
  assert.match(migration, /news_evidence_snapshot_eligible_idx/);
  assert.match(migration, /news_evidence_batches/);
  assert.match(migration, /expected_count/);
  assert.match(route, /MAX_WRITE_BYTES = 400_000/);
  assert.match(route, /MAX_PAGE_ITEMS = 50/);
  assert.match(store, /NEWS_EVIDENCE_CURSOR_STALE/);
  assert.match(store, /sort_time<\? OR \(sort_time=\? AND event_key<\?\)/);
  assert.match(store, /pageSize \+ 1/);
  assert.match(store, /next_cursor/);
  assert.doesNotMatch(store, / OFFSET \?/);
  assert.match(store, /SELECT count\(\*\) AS count FROM news_evidence_records/);
  assert.match(store, /news_evidence_staging/);
  assert.match(store, /news_evidence_batches/);
  assert.match(store, /next_offset/);
  assert.match(route, /cleanup_active_snapshot/);
  assert.match(store, /LIMIT 200/);
  assert.match(store, /INSERT INTO news_evidence_state/);
  assert.match(store, /WHERE snapshot_id<>\?/);
  assert.ok(route.indexOf("rejectPreviewWrite()") < route.indexOf("authorizeReleaseValidation("));
  assert.ok(route.indexOf("rejectPreviewWrite()") < route.indexOf("readBoundedBody(request"));
  assert.match(sync, /news evidence snapshot expected \{total\} rows but staged \{received\}/);
  assert.match(sync, /"activate_snapshot": snapshot_id/);
  assert.equal(manifest.resources.newsEvidence, "/api/news-evidence");
  assert.ok(!manifest.statusInlineKeys.includes("news_evidence"));
});

test("rejects oversized snapshots before preparing a D1 statement", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  const binding = { prepare() { prepared = true; } };
  const request = new Request("https://example.test/api/learning", {
    method: "POST",
    headers: { "content-length": String(MAX_DASHBOARD_SNAPSHOT_BYTES + 1) },
    body: "{}",
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 3), "too_large");
  assert.equal(prepared, false);
});

test("rejects an oversized snapshot when content-length understates the body", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  const binding = { prepare() { prepared = true; } };
  const body = JSON.stringify({ data: "x".repeat(MAX_DASHBOARD_SNAPSHOT_BYTES) });
  const request = new Request("https://example.test/api/learning", {
    method: "POST",
    headers: { "content-length": "2" },
    body,
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 3), "too_large");
  assert.equal(prepared, false);
});

test("bounds a streamed snapshot without content-length", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  let cancelled = false;
  let pulls = 0;
  const binding = { prepare() { prepared = true; } };
  const stream = new ReadableStream({
    pull(controller) {
      pulls += 1;
      controller.enqueue(new Uint8Array(
        pulls === 1 ? MAX_DASHBOARD_SNAPSHOT_BYTES : 1,
      ));
    },
    cancel() { cancelled = true; },
  });
  const request = new Request("https://example.test/api/market-chart", {
    method: "POST",
    body: stream,
    duplex: "half",
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 2), "too_large");
  assert.equal(prepared, false);
  assert.equal(cancelled, true);
});

test("lets D1 validate raw snapshot JSON in the same write", async () => {
  const { writeDashboardSnapshot } = await import("../app/api/_shared/dashboard-snapshot.ts");
  const calls = [];
  const binding = {
    prepare(sql) {
      return {
        bind(...values) {
          calls.push({ sql, values });
          return { run: async () => ({ meta: { changes: 1 } }) };
        },
      };
    },
  };
  const body = JSON.stringify({ generated_at: "2026-08-11T12:00:00Z" });
  const request = new Request("https://example.test/api/ingest", {
    method: "POST",
    headers: { "content-length": String(Buffer.byteLength(body)) },
    body,
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 1), "stored");
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /WHERE json_valid\(payload\)/);
  assert.equal(calls[0].values[0], body);
  assert.equal(calls[0].values[1], 1);
});

test("handles a non-JSON service failure without exposing a parser error", () => {
  const resource = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(resource, /数据服务暂时不可用/);
  assert.match(resource, /await response\.text\(\)/);
  assert.doesNotMatch(resource, /await response\.json\(\)/);
  assert.match(audit, /DASHBOARD_REFRESH_INTERVALS\.learning/);
});

test("shows single events immediately and keeps later changes in one thread", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /主题流/);
  assert.match(page, /个事件/);
  assert.match(page, /市场反应流/);
  assert.match(page, /新发生/);
  assert.match(page, /有后续时会自动接成一条脉络/);
  assert.match(page, /showAllStoryEvents/);
  assert.match(page, /showAllStorylines/);
  assert.match(page, /expandedStorylines/);
  assert.match(css, /\.single-event-index>div:not\(\.show-all-mobile-items\)>article:nth-child\(n\+9\)/);
  assert.match(css, /\.story-grid:not\(\.show-all-mobile-items\)>article:nth-child\(n\+5\)/);
  assert.match(css, /\.story-grid ol\.story-timeline \{ display:none/);
  assert.match(css, /\.story-grid ol\.story-timeline\.is-open \{ display:block/);
  assert.match(css, /\.single-event-index>\.mobile-reveal-button \{ display:block;[^}]*min-height:48px/);
  assert.match(css, /\.story-grid \{ gap:18px; border:0; background:transparent; \}/);
  assert.doesNotMatch(page, /暂无后续进展/);
  assert.match(page, /第一次进展立即显示，后续变化接在一起/);
  assert.ok(page.indexOf('className="story-grid"') < page.indexOf('className="theme-streams"'), "events must appear before secondary topic streams");
  assert.match(page, /版本需要更新/);
  assert.doesNotMatch(page, /还没有形成故事链/);
  assert.doesNotMatch(page, /故事开始/);
  assert.doesNotMatch(page, /TEMPORAL EVENT GRAPH V5/);
  assert.doesNotMatch(page, /Runtime Git SHA/);
  assert.doesNotMatch(page, /Story Policy/);
  assert.match(page, /未归属事件/);
  assert.match(css, /\.story-grid[^}]+background:#aaa59a/);
  assert.match(css, /html \{ background:var\(--paper\)/);
  assert.doesNotMatch(css, /\.story-grid[^}]+background:var\(--ink\)/);
});

test("keeps the learning disclaimer short and explicit", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, /仅供研究观察，不代表盈利，也不会自动下单/);
  assert.doesNotMatch(source, /早期曲线用于观察学习过程/);
  assert.doesNotMatch(source, /Champion 始终是 Always Wait/);
});

test("uses one modal timeline for model generations and market decisions", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /打开交互图表/);
  assert.match(page, /新闻修正量/);
  assert.match(page, /核心新闻修正/);
  assert.match(page, /大视野新闻修正量/);
  assert.match(page, /return-flow/);
  assert.match(page, /本组开始前的历史累计/);
  assert.match(page, /加入本组后的连续累计/);
  assert.match(page, /本组独立贡献/);
  assert.match(modal, /长期 OOS 曲线/);
  assert.match(modal, /每组独立成绩/);
  assert.match(modal, /所有模型的训练组成绩/);
  assert.match(modal, /const pageSize = 6/);
  assert.match(modal, /visibleRows\.map/);
  assert.match(modal, /function VersionPagination/);
  assert.match(modal, /第 \{formatExactCount\(page \+ 1\)\} \/ \{formatExactCount\(pageCount\)\} 页/);
  assert.match(modal, /训练组分页（/);
  assert.match(modal, /aria-label="上一页训练组"/);
  assert.match(modal, /aria-label="下一页训练组"/);
  assert.doesNotMatch(modal, /pendingPageScrollRef|scroller\.scrollTo|scrollIntoView/);
  assert.match(modal, /className="version-page-results" aria-busy=\{pageLoading\}/);
  assert.match(modal, /busy=\{pageLoading\}/);
  assert.match(css, /version-page-results \{ min-height:420px; display:flex; flex-direction:column/);
  assert.match(modal, /position="bottom"/);
  assert.match(modal, /buildTrainingCutoffChart/);
  assert.match(modal, /crossesMissingCutoff/);
  assert.match(modal, /strokeDasharray=\{crossesMissingCutoff \? "7 6"/);
  assert.match(modal, /共同训练截止量对齐/);
  assert.match(modal, /查看模型明细/);
  assert.match(modal, /最近20个训练截止点/);
  assert.match(modal, /图中 \{formatExactCount\(graphRows\.length\)\} \/ \{formatExactCount\(matureRows\.length\)\} 个成熟结果/);
  assert.match(modal, /组等待结果/);
  assert.doesNotMatch(modal, /横轴按共同训练运行时间排列|pointerTime/);
  assert.match(modal, /每30分钟（固定 :00 \/ :30）/);
  assert.match(page, /六套模型，现在表现怎样/);
  assert.match(page, /等待新版生成/);
  assert.match(page, /training-card-total/);
  assert.match(page, /className="training-progress-tail"/);
  assert.match(css, /\.training-card-total strong \.training-progress-tail \{[^}]*font-size:\.42em/);
  assert.doesNotMatch(css, /\.training-progress-pair small \{/);
  assert.match(page, /还差/);
  assert.doesNotMatch(page, /含新闻的决策时点/);
  assert.doesNotMatch(page, /重复决策样本，不是文章数/);
  assert.doesNotMatch(page, /learning-data-flow/);
  assert.match(page, /方法与实盘边界/);
  assert.match(modal, /K线与决策/);
  assert.match(modal, /仓位与退出/);
  assert.doesNotMatch(modal, /冻结 Shadow 动作/);
  assert.match(modal, /每小时 :00 \/ :30/);
  assert.match(modal, /每5分钟/);
  assert.doesNotMatch(modal, /成本后 EV 较优方向/);
  assert.doesNotMatch(modal, /setArrowMode/);
  assert.match(modal, /U5 只是统一波动尺度，不是 WAIT 开关/);
  assert.match(modal, /<details className="wait-explainer"><summary>方向怎样产生<\/summary>/);
  assert.match(modal, /<details className="market-reading-guide"><summary>图表怎么看<\/summary>/);
  assert.match(modal, /模型选择 vs 固定 1\.0x/);
  assert.match(modal, /顺序 Exit Ridge vs 固定持有30分钟/);
  assert.match(modal, /两套独立实验/);
  assert.match(modal, /仓位倍率 OOS/);
  assert.match(modal, /提前退出 OOS/);
  assert.match(modal, /总计 <CountValue value=\{count\} suffix=" 笔" \/>/);
  assert.match(modal, /当前显示最新 \{formatExactCount\(visibleCount\)\} 笔/);
  assert.match(modal, /图中压缩为/);
  assert.match(modal, /resource=execution-point/);
  assert.match(modal, /第 \{formatExactCount\(page \+ 1\)\} 段 · 共 \{formatExactCount\(total\)\} 个历史绘图点/);
  assert.match(modal, /aria-label="查看较早时间段"/);
  assert.match(modal, /aria-label="查看较晚时间段"/);
  assert.match(modal, /className="market-action-filters"/);
  assert.match(modal, /LONG <span>看多<\/span>/);
  assert.match(modal, /market-version-toggle/);
  assert.match(css, /\.execution-chart-grid \{ display:grid; grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.execution-history-nav/);
  assert.match(modal, /目前没有提前退出/);
  assert.doesNotMatch(modal, /等待退出 OOS/);
  assert.match(modal, /WAIT 不创建仓位/);
  assert.match(modal, /点击图中的三角形/);
  assert.match(modal, /Ridge 预测未来30分钟连续收益/);
  assert.match(modal, /较高的一边只要大于0就记录为 Shadow 方向/);
  assert.match(modal, /return bestEv > 0 \? bestAction : "WAIT"/);
  assert.match(modal, /每根K线5分钟 · 每个箭头预测未来30分钟/);
  assert.match(modal, /全部历史/);
  assert.match(modal, /查看更早行情/);
  assert.match(modal, /查看较新行情/);
  assert.match(modal, /title="暂无行情数据"/);
  assert.match(modal, /模型当时尚未开始预测/);
  assert.match(modal, /这段时间没有预测/);
  assert.match(modal, /marketGaps/);
  assert.match(modal, /"数据缺口"/);
  assert.match(modal, /gap\.duration >= 45 \* 60_000/);
  assert.match(modal, /历史＋实时成熟 OOS（只追加，不重写）/);
  assert.match(modal, /24开市小时/);
  assert.match(modal, /7个开市日/);
  assert.match(modal, /30个开市日/);
  assert.match(modal, /全部总览/);
  assert.match(modal, /较早一段/);
  assert.match(modal, /较晚一段/);
  assert.match(modal, /回到最新/);
  assert.match(modal, /全部历史只画压缩轮廓/);
  assert.match(modal, /Page by elapsed market-open time/);
  assert.match(modal, /Plot result time, not wall-clock time/);
  assert.match(modal, /curve-gap-bridge/);
  assert.match(modal, /压缩历史轮廓/);
  assert.match(modal, /curve-gap-carry-in/);
  assert.match(modal, /窗口开始前的压缩历史轮廓/);
  assert.doesNotMatch(modal, /points\.unshift\(\{ decision_time: new Date\(start\)/);
  assert.doesNotMatch(modal, /points\.push\(\{ decision_time: new Date\(end\)/);
  assert.match(modal, /成本后EV较高方向/);
  assert.match(modal, /模型版本/);
  assert.match(modal, /历史规则不一致/);
  assert.match(modal, /getUTCMinutes\(\) % 30 === 0/);
  assert.match(modal, /const xAtIndex/);
  assert.match(modal, /条模型评分/);
  assert.match(modal, /所有模型的训练组成绩/);
  assert.match(modal, /按同一训练截止点比较/);
  assert.match(modal, /pendingRows = graphGroups\.length - matureRows\.length/);
  assert.match(modal, /最近20个训练截止点/);
  assert.match(modal, /aria-label=\{pointLabel\}/);
  assert.match(modal, /comparisonCutoff/);
  assert.doesNotMatch(modal, /activeCycle|hoveredCycle|pinnedCycle/);
  assert.match(modal, /versionBoundaries/);
  assert.match(modal, /pools\.direction !== null && pools\.direction !== state\.lastDirectionRows/);
  assert.match(modal, /pools\.news !== null && pools\.news !== state\.lastNewsRows/);
  assert.match(modal, /sort\(\(a, b\) => Date\.parse\(a\) - Date\.parse\(b\)\)/);
  assert.match(modal, /方向 \$\{boundary\.direction\}/);
  assert.match(modal, /新闻 \$\{boundary\.news\}/);
  assert.match(modal, /version-boundary-badge/);
  assert.match(modal, /const laneEnds: number\[\] = \[\]/);
  assert.match(modal, /boundaryLayouts/);
  assert.match(modal, /const compactBoundaryRail = range !== "24h"/);
  assert.match(modal, /clusterTimelineItems\(displayedBoundaries/);
  assert.match(modal, /boundaryDividerY = compactBoundaryRail \? 24/);
  assert.match(modal, /<circle className="version-event-dot" aria-hidden="true"/);
  assert.doesNotMatch(modal, /version-event-control|version-event-hit|selectedBoundary|hoveredBoundary/);
  assert.doesNotMatch(modal, /curve-event-readout|完整换版证据|点选图表上方圆点查看/);
  assert.doesNotMatch(css, /\.version-event-control|\.version-event-hit|\.curve-event-readout/);
  assert.doesNotMatch(modal, /<title>\{boundary\./);
  assert.match(css, /\.curve-navigation-actions button \{[^}]*width:44px;[^}]*min-height:44px/);
  assert.match(modal, /version-boundary-leader/);
  assert.match(modal, /boundaryDividerY/);
  assert.doesNotMatch(modal, /标签分别显示方向池与新闻池/);
  assert.match(modal, /version-label-divider/);
  assert.doesNotMatch(modal, /changes\[0\]\?\.training_rows/);
  assert.match(modal, /模型换版本/);
  assert.match(modal, /30分钟结果/);
  assert.match(modal, /无效样本 · 已隔离/);
  assert.doesNotMatch(modal, /三种动作同一30分钟结果/);
  assert.doesNotMatch(modal, /30分钟退出线/);
  assert.match(css, /\.version-pagination/);
  assert.match(css, /\.version-pagination button \{ width:46px; height:46px/);
  assert.match(css, /\.execution-history-nav button \{[^}]*min-height:44px;[^}]*font-size:18px/);
  assert.match(css, /font-size:clamp\(24px,7vw,28px\)/);
  assert.match(css, /height:calc\(100dvh - 16px\)/);
  assert.match(css, /grid-template-rows:auto auto minmax\(0,1fr\) auto/);
  assert.match(modal, /graph-modal-\$\{tab\}/);
  assert.match(modal, /useLayoutEffect\(\(\) => \{[\s\S]*const cancel = settleResponsiveScroll\(options => bodyRef\.current\?\.scrollTo\(options\), \(\) => bodyRef\.current\?\.scrollTop \?\? 0, pendingScrollTop\.current!\);[\s\S]*return cancel;[\s\S]*\}, \[tab\]\)/);
  assert.match(modal, /graph-scope-mobile/);
  assert.match(css, /graph-modal\.graph-modal-curve,\.graph-modal\.graph-modal-versions \{ height:calc\(100dvh - 16px\); max-height:none; grid-template-rows:auto auto minmax\(0,1fr\) auto/);
  assert.match(css, /graph-modal\.graph-modal-curve>\.graph-modal-body,\.graph-modal\.graph-modal-versions>\.graph-modal-body \{ min-height:0; max-height:none; overflow:auto/);
  assert.match(css, /scrollbar-gutter:stable/);
  assert.match(css, /long-curve-block>\.learning-svg \{ height:clamp\(390px,48dvh,520px\)/);
  assert.match(css, /\.curve-navigation/);
  assert.match(css, /version-ledger-controls \{ display:grid; grid-template-columns:minmax\(210px,1\.16fr\) minmax\(190px,1fr\) minmax\(180px,\.9fr\)/);
  assert.doesNotMatch(css, /\.modal-version-ledger>header \{[^}]*position:sticky/);
  assert.match(modal, /className="version-result-head"/);
  assert.match(modal, /className="version-training"/);
  assert.match(modal, /className="version-result-metrics"/);
  assert.match(modal, /data-label="上线后"/);
  assert.match(modal, /data-label="本组收益"/);
  assert.match(modal, /data-label="PF \/ 出方向"/);
  assert.match(css, /\.version-result-metrics>\[data-label\]::before \{ content:attr\(data-label\)/);
  assert.match(css, /@media \(max-width:1100px\)\{[\s\S]*?\.version-ledger>header \{ grid-template-columns:1fr/);
  assert.match(css, /long-curve-block>\.chart-legend \{ margin-top:16px; padding-bottom:10px/);
  assert.match(modal, /预测 \/ 方向/);
  assert.match(modal, /row\.decision_time \?\? row\.time/);
  assert.match(modal, /row\.scored_at \?\? row\.time/);
});

test("keeps the learning page focused and folds secondary research below the scoreboard", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const summary = page.indexOf('<div className="learning-summary-grid">');
  const graph = page.indexOf('<section className="graph-launch">');
  const scoreboard = page.indexOf('<section className="model-score-summary">');
  const execution = page.indexOf("<ExecutionResearch", scoreboard);
  const methods = page.indexOf('<details className="model-method-note">', execution);
  assert.ok(summary >= 0 && graph > summary);
  assert.ok(scoreboard > graph && execution > scoreboard && methods > execution);
  assert.doesNotMatch(page, /learning-audit-details|NEWS MODEL CONTRACT/);
  assert.doesNotMatch(page, /league-cost-note/);
  assert.match(page, /仓位与退出研究/);
});

test("keeps dashboard navigation and graph controls usable on phones", () => {
  const dashboard = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const shell = readFileSync(new URL("../app/_components/DashboardShell.tsx", import.meta.url), "utf8");
  const navigation = readFileSync(new URL("../app/_components/DashboardNavigation.tsx", import.meta.url), "utf8");
  const responsiveScroll = readFileSync(new URL("../app/_lib/responsive-scroll.ts", import.meta.url), "utf8");
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const mobileNav = readFileSync(new URL("../app/_components/MobileDashboardNav.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /className="audit-tabs"/);
  assert.match(page, /className="audit-view-picker"/);
  assert.match(page, /aria-label="切换证据台页面"/);
  assert.match(page, /pendingScrollTop\.current = window\.scrollY;[\s\S]*useLayoutEffect\(\(\) => \{[\s\S]*const cancel = settleResponsiveScroll\(options => window\.scrollTo\(options\), \(\) => window\.scrollY, pendingScrollTop\.current!\);[\s\S]*return cancel;[\s\S]*\}, \[view\]\)/);
  assert.match(dashboard, /pendingScrollTop\.current = currentScrollTop;[\s\S]*useLayoutEffect\(\(\) => \{[\s\S]*const cancel = settleResponsiveScroll\(options => window\.scrollTo\(options\), \(\) => window\.scrollY, pendingScrollTop\.current!\);[\s\S]*return cancel;[\s\S]*\}, \[location\]\)/);
  assert.match(responsiveScroll, /matchMedia\("\(max-width: 850px\)"\)\.matches/);
  assert.match(responsiveScroll, /if \(isPhoneViewport\(\)\) \{[\s\S]*scroll\(\{ top: 0, left: 0, behavior: "instant" \}\)/);
  assert.match(responsiveScroll, /let remainingFrames = 30/);
  assert.match(responsiveScroll, /stableFrames = Math\.abs\(readTop\(\) - desktopTop\) <= 1 \? stableFrames \+ 1 : 0/);
  assert.match(responsiveScroll, /stableFrames < 6 && remainingFrames > 0/);
  assert.match(responsiveScroll, /cancelAnimationFrame\(frame\)/);
  assert.doesNotMatch(page, /scrollAuditTabs|auditTabsRef|向左查看更多审计视图|向右查看更多审计视图/);
  assert.match(shell, /<MobileDashboardNav[\s\S]*activeDestination=\{activeDestination\}/);
  assert.match(mobileNav, /DASHBOARD_GLOBAL_DESTINATIONS/);
  assert.doesNotMatch(mobileNav, /const SECTIONS|学习曲线|AI 模型用量|系统健康/);
  for (const label of ["总览", "新闻与决策", "系统", "管理员登录"]) {
    assert.match(navigation, new RegExp(label));
  }
  assert.match(mobileNav, /aria-label="切换主要区域"/);
  assert.match(shell, /DASHBOARD_ADMIN_DESTINATIONS\.map/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.dashboard-section-nav a \{ min-width:0; flex:1 1 0; \}/);
  assert.match(css, /\.topbar \{ align-items:stretch; flex-direction:column/);
  assert.match(css, /\.dashboard-global-nav \{ display:none; \}/);
  assert.match(css, /\.dashboard-header \.mobile-dashboard-nav \{ grid-column:1; display:grid; grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.audit-tabs-shell \{ display:none; \}/);
  assert.match(css, /\.audit-view-picker \{ position:sticky; top:0;[\s\S]*?grid-template-columns:auto minmax\(0,1fr\)/);
  assert.match(css, /\.audit-main \.audit-intro>div:first-child \{ display:none; \}/);
  assert.match(css, /\.coverage-card \{ display:grid;[\s\S]*?min-height:0;/);
  assert.match(css, /\.evidence-summary \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\); gap:8px/);
  assert.match(css, /\.quota-capacity-grid \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\); \}/);
  assert.match(css, /@media \(max-width:430px\)\{[\s\S]*?\.throughput-summary \{ grid-template-columns:1fr; \}/);
  assert.match(css, /\.graph-modal>nav \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.graph-modal,\.graph-modal\.graph-modal-curve,\.graph-modal\.graph-modal-versions \{ width:100vw; height:100dvh/);
  assert.match(page, /return-value return-history/);
  assert.match(page, /return-value return-total/);
  assert.match(page, /return-value return-group/);
  assert.match(css, /\.compact-model-summary article \{ grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.return-flow \{ width:100%; grid-template-columns:minmax\(0,1fr\) 12px minmax\(0,1fr\) 10px minmax\(0,1fr\)/);
  assert.match(css, /\.story-grid>article \{ overflow:hidden/);
  assert.match(css, /\.unassigned-story-events>div \{ grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.return-value>span,\.return-value>strong \{ overflow:visible;[\s\S]*?font-size:clamp\(14px,4\.4vw,17px\)/);
  assert.match(css, /\.summary-cadence \{ display:grid; grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)/);
  assert.match(modal, /mobile-chart-scroll/);
  assert.match(modal, /左右滑动浏览/);
  assert.match(modal, /closeButtonRef\.current\?\.focus\(\)/);
  assert.match(modal, /openerRef\.current\?\.focus\(\)/);
  assert.match(modal, /event\.key !== "Tab"/);
  assert.match(modal, /select:not\(\[disabled\]\), summary, \[href\]/);
  assert.match(modal, /element\.getClientRects\(\)\.length > 0/);
  assert.match(css, /\.mobile-chart-scroll \{ width:100%; overflow-x:auto/);
  assert.match(css, /\.long-curve-block \.mobile-chart-scroll \{ overflow-x:auto; \}/);
  assert.match(css, /\.long-curve-block \.mobile-chart-scroll>\.learning-svg \{ width:720px; min-width:720px; min-height:300px; height:300px;/);
  assert.match(css, /\.execution-chart \.mobile-chart-scroll \{ overflow-x:hidden; \}/);
  assert.match(css, /\.execution-history-nav \{ display:grid; grid-template-columns:44px minmax\(0,1fr\) 44px;/);
  assert.match(css, /\.execution-history-nav button \{ width:44px; min-width:44px; min-height:44px;/);
  assert.match(css, /\.market-history-nav \{[^}]*margin:10px 0 0;[^}]*border:1px solid/);
  assert.match(css, /\.prediction-counts \{[^}]*border-top:0/);
  assert.match(css, /\.curve-navigation-actions \{ grid-column:1\/-1; display:flex; width:max-content/);
  assert.match(css, /\.curve-navigation-actions button \{[^}]*flex:0 0 44px;[^}]*width:44px/);
  assert.match(css, /\.version-page-results \{ min-height:0; gap:10px; padding:12px 14px;/);
  assert.match(css, /\.version-page-results>article \{ border:1px solid rgba\(17,17,15,\.36\); padding:0; background:var\(--paper\); \}/);
  assert.match(css, /\.market-chart-block \.mobile-chart-scroll \{ overflow-x:auto; \}/);
  assert.match(css, /\.market-chart-block \.mobile-chart-scroll>\.learning-svg \{ display:block; width:720px; min-width:720px; min-height:300px; height:300px;/);
  assert.match(css, /\.market-action-filters \{ grid-column:1\/-1; display:grid; grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(css, /\.market-action-filters button\.active\+button\.active \{ border-left-color:rgba\(239,235,223,\.58\); \}/);
  assert.match(modal, /const selectNearestDecision = \(event: ReactMouseEvent<SVGSVGElement>\)/);
  assert.match(modal, /onClick=\{selectNearestDecision\}/);
  assert.match(modal, /左右滑动浏览 · 点击箭头查看30分钟结果/);
  assert.match(modal, /className="market-selected-window-caption"/);
  assert.match(modal, /预测 \{timeLabel\(activeSelected\.decision_time\)\} → 30分钟后/);
  assert.doesNotMatch(modal, /selected-window[^\n]*<text/);
  assert.match(modal, /useLayoutEffect\(\(\) => \{[\s\S]*?chart\.scrollLeft = chart\.scrollWidth - chart\.clientWidth;[\s\S]*?historyState, latestCandleTime/);
  assert.match(modal, /Math\.max\(88, Math\.min\(220, 30 \+ label\.length \* 14\)\)/);
  assert.match(css, /\.market-selected-window-caption \{ display:flex;/);
  assert.match(modal, /左右滑动浏览长期曲线 · 文字与时间轴保持可读大小/);
  assert.match(css, /\.market-chart-block>\.chart-legend \{ display:flex; flex-wrap:wrap;/);
  assert.match(css, /\.execution-scorecards \{ grid-template-columns:minmax\(0,1fr\); gap:0; border-width:1px 0; background:transparent; \}/);
  assert.match(css, /\.execution-scorecards article\+article \{ border-top:1px solid rgba\(17,17,15,\.36\); \}/);
  assert.match(css, /\.execution-scorecards article>span \{ max-width:34ch; font-size:11px; line-height:1\.55; \}/);
  assert.match(css, /\.quota-row \{ grid-template-columns:minmax\(72px,\.8fr\) minmax\(88px,1fr\) auto;/);
  assert.match(css, /\.chart-block \{ overflow:visible/);
  assert.match(css, /\.graph-modal-backdrop \{ position:fixed; inset:0; z-index:1100/);
  assert.match(css, /\.audit-intro>div:first-child \.eyebrow \{ display:none/);
  assert.match(css, /\.audit-intro h1 \{ font-size:clamp\(32px,9vw,38px\)/);
  assert.match(css, /\.daily-brief-desk,\s*\.news-search-desk,\s*\.decision-audit,\s*\.shadow-league,\s*\.coverage-grid \{ border-top:1px solid rgba\(17,17,15,\.55\); \}/);
});

test("keeps expanded news readable by progressively revealing technical evidence on phones", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /showSupportingEvidence/);
  assert.match(page, /className="news-secondary-toggle"/);
  assert.match(page, /news-secondary-evidence \$\{showSupportingEvidence \? "is-open" : ""\}/);
  assert.match(page, /查看证据、分类与时间线/);
  assert.match(css, /\.news-secondary-toggle \{ display:none; \}/);
  assert.match(css, /\.news-secondary-evidence \{ display:none; padding-top:15px; \}/);
  assert.match(css, /\.news-secondary-evidence\.is-open \{ display:block; \}/);
  assert.match(css, /\.news-row \{ scroll-margin-top:46px;/);
});

test("presents Daily Brief as a compact Gemma synthesis with readable states", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /DEGRADED: "已完成"/);
  assert.match(page, /EMPTY: "无资料"/);
  assert.match(page, /UPDATING: isToday \? "今日" : "处理中"/);
  assert.doesNotMatch(page, /部分资料不可用/);
  assert.doesNotMatch(page, /系统降级整理/);
  assert.doesNotMatch(page, /GEMMA 4 · 综合摘要/);
  assert.match(page, /isCurrent \? "今日黄金脉络" : "当日黄金脉络"/);
  assert.match(page, /brief\.overview/);
  assert.match(page, /brief\.drivers/);
  assert.match(page, /brief\.watch_next/);
  assert.match(page, /关键驱动/);
  assert.match(page, /接下来关注/);
  assert.doesNotMatch(page, /本版以重点摘要格式保存/);
  assert.match(page, /daily_news_brief_summary\?\.brief_date/);
  assert.match(page, /DAILY_BRIEF_VISIBLE_DATES = 4/);
  assert.match(page, /phase === "DEGRADED"[\s\S]*\? "需注意"/);
  assert.match(page, /className=\{`audit-main audit-view-\$\{view\}`\}/);
  assert.match(page, /className="brief-history-picker"/);
  assert.match(page, /选择更早的每日简报/);
  assert.match(page, /items\.slice\(0, 2\)/);
  assert.match(page, /items\.slice\(2\)/);
  assert.match(page, /className="brief-evidence"/);
  assert.match(page, /className="brief-evidence-stories"/);
  assert.match(page, /qualityNote && <p className="brief-quality-note">/);
  assert.match(page, /再看 \{formatExactCount\(remainingEvidence\.length\)\} 个依据/);
  assert.match(page, /这版没有保存总摘要，可展开查看重点依据/);
  assert.match(page, /Gemma 汇总未生成，当前为系统整理版/);
  assert.match(page, /条资料未纳入：正文缺失或复核失败/);
  assert.match(css, /\.daily-brief-desk button small \{ color:inherit; font-size:12px;/);
  assert.match(css, /\.brief-overview \{ max-width:900px; margin:24px auto 20px;/);
  assert.match(css, /\.brief-overview-lead \{ max-width:46em; margin:0; font-family:var\(--font-sans\),sans-serif; font-size:19px;/);
  assert.match(css, /\.brief-overview-points \{ display:grid; grid-template-columns:minmax\(0,1\.35fr\) minmax\(240px,\.65fr\);/);
  assert.match(css, /\.daily-brief-desk \.brief-overview-lead \{ font-size:17px; line-height:1\.7; \}/);
  assert.match(css, /\.daily-brief-desk header nav \{ display:grid; grid-template-columns:repeat\(4,minmax\(0,1fr\)\); width:100%; \}/);
  assert.match(css, /\.audit-main\.audit-view-briefs \.audit-intro \{ grid-template-columns:minmax\(0,1fr\) minmax\(300px,\.45fr\);/);
  assert.match(css, /\.brief-history-picker select \{ min-height:44px;/);
  assert.match(css, /\.brief-history-picker \{[^}]*font-size:12px;/);
  assert.match(css, /\.brief-quality-note \{ grid-column:1\/-1;/);
  assert.match(css, /\.brief-evidence-head \{ display:flex; align-items:baseline;/);
  assert.match(css, /\.brief-evidence-stories>summary \{ display:grid; grid-template-columns:1fr auto auto; align-items:center; min-height:64px;/);
});

test("explains U5 as a risk scale rather than a probability", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /30分钟波动风险/);
  assert.match(source, /risk-scale/);
  assert.match(source, /它不是亏损概率，也不代表方向/);
  assert.match(source, /research_forecast/);
  assert.match(source, /30分钟预测/);
  assert.match(source, /forecast-state/);
  assert.match(source, /新闻覆盖：降级/);
  assert.match(source, /复核正在自动重试/);
  assert.doesNotMatch(source, /复核正在自动恢复/);
  assert.match(source, /当前预测仅使用决策时已完成的新闻证据/);
  assert.match(source, /当前无符合条件的新闻/);
  assert.match(source, /新闻系统运行正常/);
  assert.match(source, /新闻输入不可用/);
  assert.match(source, /Market-only 仍独立评估/);
  assert.doesNotMatch(source, /成本后 EV 较高方向/);
  assert.doesNotMatch(source, /固定观察30分钟 · 不下单/);
  assert.doesNotMatch(source, /30分钟结果窗口已完成/);
  assert.match(readFileSync(new URL("../app/globals.css", import.meta.url), "utf8"), /timeline-panel \{ grid-column:1; grid-row:1 \/ span 3; \}/);
});

test("keeps live quotes online between five-minute decisions", async () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 5_000).toISOString(),
    system: { online: true, quote_age_seconds: 2 },
    latest: { source_received_time: "2026-08-06T00:00:00+00:00" },
  }, now);
  assert.equal(payload.system.online, true);
  assert.equal(payload.system.quote_age_seconds, 7);
});

test("shows a broker-close decision pause without presenting a component fault", () => {
  const source = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  assert.match(source, /decision_output_message/);
  assert.match(source, /最新决策/);
});

test("marks the mirror offline when the quote heartbeat stops arriving", () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 90_000).toISOString(),
    system: { online: true, quote_age_seconds: 2 },
  }, now);
  assert.equal(payload.system.online, false);
  assert.equal(payload.system.quote_age_seconds, 92);
});

test("does not turn a locally offline collector back online", () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 1_000).toISOString(),
    system: { online: false, quote_age_seconds: 1 },
  });
  assert.equal(payload.system.online, false);
});

test("loads market history by bounded range instead of one growing snapshot", () => {
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/market-history/route.ts", import.meta.url), "utf8");
  assert.match(modal, /history_resource/);
  assert.match(modal, /historyQuery\.set\("before", before\)/);
  assert.match(modal, /setBefore\(candles\[0\]\.time\)/);
  assert.match(route, /OVERVIEW_POINTS = 480/);
  assert.match(route, /OVERVIEW_DECISIONS = 480/);
  assert.match(route, /source_decision_count/);
  assert.match(route, /decision_downsampled/);
  assert.match(route, /WHERE time_epoch>=\? AND time_epoch<\?/);
  assert.match(route, /ON CONFLICT\(decision_key\) DO UPDATE/);
  assert.match(route, /MAX_INGEST_BYTES = 400_000/);
  assert.match(route, /ORDER BY decision_epoch,decision_key/);
  assert.match(modal, /loadDashboardResource<MarketData>/);
  assert.match(modal, /return \(\) => \{ cancelled = true; \}/);
  assert.match(modal, /!candles\.length \? <div className="graph-visual-stage market-empty-stage">/);
  assert.match(modal, /onClick=\{goLater\}>→ 返回较新行情/);
  assert.match(route, /previousCandleEnd/);
  assert.match(modal, /Plot trading time, not wall-clock time/);
  assert.doesNotMatch(modal, /休市 \$\{Math\.max/);
});

test("explains training rows separately from independent news events", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const metrics = readFileSync(new URL("../app/_lib/news-metrics.ts", import.meta.url), "utf8");
  assert.match(source, /newsMetrics\.training\.current_contract_rows/);
  assert.match(source, /newsMetrics\.training\.distinct_events/);
  assert.match(source, /文章、独立事件、预测读取和训练记录是四种不同口径/);
  assert.match(metrics, /schema_version: "news-metrics-v1"/);
  assert.match(metrics, /One compatibility boundary; views never reinterpret news counts themselves/);
  assert.doesNotMatch(source, /news_evidence_summary\?\./);
});

test("live room reports articles and independent events instead of revision rows", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /NEWS ARTICLES/);
  assert.match(source, /newsMetrics\.articles\.received/);
  assert.match(source, /newsMetrics\.events\.independent/);
  assert.doesNotMatch(source, /NEWS REVISIONS/);
  assert.doesNotMatch(source, /counts\.news_revisions/);
});

test("shows residual and news-only research directions without implying execution", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, /修正量自己的30分钟方向研究/);
  assert.match(source, /正修正显示 LONG，负修正显示 SHORT/);
  assert.match(source, /只看新闻的30分钟方向研究/);
  assert.match(source, /model\.recommended_action/);
  assert.doesNotMatch(source, /REPLAYED_FROM_FROZEN_POST_COST_EV/);
  assert.doesNotMatch(source, /暂不参考方向/);
  assert.doesNotMatch(source, /仅显示修正值，不单独判断方向/);
});

test("loads bounded learning history only when interactive charts need it", () => {
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const compact = readFileSync(new URL("../build/preview-learning.ts", import.meta.url), "utf8");
  assert.match(compact, /learning_preview_summary: true/);
  assert.match(compact, /preview_status_summary: true/);
  assert.match(compact, /identity_curves: \[\]/);
  assert.match(audit, /refreshStatus\(!fullStatusReadyRef\.current\)/);
  assert.match(audit, /refreshLearning\(!fullLearningReadyRef\.current\)/);
  assert.match(audit, /if \(!fullLearningReadyRef\.current\) void refreshLearning\(true\)/);
  assert.match(audit, /historyResource=\{payload\?\.learning_history_resource\}/);
  assert.match(modal, /resource: "version-group"/);
  assert.match(modal, /resource=curve-overview/);
  assert.match(modal, /const resolvedCurves = historyResource \? historyCurves\[cadence\] \?\? \[\] : curves/);
  assert.doesNotMatch(modal, /const resolvedCurves = historyCurves\[cadence\] \?\? curves/);
  assert.match(modal, /next_cursor/);
  assert.match(modal, /const pageCursor = pageCursors\[page\]/);
  assert.doesNotMatch(modal, /loadedPageKeys/);
});

test("distinguishes market history loading, empty, and failed states", () => {
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const resource = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const history = readFileSync(new URL("../app/api/market-history/route.ts", import.meta.url), "utf8");
  const schema = readFileSync(new URL("../db/schema.ts", import.meta.url), "utf8");
  const migration = readFileSync(new URL("../drizzle/0006_materialized_history_overviews.sql", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(modal, /historyState === "loading"/);
  assert.match(modal, /historyState === "error"/);
  assert.match(modal, /正在读取行情/);
  assert.match(modal, /activeMarket = remoteHistory \? historyResult\?\.data \?\? market : market/);
  assert.match(modal, /className="market-visual-shell" aria-busy=\{historyState === "loading"\}/);
  assert.match(modal, /className="market-refresh-signal" role="status"/);
  assert.match(modal, /disabled=\{historyState === "loading" \|\| !canGoEarlier\}/);
  assert.match(modal, /disabled=\{historyState === "loading" \|\| !canGoLater\}/);
  assert.doesNotMatch(modal, /historyState === "loading" && candles\.length > 0 && <GraphLoading/);
  assert.match(css, /\.market-refresh-signal \{ position:absolute/);
  assert.match(modal, /正在读取长期曲线/);
  assert.match(modal, /正在读取这组成绩/);
  assert.match(modal, /graph-state-compact/);
  assert.doesNotMatch(modal, /if \(pageLoading \|\| overviewState === "loading"\)/);
  assert.doesNotMatch(modal, /if \(historyLoading\) return <GraphLoading/);
  assert.doesNotMatch(modal, /historyState === "loading"\) return <GraphLoading/);
  assert.match(modal, /graph-visual-stage market-empty-stage/);
  assert.doesNotMatch(modal, /compact-market-empty/);
  assert.match(css, /graph-visual-stage \{ min-height:clamp\(420px,58dvh,620px\)/);
  assert.match(modal, /title="暂无行情数据"/);
  assert.match(modal, /重新读取/);
  assert.doesNotMatch(modal, /还没有保存过可绘制的 Bid\/Ask 行情/);
  assert.doesNotMatch(modal, /等待可验证数据/);
  assert.doesNotMatch(resource, /MIN_VISIBLE_LOADING_MS|waitForMinimumLoading/);
  assert.match(modal, /loadDashboardResource/);
  assert.match(modal, /readDashboardResource/);
  assert.match(modal, /HISTORY_CACHE_MAX_AGE_MS = 60_000/);
  assert.match(modal, /Number\.POSITIVE_INFINITY/);
  assert.match(modal, /initialHistoryResult/);
  assert.doesNotMatch(modal, /waitForMinimumLoading|startedAt/);
  assert.match(modal, /point\.source_gap_before/);
  assert.match(modal, /first\.source_gap_before/);
  assert.match(modal, /overviewStep/);
  assert.match(modal, /Date\.parse\(point\.decision_time\) - Date\.parse\(previous\.decision_time\) >= overviewStep/);
  assert.doesNotMatch(modal, /source_gap_before \?\?/);
  assert.match(history, /market_history_overview/);
  assert.match(history, /market_decision_overviews/);
  assert.match(schema, /marketHistoryOverview/);
  assert.match(schema, /marketDecisionOverviews/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS `market_history_overview`/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS `market_decision_overviews`/);
  assert.doesNotMatch(history, /SELECT count\(\*\) count, min\(time_epoch\)/);
  assert.doesNotMatch(history, /row_number\(\) OVER/);
  assert.match(css, /graph-data-pulse/);
  assert.match(css, /prefers-reduced-motion:reduce/);
});

test("reflows news evidence into readable mobile cards", () => {
  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(view, /className="evidence-event-cell"/);
  assert.match(view, /className="evidence-status-cell"/);
  assert.match(view, /统一来源身份：/);
  assert.match(view, /原始发布域名：/);
  assert.match(view, /Gemini 与 Gemma 负责理解事件语义/);
  assert.match(view, /showAllEvidence/);
  assert.match(view, /showEvidenceMetrics/);
  assert.match(view, /className="evidence-metrics-toggle"/);
  assert.match(view, /mergeNewsEvidenceByEvent/);
  assert.match(view, /const evidenceArchiveReady = Boolean\([\s\S]*evidenceArchive\.snapshot_id && evidenceArchive\.mode === evidenceMode/);
  assert.match(view, /evidenceArchiveReady[\s\S]*mergeNewsEvidenceByEvent\(evidenceArchive\.items\)/);
  assert.doesNotMatch(view, /evidenceArchive\.items\.length > 0/);
  assert.match(view, /!evidenceArchiveReady && evidencePayloadHasDuplicates/);
  assert.match(view, /sortNewsEvidenceByTime\(merged\.values\(\)\)/);
  assert.match(view, /new Map<string, NewsEvidence>/);
  assert.match(view, /evidenceMode}:\$\{row\.event_key}/);
  assert.doesNotMatch(view, /evidenceMode}:\$\{row\.event_key}:\$\{index}/);
  assert.match(css, /@media \(max-width:640px\)[\s\S]*\.evidence-table thead \{ position:absolute/);
  assert.match(css, /grid-template-areas:"event event" "status time" "usage usage"/);
  assert.match(css, /\.evidence-event-cell \{ grid-area:event/);
  assert.match(css, /\.evidence-status-cell \{ grid-area:status/);
  assert.match(css, /\.evidence-usage-cell \{ grid-area:usage/);
  assert.match(css, /\.evidence-time-cell \{ grid-area:time/);
  assert.match(css, /\.evidence-status-copy \{ display:none!important/);
  assert.match(css, /\.evidence-model-list \{ display:none!important/);
  assert.match(css, /\.evidence-table-wrap:not\(\.show-all-mobile-items\) \.evidence-table tbody>tr:nth-child\(n\+9\)/);
  assert.match(css, /\.evidence-desk>\.mobile-reveal-button \{ display:block;[^}]*min-height:48px/);
  assert.match(css, /\.evidence-metrics-block \{ display:none/);
  assert.match(css, /grid-template-areas:"event" "status" "time" "usage"/);
});

test("sorts every news evidence filter by publication time before status", () => {
  const rows = sortNewsEvidenceByTime([
    {
      event_key: "old-used",
      source_published_time: "2026-08-14T16:55:17+00:00",
      collector_first_seen_time: "2026-08-14T17:27:49+00:00",
      model_seen: true,
    },
    {
      event_key: "new-unseen",
      source_published_time: "2026-08-17T00:45:00+00:00",
      collector_first_seen_time: "2026-08-17T00:45:04+00:00",
      model_seen: false,
    },
    {
      event_key: "receipt-fallback",
      source_published_time: null,
      collector_first_seen_time: "2026-08-16T20:16:47+00:00",
      model_seen: false,
    },
  ]);

  assert.deepEqual(rows.map(row => row.event_key), [
    "new-unseen", "receipt-fallback", "old-used",
  ]);
});

test("keeps shared news retrieval bounded and phone readable", () => {
  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/news-search/route.ts", import.meta.url), "utf8");
  const retrieval = readFileSync(new URL("../app/api/_shared/news-retrieval.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(view, /view === "search"/);
  assert.match(view, /placeholder="标题、来源或主题"/);
  assert.match(view, /id="news-search-from" type="date"/);
  assert.match(view, /id="news-search-to" type="date"/);
  assert.match(route, /parseNewsRetrievalRequest/);
  assert.match(route, /retrieveNews/);
  assert.doesNotMatch(route, /SELECT .* FROM news_index/);
  assert.match(retrieval, /MAX_QUERY_CHARACTERS = 80/);
  assert.match(retrieval, /MAX_PAGE_SIZE = 20/);
  assert.match(retrieval, /LIMIT \? OFFSET \?/);
  assert.match(retrieval, /IMMUTABLE_PREVIEW_SNAPSHOT/);
  assert.match(css, /\.search-pages button \{[^}]*min-width:44px;[^}]*min-height:44px/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.search-filter-grid \{ grid-template-columns:1fr; \}/);
});

test("keeps the legacy news Q&A queue protected and paused without a duplicate surface", () => {
  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/news-questions/route.ts", import.meta.url), "utf8");
  const workerRoute = readFileSync(
    new URL("../app/api/assistant-worker/news-questions/route.ts", import.meta.url), "utf8",
  );
  const queue = readFileSync(new URL("../app/api/_shared/news-questions.ts", import.meta.url), "utf8");
  const auth = readFileSync(new URL("../app/api/_shared/dashboard-operator-auth.ts", import.meta.url), "utf8");
  const sync = readFileSync(new URL("../../scripts/run_dashboard_sync.py", import.meta.url), "utf8");

  assert.doesNotMatch(view, /view === "qa"/);
  assert.doesNotMatch(view, /PRIVATE · EVIDENCE GROUNDED|私有问答|\/api\/news-questions/);
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  assert.match(app, /value === "qa"\) return "briefs"/);
  assert.match(app, /currentUrl\.searchParams\.get\("view"\) !== destination\.auditView[\s\S]*?replaceState\(null, "", canonicalHref\(destination\)\)/);
  assert.match(route, /rejectPreviewWrite\(\)/);
  const postRoute = route.slice(route.indexOf("export async function POST"));
  assert.ok(
    postRoute.indexOf("rejectPreviewWrite()") < postRoute.indexOf("authenticateDashboardOperatorRequest(request, env)"),
    "Preview writes must reject before human authentication",
  );
  assert.match(route, /authenticateDashboardOperatorRequest\(request, env\)/);
  assert.doesNotMatch(route, /isIngestAuthorized|claimNewsQuestion|completeNewsQuestion/);
  assert.match(workerRoute, /isIngestAuthorized\(request\)/);
  assert.match(workerRoute, /claimNewsQuestion/);
  assert.match(auth, /jwtVerify/);
  assert.match(auth, /algorithms: \["RS256"\]/);
  assert.match(queue, /status='PROCESSING'/);
  assert.match(queue, /lease_expires_at/);
  assert.match(queue, /attempt_count>=max_attempts/);
  assert.match(queue, /owner_id=\?/);
  const qaSync = sync.slice(
    sync.indexOf("def _sync_news_questions("),
    sync.indexOf("\ndef ", sync.indexOf("def _sync_news_questions(") + 1),
  );
  assert.match(qaSync, /return None/);
  assert.match(qaSync, /Daily Brief use separate workers/);
  assert.doesNotMatch(qaSync, /\/news-search\?/);
  assert.doesNotMatch(qaSync, /recent_news/);
});

test("keeps chat admission owner-authenticated and event replay finite", () => {
  const route = readFileSync(new URL("../app/api/assistant-chat/route.ts", import.meta.url), "utf8");
  const workerRoute = readFileSync(
    new URL("../app/api/assistant-worker/chat/route.ts", import.meta.url), "utf8",
  );
  const runtime = readFileSync(
    new URL("../app/api/_shared/assistant-chat.ts", import.meta.url), "utf8",
  );
  const migration = readFileSync(
    new URL("../drizzle/0011_assistant_chat_runtime.sql", import.meta.url), "utf8",
  );
  const leaseMigration = readFileSync(
    new URL("../drizzle/0012_assistant_turn_lease_bound.sql", import.meta.url), "utf8",
  );
  const postRoute = route.slice(route.indexOf("export async function POST"));
  assert.ok(
    postRoute.indexOf("rejectPreviewWrite()")
      < postRoute.indexOf("authenticateDashboardOperatorRequest(request, env)"),
    "Preview chat writes must reject before human authentication",
  );
  assert.match(route, /authenticateDashboardOperatorRequest\(request, env\)/);
  assert.doesNotMatch(route, /isIngestAuthorized|claimAssistantChatTurn|completeAssistantChatTurn/);
  assert.match(workerRoute, /isIngestAuthorized\(request\)/);
  assert.match(route, /last-event-id/);
  assert.match(route, /text\/event-stream/);
  assert.match(route, /X-Assistant-Next-Sequence/);
  assert.doesNotMatch(route, /ReadableStream|setInterval|setTimeout/);
  assert.match(runtime, /activePerOwner: 2/);
  assert.match(runtime, /activeGlobal: 10/);
  assert.match(runtime, /lease_expires_at>\?/);
  assert.match(runtime, /LEASE_RENEWED/);
  assert.match(workerRoute, /action === "RENEW"/);
  assert.match(runtime, /automaticAssistantTitleStatements/);
  assert.match(runtime, /scheduleAssistantCompaction/);
  assert.match(migration, /assistant_turn_events_immutable_update/);
  assert.match(migration, /assistant_turn_jobs_terminal_immutable/);
  assert.match(migration, /assistant event sequence must be contiguous/);
  assert.match(leaseMigration, /assistant lease cannot outlive turn/);
});

test("separates Access-owned human APIs from the ingest worker control plane", () => {
  const humanRoutes = [
    "../app/api/assistant-chat/route.ts",
    "../app/api/assistant-conversations/route.ts",
    "../app/api/news-questions/route.ts",
    "../app/api/operator-retry/route.ts",
  ].map(path => readFileSync(new URL(path, import.meta.url), "utf8"));
  const workerRoutes = [
    "../app/api/assistant-worker/chat/route.ts",
    "../app/api/assistant-worker/conversations/route.ts",
    "../app/api/assistant-worker/news-questions/route.ts",
  ].map(path => readFileSync(new URL(path, import.meta.url), "utf8"));
  const protectedAliases = [
    "admin-status", "assistant-health", "assistant-chat",
    "assistant-conversations", "news-questions", "operator-retry",
  ].map(name => readFileSync(
    new URL(`../app/admin/api/${name}/route.ts`, import.meta.url), "utf8",
  ));
  const sync = readFileSync(new URL("../../scripts/run_dashboard_sync.py", import.meta.url), "utf8");
  const chatWorker = readFileSync(
    new URL("../../xauusd_forecaster/assistant_chat_worker.py", import.meta.url), "utf8",
  );
  const security = readFileSync(
    new URL("../../docs/contracts/ASSISTANT_SECURITY.md", import.meta.url), "utf8",
  );

  for (const route of humanRoutes) {
    assert.match(route, /authenticateDashboardOperatorRequest\(request, env\)/);
    assert.doesNotMatch(route, /isIngestAuthorized|mode === "machine"/);
  }
  for (const route of protectedAliases) {
    assert.match(route, /export \{ (?:GET|GET, POST) \} from "\.\.\/\.\.\/\.\.\/api\//);
  }
  for (const route of workerRoutes) {
    assert.match(route, /isIngestAuthorized\(request\)/);
    assert.doesNotMatch(route, /authenticateDashboardOperatorRequest/);
    const getRoute = route.slice(route.indexOf("export async function GET"));
    assert.ok(
      getRoute.indexOf("rejectPreviewWrite()")
        < getRoute.indexOf("isIngestAuthorized(request)"),
      "Preview worker claims must reject before machine authentication",
    );
  }
  assert.match(sync, /PAUSED_NO_MODEL/);
  assert.doesNotMatch(sync, /\/assistant-worker\/chat/);
  assert.doesNotMatch(sync, /\/assistant-worker\/news-questions/);
  assert.match(
    workerRoutes[1],
    /params\.get\("index_version"\).*ASSISTANT_MEMORY_INDEX_VERSION/s,
  );
  assert.match(chatWorker, /\/assistant-worker\/chat/);
  assert.doesNotMatch(sync, /mode=machine|mode.*claim/);
  assert.match(security, /canonical browser API aliases/);
  assert.match(security, /\/api\/assistant-worker\/\*/);
});

test("renders a recoverable responsive Assistant workbench without unsafe HTML", () => {
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const view = readFileSync(new URL("../app/_views/AssistantView.tsx", import.meta.url), "utf8");
  const rail = readFileSync(
    new URL("../app/_components/AssistantConversationRail.tsx", import.meta.url), "utf8",
  );
  const transcript = readFileSync(
    new URL("../app/_components/AssistantTranscript.tsx", import.meta.url), "utf8",
  );
  const client = readFileSync(
    new URL("../app/_lib/assistant-chat-client.ts", import.meta.url), "utf8",
  );
  const fixture = readFileSync(
    new URL("../app/_lib/assistant-preview-fixture.ts", import.meta.url), "utf8",
  );
  const conversations = readFileSync(
    new URL("../app/api/_shared/assistant-conversations.ts", import.meta.url), "utf8",
  );
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(app, /room === "assistant"/);
  assert.match(app, /<AssistantView \/>/);
  assert.match(app, /url\.pathname === "\/admin\/assistant"/);
  assert.match(app, /return "\/admin\/assistant"/);
  assert.match(view, /fetchAssistantConversations/);
  assert.match(view, /replayAssistantEvents/);
  assert.match(view, /document\.visibilityState === "hidden"/);
  assert.match(view, /31 \* 60 \* 1_000/);
  assert.doesNotMatch(view, /EventSource|setInterval/);
  assert.match(client, /"Last-Event-ID": String\(after\)/);
  assert.match(client, /ACCESS_LOGIN_REQUIRED/);
  assert.match(view, /sequence\.terminal/);
  assert.match(conversations, /active_turn: PublicAssistantActiveTurn \| null/);
  assert.match(conversations, /ASSISTANT_ACTIVE_TURN_STATUSES_SQL/);
  assert.match(rail, /aria-label="Assistant 会话列表"/);
  assert.match(rail, /已归档/);
  assert.match(transcript, /加载更早消息/);
  assert.match(transcript, /取消本轮/);
  assert.match(transcript, /查看本轮处理记录/);
  assert.match(transcript, /assistant-transcript-banners/);
  assert.match(transcript, /href="\/admin">管理员登录/);
  assert.match(transcript, /AURUM \/ PROVISIONAL/);
  assert.match(transcript, /ASSISTANT PAUSED/);
  assert.match(transcript, /等待新的 API 模型/);
  assert.doesNotMatch(transcript, /本机模型正在处理其他问题/);
  assert.match(transcript, /disabled=\{Boolean\(activeTurn\) \|\| preview \|\| paused\}/);
  assert.doesNotMatch(transcript, /ASSISTANT_CONTEXT_LIMIT_TOKENS/);
  assert.match(transcript, /ASSISTANT_MAX_MESSAGE_BYTES \* 0\.75/);
  assert.doesNotMatch(transcript, /16,000 bytes/);
  assert.doesNotMatch(transcript, /dangerouslySetInnerHTML/);
  assert.match(fixture, /管理与发送仅生产可用 · 不调用模型/);
  assert.match(css, /\.assistant-workbench \{[^}]*grid-template-columns:300px minmax\(0,1fr\)/);
  assert.match(css, /\.assistant-workbench \{[^}]*gap:0/);
  assert.match(css, /\.assistant-transcript \{[^}]*border-left:1px solid var\(--ink\)/);
  assert.match(css, /body:has\(\.assistant-main\) \{[^}]*display:flex; flex-direction:column; overflow:hidden/);
  assert.match(css, /body:has\(\.assistant-main\) > \.dashboard-shell \{[^}]*height:auto; min-height:0; flex:1 1 auto/);
  assert.match(css, /\.assistant-conversation-rail\.is-open \{ transform:translateX\(0\); \}/);
  assert.match(css, /\.assistant-composer-meta button \{[^}]*min-height:46px/);
  assert.match(css, /\.assistant-chat-error button,\.assistant-chat-error a \{[^}]*min-height:44px/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-open-rail \{ display:grid/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-transcript \{ border-left:0/);
  assert.match(css, /\.assistant-message>p \{[^}]*overflow-wrap:anywhere; white-space:pre-wrap/);
});

test("renders only validated Assistant content blocks with phone-owned overflow", () => {
  const renderer = readFileSync(
    new URL("../app/_components/AssistantContentBlocks.tsx", import.meta.url), "utf8",
  );
  const transcript = readFileSync(
    new URL("../app/_components/AssistantTranscript.tsx", import.meta.url), "utf8",
  );
  const protocol = readFileSync(
    new URL("../app/api/_shared/assistant-content.ts", import.meta.url), "utf8",
  );
  const migration = readFileSync(
    new URL("../drizzle/0014_assistant_structured_content.sql", import.meta.url), "utf8",
  );
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(protocol, /assistant\.content\.v1/);
  for (const blockType of ["markdown", "news_card", "table", "metric", "callout"]) {
    assert.match(protocol, new RegExp(`"${blockType}"`));
    assert.match(renderer, new RegExp(`block\\.type === "${blockType}"`));
  }
  assert.match(transcript, /<AssistantContentBlocks document=\{message\.content_document\}/);
  assert.doesNotMatch(renderer, /dangerouslySetInnerHTML|innerHTML|srcDoc/);
  assert.match(renderer, /rel="noopener noreferrer"/);
  assert.match(renderer, /scope="col"/);
  assert.match(renderer, /showModal\(\)/);
  assert.match(renderer, /\/api\/news-content\?key=/);
  assert.match(renderer, /GEMINI 中文摘要/);
  assert.match(renderer, /GEMMA 市场影响判断/);
  assert.doesNotMatch(renderer, /<footer>[\s\S]*完成<\/button>[\s\S]*<\/footer>/);
  assert.doesNotMatch(css, /\.assistant-news-dialog>article>footer button/);
  assert.match(renderer, /block\.data\.tone === "BOUNDARY"\) return null/);
  assert.doesNotMatch(transcript, /assistant-user-prompt-mobile|openPromptId/);
  assert.doesNotMatch(transcript, /回答属于决策支持/);
  assert.match(transcript, /conversation && !preview/);
  assert.match(transcript, /aria-haspopup="menu"/);
  assert.match(transcript, /assistant-manage-toggle/);
  assert.match(migration, /content_document_json/);
  assert.match(migration, /assistant_messages_structured_content_contract/);
  assert.match(css, /\.assistant-content-table>div \{[^}]*max-width:100%; overflow-x:auto/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-content-blocks \{ grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.preview-banner\{[^}]*z-index:1000/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-workbench \{[^}]*z-index:auto/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-conversation-rail \{[^}]*z-index:1020/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-rail-scrim \{[^}]*z-index:1010/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-news-dialog \{[^}]*inset:50% 12px auto; width:auto; max-width:none; height:auto; max-height:calc\(100dvh - 24px\); margin:0 auto;[^}]*transform:translateY\(-50%\)/);
  assert.match(css, /\.assistant-news-dialog \{[^}]*position:fixed; inset:0;[^}]*width:min\(720px,calc\(100vw - 48px\)\);[^}]*margin:auto/);
  assert.match(css, /\.dashboard-header \.mobile-dashboard-nav>label \{ grid-template-columns:auto minmax\(0,1fr\)/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-composer-shell form \{[^}]*grid-template-columns:minmax\(0,1fr\) 52px/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-composer-shell \{[^}]*min-height:69px/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-composer-shell textarea \{[^}]*height:52px;[^}]*max-height:52px/);
  assert.match(css, /\.dashboard-shell\.is-admin>\.assistant-main \{[^}]*height:auto; min-height:0/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-thread-heading h1 \{[^}]*font-size:clamp\(18px,4\.8vw,20px\)/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-message\.is-user>p \{[^}]*font-size:15px; line-height:1\.68/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-news-card-trigger>strong \{[^}]*font-size:19px; line-height:1\.22/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-action-menu \{[^}]*width:min\(136px,calc\(100vw - 24px\)\);[^}]*grid-template-columns:minmax\(0,1fr\); justify-content:stretch; gap:0/);
  assert.match(css, /\.assistant-news-dialog-body \{[^}]*align-content:start/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-news-dialog-body dl \{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /@media \(max-width:850px\)[\s\S]*\.assistant-news-dialog>article>footer \{[^}]*flex-direction:row/);
  assert.match(css, /\.assistant-news-card-trigger>span:first-child \{[^}]*font-size:11px/);
  assert.match(css, /\.assistant-transcript-head \{ grid-row:1/);
  assert.match(css, /\.assistant-transcript-banners \{ grid-row:2/);
  assert.match(css, /\.assistant-message-scroll \{ grid-row:3/);
  assert.match(css, /\.assistant-composer-shell \{ grid-row:4/);
});

test("release validation authorizes before exposing a non-mutating context", async () => {
  const {
    authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
  } = await import(
    "../app/api/_shared/release-validation.ts"
  );
  const ordinary = new Request("https://example.test/api/audit", { method: "POST" });
  assert.equal(await authorizeReleaseValidation(
    ordinary, "audit-write", async () => true,
  ), null);

  const request = new Request("https://example.test/api/audit", {
    method: "POST",
    headers: {
      "X-Aurum-Release-Validation": "dry-run",
      "X-Aurum-Validation-Run": "validation-run-1",
      "X-Aurum-Request-ID": "request-1",
    },
  });
  let authorized = false;
  const context = await authorizeReleaseValidation(request, "audit-write", async () => {
    authorized = true;
    return true;
  });
  assert.equal(authorized, true);
  assert.equal(isReleaseValidationContext(context), true);
  const response = releaseValidationResponse(context, { json: "d1-json1" });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "DRY_RUN_OK",
    route_family: "audit-write",
    validation_run: "validation-run-1",
    request_id: "request-1",
    mutated: false,
    work: { json: "d1-json1" },
  });

  const unauthorized = await authorizeReleaseValidation(
    request, "audit-write", async () => false,
  );
  assert.equal(unauthorized.status, 401);
  assert.deepEqual(await unauthorized.json(), { error: "unauthorized" });
});

test("production-shaped release validation reaches work before every mutation", () => {
  for (const [path, family, completion] of [
    ["../app/api/ingest/route.ts", "status-ingest", "releaseValidationResponse(validation"],
    ["../app/api/audit/route.ts", "audit-write", "releaseValidationResponse(validation"],
    ["../app/api/learning/route.ts", "learning-write", "releaseValidationResponse(validation"],
    ["../app/api/market-chart/route.ts", "market-chart-write", "releaseValidationResponse(validation"],
    ["../app/api/market-history/route.ts", "market-history-write", "releaseValidationResponse(validation"],
    ["../app/api/learning-history/route.ts", "learning-history-write", "releaseValidationResponse(validation"],
    ["../app/api/news-evidence/route.ts", "news-evidence-write", "releaseValidationResponse(validation"],
    ["../app/api/news-index/route.ts", "news-index-write", "finishReleaseValidation(binding, validation"],
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    const auth = source.indexOf(`request, "${family}", isIngestAuthorized`);
    const bodyRead = Math.max(
      source.indexOf("readBoundedBody(request", auth),
      source.indexOf("writeDashboardSnapshot(request", auth),
    );
    const response = source.indexOf(completion, bodyRead);
    const mutation = Math.max(
      source.indexOf(".run()", response), source.indexOf("binding.batch(", response),
      source.indexOf("writeDashboardSnapshot(request", response + 1),
    );
    assert.ok(auth >= 0 && bodyRead > auth && response > bodyRead, path);
    if (mutation >= 0) assert.ok(mutation > response, path);
  }
});

test("snapshot dry-run reads bounds and uses read-only D1 JSON validation", async () => {
  const { writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  const calls = [];
  const binding = {
    prepare(sql) {
      calls.push(sql);
      return { bind() { return { first: async () => ({ valid: 1 }) }; } };
    },
  };
  const body = JSON.stringify({ generated_at: "2026-08-20T00:00:00Z" });
  const result = await writeDashboardSnapshot(new Request("https://example.test/api/audit", {
    method: "POST", body,
  }), binding, 4, { dryRun: true });
  assert.equal(result, "validated");
  assert.deepEqual(calls, ["SELECT json_valid(?) AS valid"]);
});

test("split audit routes share authenticated bounded zero-mutation validation", () => {
  const helper = readFileSync(new URL(
    "../app/api/_shared/audit-detail-snapshot.ts", import.meta.url,
  ), "utf8");
  assert.match(helper, /authorizeReleaseValidation\([\s\S]*validationFamily, isIngestAuthorized/);
  assert.match(helper, /dryRun: isReleaseValidationContext\(validation\)/);
  assert.match(helper, /maxBytes: AUDIT_DETAIL_SNAPSHOT_BYTES/);
  assert.match(helper, /result === "too_large"[\s\S]*status: 413/);
  assert.match(helper, /result === "invalid"[\s\S]*status: 400/);
  assert.match(helper, /result === "validated"[\s\S]*releaseValidationResponse/);
  assert.match(helper, /mutation_boundary: `audit-snapshot-\$\{snapshotId\}-upsert`/);
  for (const [resource, id, family] of [
    ["briefs", "briefs", "audit-briefs-write"],
    ["stories", "stories", "audit-stories-write"],
    ["decisions", "decisions", "audit-decisions-write"],
  ]) {
    const route = readFileSync(new URL(
      `../app/api/audit-${resource}/route.ts`, import.meta.url,
    ), "utf8");
    assert.match(route, new RegExp(`AUDIT_SNAPSHOT_IDS\\.${id}`));
    assert.match(route, new RegExp(`"${family}"`));
  }
});

test("Worker validation manifest owns every production route and direct router", () => {
  const manifest = JSON.parse(readFileSync(
    new URL("../worker-validation-manifest.json", import.meta.url), "utf8",
  ));
  const declared = new Set(manifest.routes.map(route => `${route.method} ${route.path}`));
  const discover = (directory, prefix = "") => {
    const found = [];
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        found.push(...discover(new URL(`${entry.name}/`, directory), `${prefix}/${entry.name}`));
      } else if (entry.name === "route.ts") {
        const source = readFileSync(new URL(entry.name, directory), "utf8");
        const methods = new Set();
        for (const match of source.matchAll(/export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b/g)) {
          methods.add(match[1]);
        }
        for (const match of source.matchAll(/export\s+const\s+(GET|POST|PUT|PATCH|DELETE)\s*=/g)) {
          methods.add(match[1]);
        }
        for (const match of source.matchAll(/export\s*\{([^}]+)\}\s*from/g)) {
          for (const entry of match[1].split(",").map(value => value.trim())) {
            const exported = entry.split(/\s+as\s+/).at(-1);
            if (/^(GET|POST|PUT|PATCH|DELETE)$/.test(exported)) methods.add(exported);
          }
        }
        for (const method of methods) {
          found.push(`${method} ${prefix}`);
        }
      }
    }
    return found;
  };
  const discovered = discover(new URL("../app/", import.meta.url));
  for (const route of discovered) {
    assert.ok(declared.has(route), `WORKER_ROUTE_VALIDATION_POLICY_MISSING: ${route}`);
  }
  const workerDirectory = new URL("../worker/", import.meta.url);
  for (const entry of readdirSync(workerDirectory, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".ts")) continue;
    const workerSource = readFileSync(new URL(entry.name, workerDirectory), "utf8");
    const directPaths = new Set();
    for (const match of workerSource.matchAll(/url\.pathname\s*===\s*"([^"]+)"/g)) {
      directPaths.add(match[1]);
    }
    for (const path of directPaths) {
      assert.ok(manifest.routes.some(route => route.path === path
          && route.boundary === "DIRECT_WORKER_ROUTE" && route.method === "ANY"),
        `WORKER_ROUTE_VALIDATION_POLICY_MISSING: DIRECT ${path}`);
    }
  }
  for (const route of manifest.routes) {
    assert.ok(route.family && route.boundary && route.criticality && route.strategy);
    assert.ok(Array.isArray(route.owners) && route.owners.length > 0);
    assert.ok(route.owners.includes("web/worker/*.ts"));
    if (!route.cpu_required) {
      assert.equal(route.criticality, "OPTIONAL");
      assert.ok(route.cpu_exempt_reason || manifest.optional_cpu_exempt_reason);
    }
    if (["CRITICAL", "HEAVY"].includes(route.criticality)
        && route.boundary !== "STATIC_ASSET") {
      assert.equal(route.cpu_required, true);
    }
    if (route.strategy === "PRODUCTION_SHAPED_DRY_RUN") {
      assert.ok(route.fixture && route.auth_required && route.cpu_required);
      assert.ok(route.acceptance_samples >= 10);
    }
    if (["news-content-write", "news-evidence-write", "news-index-write"].includes(route.family)) {
      assert.ok(Array.isArray(route.scenarios) && route.scenarios.length >= 2);
    }
  }
  assert.deepEqual(
    manifest.routes.find(route => route.family === "news-index-write").scenarios
      .map(scenario => scenario.name),
    ["normal", "reset", "withdrawal", "prune", "reconcile", "neutralize"],
  );
  assert.deepEqual(
    manifest.routes.find(route => route.family === "news-evidence-write").scenarios
      .map(scenario => scenario.name),
    ["prepare", "stage", "activate", "cleanup"],
  );
  assert.deepEqual(
    manifest.routes.find(route => route.family === "news-content-write").scenarios
      .map(scenario => scenario.name),
    ["normal", "reset"],
  );
});

test("non-production builds target an isolated Preview Worker", () => {
  const config = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
  const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(config.name, "aurum-signal-room");
  assert.equal(packageJson.scripts["cf:preview-upload"],
    "wrangler versions upload --name aurum-signal-room-preview");
  assert.ok(!packageJson.scripts["cf:preview-upload"].includes("--env"));
});

test("route inventory parser covers const and re-exported handlers", () => {
  const source = "export const GET = handler; export { put as POST, DELETE } from './shared';";
  const methods = new Set();
  for (const match of source.matchAll(/export\s+const\s+(GET|POST|PUT|PATCH|DELETE)\s*=/g)) {
    methods.add(match[1]);
  }
  for (const match of source.matchAll(/export\s*\{([^}]+)\}\s*from/g)) {
    for (const entry of match[1].split(",").map(value => value.trim())) {
      const exported = entry.split(/\s+as\s+/).at(-1);
      if (/^(GET|POST|PUT|PATCH|DELETE)$/.test(exported)) methods.add(exported);
    }
  }
  assert.deepEqual([...methods].sort(), ["DELETE", "GET", "POST"]);
});
