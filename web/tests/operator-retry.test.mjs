import assert from "node:assert/strict";
import test from "node:test";

import {
  claimOperatorRetryRequest,
  createOperatorRetryRequests,
  finishOperatorRetryRequest,
  listOperatorRetryJobs,
  parseOperatorRetryCustomTime,
  parseOperatorRetryIdempotencyKey,
  parseOperatorRetryMode,
  parseOperatorRetryReason,
  syncOperatorRetryJobs,
} from "../app/api/_shared/operator-retry.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";

test("operator retry inputs use explicit durable modes and bounded identity", () => {
  for (const mode of [
    "KEEP_ORIGINAL", "IMMEDIATE", "DELAY_15_MIN", "DELAY_1_HOUR",
    "IDLE_CAPACITY", "CUSTOM_TIME",
  ]) assert.equal(parseOperatorRetryMode(mode), mode);
  assert.throws(() => parseOperatorRetryMode("retry-ish"), /选项无效/);
  assert.equal(parseOperatorRetryReason("  repaired   deployment  "), "repaired deployment");
  assert.throws(() => parseOperatorRetryReason(""), /调整原因/);
  assert.equal(parseOperatorRetryIdempotencyKey("0123456789abcdef"), "0123456789abcdef");
  assert.throws(() => parseOperatorRetryIdempotencyKey("short"), /Idempotency/);
});

test("custom UTC+8 input becomes canonical UTC and rejects hidden extreme times", () => {
  const now = new Date("2026-08-19T03:00:00.000Z");
  assert.equal(
    parseOperatorRetryCustomTime("CUSTOM_TIME", "2026-08-19T12:30:00+08:00", now),
    "2026-08-19T04:30:00.000Z",
  );
  assert.equal(parseOperatorRetryCustomTime("IMMEDIATE", null, now), null);
  assert.throws(
    () => parseOperatorRetryCustomTime("CUSTOM_TIME", "2026-08-18T00:00:00Z", now),
    /不能早于/,
  );
  assert.throws(
    () => parseOperatorRetryCustomTime("CUSTOM_TIME", "2028-08-19T03:00:00Z", now),
    /不能超过一年/,
  );
});

const job = (suffix, state = "BACKING_OFF") => ({
  job_id: suffix.repeat(64), task_type: "ACTIVE_IMPACT", title: `Job ${suffix}`,
  state, priority: "NORMAL", available_at: "2026-08-19T06:00:00.000Z",
  attempt_count: 3, last_error: "ConnectionResetError",
  last_failure_at: "2026-08-19T00:46:00.000Z", lease_expires_at: null,
  override_mode: null, override_requested_at: null,
  original_available_at: "2026-08-19T06:00:00.000Z",
});

test("bulk admission is per-job, owner-audited, and browser replay is idempotent", async () => {
  const database = new D1TestDatabase(["0020_operator_retry_scheduling.sql"]);
  await syncOperatorRetryJobs(database, [job("a"), job("b", "LEASED")], new Date("2026-08-19T03:00:00Z"));
  const input = {
    operatorId: "cloudflare-access:owner", idempotencyKey: "00000000-0000-4000-8000-000000000001",
    jobIds: ["a".repeat(64), "b".repeat(64)], mode: "IMMEDIATE",
    reason: "repair deployed", requestedAvailableAt: null,
    now: new Date("2026-08-19T03:01:00Z"),
  };
  const first = await createOperatorRetryRequests(database, input);
  assert.equal(first[0].status, "PENDING");
  assert.equal(first[1].code, "JOB_NOT_MUTABLE");
  const replay = await createOperatorRetryRequests(database, input);
  assert.equal(replay[0].duplicate, true);
  assert.equal(database.database.prepare("SELECT count(*) AS n FROM operator_retry_requests").get().n, 1);
  assert.equal(database.database.prepare("SELECT count(*) AS n FROM operator_retry_request_events").get().n, 1);

  const conflict = await createOperatorRetryRequests(database, { ...input, mode: "DELAY_1_HOUR" });
  assert.equal(conflict[0].code, "IDEMPOTENCY_CONFLICT");
});

test("machine lease completion is bounded and public reads contain no secrets", async () => {
  const database = new D1TestDatabase(["0020_operator_retry_scheduling.sql"]);
  await syncOperatorRetryJobs(database, [job("c")], new Date("2026-08-19T03:00:00Z"));
  await createOperatorRetryRequests(database, {
    operatorId: "cloudflare-access:owner", idempotencyKey: "00000000-0000-4000-8000-000000000002",
    jobIds: ["c".repeat(64)], mode: "IDLE_CAPACITY", reason: "use spare capacity",
    requestedAvailableAt: null, now: new Date("2026-08-19T03:01:00Z"),
  });
  const claimed = await claimOperatorRetryRequest(database, "windows:one", new Date("2026-08-19T03:02:00Z"));
  assert.equal(claimed.status, "APPLYING");
  const finished = await finishOperatorRetryRequest(database, {
    request_id: claimed.request_id, lease_token: claimed.lease_token,
    status: "APPLIED", result: { current: { available_at: "2026-08-19T03:02:00Z" } },
  }, new Date("2026-08-19T03:03:00Z"));
  assert.equal(finished.status, "APPLIED");
  assert.equal(await finishOperatorRetryRequest(database, {
    request_id: claimed.request_id, lease_token: claimed.lease_token,
    status: "APPLIED", result: {},
  }), null);
  const listed = await listOperatorRetryJobs(database);
  assert.equal(listed.items[0].title, "Job c");
  assert.doesNotMatch(JSON.stringify(listed), /credential|api[_-]?key/i);
});
