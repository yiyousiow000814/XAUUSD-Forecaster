import { parseAssistantRoutingProvenance } from "./assistant-routing";
import {
  parseAssistantContentDocument,
  type AssistantContentDocument,
} from "./assistant-content";

export const ASSISTANT_TITLE_PROMPT_VERSION = "assistant-title-v1";
export const MAX_TITLE_GRAPHEMES = 32;

export const ASSISTANT_CONVERSATION_LIMITS = {
  conversationListSize: 50,
  messagePageSize: 50,
  titleJobLeaseMs: 3 * 60 * 1_000,
  titleJobMaxAttempts: 3,
} as const;

export type AssistantTitleSource = "PROVISIONAL" | "AI" | "USER";
export type AssistantConversationStatus = "ACTIVE" | "ARCHIVED";
export type AssistantMessageRole = "USER" | "ASSISTANT";
export type AssistantTitleJobStatus =
  | "PENDING"
  | "PROCESSING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";
export const ASSISTANT_ACTIVE_TURN_STATUSES = ["PENDING", "PROCESSING"] as const;
export const ASSISTANT_ACTIVE_TURN_STATUSES_SQL = ASSISTANT_ACTIVE_TURN_STATUSES
  .map(status => `'${status}'`)
  .join(",");

export type PublicAssistantActiveTurn = {
  id: string;
  status: typeof ASSISTANT_ACTIVE_TURN_STATUSES[number];
  event_sequence: number;
  created_at: string;
};

export type PublicAssistantConversation = {
  id: string;
  title: string;
  title_source: AssistantTitleSource;
  created_at: string;
  last_activity_at: string;
  archived_at: string | null;
  summary_version: number;
  status: AssistantConversationStatus;
  title_job_status: AssistantTitleJobStatus | null;
  active_turn: PublicAssistantActiveTurn | null;
};

export type PublicAssistantMessage = {
  id: string;
  conversation_id: string;
  role: AssistantMessageRole;
  content: string;
  content_document: AssistantContentDocument | null;
  created_at: string;
  provenance: Record<string, unknown>;
};

type ConversationRow = Record<string, unknown> & {
  id: string;
  owner_id: string;
  title: string;
  title_source: AssistantTitleSource;
  title_revision: number;
  title_request_version: number;
  pending_title_job_id: string | null;
  created_at: string;
  last_activity_at: string;
  archived_at: string | null;
  summary_version: number;
  status: AssistantConversationStatus;
};

type TitleJobRow = Record<string, unknown> & {
  id: string;
  conversation_id: string;
  input_version: number;
  expected_title_revision: number;
  first_user_message_id: string;
  assistant_message_id: string;
  status: AssistantTitleJobStatus;
  lease_token: string | null;
  lease_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
  prompt_version: string;
};

export class AssistantConversationInputError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const parsedObject = (value: unknown) => {
  if (typeof value !== "string" || !value) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
};

const graphemes = (value: string) => Array.from(
  new Intl.Segmenter("zh-CN", { granularity: "grapheme" }).segment(value),
  part => part.segment,
);

const oneLine = (value: unknown) => String(value ?? "")
  .normalize("NFKC")
  .replace(/\s+/g, " ")
  .trim();

const withoutSurroundingQuotes = (value: string) => {
  const pairs = [["\"", "\""], ["'", "'"], ["“", "”"], ["‘", "’"], ["「", "」"], ["『", "』"]];
  for (const [left, right] of pairs) {
    if (value.startsWith(left) && value.endsWith(right) && value.length > left.length + right.length) {
      return value.slice(left.length, -right.length).trim();
    }
  }
  return value;
};

const boundedTitle = (value: string) => {
  const parts = graphemes(value);
  if (parts.length <= MAX_TITLE_GRAPHEMES) return value;
  return `${parts.slice(0, MAX_TITLE_GRAPHEMES - 1).join("")}…`;
};

export function provisionalAssistantTitle(firstMessage: unknown) {
  const normalized = oneLine(firstMessage);
  if (!normalized) {
    throw new AssistantConversationInputError("INVALID_MESSAGE", "消息内容不能为空");
  }
  return boundedTitle(normalized);
}

export function parseAssistantTitle(value: unknown) {
  const title = withoutSurroundingQuotes(oneLine(value));
  const length = graphemes(title).length;
  if (!title || length < 2 || length > MAX_TITLE_GRAPHEMES) {
    throw new AssistantConversationInputError("INVALID_TITLE", "标题需要2至32个字符");
  }
  return title;
}

const genericGeneratedTitle = /^(?:(?:关于)?(?:黄金(?:市场)?|xauusd)(?:的)?(?:对话|讨论|问题)|用户(?:询问|问了)(?:关于)?(?:黄金(?:市场)?|xauusd)(?:的)?问题|conversation\s+(?:about|on)\s+(?:gold|xauusd))$/iu;

export function parseGeneratedAssistantTitle(value: unknown) {
  const title = parseAssistantTitle(value);
  if (genericGeneratedTitle.test(title)) {
    throw new AssistantConversationInputError("GENERIC_TITLE", "标题需要准确描述具体主题");
  }
  return title;
}

export function parseAssistantIdempotencyKey(value: string | null) {
  const key = value?.trim() ?? "";
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(key)) {
    throw new AssistantConversationInputError(
      "INVALID_IDEMPOTENCY_KEY",
      "缺少有效的 Idempotency-Key",
    );
  }
  return key;
}

export function publicAssistantConversation(
  row: Record<string, unknown>,
): PublicAssistantConversation {
  const jobStatus = row.title_job_status;
  return {
    id: String(row.id),
    title: String(row.title),
    title_source: String(row.title_source) as AssistantTitleSource,
    created_at: String(row.created_at),
    last_activity_at: String(row.last_activity_at),
    archived_at: typeof row.archived_at === "string" ? row.archived_at : null,
    summary_version: Number(row.summary_version ?? 0),
    status: String(row.status) as AssistantConversationStatus,
    title_job_status: typeof jobStatus === "string"
      ? jobStatus as AssistantTitleJobStatus
      : null,
    active_turn: typeof row.active_turn_id === "string" ? {
      id: row.active_turn_id,
      status: String(row.active_turn_status) as PublicAssistantActiveTurn["status"],
      event_sequence: Number(row.active_turn_event_sequence),
      created_at: String(row.active_turn_created_at),
    } : null,
  };
}

export function publicAssistantMessage(
  row: Record<string, unknown>,
): PublicAssistantMessage {
  const provenance = parsedObject(row.provenance_json);
  const agent = provenance.agent && typeof provenance.agent === "object"
    && !Array.isArray(provenance.agent)
    ? provenance.agent as Record<string, unknown>
    : null;
  const rawEvidence = Array.isArray(agent?.evidence_ids)
    ? agent.evidence_ids
    : Array.isArray(provenance.evidence_ids) ? provenance.evidence_ids : [];
  const evidenceIds = rawEvidence.filter(
    (item): item is string => typeof item === "string",
  );
  let contentDocument: AssistantContentDocument | null = null;
  if (typeof row.content_document_json === "string") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(row.content_document_json);
    } catch {
      throw new AssistantConversationInputError(
        "INVALID_STORED_CONTENT", "Assistant 历史内容无效",
      );
    }
    contentDocument = parseAssistantContentDocument(parsed, {
      answer: String(row.content),
      evidenceIds,
    });
    if (row.content_protocol !== contentDocument.protocol
      || row.content_document_sha256 !== contentDocument.document_sha256) {
      throw new AssistantConversationInputError(
        "INVALID_STORED_CONTENT", "Assistant 历史内容来源不一致",
      );
    }
  } else if (
    (row.content_protocol !== null && row.content_protocol !== undefined)
    || (row.content_document_sha256 !== null && row.content_document_sha256 !== undefined)
  ) {
    throw new AssistantConversationInputError(
      "INVALID_STORED_CONTENT", "Assistant 历史内容不完整",
    );
  }
  return {
    id: String(row.id),
    conversation_id: String(row.conversation_id),
    role: String(row.role) as AssistantMessageRole,
    content: String(row.content),
    content_document: contentDocument,
    created_at: String(row.created_at),
    provenance,
  };
}

const conversationSelect = `SELECT c.*,
  (SELECT j.status FROM assistant_title_jobs j
   WHERE j.id=c.pending_title_job_id) AS title_job_status,
  active_turn.id AS active_turn_id,
  active_turn.status AS active_turn_status,
  active_turn.event_sequence AS active_turn_event_sequence,
  active_turn.created_at AS active_turn_created_at
  FROM assistant_conversations c
  LEFT JOIN assistant_turn_jobs active_turn ON active_turn.id=(
    SELECT candidate.id FROM assistant_turn_jobs candidate
    WHERE candidate.conversation_id=c.id
      AND candidate.status IN (${ASSISTANT_ACTIVE_TURN_STATUSES_SQL})
    ORDER BY candidate.created_at DESC,candidate.id DESC LIMIT 1
  )`;

export async function listOwnerAssistantConversations(
  binding: D1Database,
  ownerId: string,
  options: { archived?: boolean; limit?: number } = {},
) {
  const status = options.archived ? "ARCHIVED" : "ACTIVE";
  const limit = Math.max(1, Math.min(
    ASSISTANT_CONVERSATION_LIMITS.conversationListSize,
    Number.isSafeInteger(options.limit) ? Number(options.limit) : 20,
  ));
  const rows = await binding.prepare(
    `${conversationSelect}
     WHERE c.owner_id=? AND c.status=?
     ORDER BY c.last_activity_at DESC,c.id DESC LIMIT ?`,
  ).bind(ownerId, status, limit).all<ConversationRow>();
  return rows.results.map(publicAssistantConversation);
}

export async function getOwnerAssistantConversation(
  binding: D1Database,
  ownerId: string,
  conversationId: string,
) {
  const row = await binding.prepare(
    `${conversationSelect} WHERE c.owner_id=? AND c.id=?`,
  ).bind(ownerId, conversationId).first<ConversationRow>();
  return row ? publicAssistantConversation(row) : null;
}

export async function listOwnerAssistantMessages(
  binding: D1Database,
  ownerId: string,
  conversationId: string,
  options: { beforeCreatedAt?: string; beforeId?: string; limit?: number } = {},
) {
  const limit = Math.max(1, Math.min(
    ASSISTANT_CONVERSATION_LIMITS.messagePageSize,
    Number.isSafeInteger(options.limit) ? Number(options.limit) : 30,
  ));
  const beforeCreatedAt = options.beforeCreatedAt?.trim() ?? "";
  const beforeId = options.beforeId?.trim() ?? "";
  if (
    (beforeCreatedAt && !beforeId)
    || (!beforeCreatedAt && beforeId)
    || (beforeCreatedAt && (
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(beforeCreatedAt)
      || !Number.isFinite(Date.parse(beforeCreatedAt))
      || !/^[A-Za-z0-9:._-]{1,128}$/.test(beforeId)
    ))
  ) {
    throw new AssistantConversationInputError("INVALID_CURSOR", "消息游标无效");
  }
  const rows = await binding.prepare(
    `SELECT m.* FROM assistant_messages m
     JOIN assistant_conversations c ON c.id=m.conversation_id
     WHERE c.owner_id=? AND c.id=?
       AND (?='' OR m.created_at<? OR (m.created_at=? AND m.id<?))
     ORDER BY m.created_at DESC,m.id DESC LIMIT ?`,
  ).bind(
    ownerId, conversationId,
    beforeCreatedAt, beforeCreatedAt, beforeCreatedAt, beforeId,
    limit + 1,
  ).all<Record<string, unknown>>();
  const hasMore = rows.results.length > limit;
  const page = rows.results.slice(0, limit).map(publicAssistantMessage);
  const tail = page.at(-1);
  return {
    items: page.reverse(),
    next_cursor: hasMore && tail
      ? { before_created_at: tail.created_at, before_id: tail.id }
      : null,
  };
}

export async function renameOwnerAssistantConversation(
  binding: D1Database,
  ownerId: string,
  conversationId: string,
  title: string,
) {
  const timestamp = new Date().toISOString();
  const results = await binding.batch<ConversationRow>([
    binding.prepare(
      `UPDATE assistant_title_jobs SET status='CANCELLED',completed_at=?,failure_code='USER_RENAMED',
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CANCELLED','occurred_at',?,'failure_code','USER_RENAMED')),
       lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
       WHERE id=(SELECT pending_title_job_id FROM assistant_conversations
                 WHERE owner_id=? AND id=?)
         AND status IN ('PENDING','PROCESSING')`,
    ).bind(timestamp, timestamp, ownerId, conversationId),
    binding.prepare(
      `UPDATE assistant_conversations SET title=?,title_source='USER',
       title_revision=title_revision+1,pending_title_job_id=NULL
       WHERE owner_id=? AND id=? RETURNING *`,
    ).bind(title, ownerId, conversationId),
  ]);
  const row = results.at(-1)?.results?.[0];
  return row
    ? getOwnerAssistantConversation(binding, ownerId, conversationId)
    : null;
}

export async function setOwnerAssistantConversationArchived(
  binding: D1Database,
  ownerId: string,
  conversationId: string,
  archived: boolean,
  now = new Date(),
) {
  const row = await binding.prepare(
    `UPDATE assistant_conversations SET status=?,archived_at=?
     WHERE owner_id=? AND id=? RETURNING *`,
  ).bind(
    archived ? "ARCHIVED" : "ACTIVE",
    archived ? now.toISOString() : null,
    ownerId,
    conversationId,
  ).first<ConversationRow>();
  if (!row) return null;
  return getOwnerAssistantConversation(binding, ownerId, conversationId);
}

export type TitleRegenerationOutcome =
  | { kind: "CREATED" | "EXISTING"; job_id: string; status: AssistantTitleJobStatus }
  | { kind: "NOT_FOUND" | "NO_ASSISTANT_MESSAGE" | "ALREADY_PENDING" };

export async function requestAssistantTitleRegeneration(
  binding: D1Database,
  input: {
    ownerId: string;
    conversationId: string;
    idempotencyKey: string;
    now?: Date;
  },
): Promise<TitleRegenerationOutcome> {
  const existing = await binding.prepare(
    `SELECT j.* FROM assistant_title_jobs j
     JOIN assistant_conversations c ON c.id=j.conversation_id
     WHERE c.owner_id=? AND c.id=? AND j.idempotency_key=?`,
  ).bind(input.ownerId, input.conversationId, input.idempotencyKey)
    .first<TitleJobRow>();
  if (existing) return { kind: "EXISTING", job_id: existing.id, status: existing.status };

  const conversation = await binding.prepare(
    "SELECT * FROM assistant_conversations WHERE owner_id=? AND id=?",
  ).bind(input.ownerId, input.conversationId).first<ConversationRow>();
  if (!conversation) return { kind: "NOT_FOUND" };
  const messageInputs = await binding.batch<{ id: string }>([
    binding.prepare(
      `SELECT id FROM assistant_messages WHERE conversation_id=? AND role='USER'
       ORDER BY created_at,id LIMIT 1`,
    ).bind(input.conversationId),
    binding.prepare(
      `SELECT id FROM assistant_messages WHERE conversation_id=? AND role='ASSISTANT'
       ORDER BY created_at DESC,id DESC LIMIT 1`,
    ).bind(input.conversationId),
  ]);
  const firstUserMessageId = messageInputs[0]?.results?.[0]?.id;
  const assistantMessageId = messageInputs[1]?.results?.[0]?.id;
  if (!firstUserMessageId || !assistantMessageId) return { kind: "NO_ASSISTANT_MESSAGE" };
  if (conversation.pending_title_job_id) return { kind: "ALREADY_PENDING" };

  const timestamp = (input.now ?? new Date()).toISOString();
  const jobId = crypto.randomUUID();
  const results = await binding.batch<TitleJobRow>([
    binding.prepare(
      `UPDATE assistant_conversations SET
       title_request_version=title_request_version+1,pending_title_job_id=?
       WHERE owner_id=? AND id=? AND pending_title_job_id IS NULL
       RETURNING *`,
    ).bind(jobId, input.ownerId, input.conversationId),
    binding.prepare(
      `INSERT INTO assistant_title_jobs (
       id,conversation_id,idempotency_key,requested_by,input_version,
       expected_title_revision,first_user_message_id,assistant_message_id,
       status,available_at,attempt_count,max_attempts,prompt_version,created_at
       )
       SELECT ?,id,?,'USER',title_request_version,title_revision,?,?,'PENDING',?,0,?,?,?
       FROM assistant_conversations
       WHERE owner_id=? AND id=? AND pending_title_job_id=?
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      jobId, input.idempotencyKey, firstUserMessageId, assistantMessageId, timestamp,
      ASSISTANT_CONVERSATION_LIMITS.titleJobMaxAttempts,
      ASSISTANT_TITLE_PROMPT_VERSION, timestamp,
      input.ownerId, input.conversationId, jobId,
    ),
  ]);
  const job = results.at(-1)?.results?.[0];
  if (job) return { kind: "CREATED", job_id: job.id, status: job.status };

  const replay = await binding.prepare(
    `SELECT j.* FROM assistant_title_jobs j
     JOIN assistant_conversations c ON c.id=j.conversation_id
     WHERE c.owner_id=? AND c.id=? AND j.idempotency_key=?`,
  ).bind(input.ownerId, input.conversationId, input.idempotencyKey)
    .first<TitleJobRow>();
  return replay
    ? { kind: "EXISTING", job_id: replay.id, status: replay.status }
    : { kind: "ALREADY_PENDING" };
}

export function automaticAssistantTitleStatements(
  binding: D1Database,
  input: { conversationId: string; assistantMessageId: string; now?: Date },
) {
  const timestamp = (input.now ?? new Date()).toISOString();
  const titleJobId = crypto.randomUUID();
  return {
    titleJobId,
    statements: [
      binding.prepare(
        `UPDATE assistant_conversations SET
         title_request_version=title_request_version+1,pending_title_job_id=?
         WHERE id=? AND title_source='PROVISIONAL' AND title_request_version=0
           AND pending_title_job_id IS NULL
           AND EXISTS (
             SELECT 1 FROM assistant_messages
             WHERE id=? AND conversation_id=assistant_conversations.id AND role='ASSISTANT'
           )
         RETURNING *`,
      ).bind(titleJobId, input.conversationId, input.assistantMessageId),
      binding.prepare(
        `INSERT INTO assistant_title_jobs (
         id,conversation_id,idempotency_key,requested_by,input_version,
         expected_title_revision,first_user_message_id,assistant_message_id,
         status,available_at,attempt_count,max_attempts,prompt_version,created_at
         )
         SELECT ?,id,?,'AUTOMATIC',title_request_version,title_revision,
         (SELECT id FROM assistant_messages
          WHERE conversation_id=assistant_conversations.id AND role='USER'
          ORDER BY created_at,id LIMIT 1),?,'PENDING',?,0,?,?,?
         FROM assistant_conversations WHERE pending_title_job_id=?
         ON CONFLICT DO NOTHING RETURNING *`,
      ).bind(
        titleJobId, `automatic:${input.assistantMessageId}`,
        input.assistantMessageId, timestamp,
        ASSISTANT_CONVERSATION_LIMITS.titleJobMaxAttempts,
        ASSISTANT_TITLE_PROMPT_VERSION, timestamp, titleJobId,
      ),
    ],
  };
}

export async function scheduleAutomaticAssistantTitle(
  binding: D1Database,
  input: { conversationId: string; assistantMessageId: string; now?: Date },
) {
  const prepared = automaticAssistantTitleStatements(binding, input);
  const results = await binding.batch<TitleJobRow>(prepared.statements);
  return results[1]?.results?.[0] ?? null;
}

const titleJobHistoryCleanup = `lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL`;

export async function claimAssistantTitleJob(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(
    now.getTime() + ASSISTANT_CONVERSATION_LIMITS.titleJobLeaseMs,
  ).toISOString();
  const results = await binding.batch<TitleJobRow>([
    binding.prepare(
      `UPDATE assistant_conversations SET pending_title_job_id=NULL
       WHERE pending_title_job_id IN (
         SELECT id FROM assistant_title_jobs WHERE status='PROCESSING'
           AND lease_expires_at<=? AND attempt_count>=max_attempts
       )`,
    ).bind(timestamp),
    binding.prepare(
      `UPDATE assistant_title_jobs SET status='FAILED',failure_code='LEASE_EXPIRED',
       completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',1)),${titleJobHistoryCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count>=max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_title_jobs SET status='PENDING',failure_code='LEASE_EXPIRED',
       available_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',0)),${titleJobHistoryCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count<max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_title_jobs SET status='PROCESSING',lease_owner=?,lease_token=?,
       lease_expires_at=?,attempt_count=attempt_count+1,failure_code=NULL,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CLAIMED','occurred_at',?,'attempt',attempt_count+1,
           'worker_id',?))
       WHERE id=(SELECT id FROM assistant_title_jobs
         WHERE status='PENDING' AND available_at<=?
         ORDER BY created_at,id LIMIT 1)
       RETURNING *`,
    ).bind(workerId, leaseToken, leaseExpiresAt, timestamp, workerId, timestamp),
  ]);
  const job = results.at(-1)?.results?.[0];
  if (!job) return null;
  const messages = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `SELECT substr(content,1,1000) AS content FROM assistant_messages
       WHERE conversation_id=? AND id=? AND role='USER'`,
    ).bind(job.conversation_id, job.first_user_message_id),
    binding.prepare(
      `SELECT substr(content,1,2000) AS content FROM assistant_messages
       WHERE conversation_id=? AND id=? AND role='ASSISTANT'`,
    ).bind(job.conversation_id, job.assistant_message_id),
  ]);
  const firstUserMessage = messages[0]?.results?.[0]?.content;
  const latestAssistantMessage = messages[1]?.results?.[0]?.content;
  if (typeof firstUserMessage !== "string" || typeof latestAssistantMessage !== "string") {
    await failAssistantTitleJob(binding, {
      id: job.id,
      lease_token: job.lease_token,
      failure_code: "TITLE_CONTEXT_MISSING",
    }, now);
    return null;
  }
  return {
    id: job.id,
    conversation_id: job.conversation_id,
    lease_token: String(job.lease_token),
    lease_expires_at: String(job.lease_expires_at),
    attempt_count: Number(job.attempt_count),
    prompt_version: String(job.prompt_version),
    first_user_message: firstUserMessage,
    latest_assistant_message: latestAssistantMessage,
  };
}

export async function completeAssistantTitleJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const title = parseGeneratedAssistantTitle(input.title);
  const modelVersion = String(input.model_version ?? "").trim();
  const promptVersion = String(input.prompt_version ?? "").trim();
  if (!modelVersion || modelVersion.length > 120 || !promptVersion || promptVersion.length > 120) {
    throw new AssistantConversationInputError("INVALID_TITLE_PROVENANCE", "标题来源无效");
  }
  let routing;
  try {
    routing = parseAssistantRoutingProvenance(input.routing, "CONVERSATION_TITLE");
  } catch {
    throw new AssistantConversationInputError("INVALID_TITLE_PROVENANCE", "标题来源无效");
  }
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_title_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<TitleJobRow>();
  if (!job) return null;
  if (promptVersion !== job.prompt_version) {
    throw new AssistantConversationInputError("INVALID_TITLE_PROVENANCE", "标题来源无效");
  }
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_title_jobs SET status='COMPLETED',generated_title=?,model_version=?,completed_at=?,
       failure_code=NULL,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','COMPLETED','occurred_at',?,'attempt',attempt_count,
           'routing',json(?))),
       ${titleJobHistoryCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      title, modelVersion, timestamp, timestamp, JSON.stringify(routing),
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET title=?,title_source='AI',
       title_revision=title_revision+1,pending_title_job_id=NULL
       WHERE id=? AND pending_title_job_id=? AND title_revision=?
       RETURNING *`,
    ).bind(title, job.conversation_id, id, job.expected_title_revision),
  ]);
  const completed = results[0]?.results?.[0];
  if (!completed) return null;
  return {
    job_id: id,
    status: "COMPLETED" as const,
    title_applied: Boolean(results[1]?.results?.[0]),
  };
}

export async function failAssistantTitleJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const failureCode = String(input.failure_code ?? "TITLE_GENERATION_FAILED")
    .trim().toUpperCase();
  if (!/^[A-Z0-9_]{3,64}$/.test(failureCode)) {
    throw new AssistantConversationInputError("INVALID_FAILURE_CODE", "失败代码无效");
  }
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_title_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<TitleJobRow>();
  if (!job) return null;
  const terminal = Number(job.attempt_count) >= Number(job.max_attempts);
  const delaySeconds = Math.min(120, 30 * (2 ** Math.max(0, Number(job.attempt_count) - 1)));
  const availableAt = terminal
    ? timestamp
    : new Date(now.getTime() + delaySeconds * 1_000).toISOString();
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_title_jobs SET status=?,available_at=?,failure_code=?,
       completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','FAILED','occurred_at',?,'attempt',attempt_count,
           'failure_code',?,'terminal',?)),${titleJobHistoryCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      terminal ? "FAILED" : "PENDING", availableAt, failureCode,
      terminal ? timestamp : null, timestamp, failureCode, terminal ? 1 : 0,
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET pending_title_job_id=NULL
       WHERE pending_title_job_id=? AND ?=1`,
    ).bind(id, terminal ? 1 : 0),
  ]);
  const row = results[0]?.results?.[0];
  return row ? { job_id: id, status: String(row.status) as AssistantTitleJobStatus } : null;
}

export async function deferAssistantTitleJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_title_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<TitleJobRow>();
  if (!job) return null;
  const terminal = Number(job.attempt_count) >= Number(job.max_attempts);
  const availableAt = terminal
    ? timestamp : new Date(now.getTime() + 60_000).toISOString();
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_title_jobs SET status=?,available_at=?,
       failure_code='NO_MODEL_CAPACITY',completed_at=?,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CAPACITY_DEFERRED','occurred_at',?,
           'attempt',attempt_count,'failure_code','NO_MODEL_CAPACITY','terminal',?)),
       ${titleJobHistoryCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      terminal ? "FAILED" : "PENDING", availableAt,
      terminal ? timestamp : null, timestamp, terminal ? 1 : 0,
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET pending_title_job_id=NULL
       WHERE pending_title_job_id=? AND ?=1`,
    ).bind(id, terminal ? 1 : 0),
  ]);
  const row = results[0]?.results?.[0];
  return row ? { job_id: id, status: String(row.status) } : null;
}
