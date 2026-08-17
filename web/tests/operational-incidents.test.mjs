import assert from "node:assert/strict";
import test from "node:test";

import { correlateOperationalEvents, globalOperationalIncidents } from "../app/_lib/operational-incidents.ts";

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
    new Set([incidents[0].root_event, ...incidents[0].related_events].map(item => item.code)),
    new Set(capacityChain().map(item => item.code)),
  );
  assert.ok(incidents[0].affected_scopes.includes("daily_news_brief"));
  assert.ok(incidents[0].affected_scopes.includes("news_semantic_pipeline"));
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
});

test("keeps unexplained component health reasons independently visible", () => {
  const incidents = correlateOperationalEvents([
    ...capacityChain(),
    event("OPS_COMPONENT_UNHEALTHY", "news_semantic_pipeline", {
      reason_codes: ["ANNOTATOR_HEARTBEAT_STALE"],
    }, { severity: "ERROR", blocking: true }),
  ]);
  assert.equal(incidents.length, 2);
  assert.ok(incidents.some(item => item.root_event.code === "OPS_COMPONENT_UNHEALTHY"));
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
