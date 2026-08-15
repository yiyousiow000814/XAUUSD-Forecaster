import assert from "node:assert/strict";
import test from "node:test";

import {
  claimNewsQuestion,
  completeNewsQuestion,
  createNewsQuestion,
  deferNewsQuestion,
  deriveRetrievalQuery,
  failNewsQuestion,
  getOwnerNewsQuestion,
  INSUFFICIENT_EVIDENCE_ANSWER,
  listOwnerNewsQuestions,
  NewsQuestionInputError,
} from "../app/api/_shared/news-questions.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";
import { assistantRouting } from "./assistant-routing-fixture.mjs";
import {
  buildAssistantEvidenceValidation,
} from "../app/api/_shared/assistant-evidence.ts";

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

const validation = async (answer, ids, mode = "CITATION_COVERAGE") => (
  await buildAssistantEvidenceValidation({
    claims: [{ text: answer, evidence_ids: mode === "CITATION_COVERAGE" ? ids : [] }],
  }, mode === "CITATION_COVERAGE" ? ids : [], {
    mode,
    maxCitedEvidence: 12,
  })
).receipt;

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
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_conversations",
  ).get().n, 3);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_messages",
  ).get().n, 3);
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
  assert.throws(
    () => database.database.prepare(
      "UPDATE news_questions SET status='ANSWERED' WHERE id=?",
    ).run(created.item.id),
    /current news answer requires evidence validation/,
  );
  await assert.rejects(
    completeNewsQuestion(database, {
      id: created.item.id,
      lease_token: recovered.lease_token,
      answer_status: "ANSWERED",
      answer: "错误版本不应发布。",
      evidence_ids: [evidenceId],
      model_version: "gemma-test",
      prompt_version: "news-qa-v1",
      retrieval: provenance(recovered, [evidenceId]),
    }, minute(4)),
    error => error instanceof NewsQuestionInputError && error.code === "INVALID_PROMPT_PROVENANCE",
  );
  await assert.rejects(
    completeNewsQuestion(database, {
      id: created.item.id,
      lease_token: recovered.lease_token,
      answer_status: "ANSWERED",
      answer: "美联储表态改变了利率预期。",
      evidence_ids: ["invented"],
      model_version: "gemma-test",
      prompt_version: recovered.prompt_version,
      retrieval: provenance(recovered, [evidenceId]),
    }, minute(4)),
    error => error instanceof NewsQuestionInputError && error.code === "UNVERIFIED_EVIDENCE",
  );

  const answer = "美联储表态改变了利率预期。";
  const evidenceValidation = await validation(answer, [evidenceId]);
  const forgedValidation = structuredClone(evidenceValidation);
  forgedValidation.answer_sha256 = "0".repeat(64);
  await assert.rejects(
    completeNewsQuestion(database, {
      id: created.item.id,
      lease_token: recovered.lease_token,
      answer_status: "ANSWERED",
      answer,
      evidence_ids: [evidenceId],
      evidence_validation: forgedValidation,
      model_version: "gemma-test",
      prompt_version: recovered.prompt_version,
      retrieval: provenance(recovered, [evidenceId]),
      routing: assistantRouting("NEWS_QA"),
    }, minute(4)),
    error => error instanceof NewsQuestionInputError
      && error.code === "INVALID_EVIDENCE_VALIDATION",
  );

  const completed = await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: recovered.lease_token,
    answer_status: "ANSWERED",
    answer,
    evidence_ids: [evidenceId],
    evidence_validation: evidenceValidation,
    model_version: "gemma-test",
    prompt_version: recovered.prompt_version,
    retrieval: provenance(recovered, [evidenceId]),
    routing: assistantRouting("NEWS_QA"),
  }, minute(4));
  assert.equal(completed.status, "ANSWERED");
  assert.equal(database.row(created.item.id).processing_started_at, null);
  assert.deepEqual(completed.evidence_ids, [evidenceId]);
  assert.deepEqual(completed.evidence_validation, evidenceValidation);
  assert.equal(completed.evidence_validation.entailment_status, "NOT_VERIFIED");
  assert.equal(completed.retrieval.source_mode, "D1_ARCHIVE");
  assert.deepEqual(
    JSON.parse(database.row(created.item.id).attempt_history_json).map(item => item.event),
    ["CLAIMED", "LEASE_EXPIRED", "CLAIMED", "ANSWERED"],
  );
  assert.throws(
    () => database.database.prepare(
      "UPDATE news_questions SET prompt_version='news-qa-v1' WHERE id=?",
    ).run(created.item.id),
    /prompt version is immutable/,
  );
  assert.throws(
    () => database.database.prepare(
      "UPDATE news_questions SET evidence_validation_json='{}' WHERE id=?",
    ).run(created.item.id),
    /news evidence validation is immutable/,
  );
});

test("no retrieval evidence publishes the fixed honest result without a model identity", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  const claim = await claimNewsQuestion(database, "worker:a", minute(0));
  const evidenceValidation = await validation(
    INSUFFICIENT_EVIDENCE_ANSWER,
    [],
    "INSUFFICIENT_EVIDENCE",
  );
  const completed = await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: claim.lease_token,
    answer_status: "INSUFFICIENT_EVIDENCE",
    answer: "untrusted copy",
    evidence_ids: [],
    evidence_validation: evidenceValidation,
    model_version: "untrusted-model",
    prompt_version: claim.prompt_version,
    retrieval: provenance(claim, []),
  }, minute(0));
  assert.equal(completed.answer, INSUFFICIENT_EVIDENCE_ANSWER);
  assert.equal(completed.answer_status, "INSUFFICIENT_EVIDENCE");
  assert.equal(completed.model_version, null);
  assert.deepEqual(completed.evidence_ids, []);
  assert.deepEqual(completed.evidence_validation, evidenceValidation);
  const message = database.row(completed.assistant_message_id, "assistant_messages");
  assert.equal(message.content, INSUFFICIENT_EVIDENCE_ANSWER);
  assert.equal(JSON.parse(message.provenance_json).model_version, null);
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

test("capacity deferral releases Q&A under the finite attempt budget", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  const claimed = await claimNewsQuestion(database, "worker:a", minute(0));

  const deferred = await deferNewsQuestion(database, {
    id: created.item.id,
    lease_token: claimed.lease_token,
  }, minute(0));

  assert.equal(deferred.status, "PENDING");
  assert.equal(deferred.attempt_count, 1);
  assert.equal(await claimNewsQuestion(database, "worker:early", minute(0.5)), null);
  const reclaimed = await claimNewsQuestion(database, "worker:later", minute(1));
  assert.equal(reclaimed.attempt_count, 2);
  assert.equal((await deferNewsQuestion(database, {
    id: created.item.id, lease_token: reclaimed.lease_token,
  }, minute(1))).status, "PENDING");
  const finalClaim = await claimNewsQuestion(database, "worker:final", minute(2));
  assert.equal(finalClaim.attempt_count, 3);
  assert.equal((await deferNewsQuestion(database, {
    id: created.item.id, lease_token: finalClaim.lease_token,
  }, minute(2))).status, "FAILED");
  assert.equal(await claimNewsQuestion(database, "worker:late", minute(3)), null);
  assert.deepEqual(
    JSON.parse(database.row(created.item.id).attempt_history_json)
      .map(receipt => receipt.event),
    [
      "CLAIMED", "CAPACITY_DEFERRED", "CLAIMED",
      "CAPACITY_DEFERRED", "CLAIMED", "CAPACITY_DEFERRED",
    ],
  );
});

test("pending work expires instead of consuming stale model capacity", async () => {
  const database = new D1TestDatabase();
  const created = await create(database, 1);
  assert.equal(await claimNewsQuestion(database, "worker:a", minute(31)), null);
  assert.equal(database.row(created.item.id).status, "EXPIRED");
});
