import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AssistantEventContractError,
  AssistantEventSequence,
  MAX_ASSISTANT_ANSWER_DELTA_BYTES,
  encodeAssistantSse,
  parseAssistantEvent,
} from "../app/api/_shared/assistant-events.ts";

const fixture = () => JSON.parse(readFileSync(
  new URL("../../tests/fixtures/assistant_event_v1.json", import.meta.url),
  "utf8",
));

test("shared v1 fixture is a complete bounded terminal sequence", () => {
  const sequence = new AssistantEventSequence();
  for (const event of fixture()) sequence.append(event);

  assert.equal(sequence.terminal, true);
  assert.deepEqual(sequence.events.map(event => event.sequence), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  assert.equal(sequence.events.at(-2).message_id, "message-assistant-1");
  const copied = sequence.events;
  copied.at(-2).payload.evidence_ids.length = 0;
  assert.deepEqual(sequence.events.at(-2).payload.evidence_ids, ["news:1", "news:2"]);
});

test("SSE uses sequence resume ids and one strict JSON envelope", () => {
  const event = parseAssistantEvent(fixture()[7]);
  const encoded = encodeAssistantSse(event);

  assert.match(encoded, /^id: 8\nevent: answer\.delta\ndata: \{/);
  assert.equal(encoded.endsWith("\n\n"), true);
  assert.equal(encoded.includes("\r"), false);
  assert.deepEqual(JSON.parse(encoded.split("data: ")[1]), event);
  assert.equal(encoded.includes("private reasoning"), false);
});

test("envelopes reject protocol drift, hidden fields, and private reasoning", () => {
  const invalid = [
    { value: { ...fixture()[1], protocol: "assistant.event.v0" }, code: "UNSUPPORTED_EVENT_PROTOCOL" },
    { value: { ...fixture()[1], sequence: true }, code: "INVALID_EVENT_SEQUENCE" },
    { value: { ...fixture()[1], extra: "hidden" }, code: "INVALID_EVENT_ENVELOPE" },
    {
      value: { ...fixture()[1], payload: { reasoning_class: "ANALYTICAL", reasoning: "private" } },
      code: "INVALID_EVENT_PAYLOAD",
    },
    { value: { ...fixture()[1], message_id: "message-1" }, code: "INVALID_EVENT_MESSAGE" },
  ];
  for (const item of invalid) {
    assert.throws(
      () => parseAssistantEvent(item.value),
      error => error instanceof AssistantEventContractError && error.code === item.code,
    );
  }
});

test("non-finite and oversized deltas fail closed", () => {
  const base = fixture()[7];
  assert.throws(
    () => parseAssistantEvent({ ...base, payload: { text: Number.NaN } }),
    error => error instanceof AssistantEventContractError
      && error.code === "INVALID_EVENT_PAYLOAD",
  );
  assert.throws(
    () => parseAssistantEvent({
      ...base,
      payload: { text: "x".repeat(MAX_ASSISTANT_ANSWER_DELTA_BYTES + 1) },
    }),
    error => error instanceof AssistantEventContractError
      && error.code === "INVALID_EVENT_PAYLOAD",
  );
});

test("event timestamps are exact canonical UTC milliseconds", () => {
  const base = fixture()[0];
  for (const occurredAt of [
    "2026-02-30T10:00:00.000Z",
    "2026-08-15T18:00:00.000+08:00",
    "2026-08-15T10:00:00Z",
  ]) {
    assert.throws(
      () => parseAssistantEvent({ ...base, occurred_at: occurredAt }),
      error => error.code === "INVALID_EVENT_TIME",
    );
  }
});

test("sequence rejects gaps, identity changes, unfinished tools, and terminal replay", () => {
  const events = fixture();
  const gap = new AssistantEventSequence();
  assert.throws(
    () => gap.append({ ...events[0], sequence: 2 }),
    error => error.code === "INVALID_EVENT_SEQUENCE",
  );

  const changed = new AssistantEventSequence();
  changed.append(events[0]);
  assert.throws(
    () => changed.append({ ...events[1], conversation_id: "conversation-2" }),
    error => error.code === "EVENT_OWNERSHIP_MISMATCH",
  );

  const activeTool = new AssistantEventSequence();
  for (const event of events.slice(0, 6)) {
    if (event.type === "tool.completed") continue;
    activeTool.append({ ...event, sequence: activeTool.events.length + 1 });
  }
  assert.throws(
    () => activeTool.append({ ...events[6], sequence: activeTool.events.length + 1 }),
    error => error.code === "INVALID_EVENT_ORDER",
  );

  const terminal = new AssistantEventSequence();
  for (const event of events) terminal.append(event);
  assert.throws(
    () => terminal.append({ ...events[7], event_id: "event-after-terminal", sequence: 12 }),
    error => error.code === "EVENT_AFTER_TERMINAL",
  );
});
