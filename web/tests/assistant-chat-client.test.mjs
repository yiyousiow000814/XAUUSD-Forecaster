import assert from "node:assert/strict";
import test from "node:test";

import {
  AssistantClientError,
  assistantAnswerDraft,
  assistantProgressItems,
  fetchAssistantConversations,
  mergeAssistantMessages,
  planAssistantConversationSelection,
  parseAssistantSse,
  replayAssistantEvents,
} from "../app/_lib/assistant-chat-client.ts";
import {
  assistantPreviewConversations,
  assistantPreviewEvents,
  assistantPreviewMessages,
} from "../app/_lib/assistant-preview-fixture.ts";
import { encodeAssistantSse } from "../app/api/_shared/assistant-events.ts";

test("conversation reselection refreshes in place instead of blanking the transcript", () => {
  assert.equal(
    planAssistantConversationSelection("conversation-1", "conversation-1", false),
    "REFRESH_CURRENT",
  );
  assert.equal(
    planAssistantConversationSelection("conversation-1", "conversation-1", true),
    "REFRESH_CURRENT",
  );
  assert.equal(
    planAssistantConversationSelection("conversation-1", "conversation-2", false),
    "LOAD_REMOTE",
  );
  assert.equal(
    planAssistantConversationSelection(null, "conversation-preview-rates", true),
    "LOAD_PREVIEW",
  );
});

test("finite Assistant SSE replay validates ids, cursors, and public progress", async () => {
  const body = assistantPreviewEvents.map(encodeAssistantSse).join("");
  const parsed = parseAssistantSse(body.replaceAll("\n", "\r\n"));
  assert.deepEqual(parsed, assistantPreviewEvents);

  let request = null;
  const controller = new AbortController();
  const replay = await replayAssistantEvents(
    "turn-preview-rates",
    0,
    controller.signal,
    async (input, init) => {
      request = { input, init };
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "X-Assistant-Event-Protocol": "assistant.event.v1",
          "X-Assistant-Turn-Status": "ANSWERED",
          "X-Assistant-Next-Sequence": "14",
          "X-Assistant-Has-More": "false",
        },
      });
    },
  );
  assert.equal(request.input, "/api/assistant-chat?mode=events&id=turn-preview-rates&after=0&limit=100");
  assert.equal(new Headers(request.init.headers).get("last-event-id"), "0");
  assert.equal(request.init.credentials, "same-origin");
  assert.equal(replay.turn_status, "ANSWERED");
  assert.equal(replay.next_sequence, 14);
  assert.equal(replay.has_more, false);
  assert.equal(replay.events.length, 14);
  assert.deepEqual(
    replay.events.filter(event => event.type === "content.block")
      .map(event => event.payload.block_type),
    ["markdown", "metric", "news_card", "news_card", "table", "callout"],
  );

  const progress = assistantProgressItems(replay.events);
  assert.deepEqual(progress.map(item => item.state), [
    "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED",
  ]);
  assert.match(progress[2].detail, /2 条证据/);
  assert.equal(progress[1].label, "证据检索规划已完成");
  assert.equal(progress[3].label, "回答整理已完成");
  assert.equal(progress[3].detail, "最终回答已通过持久化门槛");
  assert.equal(JSON.stringify(progress).includes("chain-of-thought"), false);
  assert.equal(assistantAnswerDraft(replay.events), "实际利率是持有无息黄金的机会成本。");

  const parallelProgress = assistantProgressItems([
    ...assistantPreviewEvents.slice(0, 3),
    {
      ...assistantPreviewEvents[2],
      event_id: "event-preview-parallel-tool",
      sequence: 4,
      payload: { ...assistantPreviewEvents[2].payload, call_id: "call-preview-market" },
    },
  ]);
  assert.deepEqual(parallelProgress.map(item => item.state), [
    "COMPLETED", "COMPLETED", "ACTIVE", "ACTIVE",
  ]);
});

test("Assistant SSE rejects transport identity drift and sequence gaps", () => {
  const valid = encodeAssistantSse(assistantPreviewEvents[0]);
  assert.throws(
    () => parseAssistantSse(valid.replace("id: 1", "id: 2")),
    error => error instanceof AssistantClientError
      && error.code === "INVALID_ASSISTANT_STREAM",
  );
  assert.throws(
    () => parseAssistantSse(valid.replace("id: 1\n", "")),
    /身份不一致/,
  );
  const gap = [assistantPreviewEvents[0], {
    ...assistantPreviewEvents[1], sequence: 3,
  }].map(event => encodeAssistantSse(event)).join("");
  assert.throws(() => parseAssistantSse(gap), /序号不连续/);
  assert.equal(assistantAnswerDraft([
    assistantPreviewEvents[4], assistantPreviewEvents[5], {
      ...assistantPreviewEvents[7],
      type: "cancelled",
      payload: { reason: "USER_CANCELLED" },
    },
  ]), null);
});

test("conversation transport preserves active-turn recovery and Preview labeling", async () => {
  const fixture = assistantPreviewConversations[0];
  const result = await fetchAssistantConversations(false, async () => new Response(
    JSON.stringify({ items: [{
      ...fixture,
      active_turn: {
        id: "turn-recoverable",
        status: "PROCESSING",
        event_sequence: 4,
        created_at: "2026-08-15T10:00:00.000Z",
      },
    }], preview: true }),
    {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "X-Aurum-Preview": "synthetic-empty-assistant",
      },
    },
  ));
  assert.equal(result.preview, true);
  assert.deepEqual(result.items[0].active_turn, {
    id: "turn-recoverable",
    status: "PROCESSING",
    event_sequence: 4,
    created_at: "2026-08-15T10:00:00.000Z",
  });
});

test("Access login HTML is never accepted as an Assistant API response", async () => {
  const loginPage = () => new Response("<!doctype html><title>Access login</title>", {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
  await assert.rejects(
    fetchAssistantConversations(false, async () => loginPage()),
    error => error instanceof AssistantClientError
      && error.code === "ACCESS_LOGIN_REQUIRED"
      && error.status === 401,
  );
  await assert.rejects(
    replayAssistantEvents(
      "turn-preview-rates", 0, new AbortController().signal,
      async () => loginPage(),
    ),
    error => error instanceof AssistantClientError
      && error.code === "ACCESS_LOGIN_REQUIRED"
      && error.status === 401,
  );
});

test("message paging deduplicates immutable history and rejects changed copies", () => {
  const messages = assistantPreviewMessages("conversation-preview-rates");
  assert.deepEqual(
    mergeAssistantMessages([messages[1]], [messages[0], messages[1]])
      .map(message => message.id),
    ["message-preview-user-1", "message-preview-assistant-1"],
  );
  assert.throws(
    () => mergeAssistantMessages(messages, [{
      ...messages[0], content: "changed historical content",
    }]),
    error => error instanceof AssistantClientError
      && error.code === "IMMUTABLE_MESSAGE_CHANGED",
  );
});
