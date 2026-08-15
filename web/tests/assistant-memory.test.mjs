import assert from "node:assert/strict";
import test from "node:test";

import {
  ASSISTANT_COMPACTION_PROMPT_VERSION,
  DEFAULT_ASSISTANT_CONTEXT_PROFILE,
  buildAssistantContext,
  claimAssistantCompactionJob,
  completeAssistantCompactionJob,
  createAssistantPinnedEntry,
  deferAssistantCompactionJob,
  failAssistantCompactionJob,
  scheduleAssistantCompaction,
} from "../app/api/_shared/assistant-memory.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";
import { assistantRouting } from "./assistant-routing-fixture.mjs";

const owner = "cloudflare-access:memory-owner";
const otherOwner = "cloudflare-access:other-owner";
const baseTime = Date.parse("2026-08-15T10:00:00.000Z");
const instant = minutes => new Date(baseTime + minutes * 60_000);

const compactProfile = {
  ...DEFAULT_ASSISTANT_CONTEXT_PROFILE,
  id: "assistant-context-contract-test-v1",
  contextLimitTokens: 2_000,
  greenThresholdRatio: 0.25,
  yellowThresholdRatio: 0.65,
  reservedSystemTokens: 100,
  reservedToolDefinitionTokens: 100,
  reservedReasoningTokens: 100,
  reservedOutputTokens: 100,
  pinnedTokenBudget: 800,
  summaryTokenBudget: 800,
  historicalMemoryTokenBudget: 400,
  recentTurnsTokenBudget: 700,
  currentUserTokenBudget: 400,
  toolEvidenceTokenBudget: 400,
  recentTurnLimit: 2,
  compactionMessageLimit: 2,
  compactionSourceTokenBudget: 800,
};

function seedConversation(database, {
  id = "conversation-memory",
  actor = owner,
  turns = 5,
  contentSize = 36,
} = {}) {
  database.database.prepare(
    `INSERT INTO assistant_conversations (
       id,owner_id,initial_idempotency_key,title,title_source,created_at,
       last_activity_at,summary_version,status
     ) VALUES (?,?,?,'Memory contract','PROVISIONAL',?,?,0,'ACTIVE')`,
  ).run(
    id,
    actor,
    `memory-idempotency-${id}`,
    instant(0).toISOString(),
    instant(turns * 2).toISOString(),
  );
  const insert = database.database.prepare(
    `INSERT INTO assistant_messages (
       id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
     ) VALUES (?,?,?,?,?,?,?,?)`,
  );
  const ids = [];
  for (let index = 1; index <= turns * 2; index += 1) {
    const messageId = `${id}-message-${String(index).padStart(2, "0")}`;
    ids.push(messageId);
    const provenance = index === 2 ? {
      kind: "TEST_TOOL_RESULT",
      evidence_ids: ["evidence:memory-1"],
      retrieval: {
        canonical_evidence_ids: ["evidence:memory-1"],
        cutoff: instant(index).toISOString(),
      },
      tool_refs: ["news-search:v1"],
    } : { kind: "TEST_MESSAGE" };
    insert.run(
      messageId,
      id,
      index % 2 ? "USER" : "ASSISTANT",
      `${index % 2 ? "用户约束" : "回答"}-${index}-` + "甲".repeat(contentSize),
      instant(index).toISOString(),
      JSON.stringify(provenance),
      "MEMORY_TEST",
      `${id}-source-${index}`,
    );
  }
  return { conversationId: id, messageIds: ids };
}

const completePayload = (claim, summary, pins = []) => ({
  id: claim.id,
  lease_token: claim.lease_token,
  summary,
  covered_message_ids: claim.source_messages.map(message => message.id),
  pinned_entries: pins,
  model_version: "gemma-compaction-contract-test",
  prompt_version: claim.prompt_version,
  context_profile_id: claim.context_profile_id,
  routing: assistantRouting("CONTEXT_COMPACTION"),
});

test("compaction advances incrementally and retains canonical history, pins, and evidence anchors", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database);
  const originalActivity = database.row(
    seeded.conversationId, "assistant_conversations",
  ).last_activity_at;

  const scheduled = await scheduleAssistantCompaction(database, seeded.conversationId, {
    now: instant(11), profile: compactProfile,
  });
  assert.equal(scheduled.kind, "CREATED");
  assert.ok(["YELLOW", "RED"].includes(scheduled.capacity_state));
  const first = await claimAssistantCompactionJob(database, "worker:memory", instant(11));
  assert.equal(first.prior_summary, null);
  assert.deepEqual(
    first.source_messages.map(message => message.id),
    seeded.messageIds.slice(0, 2),
  );
  assert.equal(first.prompt_version, ASSISTANT_COMPACTION_PROMPT_VERSION);

  const completed = await completeAssistantCompactionJob(database, completePayload(
    first,
    "用户要求保留证据来源；系统已回答第一段问题，后续工作仍未解决。",
    [{
      kind: "CONSTRAINT",
      content: "后续回答必须保留证据来源。",
      origin_message_ids: [seeded.messageIds[0]],
      evidence_ids: ["evidence:memory-1"],
      source_refs: ["source:memory-contract"],
      important_timestamps: [instant(1).toISOString()],
      tool_refs: ["news-search:v1"],
      artifact_refs: ["artifact:memory-contract"],
    }],
  ), instant(12), compactProfile);
  assert.equal(completed.summary_version, 1);
  assert.equal(completed.pinned_entries_created, 1);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS count FROM assistant_messages WHERE conversation_id=?",
  ).get(seeded.conversationId).count, seeded.messageIds.length);
  const conversation = database.row(seeded.conversationId, "assistant_conversations");
  assert.equal(conversation.summary_version, 1);
  assert.equal(conversation.last_activity_at, originalActivity);
  assert.deepEqual(
    JSON.parse(database.row(first.id, "assistant_compaction_jobs").attempt_history_json)
      .map(receipt => receipt.event),
    ["CLAIMED", "COMPLETED"],
  );
  assert.equal(
    JSON.parse(database.row(first.id, "assistant_compaction_jobs").attempt_history_json)
      .at(-1).routing.task_type,
    "CONTEXT_COMPACTION",
  );
  assert.throws(
    () => database.database.prepare(
      "UPDATE assistant_compaction_jobs SET attempt_history_json='[]' WHERE id=?",
    ).run(first.id),
    /append-only/,
  );
  const summary = database.database.prepare(
    "SELECT * FROM assistant_summaries WHERE conversation_id=? AND version=1",
  ).get(seeded.conversationId);
  const anchors = JSON.parse(summary.anchors_json);
  assert.deepEqual(anchors.evidence_ids, ["evidence:memory-1"]);
  assert.ok(anchors.tool_refs.includes("news-search:v1"));
  assert.ok(anchors.important_timestamps.includes(instant(2).toISOString()));
  assert.throws(
    () => database.database.prepare("UPDATE assistant_summaries SET content='changed'").run(),
    /immutable/,
  );
  assert.throws(
    () => database.database.prepare("DELETE FROM assistant_pinned_entries").run(),
    /immutable/,
  );

  assert.equal(completed.next_compaction.kind, "CREATED");
  const second = await claimAssistantCompactionJob(database, "worker:memory", instant(12));
  assert.equal(second.prior_summary.version, 1);
  assert.equal(second.prior_summary.content, summary.content);
  assert.deepEqual(
    second.source_messages.map(message => message.id),
    seeded.messageIds.slice(2, 4),
  );
  assert.equal(
    second.source_messages.some(message => seeded.messageIds.slice(0, 2).includes(message.id)),
    false,
  );
  assert.equal(second.pinned_state[0].content, "后续回答必须保留证据来源。" );
});

test("invalid or failed compaction never replaces the last valid summary", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database);
  await scheduleAssistantCompaction(database, seeded.conversationId, {
    now: instant(11), profile: compactProfile,
  });
  const first = await claimAssistantCompactionJob(database, "worker:memory", instant(11));
  await completeAssistantCompactionJob(
    database, completePayload(first, "第一版有效增量摘要。"), instant(11.5), compactProfile,
  );
  const second = await claimAssistantCompactionJob(database, "worker:memory", instant(12));

  await assert.rejects(
    completeAssistantCompactionJob(database, {
      ...completePayload(second, "不完整覆盖不应生效。"),
      covered_message_ids: [second.source_messages[0].id],
    }, instant(12.1), compactProfile),
    /没有确认全部冻结输入消息/,
  );
  await assert.rejects(
    completeAssistantCompactionJob(database, {
      ...completePayload(second, "错误规则版本不应生效。"),
      prompt_version: "assistant-compaction-v0",
    }, instant(12.1), compactProfile),
    /profile 无效/,
  );
  const failure = await failAssistantCompactionJob(database, {
    id: second.id,
    lease_token: second.lease_token,
    failure_code: "MODEL_OUTPUT_INVALID",
  }, instant(12.2));
  assert.equal(failure.status, "PENDING");
  assert.deepEqual(
    JSON.parse(database.row(second.id, "assistant_compaction_jobs").attempt_history_json)
      .map(receipt => receipt.event),
    ["CLAIMED", "FAILED"],
  );
  const conversation = database.row(seeded.conversationId, "assistant_conversations");
  assert.equal(conversation.summary_version, 1);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS count FROM assistant_summaries WHERE conversation_id=?",
  ).get(seeded.conversationId).count, 1);
  assert.equal(database.database.prepare(
    "SELECT content FROM assistant_summaries WHERE conversation_id=? AND version=1",
  ).get(seeded.conversationId).content, "第一版有效增量摘要。" );
});

test("compaction leases recover finitely and stale workers cannot publish", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database);
  await scheduleAssistantCompaction(database, seeded.conversationId, {
    now: instant(11), profile: compactProfile,
  });
  const stale = await claimAssistantCompactionJob(database, "worker:stale", instant(11));
  assert.equal(await failAssistantCompactionJob(database, {
    id: stale.id,
    lease_token: stale.lease_token,
    failure_code: "LATE_WORKER_FAILURE",
  }, instant(15)), null);
  const recovered = await claimAssistantCompactionJob(database, "worker:recovered", instant(15));
  assert.equal(recovered.id, stale.id);
  assert.equal(recovered.attempt_count, 2);
  assert.equal(await completeAssistantCompactionJob(
    database, completePayload(stale, "过期工作者摘要。"), instant(15), compactProfile,
  ), null);
  let failure = await failAssistantCompactionJob(database, {
    id: recovered.id,
    lease_token: recovered.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, instant(15));
  assert.equal(failure.status, "PENDING");
  const finalClaim = await claimAssistantCompactionJob(database, "worker:final", instant(16));
  failure = await failAssistantCompactionJob(database, {
    id: finalClaim.id,
    lease_token: finalClaim.lease_token,
    failure_code: "NO_MODEL_CAPACITY",
  }, instant(16));
  assert.equal(failure.status, "FAILED");
  assert.equal(database.row(seeded.conversationId, "assistant_conversations").summary_version, 0);
  assert.equal(database.row(seeded.conversationId, "assistant_conversations").pending_compaction_job_id, null);
  assert.equal(database.database.prepare("SELECT count(*) AS count FROM assistant_summaries").get().count, 0);
  const replacement = await scheduleAssistantCompaction(database, seeded.conversationId, {
    now: instant(17), profile: compactProfile,
  });
  assert.equal(replacement.kind, "CREATED");
  const failedJob = database.row(stale.id, "assistant_compaction_jobs");
  const replacementJob = database.row(replacement.job_id, "assistant_compaction_jobs");
  assert.equal(failedJob.output_summary_version, 1);
  assert.equal(replacementJob.output_summary_version, 1);
  assert.ok(replacementJob.input_version > failedJob.input_version);
});

test("capacity deferral releases compaction under the finite attempt budget", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database);
  await scheduleAssistantCompaction(database, seeded.conversationId, {
    now: instant(11), profile: compactProfile,
  });
  const claimed = await claimAssistantCompactionJob(database, "worker:a", instant(11));

  const deferred = await deferAssistantCompactionJob(database, {
    id: claimed.id,
    lease_token: claimed.lease_token,
  }, instant(11));

  assert.equal(deferred.status, "PENDING");
  assert.equal(
    database.row(claimed.id, "assistant_compaction_jobs").attempt_count,
    1,
  );
  assert.equal(
    await claimAssistantCompactionJob(database, "worker:early", instant(11.5)),
    null,
  );
  const reclaimed = await claimAssistantCompactionJob(
    database, "worker:later", instant(12),
  );
  assert.equal(reclaimed.attempt_count, 2);
  assert.equal((await deferAssistantCompactionJob(database, {
    id: reclaimed.id, lease_token: reclaimed.lease_token,
  }, instant(12))).status, "PENDING");
  const finalClaim = await claimAssistantCompactionJob(
    database, "worker:final", instant(13),
  );
  assert.equal(finalClaim.attempt_count, 3);
  assert.equal((await deferAssistantCompactionJob(database, {
    id: finalClaim.id, lease_token: finalClaim.lease_token,
  }, instant(13))).status, "FAILED");
  assert.equal(
    database.row(seeded.conversationId, "assistant_conversations").pending_compaction_job_id,
    null,
  );
});

test("compaction admission bounds active background work per owner", async () => {
  const database = new D1TestDatabase();
  const first = seedConversation(database, { id: "conversation-capacity-1" });
  const second = seedConversation(database, { id: "conversation-capacity-2" });
  const deferred = seedConversation(database, { id: "conversation-capacity-3" });
  const other = seedConversation(database, {
    id: "conversation-capacity-other", actor: otherOwner,
  });
  assert.equal((await scheduleAssistantCompaction(database, first.conversationId, {
    profile: compactProfile,
  })).kind, "CREATED");
  assert.equal((await scheduleAssistantCompaction(database, second.conversationId, {
    profile: compactProfile,
  })).kind, "CREATED");
  assert.equal((await scheduleAssistantCompaction(database, deferred.conversationId, {
    profile: compactProfile,
  })).kind, "DEFERRED_CAPACITY");
  assert.equal(
    database.row(deferred.conversationId, "assistant_conversations").pending_compaction_job_id,
    null,
  );
  assert.equal((await scheduleAssistantCompaction(database, other.conversationId, {
    profile: compactProfile,
  })).kind, "CREATED");
});

test("turn-window overflow schedules compaction even while token capacity is GREEN", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database, {
    id: "conversation-green-turn-overflow", turns: 5, contentSize: 1,
  });
  const scheduled = await scheduleAssistantCompaction(database, seeded.conversationId);
  assert.equal(scheduled.kind, "CREATED");
  assert.equal(scheduled.capacity_state, "GREEN");
});

test("Context Builder preserves ordered layers and fails closed for owner or required-budget violations", async () => {
  const database = new D1TestDatabase();
  const seeded = seedConversation(database, { turns: 2 });
  await createAssistantPinnedEntry(database, {
    ownerId: owner,
    conversationId: seeded.conversationId,
    idempotencyKey: "pin-memory-contract-0001",
    entry: {
      kind: "UNRESOLVED",
      content: "仍需回答证据时点问题。",
      origin_message_ids: [seeded.messageIds[0]],
      evidence_ids: ["evidence:memory-1"],
      source_refs: [],
      important_timestamps: [],
      tool_refs: [],
      artifact_refs: [],
    },
    now: instant(11),
  });
  const replay = await createAssistantPinnedEntry(database, {
    ownerId: owner,
    conversationId: seeded.conversationId,
    idempotencyKey: "pin-memory-contract-0001",
    entry: {
      kind: "TOPIC",
      content: "重试不能改写固定状态。",
      origin_message_ids: [seeded.messageIds[1]],
      evidence_ids: [], source_refs: [], important_timestamps: [], tool_refs: [], artifact_refs: [],
    },
  });
  assert.equal(replay.content, "仍需回答证据时点问题。" );

  const context = await buildAssistantContext(database, {
    ownerId: owner,
    conversationId: seeded.conversationId,
    currentUserMessageId: seeded.messageIds.at(-2),
    toolEvidence: [{ evidence_id: "evidence:tool-1", content: { headline: "CPI" } }],
  });
  assert.deepEqual(context.layers.map(layer => layer.type), [
    "PINNED_STATE",
    "ROLLING_SUMMARY",
    "HISTORICAL_MEMORY",
    "RECENT_VERBATIM_TURNS",
    "CURRENT_USER_MESSAGE",
    "TOOL_EVIDENCE",
  ]);
  assert.equal(context.layers[0].items[0].origin_message_ids[0], seeded.messageIds[0]);
  assert.equal(
    context.layers[3].items.some(message => message.id === seeded.messageIds.at(-2)),
    false,
  );
  assert.equal(
    context.layers[3].items.some(message => message.id === seeded.messageIds.at(-1)),
    false,
  );
  assert.equal(await buildAssistantContext(database, {
    ownerId: otherOwner,
    conversationId: seeded.conversationId,
    currentUserMessageId: seeded.messageIds.at(-2),
  }), null);

  const tinyPinnedProfile = {
    ...DEFAULT_ASSISTANT_CONTEXT_PROFILE,
    id: "assistant-context-tiny-pinned-v1",
    pinnedTokenBudget: 10,
  };
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: seeded.conversationId,
      currentUserMessageId: seeded.messageIds.at(-2),
    }, tinyPinnedProfile),
    error => error.code === "PINNED_STATE_EXCEEDS_BUDGET",
  );

  const foreign = seedConversation(database, {
    id: "conversation-foreign", actor: otherOwner, turns: 1,
  });
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: seeded.conversationId,
      currentUserMessageId: seeded.messageIds.at(-2),
      historicalMemory: [{
        content: "foreign memory",
        canonical_message_ids: [foreign.messageIds[0]],
      }],
    }),
    error => error.code === "HISTORICAL_MEMORY_NOT_OWNER_SCOPED",
  );
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: seeded.conversationId,
      currentUserMessageId: seeded.messageIds.at(-2),
      historicalMemory: [{
        content: "future memory",
        canonical_message_ids: [seeded.messageIds.at(-1)],
      }],
    }),
    error => error.code === "HISTORICAL_MEMORY_NOT_OWNER_SCOPED",
  );
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: seeded.conversationId,
      currentUserMessageId: seeded.messageIds.at(-2),
      historicalMemory: [{ content: "unlinked memory", canonical_message_ids: [] }],
    }),
    error => error.code === "HISTORICAL_MEMORY_NOT_OWNER_SCOPED",
  );
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: seeded.conversationId,
      currentUserMessageId: seeded.messageIds.at(-2),
    }, {
      ...DEFAULT_ASSISTANT_CONTEXT_PROFILE,
      id: "assistant-context-recent-message-bound-v1",
      recentTurnLimit: 2,
      recentMessageLimit: 2,
    }),
    error => error.code === "RECENT_TURNS_EXCEED_BUDGET",
  );
  const uncompacted = seedConversation(database, {
    id: "conversation-uncompacted-gap", turns: 5,
  });
  await assert.rejects(
    buildAssistantContext(database, {
      ownerId: owner,
      conversationId: uncompacted.conversationId,
      currentUserMessageId: uncompacted.messageIds.at(-2),
    }),
    error => error.code === "COMPACTION_REQUIRED",
  );
  assert.throws(
    () => database.database.prepare(
      `INSERT INTO assistant_pinned_entries (
       id,conversation_id,idempotency_key,kind,content,origin_message_ids_json,
       evidence_ids_json,source_refs_json,important_timestamps_json,tool_refs_json,
       artifact_refs_json,created_by,created_at
       ) VALUES ('invalid-pin',?,'invalid-pin-key','TOPIC','bad',?,'[]','[]','[]','[]','[]','SYSTEM',?)`,
    ).run(seeded.conversationId, JSON.stringify([foreign.messageIds[0]]), instant(20).toISOString()),
    /canonical origins/,
  );
});
