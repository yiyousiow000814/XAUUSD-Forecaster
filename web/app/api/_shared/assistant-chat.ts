import {
  MAX_ASSISTANT_CONTENT_BLOCKS,
  verifyAssistantContentDocument,
} from "./assistant-content";
import { parseAssistantEvidenceReceipt } from "./assistant-evidence";
import {
  ASSISTANT_ACTIVE_TURN_STATUSES_SQL,
  automaticAssistantTitleStatements,
  provisionalAssistantTitle,
} from "./assistant-conversations";
import {
  ASSISTANT_EVENT_PROTOCOL_VERSION,
  AssistantEventSequence,
  type AssistantEventEnvelope,
  type AssistantEventType,
  MAX_ASSISTANT_EVENTS_PER_TURN,
  parseAssistantEvent,
} from "./assistant-events";
import { scheduleAssistantCompaction } from "./assistant-memory";
import {
  isAssistantModelIdentifier,
  parseAssistantRoutingProvenance,
} from "./assistant-routing";

export const ASSISTANT_CHAT_LIMITS = {
  activePerOwner: 2,
  activeGlobal: 10,
  admittedPerOwnerPerMinute: 5,
  eventReplaySize: 100,
  eventBatchSize: 16,
  maxAttempts: 3,
  turnTtlMs: 30 * 60 * 1_000,
  leaseMs: 5 * 60 * 1_000,
  maxMessageBytes: 16_000,
  maxAnswerBytes: 32_000,
  maxProvenanceBytes: 256_000,
} as const;

const ANSWER_DELTA_BYTES = 4_096;
const MAX_COMPLETION_EVENTS = Math.ceil(
  ASSISTANT_CHAT_LIMITS.maxAnswerBytes / ANSWER_DELTA_BYTES,
) + MAX_ASSISTANT_CONTENT_BLOCKS + 3;

export type AssistantChatStatus =
  | "PENDING"
  | "PROCESSING"
  | "ANSWERED"
  | "FAILED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELLED";

type AssistantTurnRow = Record<string, unknown> & {
  id: string;
  owner_id: string;
  conversation_id: string;
  user_message_id: string;
  idempotency_key: string;
  message_hash: string;
  status: AssistantChatStatus;
  event_sequence: number;
  available_at: string;
  expires_at: string;
  lease_token: string | null;
  lease_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
  assistant_message_id: string | null;
  failure_code: string | null;
  cancel_requested: number;
  created_at: string;
  completed_at: string | null;
};

type AssistantEventRow = Record<string, unknown> & {
  id: string;
  turn_id: string;
  protocol: string;
  sequence: number;
  type: string;
  message_id: string | null;
  occurred_at: string;
  payload_json: string;
  idempotency_key: string;
  conversation_id: string;
};

export type PublicAssistantChatTurn = {
  id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  conversation_title: string | null;
  status: AssistantChatStatus;
  event_sequence: number;
  attempt_count: number;
  failure_code: string | null;
  created_at: string;
  completed_at: string | null;
};

export class AssistantChatInputError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const objectId = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const idempotencyKey = /^[A-Za-z0-9._:-]{16,128}$/;
const errorCode = /^[A-Z][A-Z0-9_]{2,63}$/;
const sha256 = /^[0-9a-f]{64}$/;
const toolName = /^[a-z][a-z0-9_]{1,54}_v[1-9][0-9]*$/;
const activeStatuses = ASSISTANT_ACTIVE_TURN_STATUSES_SQL;

const hasUnsafeTextControl = (value: string) => [...value].some(character => (
  character.charCodeAt(0) === 0
  || character.charCodeAt(0) === 11
  || character.charCodeAt(0) === 12
));

const inputError = (code: string, message: string): never => {
  throw new AssistantChatInputError(code, message);
};

export function parseAssistantChatMessage(value: unknown) {
  if (typeof value !== "string") inputError("INVALID_MESSAGE", "消息内容无效");
  const message = value
    .normalize("NFKC")
    .replace(/\r\n?/g, "\n")
    .trim();
  const bytes = new TextEncoder().encode(message).length;
  if (!message || bytes > ASSISTANT_CHAT_LIMITS.maxMessageBytes
    || hasUnsafeTextControl(message)) {
    inputError("INVALID_MESSAGE", "消息内容需要保持在允许长度内");
  }
  return message;
}

export function parseAssistantChatIdempotencyKey(value: string | null) {
  const key = value?.trim() ?? "";
  if (!idempotencyKey.test(key)) {
    inputError("INVALID_IDEMPOTENCY_KEY", "缺少有效的 Idempotency-Key");
  }
  return key;
}

const strictObjectId = (value: unknown, field: string) => {
  if (typeof value !== "string" || !objectId.test(value)) {
    inputError("INVALID_IDENTIFIER", `${field} 无效`);
  }
  return value;
};

const hexDigest = async (value: string) => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
};

const parsedObject = (value: unknown) => {
  if (typeof value !== "string") return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
};

const publicTurn = (row: Record<string, unknown>): PublicAssistantChatTurn => ({
  id: String(row.id),
  conversation_id: String(row.conversation_id),
  user_message_id: String(row.user_message_id),
  assistant_message_id: typeof row.assistant_message_id === "string"
    ? row.assistant_message_id : null,
  conversation_title: typeof row.conversation_title === "string"
    ? row.conversation_title : null,
  status: String(row.status) as AssistantChatStatus,
  event_sequence: Number(row.event_sequence),
  attempt_count: Number(row.attempt_count),
  failure_code: typeof row.failure_code === "string" ? row.failure_code : null,
  created_at: String(row.created_at),
  completed_at: typeof row.completed_at === "string" ? row.completed_at : null,
});

const turnSelect = `SELECT turn.*,conversation.title AS conversation_title
  FROM assistant_turn_jobs turn
  JOIN assistant_conversations conversation ON conversation.id=turn.conversation_id`;

export async function getOwnerAssistantChatTurn(
  binding: D1Database,
  ownerId: string,
  turnId: string,
) {
  const row = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.id=?`,
  ).bind(ownerId, turnId).first<AssistantTurnRow>();
  return row ? publicTurn(row) : null;
}

export type CreateAssistantChatTurnOutcome =
  | { kind: "CREATED" | "EXISTING"; item: PublicAssistantChatTurn }
  | { kind: "CONFLICT" | "CAPACITY" | "BUSY" | "NOT_FOUND" };

export async function createAssistantChatTurn(
  binding: D1Database,
  input: {
    ownerId: string;
    idempotencyKey: string;
    message: string;
    conversationId?: string | null;
    now?: Date;
  },
): Promise<CreateAssistantChatTurnOutcome> {
  const timestamp = (input.now ?? new Date()).toISOString();
  const rateSince = new Date(Date.parse(timestamp) - 60_000).toISOString();
  const expiresAt = new Date(
    Date.parse(timestamp) + ASSISTANT_CHAT_LIMITS.turnTtlMs,
  ).toISOString();
  const conversationId = input.conversationId
    ? strictObjectId(input.conversationId, "conversation_id")
    : crypto.randomUUID();
  const normalizedMessage = parseAssistantChatMessage(input.message);
  const key = parseAssistantChatIdempotencyKey(input.idempotencyKey);
  const messageHash = await hexDigest(
    `${input.conversationId ?? "NEW"}\n${normalizedMessage}`,
  );
  const replay = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.idempotency_key=?`,
  ).bind(input.ownerId, key).first<AssistantTurnRow>();
  if (replay) {
    return replay.message_hash === messageHash
      && (!input.conversationId || replay.conversation_id === conversationId)
      ? { kind: "EXISTING", item: publicTurn(replay) }
      : { kind: "CONFLICT" };
  }

  if (input.conversationId) {
    const conversation = await binding.prepare(
      "SELECT status FROM assistant_conversations WHERE owner_id=? AND id=?",
    ).bind(input.ownerId, conversationId).first<{ status: string }>();
    if (!conversation || conversation.status !== "ACTIVE") return { kind: "NOT_FOUND" };
    const busy = await binding.prepare(
      `SELECT 1 AS active FROM assistant_turn_jobs
       WHERE conversation_id=? AND status IN (${activeStatuses}) LIMIT 1`,
    ).bind(conversationId).first<{ active: number }>();
    if (busy) return { kind: "BUSY" };
  }

  const turnId = crypto.randomUUID();
  const userMessageId = crypto.randomUUID();
  const eventId = crypto.randomUUID();
  const title = provisionalAssistantTitle(normalizedMessage);
  const capacitySql = `
    (SELECT count(*) FROM assistant_turn_jobs
      WHERE owner_id=? AND status IN (${activeStatuses})) < ?
    AND (SELECT count(*) FROM assistant_turn_jobs
      WHERE status IN (${activeStatuses})) < ?
    AND (SELECT count(*) FROM assistant_turn_jobs
      WHERE owner_id=? AND created_at>=?) < ?
    AND NOT EXISTS (
      SELECT 1 FROM assistant_turn_jobs WHERE owner_id=? AND idempotency_key=?
    )`;
  const capacityBindings = [
    input.ownerId, ASSISTANT_CHAT_LIMITS.activePerOwner,
    ASSISTANT_CHAT_LIMITS.activeGlobal,
    input.ownerId, rateSince, ASSISTANT_CHAT_LIMITS.admittedPerOwnerPerMinute,
    input.ownerId, key,
  ];
  const statements = [];
  if (!input.conversationId) {
    statements.push(binding.prepare(
      `INSERT INTO assistant_conversations (
       id,owner_id,initial_idempotency_key,title,title_source,created_at,
       last_activity_at,summary_version,status
       ) SELECT ?,?,?,?,'PROVISIONAL',?,?,0,'ACTIVE'
       WHERE ${capacitySql}
         AND NOT EXISTS (
           SELECT 1 FROM assistant_conversations
           WHERE owner_id=? AND initial_idempotency_key=?
         )
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      conversationId, input.ownerId, key, title, timestamp, timestamp,
      ...capacityBindings, input.ownerId, key,
    ));
  }
  statements.push(binding.prepare(
    `INSERT INTO assistant_messages (
     id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
     )
     SELECT ?,conversation.id,'USER',?,?,?,'ASSISTANT_CHAT',?
     FROM assistant_conversations conversation
     WHERE conversation.id=? AND conversation.owner_id=? AND conversation.status='ACTIVE'
       AND ${capacitySql}
       AND NOT EXISTS (
         SELECT 1 FROM assistant_turn_jobs active
         WHERE active.conversation_id=conversation.id
           AND active.status IN (${activeStatuses})
       )
     ON CONFLICT DO NOTHING RETURNING *`,
  ).bind(
    userMessageId, normalizedMessage, timestamp,
    JSON.stringify({ kind: "USER_SUBMISSION", turn_id: turnId }),
    turnId, conversationId, input.ownerId, ...capacityBindings,
  ));
  statements.push(binding.prepare(
    `INSERT INTO assistant_turn_jobs (
     id,owner_id,conversation_id,user_message_id,idempotency_key,message_hash,
     status,event_sequence,available_at,expires_at,attempt_count,max_attempts,
     attempt_history_json,created_at
     )
     SELECT ?,conversation.owner_id,conversation.id,message.id,?,?,'PENDING',0,?,?,0,?,'[]',?
     FROM assistant_conversations conversation
     JOIN assistant_messages message ON message.id=?
       AND message.conversation_id=conversation.id AND message.role='USER'
     WHERE conversation.id=? AND conversation.owner_id=?
       AND NOT EXISTS (
         SELECT 1 FROM assistant_turn_jobs WHERE owner_id=? AND idempotency_key=?
       )
     ON CONFLICT DO NOTHING RETURNING *`,
  ).bind(
    turnId, key, messageHash, timestamp, expiresAt,
    ASSISTANT_CHAT_LIMITS.maxAttempts, timestamp,
    userMessageId, conversationId, input.ownerId, input.ownerId, key,
  ));
  statements.push(binding.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     )
     VALUES (?,?,?,1,'conversation.started',NULL,?,'{}','system:conversation-started')
     RETURNING *`,
  ).bind(eventId, turnId, ASSISTANT_EVENT_PROTOCOL_VERSION, timestamp));
  statements.push(binding.prepare(
    `UPDATE assistant_turn_jobs SET event_sequence=1
     WHERE id=? AND event_sequence=0
       AND EXISTS (SELECT 1 FROM assistant_turn_events WHERE id=? AND turn_id=?)
     RETURNING *`,
  ).bind(turnId, eventId, turnId));
  statements.push(binding.prepare(
    `UPDATE assistant_conversations SET last_activity_at=?
     WHERE id=? AND EXISTS (
       SELECT 1 FROM assistant_turn_jobs WHERE id=? AND user_message_id=?
     )`,
  ).bind(timestamp, conversationId, turnId, userMessageId));

  try {
    await binding.batch(statements);
  } catch (error) {
    if (!(error instanceof Error)
      || (!error.message.includes("assistant event requires admitted turn")
        && !error.message.includes("assistant event requires active turn"))) {
      throw error;
    }
  }
  const created = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.id=?`,
  ).bind(input.ownerId, turnId).first<AssistantTurnRow>();
  if (created) return { kind: "CREATED", item: publicTurn(created) };

  const existing = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.idempotency_key=?`,
  ).bind(input.ownerId, key).first<AssistantTurnRow>();
  if (existing) {
    return existing.message_hash === messageHash
      && (!input.conversationId || existing.conversation_id === conversationId)
      ? { kind: "EXISTING", item: publicTurn(existing) }
      : { kind: "CONFLICT" };
  }
  if (input.conversationId) {
    const conversation = await binding.prepare(
      "SELECT status FROM assistant_conversations WHERE owner_id=? AND id=?",
    ).bind(input.ownerId, conversationId).first<{ status: string }>();
    if (!conversation || conversation.status !== "ACTIVE") return { kind: "NOT_FOUND" };
    const busy = await binding.prepare(
      `SELECT 1 AS active FROM assistant_turn_jobs
       WHERE conversation_id=? AND status IN (${activeStatuses}) LIMIT 1`,
    ).bind(conversationId).first<{ active: number }>();
    if (busy) return { kind: "BUSY" };
  }
  return { kind: "CAPACITY" };
}

const eventFromRow = (row: AssistantEventRow): AssistantEventEnvelope => {
  const payload = parsedObject(row.payload_json);
  if (!payload) inputError("INVALID_PERSISTED_EVENT", "事件记录无效");
  return parseAssistantEvent({
    protocol: row.protocol,
    event_id: row.id,
    conversation_id: row.conversation_id,
    user_turn_id: row.turn_id,
    message_id: row.message_id,
    sequence: Number(row.sequence),
    type: row.type,
    occurred_at: row.occurred_at,
    payload,
  });
};

async function loadTurnEvents(binding: D1Database, turnId: string) {
  const rows = await binding.prepare(
    `SELECT event.*,turn.conversation_id
     FROM assistant_turn_events event
     JOIN assistant_turn_jobs turn ON turn.id=event.turn_id
     WHERE event.turn_id=? ORDER BY event.sequence`,
  ).bind(turnId).all<AssistantEventRow>();
  return rows.results.map(eventFromRow);
}

export async function listOwnerAssistantTurnEvents(
  binding: D1Database,
  input: { ownerId: string; turnId: string; afterSequence?: number; limit?: number },
) {
  const after = Number.isSafeInteger(input.afterSequence)
    ? Number(input.afterSequence) : 0;
  if (after < 0 || after > MAX_ASSISTANT_EVENTS_PER_TURN) {
    inputError("INVALID_EVENT_CURSOR", "事件游标无效");
  }
  const limit = Math.max(1, Math.min(
    ASSISTANT_CHAT_LIMITS.eventReplaySize,
    Number.isSafeInteger(input.limit) ? Number(input.limit) : 50,
  ));
  const turn = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.id=?`,
  ).bind(input.ownerId, input.turnId).first<AssistantTurnRow>();
  if (!turn) return null;
  const rows = await binding.prepare(
    `SELECT event.*,turn.conversation_id
     FROM assistant_turn_events event
     JOIN assistant_turn_jobs turn ON turn.id=event.turn_id
     WHERE turn.owner_id=? AND event.turn_id=? AND event.sequence>?
     ORDER BY event.sequence LIMIT ?`,
  ).bind(input.ownerId, input.turnId, after, limit).all<AssistantEventRow>();
  const events = rows.results.map(eventFromRow);
  return {
    turn: publicTurn(turn),
    events,
    next_sequence: events.at(-1)?.sequence ?? after,
    has_more: events.length === limit
      && (events.at(-1)?.sequence ?? after) < Number(turn.event_sequence),
  };
}

const attemptHistory = (event: string) => `json_insert(
  attempt_history_json,'$[#]',json_object(
    'event','${event}','at',?,'attempt',attempt_count
  )
)`;

async function recoverAssistantChatTurns(binding: D1Database, now: Date) {
  const timestamp = now.toISOString();
  const rows = await binding.prepare(
    `SELECT * FROM assistant_turn_jobs
     WHERE (status='PENDING' AND expires_at<=?)
        OR (status='PROCESSING' AND lease_expires_at<=?)
     ORDER BY created_at,id LIMIT 20`,
  ).bind(timestamp, timestamp).all<AssistantTurnRow>();
  for (const row of rows.results) {
    if (Date.parse(row.expires_at) <= now.getTime()) {
      await terminalizeTurn(binding, row, "EXPIRED", "TURN_EXPIRED", now);
    } else if (Number(row.attempt_count) >= Number(row.max_attempts)) {
      await terminalizeTurn(binding, row, "FAILED", "LEASE_EXPIRED", now);
    } else {
      await binding.prepare(
        `UPDATE assistant_turn_jobs SET status='PENDING',available_at=?,
         processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
         failure_code='LEASE_EXPIRED',attempt_history_json=${attemptHistory("LEASE_EXPIRED")}
         WHERE id=? AND status='PROCESSING' AND lease_expires_at<=?`,
      ).bind(timestamp, timestamp, row.id, timestamp).run();
    }
  }
}

export async function claimAssistantChatTurn(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  if (!/^[A-Za-z0-9._:-]{3,96}$/.test(workerId)) {
    inputError("INVALID_WORKER_ID", "工作进程身份无效");
  }
  await recoverAssistantChatTurns(binding, now);
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(now.getTime() + ASSISTANT_CHAT_LIMITS.leaseMs).toISOString();
  const row = await binding.prepare(
    `UPDATE assistant_turn_jobs SET status='PROCESSING',lease_owner=?,lease_token=?,
     processing_started_at=?,lease_expires_at=MIN(?,expires_at),
     attempt_count=attempt_count+1,
     failure_code=NULL,attempt_history_json=json_insert(
       attempt_history_json,'$[#]',json_object(
         'event','CLAIMED','at',?,'attempt',attempt_count+1
       )
     )
     WHERE id=(SELECT id FROM assistant_turn_jobs
       WHERE status='PENDING' AND available_at<=? AND expires_at>?
         AND attempt_count<max_attempts
       ORDER BY created_at,id LIMIT 1)
     RETURNING *`,
  ).bind(
    workerId, leaseToken, timestamp, leaseExpiresAt,
    timestamp, timestamp, timestamp,
  ).first<AssistantTurnRow>();
  if (!row) return null;
  const message = await binding.prepare(
    "SELECT content FROM assistant_messages WHERE id=? AND conversation_id=? AND role='USER'",
  ).bind(row.user_message_id, row.conversation_id).first<{ content: string }>();
  if (!message) {
    await terminalizeTurn(binding, row, "FAILED", "USER_MESSAGE_MISSING", now);
    return null;
  }
  return {
    id: row.id,
    owner_id: row.owner_id,
    conversation_id: row.conversation_id,
    user_message_id: row.user_message_id,
    user_text: message.content,
    retrieval_cutoff: row.created_at,
    lease_token: row.lease_token,
    lease_expires_at: row.lease_expires_at,
    attempt_count: Number(row.attempt_count),
    event_sequence: Number(row.event_sequence),
  };
}

export async function renewAssistantChatTurn(
  binding: D1Database,
  input: { id: unknown; lease_token: unknown },
  now = new Date(),
) {
  const turnId = strictObjectId(input.id, "turn_id");
  const leaseToken = strictObjectId(input.lease_token, "lease_token");
  const timestamp = now.toISOString();
  const proposedExpiry = new Date(
    now.getTime() + ASSISTANT_CHAT_LIMITS.leaseMs,
  ).toISOString();
  const row = await binding.prepare(
    `UPDATE assistant_turn_jobs SET
     lease_expires_at=MIN(?,expires_at),
     attempt_history_json=${attemptHistory("LEASE_RENEWED")}
     WHERE id=? AND status='PROCESSING' AND lease_token=?
       AND lease_expires_at>? AND expires_at>? AND cancel_requested=0
     RETURNING id,lease_token,lease_expires_at,expires_at,attempt_count`,
  ).bind(
    proposedExpiry, timestamp,
    turnId, leaseToken, timestamp, timestamp,
  ).first<AssistantTurnRow>();
  return row ? {
    id: row.id,
    lease_token: row.lease_token,
    lease_expires_at: row.lease_expires_at,
    expires_at: row.expires_at,
    attempt_count: Number(row.attempt_count),
  } : null;
}

type EventDraft = {
  idempotency_key: string;
  type: AssistantEventType;
  payload: Record<string, unknown>;
};

const progressTypes = new Set<AssistantEventType>([
  "reasoning.started", "tool.started", "tool.completed", "tool.failed",
  "retrieval.started", "retrieval.completed",
]);

const validateProgressBatch = (drafts: EventDraft[]) => {
  if (!drafts.length || drafts.length > ASSISTANT_CHAT_LIMITS.eventBatchSize) {
    inputError("INVALID_EVENT_BATCH", "事件批次大小无效");
  }
  const keys = new Set<string>();
  const tools = new Map<string, boolean>();
  const retrievals = new Map<string, boolean>();
  for (const draft of drafts) {
    if (!idempotencyKey.test(draft.idempotency_key) || keys.has(draft.idempotency_key)) {
      inputError("INVALID_EVENT_IDEMPOTENCY", "事件幂等键无效");
    }
    keys.add(draft.idempotency_key);
    if (!progressTypes.has(draft.type)) inputError("INVALID_PROGRESS_EVENT", "进度事件类型无效");
    if (draft.type === "tool.started") {
      const callId = String(draft.payload.call_id);
      if (tools.has(callId)) inputError("INVALID_EVENT_BATCH", "工具开始事件重复");
      tools.set(callId, false);
    }
    if (draft.type === "tool.completed" || draft.type === "tool.failed") {
      const callId = String(draft.payload.call_id);
      if (tools.get(callId) !== false) inputError("INVALID_EVENT_BATCH", "工具事件未成对");
      tools.set(callId, true);
    }
    if (draft.type === "retrieval.started") {
      const operationId = String(draft.payload.operation_id);
      if (retrievals.has(operationId)) inputError("INVALID_EVENT_BATCH", "检索开始事件重复");
      retrievals.set(operationId, false);
    }
    if (draft.type === "retrieval.completed") {
      const operationId = String(draft.payload.operation_id);
      if (retrievals.get(operationId) !== false) {
        inputError("INVALID_EVENT_BATCH", "检索事件未成对");
      }
      retrievals.set(operationId, true);
    }
  }
  if ([...tools.values(), ...retrievals.values()].some(value => !value)) {
    inputError("INVALID_EVENT_BATCH", "进度事件必须在同一批次闭合");
  }
};

const eventStatements = (
  binding: D1Database,
  turn: AssistantTurnRow,
  event: AssistantEventEnvelope,
  idempotency: string,
) => [
  binding.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     )
     SELECT ?,id,?,?,?,?,?,?,? FROM assistant_turn_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       AND cancel_requested=0 AND event_sequence=?
     ON CONFLICT DO NOTHING RETURNING *`,
  ).bind(
    event.event_id, event.protocol, event.sequence, event.type,
    event.message_id, event.occurred_at, JSON.stringify(event.payload), idempotency,
    turn.id, turn.lease_token, event.occurred_at, event.sequence - 1,
  ),
  binding.prepare(
    `UPDATE assistant_turn_jobs SET event_sequence=?
     WHERE id=? AND event_sequence=?
       AND EXISTS (SELECT 1 FROM assistant_turn_events WHERE id=? AND turn_id=?)
     RETURNING *`,
  ).bind(event.sequence, turn.id, event.sequence - 1, event.event_id, turn.id),
];

const buildEvent = (
  turn: AssistantTurnRow,
  sequence: number,
  type: AssistantEventType,
  payload: Record<string, unknown>,
  occurredAt: string,
  messageId: string | null = null,
) => parseAssistantEvent({
  protocol: ASSISTANT_EVENT_PROTOCOL_VERSION,
  event_id: crypto.randomUUID(),
  conversation_id: turn.conversation_id,
  user_turn_id: turn.id,
  message_id: messageId,
  sequence,
  type,
  occurred_at: occurredAt,
  payload,
});

const canonicalJson = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
};

export async function appendAssistantChatEvents(
  binding: D1Database,
  input: {
    id: unknown;
    lease_token: unknown;
    events: unknown;
    now?: Date;
  },
) {
  const turnId = strictObjectId(input.id, "turn_id");
  const leaseToken = strictObjectId(input.lease_token, "lease_token");
  if (!Array.isArray(input.events)) inputError("INVALID_EVENT_BATCH", "事件批次无效");
  const drafts = input.events.map(value => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      inputError("INVALID_EVENT_BATCH", "事件批次无效");
    }
    const raw = value as Record<string, unknown>;
    if (!raw.payload || typeof raw.payload !== "object" || Array.isArray(raw.payload)) {
      inputError("INVALID_EVENT_BATCH", "事件内容无效");
    }
    return {
      idempotency_key: String(raw.idempotency_key ?? ""),
      type: String(raw.type ?? "") as AssistantEventType,
      payload: raw.payload as Record<string, unknown>,
    };
  });
  validateProgressBatch(drafts);
  const timestamp = (input.now ?? new Date()).toISOString();
  const turn = await binding.prepare(
    `SELECT * FROM assistant_turn_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       AND cancel_requested=0`,
  ).bind(turnId, leaseToken, timestamp).first<AssistantTurnRow>();
  if (!turn) return null;

  const existingByKey = await binding.prepare(
    `SELECT event.*,turn.conversation_id FROM assistant_turn_events event
     JOIN assistant_turn_jobs turn ON turn.id=event.turn_id
     WHERE event.turn_id=? AND event.idempotency_key IN (${drafts.map(() => "?").join(",")})
     ORDER BY event.sequence`,
  ).bind(turn.id, ...drafts.map(item => item.idempotency_key)).all<AssistantEventRow>();
  if (existingByKey.results.length) {
    if (existingByKey.results.length !== drafts.length) {
      inputError("EVENT_IDEMPOTENCY_CONFLICT", "事件幂等批次冲突");
    }
    const events = existingByKey.results.map(eventFromRow);
    const rowsByKey = new Map(
      existingByKey.results.map(row => [row.idempotency_key, row]),
    );
    for (const draft of drafts) {
      const row = rowsByKey.get(draft.idempotency_key);
      if (!row) inputError("EVENT_IDEMPOTENCY_CONFLICT", "事件幂等批次冲突");
      const event = eventFromRow(row);
      if (event.type !== draft.type
        || canonicalJson(event.payload) !== canonicalJson(draft.payload)) {
        inputError("EVENT_IDEMPOTENCY_CONFLICT", "事件幂等批次冲突");
      }
    }
    return events;
  }
  if (Number(turn.event_sequence) + drafts.length
    > MAX_ASSISTANT_EVENTS_PER_TURN - MAX_COMPLETION_EVENTS) {
    inputError("EVENT_BUDGET_EXCEEDED", "进度事件已达到安全上限");
  }

  const sequence = new AssistantEventSequence();
  for (const event of await loadTurnEvents(binding, turn.id)) sequence.append(event);
  const events = drafts.map((draft, index) => buildEvent(
    turn,
    Number(turn.event_sequence) + index + 1,
    draft.type,
    draft.payload,
    timestamp,
  ));
  for (const event of events) sequence.append(event);
  const statements = events.flatMap((event, index) => eventStatements(
    binding, turn, event, drafts[index].idempotency_key,
  ));
  const results = await binding.batch(statements);
  if (!results.at(-1)?.results?.length) return null;
  return events;
}

const splitUtf8 = (value: string, maximum = ANSWER_DELTA_BYTES) => {
  const chunks: string[] = [];
  let current = "";
  let size = 0;
  for (const character of value) {
    const bytes = new TextEncoder().encode(character).length;
    if (size + bytes > maximum && current) {
      chunks.push(current);
      current = "";
      size = 0;
    }
    current += character;
    size += bytes;
  }
  if (current) chunks.push(current);
  return chunks;
};

const secretKey = /(?:^|[_-])(?:api[_-]?key|credential[_-]?(?:ref|id|secret|value|material)|authorization|password|passphrase|token|secret|private[_-]?key|bearer|cookie|client[_-]?secret)(?:$|[_-])/i;

const assertNoSecrets = (value: unknown, path = "provenance", seen = new Set<object>()) => {
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) inputError("INVALID_AGENT_PROVENANCE", `${path} 无效`);
    return;
  }
  if (!value || typeof value !== "object" || seen.has(value)) {
    inputError("INVALID_AGENT_PROVENANCE", `${path} 无效`);
  }
  if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype) {
    inputError("INVALID_AGENT_PROVENANCE", `${path} 无效`);
  }
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoSecrets(item, `${path}[${index}]`, seen));
  } else {
    for (const [key, item] of Object.entries(value)) {
      if (secretKey.test(key)) inputError("SECRET_IN_PROVENANCE", "模型来源包含敏感字段");
      assertNoSecrets(item, `${path}.${key}`, seen);
    }
  }
  seen.delete(value);
};

const parseAgentProvenance = async (
  value: unknown,
  turn: AssistantTurnRow,
  finalModelVersion: string,
  answer: string,
) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 来源无效");
  }
  assertNoSecrets(value);
  const raw = structuredClone(value) as Record<string, unknown>;
  const serialized = JSON.stringify(raw);
  if (new TextEncoder().encode(serialized).length > ASSISTANT_CHAT_LIMITS.maxProvenanceBytes) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 来源过大");
  }
  const requiredKeys = new Set([
    "policy_version", "tool_registry_version", "conversation_id", "user_message_id",
    "system_instruction_version", "system_instruction_sha256", "active_context_sha256",
    "retrieval_cutoff", "budgets", "model_turn_count", "tool_round_count",
    "tool_call_count", "tool_result_tokens", "model_versions", "model_routing",
    "tool_execution", "evidence_ids", "evidence_validation", "run_sha256",
  ]);
  if (Object.keys(raw).length !== requiredKeys.size
    || Object.keys(raw).some(key => !requiredKeys.has(key))
    || raw.policy_version !== "assistant-agent-v2"
    || raw.tool_registry_version !== "assistant-tool-registry-v1"
    || raw.conversation_id !== turn.conversation_id
    || raw.user_message_id !== turn.user_message_id
    || raw.retrieval_cutoff !== turn.created_at
    || typeof raw.system_instruction_version !== "string"
    || !objectId.test(raw.system_instruction_version)
    || typeof raw.system_instruction_sha256 !== "string"
    || !sha256.test(raw.system_instruction_sha256)
    || typeof raw.active_context_sha256 !== "string"
    || !sha256.test(raw.active_context_sha256)
    || typeof raw.run_sha256 !== "string"
    || !sha256.test(raw.run_sha256)) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 来源字段无效");
  }
  const budgets = raw.budgets;
  const budgetKeys = [
    "MAX_MODEL_TURNS_PER_USER_TURN", "MAX_TOOL_CALLS_PER_USER_TURN",
    "MAX_PARALLEL_TOOL_CALLS", "MAX_TOOL_RESULT_TOKENS", "MAX_RETRIEVED_EVIDENCE",
    "MAX_ACTIVE_CONTEXT_TOKENS", "MAX_OUTPUT_TOKENS",
  ];
  if (!budgets || typeof budgets !== "object" || Array.isArray(budgets)
    || Object.keys(budgets).sort().join("|") !== [...budgetKeys].sort().join("|")) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 预算来源无效");
  }
  const budget = budgets as Record<string, unknown>;
  const budgetRanges: Record<string, readonly [number, number]> = {
    MAX_MODEL_TURNS_PER_USER_TURN: [1, 3],
    MAX_TOOL_CALLS_PER_USER_TURN: [0, 32],
    MAX_PARALLEL_TOOL_CALLS: [1, 16],
    MAX_TOOL_RESULT_TOKENS: [32, 32_768],
    MAX_RETRIEVED_EVIDENCE: [0, 100],
    MAX_ACTIVE_CONTEXT_TOKENS: [1_024, 1_000_000],
    MAX_OUTPUT_TOKENS: [32, 32_768],
  };
  if (budgetKeys.some(key => {
    const [minimum, maximum] = budgetRanges[key];
    return !Number.isSafeInteger(budget[key])
      || Number(budget[key]) < minimum || Number(budget[key]) > maximum;
  })
    || (Number(budget.MAX_TOOL_CALLS_PER_USER_TURN) !== 0
      && Number(budget.MAX_PARALLEL_TOOL_CALLS)
        > Number(budget.MAX_TOOL_CALLS_PER_USER_TURN))
    || Number(budget.MAX_OUTPUT_TOKENS)
      >= Number(budget.MAX_ACTIVE_CONTEXT_TOKENS)) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 预算超出范围");
  }
  const modelTurnCount = Number(raw.model_turn_count);
  const toolRoundCount = Number(raw.tool_round_count);
  const toolCallCount = Number(raw.tool_call_count);
  const toolResultTokens = Number(raw.tool_result_tokens);
  const versions = raw.model_versions;
  const routes = raw.model_routing;
  const execution = raw.tool_execution;
  if (!Number.isSafeInteger(modelTurnCount) || modelTurnCount < 1
    || modelTurnCount > Number(budget.MAX_MODEL_TURNS_PER_USER_TURN)
    || !Number.isSafeInteger(toolRoundCount) || toolRoundCount < 0 || toolRoundCount > 2
    || modelTurnCount !== toolRoundCount + 1
    || !Number.isSafeInteger(toolCallCount) || toolCallCount < 0
    || toolCallCount > Number(budget.MAX_TOOL_CALLS_PER_USER_TURN)
    || !Number.isSafeInteger(toolResultTokens) || toolResultTokens < 0
    || toolResultTokens > Number(budget.MAX_TOOL_RESULT_TOKENS)
    || !Array.isArray(versions) || versions.length !== modelTurnCount
    || versions.some(item => !isAssistantModelIdentifier(item))
    || new Set(versions).size !== 1
    || versions.at(-1) !== finalModelVersion
    || !Array.isArray(routes) || routes.length !== modelTurnCount
    || !Array.isArray(execution) || execution.length !== toolRoundCount) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 执行来源无效");
  }
  const parsedRoutes = routes.map((route, index) => {
    try {
      const parsed = parseAssistantRoutingProvenance(route, "ASSISTANT_CHAT");
      if (parsed.selected_model_id !== versions[index]
        || parsed.planned_tool_calls > Number(budget.MAX_PARALLEL_TOOL_CALLS)) {
        inputError("INVALID_AGENT_PROVENANCE", "Assistant 路由来源不一致");
      }
      return parsed;
    } catch {
      return inputError("INVALID_AGENT_PROVENANCE", "Assistant 路由来源无效");
    }
  });
  const evidence: string[] = [];
  const seenEvidence = new Set<string>();
  let receipts = 0;
  let resultTokens = 0;
  const seenCalls = new Set<string>();
  const expectedActorFingerprint = (await hexDigest(
    `assistant-tool-registry-v1:${turn.owner_id}:${turn.id}`,
  )).slice(0, 16);
  for (const [roundIndex, round] of execution.entries()) {
    if (!Array.isArray(round) || round.length < 1
      || round.length > Number(budget.MAX_PARALLEL_TOOL_CALLS)
      || parsedRoutes[roundIndex].planned_tool_calls < round.length) {
      inputError("INVALID_AGENT_PROVENANCE", "工具来源无效");
    }
    for (const receipt of round) {
      if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) {
        inputError("INVALID_AGENT_PROVENANCE", "工具来源无效");
      }
      const item = receipt as Record<string, unknown>;
      const receiptKeys = [
        "call_id", "name", "tool_version", "status", "error_code",
        "result_tokens", "result_sha256", "evidence_ids", "provenance",
        "started_at", "completed_at",
      ];
      const status = String(item.status);
      const callId = typeof item.call_id === "string" ? item.call_id : "";
      const toolVersion = item.tool_version;
      const receiptEvidence = item.evidence_ids;
      const publicProvenance = item.provenance;
      const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
      if (Object.keys(item).sort().join("|") !== receiptKeys.sort().join("|")
        || !objectId.test(callId) || seenCalls.has(callId)
        || typeof item.name !== "string" || !toolName.test(item.name)
        || !new Set(["SUCCEEDED", "FAILED", "REJECTED", "TIMED_OUT"]).has(status)
        || (toolVersion !== null
          && (typeof toolVersion !== "string" || !/^v[1-9][0-9]*$/.test(toolVersion)
            || !item.name.endsWith(`_${toolVersion}`)))
        || (toolVersion === null && status !== "REJECTED")
        || (status === "SUCCEEDED"
          ? item.error_code !== null || toolVersion === null
          : typeof item.error_code !== "string" || !errorCode.test(item.error_code))
        || !Number.isSafeInteger(item.result_tokens) || Number(item.result_tokens) < 0
        || Number(item.result_tokens) > 32_768
        || (status === "SUCCEEDED"
          ? Number(item.result_tokens) < 1 : Number(item.result_tokens) !== 0)
        || typeof item.result_sha256 !== "string" || !sha256.test(item.result_sha256)
        || !Array.isArray(receiptEvidence) || receiptEvidence.length > 100
        || new Set(receiptEvidence).size !== receiptEvidence.length
        || (status !== "SUCCEEDED" && receiptEvidence.length !== 0)
        || !publicProvenance || typeof publicProvenance !== "object"
        || Array.isArray(publicProvenance)
        || Object.keys(publicProvenance).length > 32
        || Object.keys(publicProvenance).some(key => !/^[a-z][a-z0-9_]{0,63}$/.test(key))
        || (publicProvenance as Record<string, unknown>).registry_version
          !== "assistant-tool-registry-v1"
        || typeof (publicProvenance as Record<string, unknown>).actor_fingerprint !== "string"
        || !/^[0-9a-f]{16}$/.test(
          String((publicProvenance as Record<string, unknown>).actor_fingerprint),
        )
        || (publicProvenance as Record<string, unknown>).actor_fingerprint
          !== expectedActorFingerprint
        || new TextEncoder().encode(JSON.stringify(publicProvenance)).length > 8_192
        || typeof item.started_at !== "string" || !timestampPattern.test(item.started_at)
        || typeof item.completed_at !== "string" || !timestampPattern.test(item.completed_at)
        || !Number.isFinite(Date.parse(item.started_at))
        || !Number.isFinite(Date.parse(item.completed_at))
        || Date.parse(item.completed_at) < Date.parse(item.started_at)) {
        inputError("INVALID_AGENT_PROVENANCE", "工具 receipt 无效");
      }
      const publicEvidence = (publicProvenance as Record<string, unknown>)
        .canonical_evidence_ids;
      if (publicEvidence !== undefined
        && JSON.stringify(publicEvidence) !== JSON.stringify(receiptEvidence)) {
        inputError("INVALID_AGENT_PROVENANCE", "工具证据来源不一致");
      }
      seenCalls.add(callId);
      receipts += 1;
      resultTokens += Number(item.result_tokens);
      for (const id of receiptEvidence) {
        if (typeof id !== "string" || !objectId.test(id)) {
          inputError("INVALID_AGENT_PROVENANCE", "工具证据编号无效");
        }
        if (!seenEvidence.has(id)) {
          seenEvidence.add(id);
          evidence.push(id);
        }
      }
    }
  }
  if (receipts !== toolCallCount || resultTokens !== toolResultTokens
    || evidence.length > Number(budget.MAX_RETRIEVED_EVIDENCE)
    || !Array.isArray(raw.evidence_ids)
    || raw.evidence_ids.length > Number(budget.MAX_RETRIEVED_EVIDENCE)
    || new Set(raw.evidence_ids).size !== raw.evidence_ids.length
    || raw.evidence_ids.some(item => typeof item !== "string" || !objectId.test(item))) {
    inputError("INVALID_AGENT_PROVENANCE", "工具来源计数不一致");
  }
  let evidenceValidation;
  try {
    evidenceValidation = await parseAssistantEvidenceReceipt(
      raw.evidence_validation,
      {
        answer,
        availableEvidenceIds: evidence,
        mode: evidence.length ? "CITATION_COVERAGE" : "NO_CITABLE_EVIDENCE",
        maxCitedEvidence: Math.max(1, Number(budget.MAX_RETRIEVED_EVIDENCE)),
      },
    );
  } catch {
    inputError("INVALID_EVIDENCE_VALIDATION", "Assistant 证据验证回执无效");
  }
  if (JSON.stringify(raw.evidence_ids)
    !== JSON.stringify(evidenceValidation.cited_evidence_ids)) {
    inputError("INVALID_EVIDENCE_VALIDATION", "Assistant 引用证据与回执不一致");
  }
  const expectedRunHash = await hexDigest(canonicalJson(
    Object.fromEntries(Object.entries(raw).filter(([key]) => key !== "run_sha256")),
  ));
  if (raw.run_sha256 !== expectedRunHash) {
    inputError("INVALID_AGENT_PROVENANCE", "Assistant 执行哈希无效");
  }
  return raw;
};

export async function completeAssistantChatTurn(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const turnId = strictObjectId(input.id, "turn_id");
  const leaseToken = strictObjectId(input.lease_token, "lease_token");
  const timestamp = now.toISOString();
  const turn = await binding.prepare(
    `SELECT * FROM assistant_turn_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(turnId, leaseToken, timestamp).first<AssistantTurnRow>();
  if (!turn) return null;
  if (Number(turn.cancel_requested) === 1) {
    return terminalizeTurn(binding, turn, "CANCELLED", "USER_CANCELLED", now);
  }
  const answer = typeof input.answer === "string"
    ? input.answer.replace(/\r\n?/gu, "\n").trim()
    : "";
  const modelVersion = typeof input.model_version === "string" ? input.model_version.trim() : "";
  if (!answer || hasUnsafeTextControl(answer)
    || new TextEncoder().encode(answer).length > ASSISTANT_CHAT_LIMITS.maxAnswerBytes
    || !isAssistantModelIdentifier(modelVersion)) {
    inputError("INVALID_ASSISTANT_ANSWER", "Assistant 回答无效");
  }
  const provenance = await parseAgentProvenance(
    input.provenance, turn, modelVersion, answer,
  );
  const evidenceIds = provenance.evidence_ids as string[];
  const contentDocument = await verifyAssistantContentDocument(
    input.content_document, {
      answer,
      evidenceIds,
    },
  ).catch(() => inputError(
    "INVALID_ASSISTANT_CONTENT", "Assistant 结构化回答无效",
  ));
  const assistantMessageId = crypto.randomUUID();
  const answerHash = await hexDigest(answer);
  const existingSequence = new AssistantEventSequence();
  for (const event of await loadTurnEvents(binding, turn.id)) existingSequence.append(event);
  const terminalEvents: Array<{ event: AssistantEventEnvelope; key: string }> = [];
  const add = (
    type: AssistantEventType,
    payload: Record<string, unknown>,
    key: string,
    messageId: string | null = null,
  ) => {
    const event = buildEvent(
      turn,
      Number(turn.event_sequence) + terminalEvents.length + 1,
      type,
      payload,
      timestamp,
      messageId,
    );
    existingSequence.append(event);
    terminalEvents.push({ event, key });
  };
  add("answer.started", {}, "completion:answer-started");
  splitUtf8(answer).forEach((text, index) => add(
    "answer.delta", { text }, `completion:answer-delta:${index}`,
  ));
  contentDocument.blocks.forEach((block, index) => add("content.block", {
    block_id: block.id,
    block_type: block.type,
    block_version: block.version,
    content_sha256: block.content_sha256,
  }, `completion:content-block:${index}`));
  add("answer.completed", {
    content_sha256: answerHash,
    evidence_ids: evidenceIds,
  }, "completion:answer-completed", assistantMessageId);
  add("conversation.completed", {}, "completion:conversation-completed");

  const assistantProvenance = JSON.stringify({
    kind: "ASSISTANT_CHAT",
    turn_id: turn.id,
    agent: provenance,
  });
  const automaticTitle = automaticAssistantTitleStatements(binding, {
    conversationId: turn.conversation_id,
    assistantMessageId,
    now,
  });
  const statements = [binding.prepare(
    `INSERT INTO assistant_messages (
     id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id,
     content_protocol,content_document_json,content_document_sha256
     )
     SELECT ?,conversation_id,'ASSISTANT',?,?,?,'ASSISTANT_CHAT',id,?,?,?
     FROM assistant_turn_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       AND cancel_requested=0
     ON CONFLICT DO NOTHING RETURNING *`,
  ).bind(
    assistantMessageId, answer, timestamp, assistantProvenance,
    contentDocument.protocol, JSON.stringify(contentDocument),
    contentDocument.document_sha256,
    turn.id, leaseToken, timestamp,
  )];
  for (const item of terminalEvents) {
    statements.push(...eventStatements(binding, turn, item.event, item.key));
  }
  const completionResultIndex = statements.length;
  statements.push(binding.prepare(
    `UPDATE assistant_turn_jobs SET status='ANSWERED',assistant_message_id=?,completed_at=?,
     processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
     failure_code=NULL,attempt_history_json=${attemptHistory("ANSWERED")}
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       AND cancel_requested=0 AND event_sequence=?
       AND EXISTS (SELECT 1 FROM assistant_messages WHERE id=? AND source_id=?)
     RETURNING *`,
  ).bind(
    assistantMessageId, timestamp, timestamp,
    turn.id, leaseToken, timestamp,
    Number(turn.event_sequence) + terminalEvents.length,
    assistantMessageId, turn.id,
  ));
  statements.push(binding.prepare(
    `UPDATE assistant_conversations SET last_activity_at=?
     WHERE id=? AND EXISTS (
       SELECT 1 FROM assistant_turn_jobs WHERE id=? AND status='ANSWERED'
         AND assistant_message_id=?
    )`,
  ).bind(timestamp, turn.conversation_id, turn.id, assistantMessageId));
  statements.push(...automaticTitle.statements);
  const results = await binding.batch<AssistantTurnRow>(statements);
  const completed = results[completionResultIndex]?.results?.[0];
  if (!completed) return null;
  try {
    await scheduleAssistantCompaction(binding, turn.conversation_id, { now });
  } catch {
    // Derived memory work never invalidates the canonical final answer.
  }
  const conversation = await binding.prepare(
    "SELECT title FROM assistant_conversations WHERE id=?",
  ).bind(turn.conversation_id).first<{ title: string }>();
  return publicTurn({ ...completed, conversation_title: conversation?.title ?? null });
}

async function terminalizeTurn(
  binding: D1Database,
  row: AssistantTurnRow,
  status: "FAILED" | "EXPIRED" | "CANCELLED",
  code: string,
  now: Date,
) {
  if (!errorCode.test(code)) inputError("INVALID_FAILURE_CODE", "失败代码无效");
  const timestamp = now.toISOString();
  const sequence = new AssistantEventSequence();
  for (const event of await loadTurnEvents(binding, row.id)) sequence.append(event);
  const type: AssistantEventType = status === "CANCELLED" ? "cancelled" : "error";
  const payload = status === "CANCELLED"
    ? { code }
    : { code, retryable: false, recovery_key: null };
  const event = buildEvent(
    row, Number(row.event_sequence) + 1, type, payload, timestamp,
  );
  sequence.append(event);
  const insert = binding.prepare(
    `INSERT INTO assistant_turn_events (
     id,turn_id,protocol,sequence,type,message_id,occurred_at,payload_json,idempotency_key
     )
     SELECT ?,id,?,?,?,?,?,?,? FROM assistant_turn_jobs
     WHERE id=? AND status IN (${activeStatuses}) AND event_sequence=?
     ON CONFLICT DO NOTHING RETURNING *`,
  ).bind(
    event.event_id, event.protocol, event.sequence, event.type,
    null, event.occurred_at, JSON.stringify(event.payload), `terminal:${status}`,
    row.id, event.sequence - 1,
  );
  const advance = binding.prepare(
    `UPDATE assistant_turn_jobs SET event_sequence=?
     WHERE id=? AND event_sequence=?
       AND EXISTS (SELECT 1 FROM assistant_turn_events WHERE id=? AND turn_id=?)
     RETURNING *`,
  ).bind(event.sequence, row.id, event.sequence - 1, event.event_id, row.id);
  const finish = binding.prepare(
    `UPDATE assistant_turn_jobs SET status=?,completed_at=?,failure_code=?,
     processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
     cancel_requested=CASE WHEN ?='CANCELLED' THEN 1 ELSE cancel_requested END,
     attempt_history_json=${attemptHistory(status)}
     WHERE id=? AND status IN (${activeStatuses}) AND event_sequence=?
     RETURNING *`,
  ).bind(status, timestamp, code, status, timestamp, row.id, event.sequence);
  const results = await binding.batch<AssistantTurnRow>([insert, advance, finish]);
  const completed = results.at(-1)?.results?.[0];
  return completed ? publicTurn(completed) : null;
}

export async function failAssistantChatTurn(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const turnId = strictObjectId(input.id, "turn_id");
  const leaseToken = strictObjectId(input.lease_token, "lease_token");
  const code = String(input.failure_code ?? "WORKER_FAILURE").trim().toUpperCase();
  if (!errorCode.test(code)) inputError("INVALID_FAILURE_CODE", "失败代码无效");
  const row = await binding.prepare(
    `SELECT * FROM assistant_turn_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(turnId, leaseToken, now.toISOString()).first<AssistantTurnRow>();
  if (!row) return null;
  if (Number(row.cancel_requested) === 1) {
    return terminalizeTurn(binding, row, "CANCELLED", "USER_CANCELLED", now);
  }
  if (Date.parse(row.expires_at) <= now.getTime()) {
    return terminalizeTurn(binding, row, "EXPIRED", "TURN_EXPIRED", now);
  }
  if (Number(row.attempt_count) >= Number(row.max_attempts)) {
    return terminalizeTurn(binding, row, "FAILED", code, now);
  }
  const timestamp = now.toISOString();
  const delay = Math.min(120, 30 * (2 ** Math.max(0, Number(row.attempt_count) - 1)));
  const availableAt = new Date(now.getTime() + delay * 1_000).toISOString();
  const historyEvent = code === "NO_MODEL_CAPACITY"
    ? "CAPACITY_DEFERRED" : "RETRY_SCHEDULED";
  const updated = await binding.prepare(
    `UPDATE assistant_turn_jobs SET status='PENDING',available_at=?,failure_code=?,
     processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
     attempt_history_json=${attemptHistory(historyEvent)}
     WHERE id=? AND status='PROCESSING' AND lease_token=? RETURNING *`,
  ).bind(availableAt, code, timestamp, row.id, leaseToken).first<AssistantTurnRow>();
  return updated ? publicTurn(updated) : null;
}

export async function deferAssistantChatTurn(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  return failAssistantChatTurn(binding, {
    ...input,
    failure_code: "NO_MODEL_CAPACITY",
  }, now);
}

export async function cancelOwnerAssistantChatTurn(
  binding: D1Database,
  ownerId: string,
  turnId: string,
  now = new Date(),
) {
  const row = await binding.prepare(
    `${turnSelect} WHERE turn.owner_id=? AND turn.id=?`,
  ).bind(ownerId, turnId).first<AssistantTurnRow>();
  if (!row) return null;
  if (row.status === "CANCELLED") return publicTurn(row);
  if (!new Set<AssistantChatStatus>(["PENDING", "PROCESSING"]).has(row.status)) {
    return null;
  }
  return terminalizeTurn(binding, row, "CANCELLED", "USER_CANCELLED", now);
}
