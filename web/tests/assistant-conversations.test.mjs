import assert from "node:assert/strict";
import test from "node:test";

import {
  claimAssistantTitleJob,
  completeAssistantTitleJob,
  deferAssistantTitleJob,
  failAssistantTitleJob,
  getOwnerAssistantConversation,
  listOwnerAssistantConversations,
  listOwnerAssistantMessages,
  parseAssistantTitle,
  parseGeneratedAssistantTitle,
  provisionalAssistantTitle,
  renameOwnerAssistantConversation,
  requestAssistantTitleRegeneration,
  setOwnerAssistantConversationArchived,
} from "../app/api/_shared/assistant-conversations.ts";
import {
  claimNewsQuestion,
  completeNewsQuestion,
  createNewsQuestion,
} from "../app/api/_shared/news-questions.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";
import { assistantRouting } from "./assistant-routing-fixture.mjs";

const owner = "cloudflare-access:owner-a";
const otherOwner = "cloudflare-access:owner-b";
const instant = value => new Date(Date.parse("2026-08-15T10:00:00.000Z") + value * 60_000);
const key = suffix => `10000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;

async function createQuestion(database, suffix = 1, now = instant(0)) {
  return createNewsQuestion(database, {
    ownerId: owner,
    idempotencyKey: key(suffix),
    question: `美联储第${suffix}次表态为什么影响黄金？`,
    now,
  });
}

const provenance = (claim, ids = ["evidence:1"]) => ({
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

async function completeQuestion(database, created, at = instant(1)) {
  const claim = await claimNewsQuestion(database, "worker:test", instant(0));
  assert.equal(claim.id, created.item.id);
  return completeNewsQuestion(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer_status: "ANSWERED",
    answer: "根据已收录证据，利率预期正在影响美元与黄金定价。",
    evidence_ids: ["evidence:1"],
    retrieval: provenance(claim),
    model_version: "gemma-test",
    prompt_version: claim.prompt_version,
    routing: assistantRouting("NEWS_QA"),
  }, at);
}

test("provisional and generated titles use single-line Unicode grapheme bounds", () => {
  const family = "👨‍👩‍👧‍👦";
  const title = provisionalAssistantTitle(`  ${family.repeat(40)}\n后续  `);
  const count = Array.from(
    new Intl.Segmenter("zh-CN", { granularity: "grapheme" }).segment(title),
  ).length;
  assert.equal(count, 32);
  assert.equal(title.endsWith("…"), true);
  assert.equal(parseAssistantTitle("“CPI 后黄金反常上涨分析”"), "CPI 后黄金反常上涨分析");
  assert.equal(parseAssistantTitle("关于黄金市场的对话"), "关于黄金市场的对话");
  assert.throws(() => parseGeneratedAssistantTitle("关于黄金市场的对话"), /具体主题/);
  assert.throws(() => parseAssistantTitle("甲".repeat(33)), /2至32/);
});

test("question admission atomically creates owner-scoped canonical conversation and user message", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  assert.equal(created.kind, "CREATED");
  assert.ok(created.item.conversation_id);
  assert.ok(created.item.user_message_id);
  assert.equal(created.item.assistant_message_id, null);
  assert.equal(created.item.conversation_title, provisionalAssistantTitle(created.item.question));

  const conversation = await getOwnerAssistantConversation(
    database, owner, created.item.conversation_id,
  );
  assert.equal(conversation.title_source, "PROVISIONAL");
  assert.equal(conversation.last_activity_at, instant(0).toISOString());
  assert.equal(
    await getOwnerAssistantConversation(database, otherOwner, created.item.conversation_id),
    null,
  );
  const messages = await listOwnerAssistantMessages(
    database, owner, created.item.conversation_id,
  );
  assert.deepEqual(messages.items.map(item => item.role), ["USER"]);
  assert.equal(messages.items[0].provenance.question_id, created.item.id);
  assert.deepEqual(
    (await listOwnerAssistantMessages(database, otherOwner, created.item.conversation_id)).items,
    [],
  );

  const replay = await createQuestion(database);
  assert.equal(replay.kind, "EXISTING");
  assert.equal(replay.item.conversation_id, created.item.conversation_id);
  assert.equal(database.database.prepare("SELECT count(*) AS n FROM assistant_messages").get().n, 1);
  assert.equal(database.database.prepare("SELECT count(*) AS n FROM assistant_conversations").get().n, 1);
});

test("answer completion atomically appends one immutable Assistant message and schedules one title job", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  const completed = await completeQuestion(database, created);
  assert.ok(completed.assistant_message_id);
  const messages = await listOwnerAssistantMessages(
    database, owner, created.item.conversation_id,
  );
  assert.deepEqual(messages.items.map(item => item.role), ["USER", "ASSISTANT"]);
  assert.equal(messages.items[1].provenance.model_version, "gemma-test");
  assert.deepEqual(messages.items[1].provenance.evidence_ids, ["evidence:1"]);
  assert.equal(
    messages.items[1].provenance.routing.policy_version,
    "assistant-routing-v1",
  );
  const conversation = await getOwnerAssistantConversation(
    database, owner, created.item.conversation_id,
  );
  assert.equal(conversation.last_activity_at, instant(1).toISOString());
  assert.equal(conversation.title_job_status, "PENDING");
  assert.equal(database.database.prepare("SELECT count(*) AS n FROM assistant_title_jobs").get().n, 1);

  const duplicate = await completeNewsQuestion(database, {
    id: created.item.id,
    lease_token: "stale-token",
  }, instant(2));
  assert.equal(duplicate, null);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_messages WHERE role='ASSISTANT'",
  ).get().n, 1);
  assert.throws(
    () => database.database.prepare("UPDATE assistant_messages SET content='changed'").run(),
    /immutable/,
  );
  assert.throws(
    () => database.database.prepare(
      "UPDATE news_questions SET assistant_message_id=user_message_id WHERE id=?",
    ).run(created.item.id),
    /immutable/,
  );
  const titleJob = database.database.prepare(
    "SELECT * FROM assistant_title_jobs WHERE conversation_id=?",
  ).get(created.item.conversation_id);
  assert.throws(
    () => database.database.prepare(
      "UPDATE assistant_title_jobs SET prompt_version='assistant-title-v0' WHERE id=?",
    ).run(titleJob.id),
    /inputs are immutable/,
  );
  assert.throws(
    () => database.database.prepare(
      `INSERT INTO assistant_title_jobs (
       id,conversation_id,idempotency_key,requested_by,input_version,
       expected_title_revision,first_user_message_id,assistant_message_id,
       status,available_at,max_attempts,prompt_version,created_at
       ) VALUES ('invalid-role-job',?,?,'USER',99,0,?,?,'PENDING',?,3,'assistant-title-v1',?)`,
    ).run(
      created.item.conversation_id,
      key(199),
      completed.assistant_message_id,
      created.item.user_message_id,
      instant(2).toISOString(),
      instant(2).toISOString(),
    ),
    /frozen conversation messages/,
  );
});

test("AI and manual title work never changes conversation activity or overwrites a user rename", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  await completeQuestion(database, created);
  const activity = instant(1).toISOString();

  const job = await claimAssistantTitleJob(database, "worker:title", instant(2));
  assert.equal(job.first_user_message, created.item.question);
  assert.equal(job.prompt_version, "assistant-title-v1");
  await assert.rejects(
    completeAssistantTitleJob(database, {
      id: job.id,
      lease_token: job.lease_token,
      title: "错误版本不应生效",
      model_version: "gemma-title-test",
      prompt_version: "assistant-title-v0",
    }, instant(2.25)),
    /标题来源无效/,
  );
  const applied = await completeAssistantTitleJob(database, {
    id: job.id,
    lease_token: job.lease_token,
    title: "“美联储利率与黄金重定价”",
    model_version: "gemma-title-test",
    prompt_version: "assistant-title-v1",
    routing: assistantRouting("CONVERSATION_TITLE"),
  }, instant(2.5));
  assert.deepEqual(applied, {
    job_id: job.id,
    status: "COMPLETED",
    title_applied: true,
  });
  let conversation = await getOwnerAssistantConversation(
    database, owner, created.item.conversation_id,
  );
  assert.equal(conversation.title, "美联储利率与黄金重定价");
  assert.equal(conversation.title_source, "AI");
  assert.equal(conversation.last_activity_at, activity);
  assert.equal(database.row(job.id, "assistant_title_jobs").model_version, "gemma-title-test");
  assert.deepEqual(
    JSON.parse(database.row(job.id, "assistant_title_jobs").attempt_history_json)
      .map(receipt => receipt.event),
    ["CLAIMED", "COMPLETED"],
  );
  assert.equal(
    JSON.parse(database.row(job.id, "assistant_title_jobs").attempt_history_json)
      .at(-1).routing.task_type,
    "CONVERSATION_TITLE",
  );
  assert.throws(
    () => database.database.prepare(
      "UPDATE assistant_title_jobs SET attempt_history_json='[]' WHERE id=?",
    ).run(job.id),
    /append-only/,
  );

  const requested = await requestAssistantTitleRegeneration(database, {
    ownerId: owner,
    conversationId: created.item.conversation_id,
    idempotencyKey: key(99),
    now: instant(3),
  });
  assert.equal(requested.kind, "CREATED");
  const replay = await requestAssistantTitleRegeneration(database, {
    ownerId: owner,
    conversationId: created.item.conversation_id,
    idempotencyKey: key(99),
    now: instant(3),
  });
  assert.equal(replay.kind, "EXISTING");
  database.database.prepare(
    `INSERT INTO assistant_messages (
     id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
     ) VALUES ('future-assistant',?,'ASSISTANT','未来一轮回答',?,'{}','TEST','future-turn')`,
  ).run(created.item.conversation_id, instant(3.1).toISOString());
  const claimed = await claimAssistantTitleJob(database, "worker:title", instant(3));
  assert.equal(
    claimed.latest_assistant_message,
    "根据已收录证据,利率预期正在影响美元与黄金定价。",
  );
  const renamed = await renameOwnerAssistantConversation(
    database, owner, created.item.conversation_id, "我的黄金研究标题",
  );
  assert.equal(renamed.title_source, "USER");
  assert.equal(renamed.last_activity_at, activity);
  assert.equal(await completeAssistantTitleJob(database, {
    id: claimed.id,
    lease_token: claimed.lease_token,
    title: "不应覆盖用户标题",
    model_version: "gemma-title-test",
    prompt_version: "assistant-title-v1",
    routing: assistantRouting("CONVERSATION_TITLE"),
  }, instant(4)), null);
  conversation = await getOwnerAssistantConversation(
    database, owner, created.item.conversation_id,
  );
  assert.equal(conversation.title, "我的黄金研究标题");
  assert.equal(conversation.last_activity_at, activity);
});

test("title leases retry finitely and archive/list operations preserve activity ordering", async () => {
  const database = new D1TestDatabase();
  const older = await createQuestion(database, 1, instant(0));
  await completeQuestion(database, older, instant(1));
  const newer = await createQuestion(database, 2, instant(2));
  const active = await listOwnerAssistantConversations(database, owner);
  assert.deepEqual(active.map(item => item.id), [
    newer.item.conversation_id,
    older.item.conversation_id,
  ]);

  let job = await claimAssistantTitleJob(database, "worker:title", instant(2));
  let failure = await failAssistantTitleJob(database, {
    id: job.id,
    lease_token: job.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, instant(2));
  assert.equal(failure.status, "PENDING");
  assert.equal(await claimAssistantTitleJob(database, "worker:title", instant(2.25)), null);
  job = await claimAssistantTitleJob(database, "worker:title", instant(3));
  failure = await failAssistantTitleJob(database, {
    id: job.id,
    lease_token: job.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, instant(3));
  assert.equal(failure.status, "PENDING");
  job = await claimAssistantTitleJob(database, "worker:title", instant(5));
  failure = await failAssistantTitleJob(database, {
    id: job.id,
    lease_token: job.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, instant(5));
  assert.equal(failure.status, "FAILED");

  const activity = older.item.asked_at;
  const archived = await setOwnerAssistantConversationArchived(
    database, owner, older.item.conversation_id, true, instant(6),
  );
  assert.equal(archived.last_activity_at, instant(1).toISOString());
  assert.equal((await listOwnerAssistantConversations(database, owner)).length, 1);
  assert.deepEqual(
    (await listOwnerAssistantConversations(database, owner, { archived: true }))
      .map(item => item.id),
    [older.item.conversation_id],
  );
  assert.notEqual(activity, archived.last_activity_at);
});

test("capacity deferral releases a title lease under the finite attempt budget", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  await completeQuestion(database, created, instant(1));
  const claimed = await claimAssistantTitleJob(database, "worker:title", instant(2));

  const deferred = await deferAssistantTitleJob(database, {
    id: claimed.id,
    lease_token: claimed.lease_token,
  }, instant(2));

  assert.equal(deferred.status, "PENDING");
  assert.equal(database.row(claimed.id, "assistant_title_jobs").attempt_count, 1);
  assert.equal(await claimAssistantTitleJob(database, "worker:early", instant(2.5)), null);
  const reclaimed = await claimAssistantTitleJob(database, "worker:later", instant(3));
  assert.equal(reclaimed.attempt_count, 2);
  assert.equal((await deferAssistantTitleJob(database, {
    id: reclaimed.id, lease_token: reclaimed.lease_token,
  }, instant(3))).status, "PENDING");
  const finalClaim = await claimAssistantTitleJob(database, "worker:final", instant(4));
  assert.equal(finalClaim.attempt_count, 3);
  assert.equal((await deferAssistantTitleJob(database, {
    id: finalClaim.id, lease_token: finalClaim.lease_token,
  }, instant(4))).status, "FAILED");
  assert.equal(
    database.row(created.item.conversation_id, "assistant_conversations").pending_title_job_id,
    null,
  );
  assert.deepEqual(
    JSON.parse(database.row(claimed.id, "assistant_title_jobs").attempt_history_json)
      .map(receipt => receipt.event),
    [
      "CLAIMED", "CAPACITY_DEFERRED", "CLAIMED",
      "CAPACITY_DEFERRED", "CLAIMED", "CAPACITY_DEFERRED",
    ],
  );
});

test("expired title leases are reclaimed and stale workers cannot apply a title", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  await completeQuestion(database, created);
  const stale = await claimAssistantTitleJob(database, "worker:stale", instant(2));
  assert.equal(await failAssistantTitleJob(database, {
    id: stale.id,
    lease_token: stale.lease_token,
    failure_code: "LATE_WORKER_FAILURE",
  }, instant(6)), null);
  const recovered = await claimAssistantTitleJob(database, "worker:recovered", instant(6));
  assert.equal(recovered.id, stale.id);
  assert.equal(recovered.attempt_count, 2);
  assert.equal(await completeAssistantTitleJob(database, {
    id: stale.id,
    lease_token: stale.lease_token,
    title: "过期工作者标题",
    model_version: "gemma-title-test",
    prompt_version: "assistant-title-v1",
    routing: assistantRouting("CONVERSATION_TITLE"),
  }, instant(6)), null);
  assert.equal((await completeAssistantTitleJob(database, {
    id: recovered.id,
    lease_token: recovered.lease_token,
    title: "恢复后的有效标题",
    model_version: "gemma-title-test",
    prompt_version: "assistant-title-v1",
    routing: assistantRouting("CONVERSATION_TITLE"),
  }, instant(6))).title_applied, true);
});

test("message history is bounded and paginates deterministically", async () => {
  const database = new D1TestDatabase();
  const created = await createQuestion(database);
  const conversationId = created.item.conversation_id;
  for (let index = 1; index <= 55; index += 1) {
    database.database.prepare(
      `INSERT INTO assistant_messages (
       id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
       ) VALUES (?,?,?,?,?,'{}','TEST',?)`,
    ).run(
      `message-${String(index).padStart(3, "0")}`,
      conversationId,
      index % 2 ? "ASSISTANT" : "USER",
      `message ${index}`,
      instant(index).toISOString(),
      `source-${index}`,
    );
  }
  const first = await listOwnerAssistantMessages(database, owner, conversationId, { limit: 50 });
  assert.equal(first.items.length, 50);
  assert.ok(first.next_cursor);
  const second = await listOwnerAssistantMessages(database, owner, conversationId, {
    limit: 50,
    beforeCreatedAt: first.next_cursor.before_created_at,
    beforeId: first.next_cursor.before_id,
  });
  assert.equal(second.items.length, 6);
  assert.equal(second.next_cursor, null);
  assert.equal(new Set([...first.items, ...second.items].map(item => item.id)).size, 56);
  await assert.rejects(
    listOwnerAssistantMessages(database, owner, conversationId, {
      beforeCreatedAt: "not-a-time", beforeId: "message-001",
    }),
    /消息游标无效/,
  );
});

test("migration backfills legacy Q&A into canonical conversations without rewriting evidence", () => {
  const database = new D1TestDatabase(["0008_news_questions.sql"]);
  database.database.prepare(
    `INSERT INTO news_questions (
     id,owner_id,idempotency_key,question_hash,question,retrieval_query,status,
     asked_at,available_at,expires_at,attempt_count,max_attempts,prompt_version,
     attempt_history_json,answer,answer_status,evidence_json,retrieval_json,answered_at,model_version
     ) VALUES (?,?,?,?,?,?,'ANSWERED',?,?,?,1,3,'news-qa-v2','[]',?,?,'["legacy:1"]','not-json',?,'gemma-legacy')`,
  ).run(
    "legacy-question", owner, key(500), "hash", "旧问题为什么影响黄金？", "黄金",
    instant(0).toISOString(), instant(0).toISOString(), instant(30).toISOString(),
    "旧回答", "ANSWERED", instant(1).toISOString(),
  );
  database.database.prepare(
    `INSERT INTO news_questions (
     id,owner_id,idempotency_key,question_hash,question,retrieval_query,status,
     asked_at,available_at,expires_at,attempt_count,max_attempts,prompt_version,
     attempt_history_json
     ) VALUES (?,?,?,?,?,?,'ANSWERED',?,?,?,1,3,'news-qa-v2','[]')`,
  ).run(
    "legacy-incomplete", owner, key(501), "incomplete-hash", "遗留不完整问题", "黄金",
    instant(0).toISOString(), instant(0).toISOString(), instant(30).toISOString(),
  );
  database.applyMigration("0009_assistant_conversations.sql");
  const question = database.row("legacy-question");
  assert.equal(question.conversation_id, "conversation:legacy-question");
  assert.equal(question.user_message_id, "message:user:legacy-question");
  assert.equal(question.assistant_message_id, "message:assistant:legacy-question");
  const assistant = database.row("message:assistant:legacy-question", "assistant_messages");
  const legacyProvenance = JSON.parse(assistant.provenance_json);
  assert.deepEqual(legacyProvenance.evidence_ids, ["legacy:1"]);
  assert.equal(legacyProvenance.retrieval, null);
  assert.equal(database.row("legacy-incomplete").assistant_message_id, null);
});
