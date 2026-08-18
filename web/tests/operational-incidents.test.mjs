import assert from "node:assert/strict";
import test from "node:test";

import { affectedOperationalScopeCount, correlateOperationalEvents, globalOperationalIncidents } from "../app/_lib/operational-incidents.ts";

const event = (code, scope, evidence = {}, overrides = {}) => ({
  code, scope, evidence,
  severity: "WARNING",
  message_zh: code,
  blocking: false,
  ...overrides,
});

function capacityChain(componentReasons = ["ACTIONABLE_NEWS_IMPACT_PENDING"]) {
  return [
    event("OPS_AI_ROUTE_CAPACITY_SATURATED", "ACTIVE_IMPACT", { capacity_deferred_15m: 50, completed_15m: 1 }),
    event("OPS_AI_BACKLOG_OVERDUE", "ACTIVE_IMPACT", { active_jobs: 98, oldest_age_seconds: 17_040 }),
    event("OPS_AI_PIPELINE_STALLED", "ACTIVE_IMPACT", { active_jobs: 98 }, { severity: "ERROR", blocking: true }),
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_IMPACT", { latest_failure_code: "MODEL_CAPACITY_DEFERRED", claimable: true }, { severity: "ERROR", blocking: true }),
    event("OPS_DAILY_BRIEF_DEFERRED", "daily_news_brief", { failure_code: "MODEL_CAPACITY_DEFERRED" }),
    event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", { reason_codes: componentReasons }, { severity: "ERROR", blocking: true }),
  ];
}

test("correlates the Gemma local-capacity chain without losing technical events", () => {
  const incidents = correlateOperationalEvents(capacityChain());
  assert.equal(incidents.length, 1);
  assert.equal(incidents[0].root_event.scope, "ACTIVE_IMPACT");
  assert.equal(incidents[0].technical_event_count, 6);
  assert.equal(incidents[0].blocking, true);
  assert.deepEqual(
    new Set([
      incidents[0].root_event,
      ...incidents[0].related_events,
      ...incidents[0].technical_events,
    ].map(item => item.code)),
    new Set(capacityChain().map(item => item.code)),
  );
  assert.ok(incidents[0].affected_scopes.includes("daily_news_brief"));
  assert.ok(incidents[0].affected_scopes.includes("news_semantic_pipeline"));
  assert.equal(affectedOperationalScopeCount(incidents), 3);
  const fiveEvents = correlateOperationalEvents(capacityChain().slice(0, 5));
  assert.equal(fiveEvents.length, 1);
  assert.equal(fiveEvents[0].technical_event_count, 5);
  assert.equal(globalOperationalIncidents(fiveEvents).length, 1);
  assert.equal(
    correlateOperationalEvents(capacityChain().toReversed())[0].incident_key,
    incidents[0].incident_key,
  );
});

test("keeps annotation output failure separate and links mixed semantic state to both", () => {
  const incidents = correlateOperationalEvents([
    ...capacityChain(["ACTIONABLE_NEWS_IMPACT_PENDING", "ACTIONABLE_NEWS_SEMANTICS_PENDING"]),
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", {
      latest_failure_code: "MODEL_OUTPUT_CONTRACT_FAILED", claimable: true,
    }, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(incidents.length, 2);
  const impact = incidents.find(item => item.root_event.scope === "ACTIVE_IMPACT");
  const annotation = incidents.find(item => item.root_event.scope === "ACTIVE_ANNOTATION");
  assert.ok(impact.affected_scopes.includes("news_semantic_pipeline"));
  assert.ok(annotation.affected_scopes.includes("news_semantic_pipeline"));
  assert.equal(annotation.category, "MODEL_OUTPUT");
  const rawComponents = incidents.flatMap(item => [
    item.root_event, ...item.related_events, ...item.technical_events,
  ]).filter(item => item.code === "OPS_COMPONENT_UNHEALTHY");
  assert.equal(rawComponents.length, 1);
});

test("correlates mixed component reasons without duplicating or escalating the raw event", () => {
  const component = event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
    reason_codes: [
      "ACTIONABLE_NEWS_SEMANTICS_PENDING",
      "ANNOTATOR_HEARTBEAT_STALE",
    ],
    actionable_failure_counts: {
      ACTIVE_ANNOTATION: {
        MODEL_REQUEST_FAILED: 4,
        MODEL_OUTPUT_CONTRACT_FAILED: 1,
      },
    },
  }, { severity: "ERROR", blocking: true });
  const incidents = correlateOperationalEvents([
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", {
      latest_failure_code: "MODEL_REQUEST_FAILED", claimable: false,
      next_retry_at: "2026-08-18T05:00:00Z",
    }),
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", {
      latest_failure_code: "MODEL_OUTPUT_CONTRACT_FAILED", claimable: true,
    }),
    component,
  ]);
  assert.equal(incidents.length, 3);
  const transport = incidents.find(item => item.category === "PROVIDER");
  const output = incidents.find(item => item.category === "MODEL_OUTPUT");
  const heartbeat = incidents.find(item => item.root_event.code === "OPS_COMPONENT_UNHEALTHY");
  assert.equal(transport.action_state, "AUTO_RECOVERING");
  assert.ok(transport.affected_scopes.includes("news_semantic_pipeline"));
  assert.deepEqual(transport.reason_projections.map(item => item.reason_code), [
    "ACTIONABLE_NEWS_SEMANTICS_PENDING",
  ]);
  assert.deepEqual(output.reason_projections, []);
  assert.deepEqual(heartbeat.reason_projections.map(item => item.reason_code), [
    "ANNOTATOR_HEARTBEAT_STALE",
  ]);
  const rawComponents = incidents.flatMap(item => [
    item.root_event, ...item.related_events, ...item.technical_events,
  ]).filter(item => item.code === "OPS_COMPONENT_UNHEALTHY");
  assert.equal(rawComponents.length, 1);
  assert.deepEqual(rawComponents[0].evidence, component.evidence);
});

test("does not collapse provider pacing into local capacity", () => {
  const incidents = correlateOperationalEvents([
    event("OPS_AI_ROUTE_CAPACITY_SATURATED", "ACTIVE_IMPACT"),
    event("OPS_DAILY_BRIEF_DEFERRED", "daily_news_brief", { failure_code: "PROVIDER_DISPATCH_DEFERRED" }),
  ]);
  assert.equal(incidents.length, 2);
  assert.deepEqual(new Set(incidents.map(item => item.category)), new Set(["CAPACITY", "PROVIDER"]));
});

test("classifies future scheduled retry as auto-recovering", () => {
  const [incident] = correlateOperationalEvents([
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", {
      claimable: false, next_retry_at: "2026-08-18T05:00:00Z",
      latest_failure_code: "MODEL_REQUEST_FAILED",
    }),
  ]);
  assert.equal(incident.state, "RECOVERING");
  assert.equal(incident.action_state, "AUTO_RECOVERING");
});

test("classifies standalone Impact and Annotation recovering components as automatic retry", () => {
  for (const [family, expectedTitle, expectedSummary] of [
    ["IMPACT", "新闻影响复核等待中", "有新闻影响复核正在等待计划重试。系统会自动再次尝试处理，目前无需手动操作。"],
    ["SEMANTICS", "新闻语义复核等待中", "有新闻语义复核正在等待计划重试。系统会自动再次尝试处理，目前无需手动操作。"],
  ]) {
    const incidents = correlateOperationalEvents([
      event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
        status: "WARN",
        reason_codes: [
          `ACTIONABLE_NEWS_${family}_PENDING`,
          `ACTIONABLE_NEWS_${family}_RECOVERING`,
        ],
      }),
    ]);
    assert.equal(incidents.length, 1, family);
    assert.equal(incidents[0].state, "RECOVERING", family);
    assert.equal(incidents[0].action_state, "AUTO_RECOVERING", family);
    assert.equal(incidents[0].title_zh, expectedTitle, family);
    assert.equal(incidents[0].summary_zh, expectedSummary, family);
    assert.equal(incidents[0].technical_event_count, 1, family);
  }
});

test("terminal and overdue semantic reasons outrank recovering evidence", () => {
  for (const suffix of ["TERMINAL", "OVERDUE"]) {
    const [incident] = correlateOperationalEvents([
      event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
        reason_codes: [
          "ACTIONABLE_NEWS_IMPACT_RECOVERING",
          `ACTIONABLE_NEWS_IMPACT_${suffix}`,
        ],
      }),
    ]);
    assert.equal(incident.severity, "ERROR", suffix);
    assert.equal(incident.state, "ACTIVE", suffix);
    assert.equal(incident.action_state, "ACTION_REQUIRED", suffix);
  }
});

test("authoritative blocking error outranks recovering evidence", () => {
  const [incident] = correlateOperationalEvents([
    event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
      reason_codes: [
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_RECOVERING",
      ],
    }, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(incident.severity, "ERROR");
  assert.equal(incident.blocking, true);
  assert.equal(incident.action_state, "ACTION_REQUIRED");
});

test("keeps ordinary warnings without current retry evidence in monitoring", () => {
  const [incident] = correlateOperationalEvents([
    event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
      reason_codes: ["ANNOTATOR_HEARTBEAT_STALE"],
    }),
  ]);
  assert.equal(incident.state, "ACTIVE");
  assert.equal(incident.action_state, "MONITORING");

  const [autoPolicyOnly] = correlateOperationalEvents([
    event("OPS_DAILY_BRIEF_DEFERRED", "daily_news_brief"),
  ]);
  assert.equal(autoPolicyOnly.action_state, "MONITORING");

  const [unregisteredSuffix] = correlateOperationalEvents([
    event("OPS_COMPONENT_UNHEALTHY", "other_component", {
      reason_codes: ["UNREGISTERED_RECOVERING"],
    }),
  ]);
  assert.equal(unregisteredSuffix.action_state, "MONITORING");
});

test("finalizes a scheduled retry from terminal or overdue blocking component state", () => {
  for (const reason of [
    "ACTIONABLE_NEWS_SEMANTICS_TERMINAL",
    "ACTIONABLE_NEWS_SEMANTICS_OVERDUE",
  ]) {
    const incidents = correlateOperationalEvents([
      event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", {
        claimable: false, next_retry_at: "2026-08-18T05:00:00Z",
        latest_failure_code: "MODEL_REQUEST_FAILED", active_jobs: 1,
      }),
      event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
        reason_codes: [reason], active_jobs: 4, failed_15m: 2,
      }, { severity: "ERROR", blocking: true }),
    ]);
    assert.equal(incidents.length, 1, reason);
    assert.equal(incidents[0].severity, "ERROR", reason);
    assert.equal(incidents[0].blocking, true, reason);
    assert.equal(incidents[0].state, "ACTIVE", reason);
    assert.equal(incidents[0].action_state, "ACTION_REQUIRED", reason);
    assert.equal(incidents[0].technical_event_count, 2, reason);
    assert.deepEqual(incidents[0].summary_metrics, [
      { label: "待处理", value: "4" },
      { label: "15 分钟失败", value: "2" },
    ], reason);
    assert.deepEqual(globalOperationalIncidents(incidents), incidents, reason);
  }
});

test("correlates pending and recovering projections into one incident", () => {
  const incidents = correlateOperationalEvents([
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_IMPACT", {
      claimable: false, next_retry_at: "2026-08-18T05:00:00Z",
      latest_failure_code: "MODEL_REQUEST_FAILED",
    }),
    event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
      reason_codes: [
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_RECOVERING",
      ],
    }),
  ]);
  assert.equal(incidents.length, 1);
  assert.deepEqual(incidents[0].reason_projections.map(item => item.reason_code), [
    "ACTIONABLE_NEWS_IMPACT_PENDING",
    "ACTIONABLE_NEWS_IMPACT_RECOVERING",
  ]);
  assert.equal(incidents[0].technical_events.length, 1);
  assert.equal(incidents[0].technical_event_count, 2);
});

test("counts a standalone incident root as an affected component", () => {
  const incidents = correlateOperationalEvents([
    event("OPS_RUNTIME_UPDATE_FAILED", "runtime_updater", {}, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(affectedOperationalScopeCount(incidents), 1);
});

test("classifies a claimable blocking retry as action required", () => {
  const [incident] = correlateOperationalEvents([
    event("OPS_AI_JOB_RETRY_LOOP", "ACTIVE_ANNOTATION", { claimable: true }, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(incident.action_state, "ACTION_REQUIRED");
});

test("keeps unknown events visible and marks taxonomy drift", () => {
  const [incident] = correlateOperationalEvents([
    event("OPS_FUTURE_UNREGISTERED", "test", {}, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(incident.root_event.evidence.taxonomy_error, "UNREGISTERED_OPERATIONAL_CODE:OPS_FUTURE_UNREGISTERED");
  assert.deepEqual(globalOperationalIncidents([incident]), [incident]);
});
