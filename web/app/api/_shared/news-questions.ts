// Mutable queue state is bounded; completed answers retain immutable provenance fields.
import {
  automaticAssistantTitleStatements,
  provisionalAssistantTitle,
} from "./assistant-conversations";
import { scheduleAssistantCompaction } from "./assistant-memory";
import {
  type AssistantRoutingProvenance,
  parseAssistantRoutingProvenance,
} from "./assistant-routing";

export const NEWS_QA_PROMPT_VERSION = "news-qa-v2";
export const INSUFFICIENT_EVIDENCE_ANSWER = "当前已收录且可追溯的新闻证据不足，无法可靠回答这个问题。";

export const NEWS_QUESTION_LIMITS = {
  activePerOwner: 2,
  activeGlobal: 10,
  admittedPerOwnerPerMinute: 5,
  listSize: 20,
  maxAttempts: 3,
  questionTtlMs: 30 * 60 * 1_000,
  leaseMs: 3 * 60 * 1_000,
} as const;

export type NewsQuestionStatus =
  | "PENDING"
  | "PROCESSING"
  | "ANSWERED"
  | "FAILED"
  | "REJECTED"
  | "EXPIRED";

export type PublicNewsQuestion = {
  id: string;
  conversation_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
  conversation_title: string | null;
  question: string;
  status: NewsQuestionStatus;
  asked_at: string;
  answer: string | null;
  answer_status: "ANSWERED" | "INSUFFICIENT_EVIDENCE" | null;
  evidence_ids: string[];
  answered_at: string | null;
  model_version: string | null;
  prompt_version: string;
  retrieval: Record<string, unknown> | null;
  attempt_count: number;
  failure_code: string | null;
};

type NewsQuestionRow = Record<string, unknown> & {
  id: string;
  owner_id: string;
  idempotency_key: string;
  question_hash: string;
  question: string;
  retrieval_query: string;
  status: NewsQuestionStatus;
  asked_at: string;
  available_at: string;
  expires_at: string;
  lease_token: string | null;
  lease_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
  prompt_version: string;
  conversation_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
};

export class NewsQuestionInputError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const STOP_WORDS = new Set([
  "今天", "当前", "现在", "最近", "新闻", "市场", "请问", "请", "帮我",
  "什么", "为何", "为什么", "怎么", "如何", "是否", "有没有", "相关", "情况", "关注",
  "the", "what", "why", "how", "is", "are", "please", "news", "today",
]);

const DOMAIN_TERMS = [
  "xauusd", "美联储", "联邦储备", "黄金", "fed", "利率", "通胀", "cpi",
  "非农", "就业", "美元", "地缘", "央行", "油价",
];

const normalizedQuestion = (value: unknown) => String(value ?? "")
  .normalize("NFKC")
  .trim()
  .replace(/\s+/g, " ");

export function parseQuestion(value: unknown) {
  const question = normalizedQuestion(value);
  if (question.length < 4 || question.length > 200) {
    throw new NewsQuestionInputError("INVALID_QUESTION", "问题需要4至200个字");
  }
  return question;
}

export function parseIdempotencyKey(value: string | null) {
  const key = value?.trim() ?? "";
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(key)) {
    throw new NewsQuestionInputError(
      "INVALID_IDEMPOTENCY_KEY",
      "缺少有效的 Idempotency-Key",
    );
  }
  return key;
}

export function deriveRetrievalQuery(question: string) {
  const normalized = normalizedQuestion(question).toLocaleLowerCase("zh-CN");
  const words: string[] = [];
  const seen = new Set<string>();
  for (const token of DOMAIN_TERMS
    .filter(value => normalized.includes(value))
    .sort((left, right) => normalized.indexOf(left) - normalized.indexOf(right))) {
    seen.add(token);
    words.push(token);
  }
  const segmenter = new Intl.Segmenter("zh-CN", { granularity: "word" });
  for (const part of segmenter.segment(normalized)) {
    if (words.length >= 6) break;
    if (!part.isWordLike) continue;
    const token = part.segment.replace(/^[\p{P}\p{S}]+|[\p{P}\p{S}]+$/gu, "");
    if (
      token.length < 2
      || STOP_WORDS.has(token)
      || /^\d+$/.test(token)
      || seen.has(token)
    ) continue;
    seen.add(token);
    words.push(token);
    if (words.length >= 6) break;
  }
  const query = words.join(" ").slice(0, 80);
  if (query) return query;
  return normalized.replace(/[\p{P}\p{S}]+/gu, " ").trim().replace(/\s+/g, " ").slice(0, 80);
}

const hexDigest = async (value: string) => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
};

const parsedJson = (value: unknown, fallback: unknown) => {
  if (typeof value !== "string" || !value) return fallback;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return fallback;
  }
};

export function publicNewsQuestion(row: Record<string, unknown>): PublicNewsQuestion {
  const evidence = parsedJson(row.evidence_json, []);
  const retrieval = parsedJson(row.retrieval_json, null);
  return {
    id: String(row.id),
    conversation_id: typeof row.conversation_id === "string" ? row.conversation_id : null,
    user_message_id: typeof row.user_message_id === "string" ? row.user_message_id : null,
    assistant_message_id: typeof row.assistant_message_id === "string" ? row.assistant_message_id : null,
    conversation_title: typeof row.conversation_title === "string" ? row.conversation_title : null,
    question: String(row.question),
    status: String(row.status) as NewsQuestionStatus,
    asked_at: String(row.asked_at),
    answer: typeof row.answer === "string" ? row.answer : null,
    answer_status: row.answer_status === "ANSWERED" || row.answer_status === "INSUFFICIENT_EVIDENCE"
      ? row.answer_status
      : null,
    evidence_ids: Array.isArray(evidence) ? evidence.map(String) : [],
    answered_at: typeof row.answered_at === "string" ? row.answered_at : null,
    model_version: typeof row.model_version === "string" ? row.model_version : null,
    prompt_version: String(row.prompt_version ?? NEWS_QA_PROMPT_VERSION),
    retrieval: retrieval && typeof retrieval === "object" && !Array.isArray(retrieval)
      ? retrieval as Record<string, unknown>
      : null,
    attempt_count: Number(row.attempt_count ?? 0),
    failure_code: typeof row.failure_code === "string" ? row.failure_code : null,
  };
}

export type CreateNewsQuestionOutcome =
  | { kind: "CREATED" | "EXISTING"; item: PublicNewsQuestion }
  | { kind: "CONFLICT" | "CAPACITY" };

export async function createNewsQuestion(
  binding: D1Database,
  input: {
    ownerId: string;
    idempotencyKey: string;
    question: string;
    now?: Date;
  },
): Promise<CreateNewsQuestionOutcome> {
  const now = input.now ?? new Date();
  const askedAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + NEWS_QUESTION_LIMITS.questionTtlMs).toISOString();
  const rateSince = new Date(now.getTime() - 60_000).toISOString();
  const retrievalQuery = deriveRetrievalQuery(input.question);
  if (!retrievalQuery) throw new NewsQuestionInputError("NO_RETRIEVAL_QUERY", "问题缺少可检索主题");
  const provisionalTitle = provisionalAssistantTitle(input.question);
  const questionHash = await hexDigest(
    `${askedAt.slice(0, 10)}\n${normalizedQuestion(input.question).toLocaleLowerCase("zh-CN")}`,
  );
  const questionId = crypto.randomUUID();
  const conversationId = crypto.randomUUID();
  const userMessageId = crypto.randomUUID();
  const results = await binding.batch<NewsQuestionRow>([
    binding.prepare(
      `INSERT INTO assistant_conversations (
       id,owner_id,initial_idempotency_key,title,title_source,created_at,
       last_activity_at,summary_version,status
       )
       SELECT ?,?,?,?,'PROVISIONAL',?,?,0,'ACTIVE'
       WHERE (SELECT count(*) FROM news_questions
              WHERE owner_id=? AND status IN ('PENDING','PROCESSING')) < ?
         AND (SELECT count(*) FROM news_questions
              WHERE status IN ('PENDING','PROCESSING')) < ?
         AND (SELECT count(*) FROM news_questions
              WHERE owner_id=? AND asked_at>=?) < ?
         AND NOT EXISTS (
           SELECT 1 FROM news_questions
           WHERE owner_id=? AND (idempotency_key=? OR question_hash=?)
         )
         AND NOT EXISTS (
           SELECT 1 FROM assistant_conversations
           WHERE owner_id=? AND initial_idempotency_key=?
         )
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      conversationId, input.ownerId, input.idempotencyKey, provisionalTitle,
      askedAt, askedAt,
      input.ownerId, NEWS_QUESTION_LIMITS.activePerOwner,
      NEWS_QUESTION_LIMITS.activeGlobal,
      input.ownerId, rateSince, NEWS_QUESTION_LIMITS.admittedPerOwnerPerMinute,
      input.ownerId, input.idempotencyKey, questionHash,
      input.ownerId, input.idempotencyKey,
    ),
    binding.prepare(
      `INSERT INTO assistant_messages (
       id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
       )
       SELECT ?,id,'USER',?,?,?,'NEWS_QA',?
       FROM assistant_conversations WHERE id=? AND owner_id=?
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      userMessageId, input.question, askedAt,
      JSON.stringify({ kind: "USER_SUBMISSION", question_id: questionId }),
      questionId, conversationId, input.ownerId,
    ),
    binding.prepare(
      `INSERT INTO news_questions (
       id,owner_id,idempotency_key,question_hash,question,retrieval_query,status,
       asked_at,available_at,expires_at,attempt_count,max_attempts,prompt_version,
       attempt_history_json,conversation_id,user_message_id
       )
       SELECT ?,?,?,?,?,?,'PENDING',?,?,?,0,?,?,'[]',id,?
       FROM assistant_conversations WHERE id=? AND owner_id=?
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      questionId, input.ownerId, input.idempotencyKey, questionHash,
      input.question, retrievalQuery, askedAt, askedAt, expiresAt,
      NEWS_QUESTION_LIMITS.maxAttempts, NEWS_QA_PROMPT_VERSION,
      userMessageId, conversationId, input.ownerId,
    ),
  ]);
  const row = results.at(-1)?.results?.[0];
  if (row) {
    return {
      kind: "CREATED",
      item: publicNewsQuestion({ ...row, conversation_title: provisionalTitle }),
    };
  }

  const existing = await binding.prepare(
    `SELECT q.*,c.title AS conversation_title FROM news_questions q
     LEFT JOIN assistant_conversations c ON c.id=q.conversation_id
     WHERE q.owner_id=? AND (q.idempotency_key=? OR q.question_hash=?)
     ORDER BY CASE WHEN q.idempotency_key=? THEN 0 ELSE 1 END
     LIMIT 1`,
  ).bind(input.ownerId, input.idempotencyKey, questionHash, input.idempotencyKey)
    .first<NewsQuestionRow>();
  if (existing) {
    if (
      existing.idempotency_key === input.idempotencyKey
      && existing.question_hash !== questionHash
    ) return { kind: "CONFLICT" };
    return { kind: "EXISTING", item: publicNewsQuestion(existing) };
  }
  return { kind: "CAPACITY" };
}

export async function listOwnerNewsQuestions(
  binding: D1Database,
  ownerId: string,
  limit: number,
) {
  const boundedLimit = Math.max(1, Math.min(NEWS_QUESTION_LIMITS.listSize, limit));
  const rows = await binding.prepare(
    `SELECT q.*,c.title AS conversation_title FROM news_questions q
     LEFT JOIN assistant_conversations c ON c.id=q.conversation_id
     WHERE q.owner_id=? ORDER BY q.asked_at DESC,q.id DESC LIMIT ?`,
  ).bind(ownerId, boundedLimit).all<NewsQuestionRow>();
  return rows.results.map(publicNewsQuestion);
}

export async function getOwnerNewsQuestion(
  binding: D1Database,
  ownerId: string,
  id: string,
) {
  const row = await binding.prepare(
    `SELECT q.*,c.title AS conversation_title FROM news_questions q
     LEFT JOIN assistant_conversations c ON c.id=q.conversation_id
     WHERE q.owner_id=? AND q.id=?`,
  ).bind(ownerId, id).first<NewsQuestionRow>();
  return row ? publicNewsQuestion(row) : null;
}

const history = (event: string) => `json_insert(
  CASE WHEN json_valid(attempt_history_json) THEN attempt_history_json ELSE '[]' END,
  '$[#]',json_object('event','${event}','at',?,'attempt',attempt_count)
)`;

export async function claimNewsQuestion(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(now.getTime() + NEWS_QUESTION_LIMITS.leaseMs).toISOString();
  const statements = [
    binding.prepare(
      `UPDATE news_questions SET status='EXPIRED',failure_code='QUESTION_EXPIRED',
       available_at=?,processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
       attempt_history_json=${history("EXPIRED")}
       WHERE status='PENDING' AND expires_at<=?`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE news_questions SET status='EXPIRED',failure_code='QUESTION_EXPIRED',
       available_at=?,processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
       attempt_history_json=${history("EXPIRED")}
       WHERE status='PROCESSING' AND expires_at<=?`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE news_questions SET status='FAILED',failure_code='LEASE_EXPIRED',
       available_at=?,processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
       attempt_history_json=${history("FAILED")}
       WHERE status='PROCESSING' AND lease_expires_at<=?
         AND expires_at>? AND attempt_count>=max_attempts`,
    ).bind(timestamp, timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE news_questions SET status='PENDING',failure_code='LEASE_EXPIRED',
       available_at=?,processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
       attempt_history_json=${history("LEASE_EXPIRED")}
       WHERE status='PROCESSING' AND lease_expires_at<=?
         AND expires_at>? AND attempt_count<max_attempts`,
    ).bind(timestamp, timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE news_questions SET status='PROCESSING',lease_owner=?,lease_token=?,
       processing_started_at=?,lease_expires_at=?,attempt_count=attempt_count+1,failure_code=NULL,
       attempt_history_json=json_insert(
         CASE WHEN json_valid(attempt_history_json) THEN attempt_history_json ELSE '[]' END,
         '$[#]',json_object('event','CLAIMED','at',?,'attempt',attempt_count+1)
       )
       WHERE id=(SELECT id FROM news_questions
         WHERE status='PENDING' AND available_at<=? AND expires_at>?
           AND attempt_count<max_attempts
         ORDER BY asked_at,id LIMIT 1)
       RETURNING *`,
    ).bind(workerId, leaseToken, timestamp, leaseExpiresAt, timestamp, timestamp, timestamp),
  ];
  const results = await binding.batch<NewsQuestionRow>(statements);
  const row = results.at(-1)?.results?.[0];
  if (!row) return null;
  return {
    id: String(row.id),
    question: String(row.question),
    retrieval_query: String(row.retrieval_query),
    retrieval_cutoff: String(row.asked_at),
    lease_token: String(row.lease_token),
    lease_expires_at: String(row.lease_expires_at),
    attempt_count: Number(row.attempt_count),
    prompt_version: String(row.prompt_version),
  };
}

type CompletionProvenance = {
  query: string;
  source_mode: "D1_ARCHIVE";
  archive_complete: true;
  ordering: ["published_time DESC", "collector_first_seen_time DESC", "detail_key DESC"];
  cutoff: string;
  result_limit: number;
  canonical_evidence_ids: string[];
};

const EVIDENCE_ID = /^[A-Za-z0-9:._-]{1,128}$/;

const completionProvenance = (
  value: unknown,
  row: NewsQuestionRow,
): CompletionProvenance => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new NewsQuestionInputError("INVALID_RETRIEVAL_PROVENANCE", "检索来源无效");
  }
  const raw = value as Record<string, unknown>;
  const ids = Array.isArray(raw.canonical_evidence_ids)
    ? [...new Set(raw.canonical_evidence_ids.map(String))]
    : [];
  const ordering = raw.ordering;
  const validOrdering = Array.isArray(ordering)
    && ordering.join("|") === "published_time DESC|collector_first_seen_time DESC|detail_key DESC";
  if (
    raw.query !== row.retrieval_query
    || raw.source_mode !== "D1_ARCHIVE"
    || raw.archive_complete !== true
    || raw.cutoff !== row.asked_at
    || !validOrdering
    || !Number.isInteger(raw.result_limit)
    || Number(raw.result_limit) < 1
    || Number(raw.result_limit) > 20
    || ids.length > 20
    || ids.some(id => !EVIDENCE_ID.test(id))
  ) throw new NewsQuestionInputError("INVALID_RETRIEVAL_PROVENANCE", "检索来源无效");
  return {
    query: row.retrieval_query,
    source_mode: "D1_ARCHIVE",
    archive_complete: true,
    ordering: [
      "published_time DESC",
      "collector_first_seen_time DESC",
      "detail_key DESC",
    ],
    cutoff: row.asked_at,
    result_limit: Number(raw.result_limit),
    canonical_evidence_ids: ids,
  };
};

export async function completeNewsQuestion(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const leased = await binding.prepare(
    `SELECT * FROM news_questions
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<NewsQuestionRow>();
  if (!leased) return null;
  const promptVersion = String(input.prompt_version ?? "").trim();
  if (promptVersion !== leased.prompt_version) {
    throw new NewsQuestionInputError("INVALID_PROMPT_PROVENANCE", "回答规则版本无效");
  }

  const provenance = completionProvenance(input.retrieval, leased);
  const answerStatus = String(input.answer_status ?? "");
  const requestedEvidence = Array.isArray(input.evidence_ids)
    ? [...new Set(input.evidence_ids.map(String))]
    : [];
  if (
    requestedEvidence.length > 12
    || requestedEvidence.some(idValue => !provenance.canonical_evidence_ids.includes(idValue))
  ) throw new NewsQuestionInputError("UNVERIFIED_EVIDENCE", "回答引用了检索结果之外的证据");

  let answer: string;
  let evidence: string[];
  let modelVersion: string | null;
  let routing: AssistantRoutingProvenance | null;
  if (answerStatus === "INSUFFICIENT_EVIDENCE") {
    if (
      provenance.canonical_evidence_ids.length !== 0
      || requestedEvidence.length !== 0
      || input.routing != null
    ) {
      throw new NewsQuestionInputError("INVALID_INSUFFICIENT_RESULT", "证据不足结果不能携带证据");
    }
    answer = INSUFFICIENT_EVIDENCE_ANSWER;
    evidence = [];
    modelVersion = null;
    routing = null;
  } else if (answerStatus === "ANSWERED") {
    answer = String(input.answer ?? "").normalize("NFKC").trim();
    modelVersion = String(input.model_version ?? "").trim();
    if (!answer || answer.length > 4_000 || !modelVersion || modelVersion.length > 120) {
      throw new NewsQuestionInputError("INVALID_MODEL_ANSWER", "模型回答无效");
    }
    if (requestedEvidence.length === 0) {
      throw new NewsQuestionInputError("UNVERIFIED_EVIDENCE", "回答必须引用已检索证据");
    }
    try {
      routing = parseAssistantRoutingProvenance(input.routing, "NEWS_QA");
    } catch {
      throw new NewsQuestionInputError("INVALID_ROUTING_PROVENANCE", "模型路由来源无效");
    }
    evidence = requestedEvidence;
  } else {
    throw new NewsQuestionInputError("INVALID_ANSWER_STATUS", "回答状态无效");
  }

  if (!leased.conversation_id || !leased.user_message_id) {
    throw new NewsQuestionInputError("MISSING_CONVERSATION_STATE", "问题缺少规范会话状态");
  }
  const assistantMessageId = crypto.randomUUID();
  const automaticTitle = automaticAssistantTitleStatements(binding, {
    conversationId: leased.conversation_id,
    assistantMessageId,
    now,
  });
  const assistantProvenance = JSON.stringify({
    kind: "NEWS_QA",
    question_id: id,
    answer_status: answerStatus,
    evidence_ids: evidence,
    retrieval: provenance,
    model_version: modelVersion,
    prompt_version: leased.prompt_version,
    routing,
  });
  const results = await binding.batch<NewsQuestionRow>([
    binding.prepare(
      `INSERT INTO assistant_messages (
       id,conversation_id,role,content,created_at,provenance_json,source_kind,source_id
       )
       SELECT ?,conversation_id,'ASSISTANT',?,?,?,'NEWS_QA',id
       FROM news_questions
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       ON CONFLICT DO NOTHING RETURNING *`,
    ).bind(
      assistantMessageId, answer, timestamp, assistantProvenance,
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE news_questions SET status='ANSWERED',answer_status=?,answer=?,
       evidence_json=?,retrieval_json=?,answered_at=?,model_version=?,
       assistant_message_id=?,failure_code=NULL,
       lease_owner=NULL,lease_token=NULL,processing_started_at=NULL,lease_expires_at=NULL,
       attempt_history_json=${history("ANSWERED")}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      answerStatus, answer, JSON.stringify(evidence), JSON.stringify(provenance),
      timestamp, modelVersion, assistantMessageId,
      timestamp, id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET last_activity_at=?
       WHERE id=(SELECT conversation_id FROM news_questions
                 WHERE id=? AND assistant_message_id=?)`,
    ).bind(timestamp, id, assistantMessageId),
    ...automaticTitle.statements,
  ]);
  const row = results[1]?.results?.[0];
  if (!row) return null;
  try {
    await scheduleAssistantCompaction(binding, String(leased.conversation_id), { now });
  } catch {
    // The canonical answer is already durable. Derived memory work is retried
    // after a later final answer or through the machine scheduling endpoint.
  }
  const conversation = await binding.prepare(
    "SELECT title FROM assistant_conversations WHERE id=?",
  ).bind(leased.conversation_id).first<{ title: string }>();
  return publicNewsQuestion({
    ...row,
    conversation_title: conversation?.title ?? null,
  });
}

export async function failNewsQuestion(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const failureCode = String(input.failure_code ?? "WORKER_FAILURE").trim().toUpperCase();
  if (!/^[A-Z0-9_]{3,64}$/.test(failureCode)) {
    throw new NewsQuestionInputError("INVALID_FAILURE_CODE", "失败代码无效");
  }
  const timestamp = now.toISOString();
  const leased = await binding.prepare(
    `SELECT * FROM news_questions
     WHERE id=? AND status='PROCESSING' AND lease_token=?`,
  ).bind(id, leaseToken).first<NewsQuestionRow>();
  if (!leased) return null;

  const expired = Date.parse(leased.expires_at) <= now.getTime();
  const exhausted = Number(leased.attempt_count) >= Number(leased.max_attempts);
  const status: NewsQuestionStatus = expired ? "EXPIRED" : exhausted ? "FAILED" : "PENDING";
  const delaySeconds = Math.min(120, 30 * (2 ** Math.max(0, Number(leased.attempt_count) - 1)));
  const availableAt = status === "PENDING"
    ? new Date(now.getTime() + delaySeconds * 1_000).toISOString()
    : timestamp;
  const event = status === "PENDING" ? "RETRY_SCHEDULED" : status;
  const row = await binding.prepare(
    `UPDATE news_questions SET status=?,available_at=?,failure_code=?,
     processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
     attempt_history_json=${history(event)}
     WHERE id=? AND status='PROCESSING' AND lease_token=?
     RETURNING *`,
  ).bind(status, availableAt, failureCode, timestamp, id, leaseToken)
    .first<NewsQuestionRow>();
  return row ? publicNewsQuestion(row) : null;
}

export async function deferNewsQuestion(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const leased = await binding.prepare(
    `SELECT * FROM news_questions
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<NewsQuestionRow>();
  if (!leased) return null;
  const expired = Date.parse(leased.expires_at) <= now.getTime();
  const exhausted = Number(leased.attempt_count) >= Number(leased.max_attempts);
  const status: NewsQuestionStatus = expired ? "EXPIRED" : exhausted ? "FAILED" : "PENDING";
  const availableAt = status === "PENDING"
    ? new Date(now.getTime() + 60_000).toISOString() : timestamp;
  const row = await binding.prepare(
    `UPDATE news_questions SET status=?,available_at=?,
     failure_code='NO_MODEL_CAPACITY',
     processing_started_at=NULL,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
     attempt_history_json=json_insert(
       CASE WHEN json_valid(attempt_history_json) THEN attempt_history_json ELSE '[]' END,
       '$[#]',json_object('event','CAPACITY_DEFERRED','at',?,
         'attempt',attempt_count,'failure_code','NO_MODEL_CAPACITY','terminal',?))
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
     RETURNING *`,
  ).bind(
    status, availableAt, timestamp, status === "PENDING" ? 0 : 1,
    id, leaseToken, timestamp,
  )
    .first<NewsQuestionRow>();
  return row ? publicNewsQuestion(row) : null;
}
