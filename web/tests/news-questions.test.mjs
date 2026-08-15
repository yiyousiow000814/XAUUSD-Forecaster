import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import {
  claimNewsQuestion,
  completeNewsQuestion,
  createNewsQuestion,
  deriveRetrievalQuery,
  failNewsQuestion,
  getOwnerNewsQuestion,
  INSUFFICIENT_EVIDENCE_ANSWER,
  listOwnerNewsQuestions,
  NewsQuestionInputError,
} from "../app/api/_shared/news-questions.ts";

class BoundStatement {
  constructor(database, sql, bindings = []) {
    this.database = database;
    this.sql = sql;
    this.bindings = bindings;
  }

  bind(...bindings) {
    return new BoundStatement(this.database, this.sql, bindings);
  }

  execute() {
    const statement = this.database.prepare(this.sql);
    if (statement.columns().length > 0) {
      return { success: true, results: statement.all(...this.bindings), meta: { changes: 0 } };
    }
    const result = statement.run(...this.bindings);
    return { success: true, results: [], meta: { changes: Number(result.changes) } };
  }

  async first() {
    return this.execute().results[0] ?? null;
  }

  async all() {
    return this.execute();
  }

  async run() {
    return this.execute();
  }
}

class D1TestDatabase {
  constructor() {
    this.database = new DatabaseSync(":memory:");
    const migration = readFileSync(
      new URL("../drizzle/0008_news_questions.sql", import.meta.url),
      "utf8",
    );
    this.database.exec(migration);
  }

  prepare(sql) {
    return new BoundStatement(this.database, sql);
  }

  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = statements.map(statement => statement.execute());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }

  row(id) {
    return this.database.prepare("SELECT * FROM news_questions WHERE id=?").get(id);
  }
}

const minute = value => new Date(Date.parse("2026-08-15T10:00:00.000Z") + value * 60_000);
const key = suffix => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;

async function create(database, suffix, overrides = {}) {
  return createNewsQuestion(database, {
    ownerId: overrides.ownerId ?? "cloudflare-access:owner-a",
    idempotencyKey: overrides.idempotencyKey ?? key(suffix),
    question: overrides.question ?? `美联储第${suffix}次表态为什么影响黄金？`,
    now: overrides.now ?? minute(0),
  });
}

const provenance = (claim, ids) => ({
  query: claim.retrieval_query,
  source_mode: "D1_ARCHIVE",
  archive_complete: true,
  ordering: [
    "published_time DESC",
    "collector_first_seen_time DESC",
    "detail_key DESC",
  ],
  cutoff: claim.retrieval_cutoff,
  result_limit: 20,
  canonical_evidence_ids: ids,
});

test("derives a bounded keyword query instead of sending a natural-language sentence as one token", () => {
  assert.equal(deriveRetrievalQuery("今天黄金市场为什么关注美联储利率？"), "黄金 美联储 利率");
  assert.equal(deriveRetrievalQuery("CPI 后黄金上涨了吗？"), "cpi 黄金 上涨");
});

test("admission is owner-scoped, idempotent, bounded, and private reads do not disclose foreign rows", async () => {
  const database = new D1TestDatabase();
  const first = await create(database, 1);
  assert.equal(first.kind, "CREATED");
  const replay = await create(database, 1);
  assert.equal(replay.kind, "EXISTING");
  assert.equal(replay.item.id, first.item.id);

  const conflict = await create(database, 1, { question: "另一个完全不同的问题是什么？" });
  assert.deepEqual(conflict, { kind: "CONFLICT" });
  const otherOwner = await create(database, 1, { ownerId: "cloudflare-access:owner-b" });
  assert.equal(otherOwner.kind, "CREATED");
  assert.notEqual(otherOwner.item.id, first.item.id);
  assert.equal(await getOwnerNewsQuestion(database, "cloudflare-access:owner-b", first.item.id), null);
  assert.deepEqual(
    (await listOwnerNewsQuestions(database, "cloudflare-access:owner-a", 20)).map(item => item.id),
    [first.item.id],
  );

  assert.equal((await create(database, 2)).kind, "CREATED");
  assert.deepEqual(await create(database, 3), { kind: "CAPACITY" });
});

test("expired leases recover, stale lease tokens cannot publish, and completion persists verified provenance", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  const firstClaim = await claimNewsQuestion(database, "worker:a", minute(0));
  assert.equal(firstClaim.id, created.item.id);
  assert.equal(firstClaim.attempt_count, 1);
  assert.equal(database.row(created.item.id).processing_started_at, minute(0).toISOString());
  assert.equal(await claimNewsQuestion(database, "worker:b", minute(1)), null);

  const recovered = await claimNewsQuestion(database, "worker:b", minute(4));
  assert.equal(recovered.id, created.item.id);
  assert.equal(recovered.attempt_count, 2);
  assert.notEqual(recovered.lease_token, firstClaim.lease_token);
  assert.equal(await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: firstClaim.lease_token,
  }, minute(4)), null);

  const evidenceId = "a".repeat(64);
  await assert.rejects(
    completeNewsQuestion(database, {
      id: created.item.id,
      lease_token: recovered.lease_token,
      answer_status: "ANSWERED",
      answer: "美联储表态改变了利率预期。",
      evidence_ids: ["invented"],
      model_version: "gemma-test",
      retrieval: provenance(recovered, [evidenceId]),
    }, minute(4)),
    error => error instanceof NewsQuestionInputError && error.code === "UNVERIFIED_EVIDENCE",
  );

  const completed = await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: recovered.lease_token,
    answer_status: "ANSWERED",
    answer: "美联储表态改变了利率预期。",
    evidence_ids: [evidenceId],
    model_version: "gemma-test",
    retrieval: provenance(recovered, [evidenceId]),
  }, minute(4));
  assert.equal(completed.status, "ANSWERED");
  assert.equal(database.row(created.item.id).processing_started_at, null);
  assert.deepEqual(completed.evidence_ids, [evidenceId]);
  assert.equal(completed.retrieval.source_mode, "D1_ARCHIVE");
  assert.deepEqual(
    JSON.parse(database.row(created.item.id).attempt_history_json).map(item => item.event),
    ["CLAIMED", "LEASE_EXPIRED", "CLAIMED", "ANSWERED"],
  );
});

test("no retrieval evidence publishes the fixed honest result without a model identity", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  const claim = await claimNewsQuestion(database, "worker:a", minute(0));
  const completed = await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: claim.lease_token,
    answer_status: "INSUFFICIENT_EVIDENCE",
    answer: "untrusted copy",
    evidence_ids: [],
    model_version: "untrusted-model",
    retrieval: provenance(claim, []),
  }, minute(0));
  assert.equal(completed.answer, INSUFFICIENT_EVIDENCE_ANSWER);
  assert.equal(completed.answer_status, "INSUFFICIENT_EVIDENCE");
  assert.equal(completed.model_version, null);
  assert.deepEqual(completed.evidence_ids, []);
});

test("worker failures back off and terminate after the bounded attempt budget", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  const claim1 = await claimNewsQuestion(database, "worker:a", minute(0));
  const retry1 = await failNewsQuestion(database, {
    id: created.item.id,
    lease_token: claim1.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, minute(0));
  assert.equal(retry1.status, "PENDING");
  assert.equal(database.row(created.item.id).processing_started_at, null);
  assert.equal(await claimNewsQuestion(database, "worker:a", minute(0.25)), null);

  const claim2 = await claimNewsQuestion(database, "worker:a", minute(1));
  const retry2 = await failNewsQuestion(database, {
    id: created.item.id,
    lease_token: claim2.lease_token,
    failure_code: "MODEL_OUTPUT_INVALID",
  }, minute(1));
  assert.equal(retry2.status, "PENDING");

  const claim3 = await claimNewsQuestion(database, "worker:a", minute(3));
  const failed = await failNewsQuestion(database, {
    id: created.item.id,
    lease_token: claim3.lease_token,
    failure_code: "MODEL_OUTPUT_INVALID",
  }, minute(3));
  assert.equal(failed.status, "FAILED");
  assert.equal(failed.attempt_count, 3);
  assert.equal(await claimNewsQuestion(database, "worker:a", minute(4)), null);
});

test("pending work expires instead of consuming stale model capacity", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  assert.equal(await claimNewsQuestion(database, "worker:a", minute(31)), null);
  assert.equal(database.row(created.item.id).status, "EXPIRED");
});
