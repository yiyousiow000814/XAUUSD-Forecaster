import assert from "node:assert/strict";
import test from "node:test";

import {
  D1CapabilityError,
  requireD1Capabilities,
} from "../app/api/_shared/d1-capabilities.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";

test("D1 capabilities fail closed with bounded missing schema evidence", async () => {
  const database = new D1TestDatabase([]);
  await assert.rejects(
    requireD1Capabilities(database, ["operator_retry_scheduling"]),
    error => {
      assert.ok(error instanceof D1CapabilityError);
      assert.deepEqual(error.missingCapabilities, ["operator_retry_scheduling"]);
      assert.deepEqual(error.missingTables, [
        "operator_retry_jobs",
        "operator_retry_requests",
        "operator_retry_request_events",
        "operator_retry_sync_state",
      ]);
      return true;
    },
  );
});

test("D1 capabilities accept the reviewed additive migrations", async () => {
  const database = new D1TestDatabase([
    "0020_operator_retry_scheduling.sql",
    "0023_operator_retry_sync_digest.sql",
    "0021_paged_news_evidence.sql",
    "0030_news_evidence_cleanup_budget.sql",
    "0022_news_projection_generation.sql",
    "0027_materialize_news_projection_counts.sql",
  ]);
  await requireD1Capabilities(database, [
    "operator_retry_scheduling", "paged_news_evidence", "news_projection_generation",
  ]);
});

test("successful capability observations are isolate-cached and failures retry", async () => {
  let calls = 0;
  let available = true;
  const binding = {
    prepare() {
      calls += 1;
      return {
        bind() { return this; },
        async all() {
          return {
            results: available ? [
              { name: "operator_retry_jobs" },
              { name: "operator_retry_requests" },
              { name: "operator_retry_request_events" },
              { name: "operator_retry_sync_state" },
            ] : [],
          };
        },
      };
    },
  };
  await requireD1Capabilities(binding, ["operator_retry_scheduling"]);
  await requireD1Capabilities(binding, ["operator_retry_scheduling"]);
  assert.equal(calls, 1);

  const recovering = {
    prepare() {
      calls += 1;
      return {
        bind() { return this; },
        async all() {
          return { results: available ? [
            { name: "news_evidence_records" }, { name: "news_evidence_state" },
            { name: "news_evidence_staging" }, { name: "news_evidence_batches" },
            { name: "news_evidence_cleanup_budget" },
          ] : [] };
        },
      };
    },
  };
  available = false;
  await assert.rejects(requireD1Capabilities(recovering, ["paged_news_evidence"]));
  available = true;
  await requireD1Capabilities(recovering, ["paged_news_evidence"]);
  assert.equal(calls, 3, "a failed observation is not cached across migration recovery");
});
