import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  appendAssistantChatEvents,
  AssistantChatInputError,
  cancelOwnerAssistantChatTurn,
  claimAssistantChatTurn,
  completeAssistantChatTurn,
  createAssistantChatTurn,
  failAssistantChatTurn,
  getOwnerAssistantChatTurn,
  listOwnerAssistantTurnEvents,
  renewAssistantChatTurn,
} from "../app/api/_shared/assistant-chat.ts";
import { getOwnerAssistantConversation, listOwnerAssistantMessages } from
  "../app/api/_shared/assistant-conversations.ts";
import { buildAssistantTextContentDocument } from
  "../app/api/_shared/assistant-content.ts";
import { assistantRouting } from "./assistant-routing-fixture.mjs";
import { D1TestDatabase } from "./d1-test-database.mjs";

const owner = "cloudflare-access:chat-owner";
const otherOwner = "cloudflare-access:other-owner";
const epoch = Date.parse("2026-08-16T01:00:00.000Z");
const atSeconds = seconds => new Date(epoch + seconds * 1_000);
const key = suffix => `chat-turn-00000000-0000-4000-${String(suffix).padStart(16, "0")}`;
const digest = value => createHash("sha256").update(value).digest("hex");

const canonicalJson = value => {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(
      name => `${JSON.stringify(name)}:${canonicalJson(value[name])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
};

const rehash = provenance => {
  delete provenance.run_sha256;
  provenance.run_sha256 = digest(canonicalJson(provenance));
  return provenance;
};

async function createTurn(database, suffix = 1, overrides = {}) {
  return createAssistantChatTurn(database, {
    ownerId: owner,
    idempotencyKey: key(suffix),
    message: `请分析第 ${suffix} 个黄金问题`,
    now: atSeconds(0),
    ...overrides,
  });
}

const evidenceValidation = (answer, available = [], cited = []) => {
  const claimRows = answer.split("\n").map((text, index) => ({
    claim_id: `claim-${index + 1}`,
    line_index: index,
    text_sha256: digest(text),
    evidence_ids: available.length ? cited : [],
  }));
  const withoutHash = {
    protocol: "assistant.evidence.v1",
    validator_version: "assistant-evidence-validator-v1",
    mode: available.length ? "CITATION_COVERAGE" : "NO_CITABLE_EVIDENCE",
    claim_count: claimRows.length,
    citation_count: claimRows.reduce((total, item) => total + item.evidence_ids.length, 0),
    available_evidence_ids: available,
    cited_evidence_ids: cited,
    claims: claimRows,
    coverage_complete: available.length > 0,
    entailment_status: "NOT_VERIFIED",
    answer_sha256: digest(answer),
  };
  return { ...withoutHash, receipt_sha256: digest(canonicalJson(withoutHash)) };
};

function directAgentProvenance(claim, answer, overrides = {}) {
  const provenance = {
    policy_version: "assistant-agent-v2",
    tool_registry_version: "assistant-tool-registry-v1",
    conversation_id: claim.conversation_id,
    user_message_id: claim.user_message_id,
    system_instruction_version: "assistant-system-v3",
    system_instruction_sha256: "a".repeat(64),
    active_context_sha256: "b".repeat(64),
    retrieval_cutoff: claim.retrieval_cutoff,
    budgets: {
      MAX_MODEL_TURNS_PER_USER_TURN: 3,
      MAX_TOOL_CALLS_PER_USER_TURN: 6,
      MAX_PARALLEL_TOOL_CALLS: 3,
      MAX_TOOL_RESULT_TOKENS: 8_192,
      MAX_RETRIEVED_EVIDENCE: 20,
      MAX_ACTIVE_CONTEXT_TOKENS: 24_576,
      MAX_OUTPUT_TOKENS: 2_048,
    },
    model_turn_count: 1,
    tool_round_count: 0,
    tool_call_count: 0,
    tool_result_tokens: 0,
    model_versions: ["gemma-4-31b-it"],
    model_routing: [assistantRouting("ASSISTANT_CHAT")],
    tool_execution: [],
    evidence_ids: [],
    evidence_validation: evidenceValidation(answer),
    ...overrides,
  };
  return rehash(provenance);
}

function toolAgentProvenance(claim, answer) {
  const evidenceIds = ["evidence:tool-1"];
  return directAgentProvenance(claim, answer, {
    model_turn_count: 2,
    tool_round_count: 1,
    tool_call_count: 1,
    tool_result_tokens: 128,
    model_versions: ["gemma-4-31b-it", "gemma-4-31b-it"],
    model_routing: [
      assistantRouting("ASSISTANT_CHAT", { planned_tool_calls: 1 }),
      assistantRouting("ASSISTANT_CHAT", { planned_tool_calls: 1 }),
    ],
    tool_execution: [[{
      call_id: "call-1",
      name: "search_news_v1",
      tool_version: "v1",
      status: "SUCCEEDED",
      error_code: null,
      result_tokens: 128,
      result_sha256: "c".repeat(64),
      evidence_ids: evidenceIds,
      provenance: {
        registry_version: "assistant-tool-registry-v1",
        actor_fingerprint: digest(
          `assistant-tool-registry-v1:${claim.owner_id}:${claim.id}`,
        ).slice(0, 16),
        source_mode: "D1_ARCHIVE",
        canonical_evidence_ids: evidenceIds,
      },
      started_at: "2026-08-16T01:00:01.000000+00:00",
      completed_at: "2026-08-16T01:00:01.100000+00:00",
    }]],
    evidence_ids: evidenceIds,
    evidence_validation: evidenceValidation(answer, evidenceIds, evidenceIds),
  });
}

test("chat admission is atomic, owner scoped, capacity bounded, and replay safe", async () => {
  const database = new D1TestDatabase();
  const created = await createTurn(database);
  assert.equal(created.kind, "CREATED");
  assert.equal(created.item.status, "PENDING");
  assert.equal(created.item.event_sequence, 1);
  assert.deepEqual(
    (await getOwnerAssistantConversation(
      database, owner, created.item.conversation_id,
    )).active_turn,
    {
      id: created.item.id,
      status: "PENDING",
      event_sequence: 1,
      created_at: atSeconds(0).toISOString(),
    },
  );

  const replay = await createTurn(database);
  assert.equal(replay.kind, "EXISTING");
  assert.equal(replay.item.id, created.item.id);
  assert.equal(await getOwnerAssistantChatTurn(database, otherOwner, created.item.id), null);
  assert.equal((await createTurn(database, 1, { message: "不同内容" })).kind, "CONFLICT");

  const messages = await listOwnerAssistantMessages(
    database, owner, created.item.conversation_id,
  );
  assert.deepEqual(messages.items.map(item => item.role), ["USER"]);
  assert.equal(messages.items[0].provenance.turn_id, created.item.id);

  const second = await createTurn(database, 2);
  assert.equal(second.kind, "CREATED");
  const refused = await createTurn(database, 3);
  assert.equal(refused.kind, "CAPACITY");
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_conversations",
  ).get().n, 2);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_messages",
  ).get().n, 2);

  const busy = await createTurn(database, 4, {
    conversationId: created.item.conversation_id,
  });
  assert.equal(busy.kind, "BUSY");
  const foreign = await createAssistantChatTurn(database, {
    ownerId: otherOwner,
    idempotencyKey: key(5),
    message: "不能附加到别人的会话",
    conversationId: created.item.conversation_id,
    now: atSeconds(0),
  });
  assert.equal(foreign.kind, "NOT_FOUND");
});

test("leased workers append only closed, validated, idempotent progress batches", async () => {
  const database = new D1TestDatabase();
  const created = await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  assert.equal(claim.id, created.item.id);
  assert.equal(claim.attempt_count, 1);
  assert.equal(claim.retrieval_cutoff, atSeconds(0).toISOString());

  const pair = [
    {
      idempotency_key: "progress-tool-start-0001",
      type: "tool.started",
      payload: { call_id: "call-1", tool_name: "search_news_v1", tool_version: "v1" },
    },
    {
      idempotency_key: "progress-tool-finish-0001",
      type: "tool.completed",
      payload: {
        call_id: "call-1",
        tool_name: "search_news_v1",
        status: "SUCCEEDED",
        result_sha256: "c".repeat(64),
        evidence_count: 1,
      },
    },
  ];
  const events = await appendAssistantChatEvents(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    events: pair,
    now: atSeconds(2),
  });
  assert.deepEqual(events.map(item => item.sequence), [2, 3]);
  const replay = await appendAssistantChatEvents(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    events: [
      pair[0],
      { ...pair[1], payload: {
        evidence_count: 1,
        result_sha256: "c".repeat(64),
        status: "SUCCEEDED",
        tool_name: "search_news_v1",
        call_id: "call-1",
      } },
    ],
    now: atSeconds(3),
  });
  assert.deepEqual(replay.map(item => item.event_id), events.map(item => item.event_id));

  await assert.rejects(
    appendAssistantChatEvents(database, {
      id: claim.id,
      lease_token: claim.lease_token,
      events: [pair[0]],
      now: atSeconds(3),
    }),
    error => error instanceof AssistantChatInputError && error.code === "INVALID_EVENT_BATCH",
  );
  await assert.rejects(
    appendAssistantChatEvents(database, {
      id: claim.id,
      lease_token: claim.lease_token,
      events: [pair[0], { ...pair[0], idempotency_key: "progress-tool-start-0002" }],
      now: atSeconds(3),
    }),
    /重复/,
  );

  const stream = await listOwnerAssistantTurnEvents(database, {
    ownerId: owner,
    turnId: claim.id,
    afterSequence: 1,
  });
  assert.deepEqual(stream.events.map(item => item.type), ["tool.started", "tool.completed"]);
  assert.equal(await listOwnerAssistantTurnEvents(database, {
    ownerId: otherOwner,
    turnId: claim.id,
  }), null);
});

test("the active worker renews its lease without outliving the turn", async () => {
  const database = new D1TestDatabase();
  await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  const renewed = await renewAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
  }, atSeconds(250));
  assert.equal(renewed.lease_expires_at, atSeconds(550).toISOString());
  assert.equal(renewed.attempt_count, 1);
  assert.equal(await renewAssistantChatTurn(database, {
    id: claim.id,
    lease_token: "wrong-lease-token",
  }, atSeconds(251)), null);
  const events = await appendAssistantChatEvents(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    events: [{
      idempotency_key: "renewed-reasoning-event-0001",
      type: "reasoning.started",
      payload: { reasoning_class: "ANALYTICAL" },
    }],
    now: atSeconds(302),
  });
  assert.equal(events[0].sequence, 2);
  const history = JSON.parse(database.database.prepare(
    "SELECT attempt_history_json FROM assistant_turn_jobs WHERE id=?",
  ).get(claim.id).attempt_history_json);
  assert.deepEqual(history.map(item => item.event), ["CLAIMED", "LEASE_RENEWED"]);

  const lateDatabase = new D1TestDatabase();
  await createTurn(lateDatabase);
  const late = await claimAssistantChatTurn(
    lateDatabase, "worker:late", atSeconds(1_600),
  );
  assert.equal(late.lease_expires_at, atSeconds(1_800).toISOString());
  assert.equal(await renewAssistantChatTurn(lateDatabase, {
    id: late.id,
    lease_token: late.lease_token,
  }, atSeconds(1_801)), null);
  assert.throws(() => lateDatabase.database.prepare(
    "UPDATE assistant_turn_jobs SET lease_expires_at=? WHERE id=?",
  ).run(atSeconds(1_900).toISOString(), late.id), /lease cannot outlive turn/);
});

test("progress reserves enough sequence capacity for the largest final answer", async () => {
  const database = new D1TestDatabase();
  await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  const pairs = Array.from({ length: 116 }, (_, index) => ([
    {
      idempotency_key: `budget-tool-start-${String(index).padStart(4, "0")}`,
      type: "tool.started",
      payload: {
        call_id: `budget-call-${index}`,
        tool_name: "search_news_v1",
        tool_version: "v1",
      },
    },
    {
      idempotency_key: `budget-tool-finish-${String(index).padStart(4, "0")}`,
      type: "tool.completed",
      payload: {
        call_id: `budget-call-${index}`,
        tool_name: "search_news_v1",
        status: "SUCCEEDED",
        result_sha256: "d".repeat(64),
        evidence_count: 0,
      },
    },
  ]));
  for (let index = 0; index < pairs.length; index += 8) {
    await appendAssistantChatEvents(database, {
      id: claim.id,
      lease_token: claim.lease_token,
      events: pairs.slice(index, index + 8).flat(),
      now: atSeconds(2),
    });
  }
  const extraPair = pairs[0].map((event, index) => ({
    ...event,
    idempotency_key: `budget-extra-event-${index}`,
    payload: { ...event.payload, call_id: "budget-extra-call" },
  }));
  await assert.rejects(appendAssistantChatEvents(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    events: extraPair,
    now: atSeconds(2),
  }), error => (
    error instanceof AssistantChatInputError && error.code === "EVENT_BUDGET_EXCEEDED"
  ));

  const answer = [
    ...Array.from({ length: 7 }, () => "a".repeat(3_999)),
    "a".repeat(4_000),
  ].join("\n");
  assert.equal(Buffer.byteLength(answer), 32_000);
  const provenance = directAgentProvenance(claim, answer);
  provenance.budgets.MAX_ACTIVE_CONTEXT_TOKENS = 32_768;
  provenance.budgets.MAX_OUTPUT_TOKENS = 4_096;
  rehash(provenance);
  const completed = await completeAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer,
    model_version: "gemma-4-31b-it",
    content_document: await buildAssistantTextContentDocument(answer),
    provenance,
  }, atSeconds(3));
  assert.equal(completed.status, "ANSWERED");
  assert.equal(completed.event_sequence, 246);
});

test("answer completion atomically persists the canonical final and terminal stream", async () => {
  const database = new D1TestDatabase();
  await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  const answer = "美元实际利率仍是黄金的主要约束，但当前证据不足以给出交易指令。";
  const completed = await completeAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer,
    model_version: "gemma-4-31b-it",
    content_document: await buildAssistantTextContentDocument(answer),
    provenance: directAgentProvenance(claim, answer),
  }, atSeconds(2));
  assert.equal(completed.status, "ANSWERED");
  assert.ok(completed.assistant_message_id);
  assert.equal(completed.event_sequence, 7);

  const messages = await listOwnerAssistantMessages(
    database, owner, claim.conversation_id,
  );
  assert.deepEqual(messages.items.map(item => item.role), ["USER", "ASSISTANT"]);
  assert.equal(messages.items[1].content, answer);
  assert.deepEqual(messages.items[1].content_document.blocks.map(block => block.type), [
    "markdown", "callout",
  ]);
  assert.equal(messages.items[1].provenance.kind, "ASSISTANT_CHAT");
  assert.equal(messages.items[1].provenance.agent.run_sha256,
    directAgentProvenance(claim, answer).run_sha256);

  const stream = await listOwnerAssistantTurnEvents(database, {
    ownerId: owner,
    turnId: claim.id,
  });
  assert.deepEqual(stream.events.map(item => item.type), [
    "conversation.started", "answer.started", "answer.delta",
    "content.block", "content.block",
    "answer.completed", "conversation.completed",
  ]);
  assert.equal(stream.events[5].message_id, completed.assistant_message_id);
  assert.equal(stream.events[5].payload.content_sha256, digest(answer));
  assert.deepEqual(stream.events.slice(3, 5).map(event => event.payload.block_id), [
    "block:answer", "block:boundary",
  ]);

  const conversation = await getOwnerAssistantConversation(
    database, owner, claim.conversation_id,
  );
  assert.equal(conversation.last_activity_at, atSeconds(2).toISOString());
  assert.equal(conversation.title_job_status, "PENDING");
  assert.equal(conversation.active_turn, null);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_title_jobs WHERE conversation_id=?",
  ).get(claim.conversation_id).n, 1);
  assert.equal(await completeAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer,
    model_version: "gemma-4-31b-it",
    provenance: directAgentProvenance(claim, answer),
  }, atSeconds(3)), null);
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_messages WHERE role='ASSISTANT'",
  ).get().n, 1);
});

test("completion rejects forged, secret-bearing, or cross-turn agent provenance", async () => {
  const cases = [
    provenance => ({ ...provenance, run_sha256: "0".repeat(64) }),
    provenance => ({ ...provenance, api_key: "secret", run_sha256: "0".repeat(64) }),
    provenance => {
      provenance.model_routing[0].selected_model_id = "other-model";
      provenance.run_sha256 = digest(canonicalJson({
        ...provenance,
        run_sha256: undefined,
      }));
      return provenance;
    },
  ];
  for (const [index, mutate] of cases.entries()) {
    const database = new D1TestDatabase();
    await createTurn(database, index + 1);
    const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
    const provenance = mutate(directAgentProvenance(claim, "回答"));
    await assert.rejects(
      completeAssistantChatTurn(database, {
        id: claim.id,
        lease_token: claim.lease_token,
        answer: "回答",
        model_version: "gemma-4-31b-it",
        provenance,
      }, atSeconds(2)),
      error => error instanceof AssistantChatInputError,
    );
    assert.equal(database.database.prepare(
      "SELECT count(*) AS n FROM assistant_messages WHERE role='ASSISTANT'",
    ).get().n, 0);
  }
});

test("completion rejects rich content that drifts from the canonical answer", async () => {
  const database = new D1TestDatabase();
  await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  const answer = "结构化输出必须与规范回答一致。";
  const content = await buildAssistantTextContentDocument(answer);
  content.blocks[1].data.body = "被篡改的边界";

  await assert.rejects(completeAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer,
    model_version: "gemma-4-31b-it",
    content_document: content,
    provenance: directAgentProvenance(claim, answer),
  }, atSeconds(2)), error => (
    error instanceof AssistantChatInputError
      && error.code === "INVALID_ASSISTANT_CONTENT"
  ));
  assert.equal(database.database.prepare(
    "SELECT count(*) AS n FROM assistant_messages WHERE role='ASSISTANT'",
  ).get().n, 0);
});

test("completion accepts exact owner-bound native tool receipts", async () => {
  const rejectedDatabase = new D1TestDatabase();
  await createTurn(rejectedDatabase);
  const rejectedClaim = await claimAssistantChatTurn(
    rejectedDatabase, "worker:chat", atSeconds(1),
  );
  const crossOwnerAnswer = "不能接受错误 owner receipt。";
  const crossOwner = toolAgentProvenance(rejectedClaim, crossOwnerAnswer);
  crossOwner.tool_execution[0][0].provenance.actor_fingerprint = "0".repeat(16);
  rehash(crossOwner);
  await assert.rejects(completeAssistantChatTurn(rejectedDatabase, {
    id: rejectedClaim.id,
    lease_token: rejectedClaim.lease_token,
    answer: crossOwnerAnswer,
    model_version: "gemma-4-31b-it",
    provenance: crossOwner,
  }, atSeconds(2)), /工具 receipt/);

  const secretAnswer = "不能接受嵌套敏感字段。";
  const nestedSecret = toolAgentProvenance(rejectedClaim, secretAnswer);
  nestedSecret.tool_execution[0][0].provenance.api_key_value = "must-not-persist";
  rehash(nestedSecret);
  await assert.rejects(completeAssistantChatTurn(rejectedDatabase, {
    id: rejectedClaim.id,
    lease_token: rejectedClaim.lease_token,
    answer: secretAnswer,
    model_version: "gemma-4-31b-it",
    provenance: nestedSecret,
  }, atSeconds(2)), error => (
    error instanceof AssistantChatInputError && error.code === "SECRET_IN_PROVENANCE"
  ));

  const driftedAnswer = toolAgentProvenance(
    rejectedClaim, "这份回执属于另一份答案。",
  );
  await assert.rejects(completeAssistantChatTurn(rejectedDatabase, {
    id: rejectedClaim.id,
    lease_token: rejectedClaim.lease_token,
    answer: "当前答案不能复用另一份证据回执。",
    model_version: "gemma-4-31b-it",
    provenance: driftedAnswer,
  }, atSeconds(2)), error => (
    error instanceof AssistantChatInputError
      && error.code === "INVALID_EVIDENCE_VALIDATION"
  ));

  const database = new D1TestDatabase();
  await createTurn(database);
  const claim = await claimAssistantChatTurn(database, "worker:chat", atSeconds(1));
  const answer = "根据已收录新闻，当前证据仍支持谨慎判断。";
  const completed = await completeAssistantChatTurn(database, {
    id: claim.id,
    lease_token: claim.lease_token,
    answer,
    model_version: "gemma-4-31b-it",
    content_document: await buildAssistantTextContentDocument(
      answer,
      { evidenceIds: ["evidence:tool-1"] },
    ),
    provenance: toolAgentProvenance(claim, answer),
  }, atSeconds(2));
  assert.equal(completed.status, "ANSWERED");
  const stream = await listOwnerAssistantTurnEvents(database, {
    ownerId: owner,
    turnId: claim.id,
  });
  assert.deepEqual(stream.events.find(
    event => event.type === "answer.completed",
  ).payload.evidence_ids, ["evidence:tool-1"]);
});

test("retry, cancellation, lease recovery, and expiry are finite", async () => {
  const database = new D1TestDatabase();
  const created = await createTurn(database);
  const first = await claimAssistantChatTurn(database, "worker:first", atSeconds(1));
  const retried = await failAssistantChatTurn(database, {
    id: first.id,
    lease_token: first.lease_token,
    failure_code: "MODEL_UNAVAILABLE",
  }, atSeconds(2));
  assert.equal(retried.status, "PENDING");
  assert.equal(await claimAssistantChatTurn(database, "worker:early", atSeconds(31)), null);
  const second = await claimAssistantChatTurn(database, "worker:second", atSeconds(32));
  assert.equal(second.id, created.item.id);
  assert.equal(second.attempt_count, 2);
  const cancelled = await cancelOwnerAssistantChatTurn(
    database, owner, second.id, atSeconds(33),
  );
  assert.equal(cancelled.status, "CANCELLED");
  assert.equal(cancelled.failure_code, "USER_CANCELLED");
  assert.throws(() => database.database.prepare(
    "UPDATE assistant_turn_jobs SET status='PENDING',completed_at=NULL WHERE id=?",
  ).run(second.id), /terminal turn is immutable|status transition is invalid/);
  assert.throws(() => database.database.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     ) VALUES ('after-terminal',?,'assistant.event.v1',3,'reasoning.started',NULL,?,'{"reasoning_class":"SIMPLE"}','after-terminal-0001')`,
  ).run(second.id, atSeconds(34).toISOString()), /active turn/);
  assert.equal((await listOwnerAssistantTurnEvents(database, {
    ownerId: owner,
    turnId: second.id,
  })).events.at(-1).type, "cancelled");
  assert.equal(await cancelOwnerAssistantChatTurn(
    database, otherOwner, second.id, atSeconds(34),
  ), null);

  const recoveryDatabase = new D1TestDatabase();
  await createTurn(recoveryDatabase, 10);
  const abandoned = await claimAssistantChatTurn(
    recoveryDatabase, "worker:abandoned", atSeconds(1),
  );
  const recovered = await claimAssistantChatTurn(
    recoveryDatabase, "worker:recovery", atSeconds(302),
  );
  assert.equal(recovered.id, abandoned.id);
  assert.equal(recovered.attempt_count, 2);

  const expiryDatabase = new D1TestDatabase();
  const expiring = await createTurn(expiryDatabase, 20);
  assert.equal(await claimAssistantChatTurn(
    expiryDatabase, "worker:expiry-sweep", atSeconds(1_801),
  ), null);
  assert.equal((await getOwnerAssistantChatTurn(
    expiryDatabase, owner, expiring.item.id,
  )).status, "EXPIRED");
});

test("database triggers keep turn inputs, events, and sequence receipts immutable", async () => {
  const database = new D1TestDatabase();
  const created = await createTurn(database);
  assert.throws(() => database.database.prepare(
    "UPDATE assistant_turn_jobs SET message_hash='changed' WHERE id=?",
  ).run(created.item.id), /inputs are immutable/);
  const oversizedUtf8 = JSON.stringify({ text: "金".repeat(6_000) });
  assert.ok(oversizedUtf8.length < 16_384);
  assert.ok(Buffer.byteLength(oversizedUtf8) > 16_384);
  assert.throws(() => database.database.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     ) VALUES ('oversized',?,'assistant.event.v1',2,'reasoning.started',NULL,?,?,'oversized-event-0001')`,
  ).run(created.item.id, atSeconds(1).toISOString(), oversizedUtf8), /constraint/);
  assert.throws(() => database.database.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     ) VALUES ('gap',?,'assistant.event.v1',3,'reasoning.started',NULL,?,'{"reasoning_class":"SIMPLE"}','gap-event-0000001')`,
  ).run(created.item.id, atSeconds(1).toISOString()), /sequence must be contiguous/);
  assert.throws(() => database.database.prepare(
    "UPDATE assistant_turn_events SET payload_json='{}' WHERE turn_id=?",
  ).run(created.item.id), /events are immutable/);
  assert.throws(() => database.database.prepare(
    "DELETE FROM assistant_turn_jobs WHERE id=?",
  ).run(created.item.id), /jobs are immutable/);
  assert.throws(() => database.database.prepare(
    `INSERT INTO assistant_messages (
     id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
     ) VALUES ('forged-rich-message',?,'ASSISTANT','forged',?,'{}','ASSISTANT_CHAT','forged-rich')`,
  ).run(created.item.conversation_id, atSeconds(2).toISOString()), /content contract/);
});
