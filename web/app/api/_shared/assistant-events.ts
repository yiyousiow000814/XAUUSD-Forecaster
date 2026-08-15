export const ASSISTANT_EVENT_PROTOCOL_VERSION = "assistant.event.v1";
export const MAX_ASSISTANT_EVENTS_PER_TURN = 256;
export const MAX_ASSISTANT_EVENT_PAYLOAD_BYTES = 16_384;
export const MAX_ASSISTANT_ANSWER_DELTA_BYTES = 4_096;
export const MAX_ASSISTANT_PRESENTATION_BYTES = 65_536;

export const assistantEventTypes = [
  "conversation.started",
  "reasoning.started",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "retrieval.started",
  "retrieval.completed",
  "answer.started",
  "answer.delta",
  "content.block",
  "answer.completed",
  "conversation.completed",
  "error",
  "cancelled",
] as const;

export type AssistantEventType = typeof assistantEventTypes[number];

export type AssistantEventEnvelope = {
  protocol: typeof ASSISTANT_EVENT_PROTOCOL_VERSION;
  event_id: string;
  conversation_id: string;
  user_turn_id: string;
  message_id: string | null;
  sequence: number;
  type: AssistantEventType;
  occurred_at: string;
  payload: Record<string, unknown>;
};

export class AssistantEventContractError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const identifier = /^[A-Za-z0-9][A-Za-z0-9:._@/-]{0,127}$/;
const toolName = /^[a-z][a-z0-9_]{1,54}_v[1-9][0-9]*$/;
const version = /^[A-Za-z][A-Za-z0-9._-]{0,63}$/;
const errorCode = /^[A-Z][A-Z0-9_]{2,63}$/;
const sha256 = /^[0-9a-f]{64}$/;
const canonicalTimestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const reasoningClasses = new Set(["SIMPLE", "ANALYTICAL", "TOOL_HEAVY"]);
const toolFailureStates = new Set(["FAILED", "REJECTED", "TIMED_OUT"]);
const eventTypeSet = new Set<string>(assistantEventTypes);

const fail = (code: string, message: string): never => {
  throw new AssistantEventContractError(code, message);
};

const strictIdentifier = (value: unknown, field: string, payload = false) => {
  if (typeof value !== "string" || !identifier.test(value)) {
    fail(
      payload ? "INVALID_EVENT_PAYLOAD" : "INVALID_EVENT_ENVELOPE",
      `Assistant event ${field} is invalid`,
    );
  }
  return value;
};

const assertStrictJson = (value: unknown, seen = new Set<object>()): void => {
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("INVALID_EVENT_PAYLOAD", "Event payload is not strict JSON");
    return;
  }
  if (typeof value !== "object") {
    fail("INVALID_EVENT_PAYLOAD", "Event payload is not strict JSON");
  }
  if (seen.has(value)) fail("INVALID_EVENT_PAYLOAD", "Event payload is recursive");
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach(item => assertStrictJson(item, seen));
  } else {
    for (const [key, item] of Object.entries(value)) {
      if (!key) fail("INVALID_EVENT_PAYLOAD", "Event payload key is invalid");
      assertStrictJson(item, seen);
    }
  }
  seen.delete(value);
};

const strictObject = (value: unknown, expected: readonly string[]) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("INVALID_EVENT_PAYLOAD", "Assistant event payload shape is invalid");
  }
  const raw = value as Record<string, unknown>;
  const keys = Object.keys(raw).sort();
  if (keys.join("|") !== [...expected].sort().join("|")) {
    fail("INVALID_EVENT_PAYLOAD", "Assistant event payload shape is invalid");
  }
  assertStrictJson(raw);
  const serialized = JSON.stringify(raw);
  if (new TextEncoder().encode(serialized).length > MAX_ASSISTANT_EVENT_PAYLOAD_BYTES) {
    fail("EVENT_PAYLOAD_BUDGET_EXCEEDED", "Assistant event payload is too large");
  }
  return JSON.parse(serialized) as Record<string, unknown>;
};

const strictCount = (value: unknown, field: string, maximum: number) => {
  if (!Number.isInteger(value) || Number(value) < 0 || Number(value) > maximum) {
    fail("INVALID_EVENT_PAYLOAD", `Assistant event ${field} is invalid`);
  }
  return Number(value);
};

const validateToolIdentity = (payload: Record<string, unknown>) => {
  strictIdentifier(payload.call_id, "call_id", true);
  if (typeof payload.tool_name !== "string" || !toolName.test(payload.tool_name)) {
    fail("INVALID_EVENT_PAYLOAD", "Assistant event tool_name is invalid");
  }
};

const validatePayload = (
  type: AssistantEventType,
  value: unknown,
): Record<string, unknown> => {
  if (new Set<AssistantEventType>([
    "conversation.started", "answer.started", "conversation.completed",
  ]).has(type)) return strictObject(value, []);
  if (type === "reasoning.started") {
    const payload = strictObject(value, ["reasoning_class"]);
    if (!reasoningClasses.has(String(payload.reasoning_class))) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant reasoning class is invalid");
    }
    return payload;
  }
  if (type === "tool.started") {
    const payload = strictObject(value, ["call_id", "tool_name", "tool_version"]);
    validateToolIdentity(payload);
    if (typeof payload.tool_version !== "string" || !/^v[1-9][0-9]*$/.test(payload.tool_version)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant event tool_version is invalid");
    }
    return payload;
  }
  if (type === "tool.completed") {
    const payload = strictObject(value, [
      "call_id", "tool_name", "status", "result_sha256", "evidence_count",
    ]);
    validateToolIdentity(payload);
    if (payload.status !== "SUCCEEDED"
      || typeof payload.result_sha256 !== "string"
      || !sha256.test(payload.result_sha256)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant tool completion is invalid");
    }
    strictCount(payload.evidence_count, "evidence_count", 100);
    return payload;
  }
  if (type === "tool.failed") {
    const payload = strictObject(value, ["call_id", "tool_name", "status", "error_code"]);
    validateToolIdentity(payload);
    if (!toolFailureStates.has(String(payload.status))
      || typeof payload.error_code !== "string"
      || !errorCode.test(payload.error_code)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant tool failure is invalid");
    }
    return payload;
  }
  if (type === "retrieval.started") {
    const payload = strictObject(value, ["operation_id", "tool_name"]);
    strictIdentifier(payload.operation_id, "operation_id", true);
    if (typeof payload.tool_name !== "string" || !toolName.test(payload.tool_name)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant retrieval tool_name is invalid");
    }
    return payload;
  }
  if (type === "retrieval.completed") {
    const payload = strictObject(value, [
      "operation_id", "evidence_count", "source_mode", "result_sha256",
    ]);
    strictIdentifier(payload.operation_id, "operation_id", true);
    strictCount(payload.evidence_count, "evidence_count", 100);
    if (typeof payload.source_mode !== "string" || !version.test(payload.source_mode)
      || typeof payload.result_sha256 !== "string" || !sha256.test(payload.result_sha256)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant retrieval completion is invalid");
    }
    return payload;
  }
  if (type === "answer.delta") {
    const payload = strictObject(value, ["text"]);
    if (typeof payload.text !== "string" || !payload.text
      || new TextEncoder().encode(payload.text).length > MAX_ASSISTANT_ANSWER_DELTA_BYTES) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant answer delta is invalid");
    }
    return payload;
  }
  if (type === "content.block") {
    const payload = strictObject(value, [
      "block_id", "block_type", "block_version", "content_sha256",
    ]);
    strictIdentifier(payload.block_id, "block_id", true);
    for (const field of ["block_type", "block_version"]) {
      if (typeof payload[field] !== "string" || !version.test(payload[field])) {
        fail("INVALID_EVENT_PAYLOAD", `Assistant event ${field} is invalid`);
      }
    }
    if (typeof payload.content_sha256 !== "string" || !sha256.test(payload.content_sha256)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant content block hash is invalid");
    }
    return payload;
  }
  if (type === "answer.completed") {
    const payload = strictObject(value, ["content_sha256", "evidence_ids"]);
    if (typeof payload.content_sha256 !== "string" || !sha256.test(payload.content_sha256)
      || !Array.isArray(payload.evidence_ids)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant answer completion is invalid");
    }
    const evidence = payload.evidence_ids;
    if (evidence.length > 20 || new Set(evidence).size !== evidence.length
      || evidence.some(item => typeof item !== "string" || !identifier.test(item))) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant answer evidence is invalid");
    }
    return payload;
  }
  if (type === "error") {
    const payload = strictObject(value, ["code", "retryable", "recovery_key"]);
    if (typeof payload.code !== "string" || !errorCode.test(payload.code)
      || typeof payload.retryable !== "boolean") {
      fail("INVALID_EVENT_PAYLOAD", "Assistant public error is invalid");
    }
    if (payload.recovery_key !== null) {
      strictIdentifier(payload.recovery_key, "recovery_key", true);
    }
    return payload;
  }
  if (type === "cancelled") {
    const payload = strictObject(value, ["code"]);
    if (typeof payload.code !== "string" || !errorCode.test(payload.code)) {
      fail("INVALID_EVENT_PAYLOAD", "Assistant cancellation is invalid");
    }
    return payload;
  }
  return fail("INVALID_EVENT_TYPE", "Assistant event type is invalid");
};

const canonicalTime = (value: unknown) => {
  if (typeof value !== "string" || !canonicalTimestamp.test(value)) {
    fail("INVALID_EVENT_TIME", "Assistant event time must be canonical UTC milliseconds");
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString() !== value) {
    fail("INVALID_EVENT_TIME", "Assistant event time is invalid");
  }
  return value;
};

export function parseAssistantEvent(value: unknown): AssistantEventEnvelope {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("INVALID_EVENT_ENVELOPE", "Assistant event envelope shape is invalid");
  }
  const raw = value as Record<string, unknown>;
  const expected = [
    "protocol", "event_id", "conversation_id", "user_turn_id", "message_id",
    "sequence", "type", "occurred_at", "payload",
  ].sort();
  if (Object.keys(raw).sort().join("|") !== expected.join("|")) {
    fail("INVALID_EVENT_ENVELOPE", "Assistant event envelope shape is invalid");
  }
  if (raw.protocol !== ASSISTANT_EVENT_PROTOCOL_VERSION) {
    fail("UNSUPPORTED_EVENT_PROTOCOL", "Assistant event protocol is unsupported");
  }
  if (typeof raw.type !== "string" || !eventTypeSet.has(raw.type)) {
    fail("INVALID_EVENT_TYPE", "Assistant event type is invalid");
  }
  const type = raw.type as AssistantEventType;
  const messageId = raw.message_id === null
    ? null : strictIdentifier(raw.message_id, "message_id");
  if (!Number.isInteger(raw.sequence)
    || Number(raw.sequence) < 1
    || Number(raw.sequence) > MAX_ASSISTANT_EVENTS_PER_TURN) {
    fail("INVALID_EVENT_SEQUENCE", "Assistant event sequence is invalid");
  }
  if ((type === "answer.completed") !== (messageId !== null)) {
    fail("INVALID_EVENT_MESSAGE", "Only answer.completed names a canonical message");
  }
  return {
    protocol: ASSISTANT_EVENT_PROTOCOL_VERSION,
    event_id: strictIdentifier(raw.event_id, "event_id"),
    conversation_id: strictIdentifier(raw.conversation_id, "conversation_id"),
    user_turn_id: strictIdentifier(raw.user_turn_id, "user_turn_id"),
    message_id: messageId,
    sequence: Number(raw.sequence),
    type,
    occurred_at: canonicalTime(raw.occurred_at),
    payload: validatePayload(type, raw.payload),
  };
}

type StreamPhase = "NEW" | "OPEN" | "ANSWERING" | "ANSWERED"
  | "COMPLETED" | "FAILED" | "CANCELLED";

export class AssistantEventSequence {
  #events: AssistantEventEnvelope[] = [];
  #phase: StreamPhase = "NEW";
  #eventIds = new Set<string>();
  #toolCalls = new Map<string, { name: string; completed: boolean }>();
  #retrievals = new Map<string, boolean>();
  #reasoningStarted = false;
  #presentationBytes = 0;

  get events() {
    return this.#events.map(event => structuredClone(event));
  }

  get terminal() {
    return new Set<StreamPhase>(["COMPLETED", "FAILED", "CANCELLED"]).has(this.#phase);
  }

  append(value: unknown) {
    const event = parseAssistantEvent(value);
    if (this.#events.length >= MAX_ASSISTANT_EVENTS_PER_TURN) {
      fail("EVENT_COUNT_BUDGET_EXCEEDED", "Assistant event count is exhausted");
    }
    if (event.sequence !== this.#events.length + 1) {
      fail("INVALID_EVENT_SEQUENCE", "Assistant event sequence is not contiguous");
    }
    if (this.#eventIds.has(event.event_id)) {
      fail("DUPLICATE_EVENT_ID", "Assistant event ID is duplicated");
    }
    const first = this.#events[0];
    if (first && (event.conversation_id !== first.conversation_id
      || event.user_turn_id !== first.user_turn_id)) {
      fail("EVENT_OWNERSHIP_MISMATCH", "Assistant event stream identity changed");
    }
    this.#advance(event);
    this.#events.push(event);
    this.#eventIds.add(event.event_id);
  }

  #advance(event: AssistantEventEnvelope) {
    if (this.terminal) fail("EVENT_AFTER_TERMINAL", "Event follows a terminal event");
    if (this.#phase === "NEW") {
      if (event.type !== "conversation.started") {
        fail("INVALID_EVENT_ORDER", "Stream must start with conversation.started");
      }
      this.#phase = "OPEN";
      return;
    }
    if (event.type === "conversation.started") {
      fail("INVALID_EVENT_ORDER", "conversation.started is duplicated");
    }
    if (event.type === "error" || event.type === "cancelled") {
      if (this.#phase === "ANSWERED") {
        fail("INVALID_EVENT_ORDER", "A persisted answer cannot become terminal progress");
      }
      this.#phase = event.type === "error" ? "FAILED" : "CANCELLED";
      return;
    }
    if (this.#phase === "OPEN") {
      this.#advanceOpen(event);
      return;
    }
    if (this.#phase === "ANSWERING") {
      if (event.type === "answer.delta") {
        this.#addPresentationBytes(new TextEncoder().encode(String(event.payload.text)).length);
        return;
      }
      if (event.type === "content.block") {
        this.#addPresentationBytes(new TextEncoder().encode(JSON.stringify(event.payload)).length);
        return;
      }
      if (event.type === "answer.completed") {
        this.#phase = "ANSWERED";
        return;
      }
      fail("INVALID_EVENT_ORDER", "Assistant answer event order is invalid");
    }
    if (this.#phase === "ANSWERED") {
      if (event.type !== "conversation.completed") {
        fail("INVALID_EVENT_ORDER", "Assistant answer must end the conversation stream");
      }
      this.#phase = "COMPLETED";
      return;
    }
    fail("INVALID_EVENT_ORDER", "Assistant event phase is invalid");
  }

  #advanceOpen(event: AssistantEventEnvelope) {
    const payload = event.payload;
    if (event.type === "reasoning.started") {
      if (this.#reasoningStarted) fail("INVALID_EVENT_ORDER", "reasoning.started is duplicated");
      this.#reasoningStarted = true;
      return;
    }
    if (event.type === "tool.started") {
      const callId = String(payload.call_id);
      if (this.#toolCalls.has(callId)) fail("INVALID_EVENT_ORDER", "Tool call is duplicated");
      this.#toolCalls.set(callId, { name: String(payload.tool_name), completed: false });
      return;
    }
    if (event.type === "tool.completed" || event.type === "tool.failed") {
      const callId = String(payload.call_id);
      const expected = this.#toolCalls.get(callId);
      if (!expected || expected.completed || expected.name !== payload.tool_name) {
        fail("INVALID_EVENT_ORDER", "Tool completion has no active call");
      }
      expected.completed = true;
      return;
    }
    if (event.type === "retrieval.started") {
      const operationId = String(payload.operation_id);
      if (this.#retrievals.has(operationId)) fail("INVALID_EVENT_ORDER", "Retrieval is duplicated");
      this.#retrievals.set(operationId, false);
      return;
    }
    if (event.type === "retrieval.completed") {
      const operationId = String(payload.operation_id);
      if (this.#retrievals.get(operationId) !== false) {
        fail("INVALID_EVENT_ORDER", "Retrieval has no active operation");
      }
      this.#retrievals.set(operationId, true);
      return;
    }
    if (event.type === "answer.started") {
      if ([...this.#toolCalls.values()].some(item => !item.completed)
        || [...this.#retrievals.values()].some(completed => !completed)) {
        fail("INVALID_EVENT_ORDER", "Answer started before progress completed");
      }
      this.#phase = "ANSWERING";
      return;
    }
    fail("INVALID_EVENT_ORDER", "Assistant progress event order is invalid");
  }

  #addPresentationBytes(amount: number) {
    this.#presentationBytes += amount;
    if (this.#presentationBytes > MAX_ASSISTANT_PRESENTATION_BYTES) {
      fail("PRESENTATION_BUDGET_EXCEEDED", "Assistant presentation stream is too large");
    }
  }
}

export function encodeAssistantSse(value: unknown) {
  const event = parseAssistantEvent(value);
  const data = JSON.stringify(event);
  return `id: ${event.sequence}\nevent: ${event.type}\ndata: ${data}\n\n`;
}
