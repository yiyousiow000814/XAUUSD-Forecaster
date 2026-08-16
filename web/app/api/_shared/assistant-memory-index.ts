import { AssistantConversationInputError } from "./assistant-conversations";

export const ASSISTANT_MEMORY_INDEX_VERSION = "assistant-memory-lexical-v1";

export const ASSISTANT_MEMORY_INDEX_LIMITS = {
  leaseMs: 2 * 60 * 1_000,
  maxAttempts: 3,
  maxIndexTerms: 64,
  maxQueryTerms: 16,
  maxCandidates: 24,
  maxItems: 8,
} as const;

type AssistantMemoryIndexJobRow = Record<string, unknown> & {
  id: string;
  owner_id: string;
  conversation_id: string;
  source_message_id: string;
  source_created_at: string;
  index_version: string;
  status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
  lease_token: string | null;
  lease_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
};

type AssistantMemoryCandidateRow = Record<string, unknown> & {
  id: string;
  conversation_id: string;
  source_message_id: string;
  source_role: "USER" | "ASSISTANT";
  source_created_at: string;
  source_content_sha256: string;
  content: string;
  provenance_json: string;
  overlap_count: number;
};

type TokenEstimator = (value: unknown) => number;

const isHan = (codepoint: number) => (
  (codepoint >= 0x3400 && codepoint <= 0x4dbf)
  || (codepoint >= 0x4e00 && codepoint <= 0x9fff)
  || (codepoint >= 0xf900 && codepoint <= 0xfaff)
);

export function tokenizeAssistantMemory(
  value: string,
  maximumTerms = ASSISTANT_MEMORY_INDEX_LIMITS.maxIndexTerms,
) {
  if (typeof value !== "string") throw new Error("Assistant memory source text must be a string");
  if (
    !Number.isSafeInteger(maximumTerms)
    || maximumTerms < 1
    || maximumTerms > ASSISTANT_MEMORY_INDEX_LIMITS.maxIndexTerms
  ) throw new Error("Assistant memory term bound is invalid");
  const normalized = value.normalize("NFKC");
  const terms: string[] = [];
  const seen = new Set<string>();
  const add = (term: string) => {
    if (term && term.length <= 64 && !seen.has(term) && terms.length < maximumTerms) {
      seen.add(term);
      terms.push(term);
    }
  };

  const characters = Array.from(normalized);
  let index = 0;
  while (index < characters.length && terms.length < maximumTerms) {
    const character = characters[index];
    const codepoint = character.codePointAt(0) ?? 0;
    if (/^[A-Za-z0-9]$/.test(character)) {
      let end = index + 1;
      while (end < characters.length && /^[A-Za-z0-9]$/.test(characters[end])) end += 1;
      add(characters.slice(index, end).join("").toLowerCase());
      index = end;
      continue;
    }
    if (isHan(codepoint)) {
      let end = index + 1;
      while (end < characters.length && isHan(characters[end].codePointAt(0) ?? 0)) end += 1;
      const run = characters.slice(index, end);
      if (run.length === 1) add(run[0]);
      else {
        for (let offset = 0; offset < run.length - 1; offset += 1) {
          add(run[offset] + run[offset + 1]);
          if (terms.length >= maximumTerms) break;
        }
      }
      index = end;
      continue;
    }
    index += 1;
  }
  return terms;
}

const sha256Hex = async (value: string) => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, "0"))
    .join("");
};

const normalizedCompletion = async (
  input: Record<string, unknown>,
  content: string,
) => {
  if (
    String(input.index_version ?? "") !== ASSISTANT_MEMORY_INDEX_VERSION
    || !Array.isArray(input.terms)
    || input.terms.length > ASSISTANT_MEMORY_INDEX_LIMITS.maxIndexTerms
    || input.terms.some(term => typeof term !== "string")
  ) throw new AssistantConversationInputError(
    "INVALID_MEMORY_INDEX_RESULT", "历史记忆索引结果无效",
  );
  const terms = tokenizeAssistantMemory(content);
  if (JSON.stringify(input.terms) !== JSON.stringify(terms)) {
    throw new AssistantConversationInputError(
      "INVALID_MEMORY_INDEX_RESULT", "历史记忆索引词项与规范消息不一致",
    );
  }
  const sourceContentSha256 = await sha256Hex(content);
  if (String(input.source_content_sha256 ?? "") !== sourceContentSha256) {
    throw new AssistantConversationInputError(
      "INVALID_MEMORY_INDEX_RESULT", "历史记忆索引摘要与规范消息不一致",
    );
  }
  return { terms, sourceContentSha256 };
};

const leaseCleanup = "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL";

export async function claimAssistantMemoryIndexJob(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(
    now.getTime() + ASSISTANT_MEMORY_INDEX_LIMITS.leaseMs,
  ).toISOString();
  const results = await binding.batch<AssistantMemoryIndexJobRow>([
    binding.prepare(
      `UPDATE assistant_memory_index_jobs SET status='FAILED',failure_code='LEASE_EXPIRED',
       completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',1)),${leaseCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count>=max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_memory_index_jobs SET status='PENDING',failure_code='LEASE_EXPIRED',
       available_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',0)),${leaseCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count<max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_memory_index_jobs SET status='PROCESSING',lease_owner=?,lease_token=?,
       lease_expires_at=?,attempt_count=attempt_count+1,failure_code=NULL,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CLAIMED','occurred_at',?,'attempt',attempt_count+1,
           'worker_id',?))
       WHERE id=(SELECT id FROM assistant_memory_index_jobs
         WHERE status='PENDING' AND available_at<=?
         ORDER BY created_at,id LIMIT 1)
       RETURNING *`,
    ).bind(workerId, leaseToken, leaseExpiresAt, timestamp, workerId, timestamp),
  ]);
  const job = results.at(-1)?.results?.[0];
  if (!job) return null;
  const message = await binding.prepare(
    `SELECT message.content,message.role,message.created_at
     FROM assistant_messages message
     JOIN assistant_conversations conversation ON conversation.id=message.conversation_id
     WHERE message.id=? AND message.conversation_id=? AND message.created_at=?
       AND conversation.owner_id=?`,
  ).bind(
    job.source_message_id, job.conversation_id, job.source_created_at, job.owner_id,
  ).first<{ content: string; role: string; created_at: string }>();
  if (!message) {
    await failAssistantMemoryIndexJob(binding, {
      id: job.id,
      lease_token: job.lease_token,
      failure_code: "MEMORY_SOURCE_MISSING",
    }, now);
    return null;
  }
  return {
    id: job.id,
    source_message_id: job.source_message_id,
    source_role: message.role,
    source_created_at: message.created_at,
    content: message.content,
    index_version: job.index_version,
    lease_token: String(job.lease_token),
    lease_expires_at: String(job.lease_expires_at),
    attempt_count: Number(job.attempt_count),
  };
}

export async function completeAssistantMemoryIndexJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_memory_index_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<AssistantMemoryIndexJobRow>();
  if (!job) return null;
  if (
    String(input.source_message_id ?? "") !== job.source_message_id
    || String(input.index_version ?? "") !== job.index_version
    || job.index_version !== ASSISTANT_MEMORY_INDEX_VERSION
  ) throw new AssistantConversationInputError(
    "INVALID_MEMORY_INDEX_RESULT", "历史记忆索引来源无效",
  );
  const message = await binding.prepare(
    `SELECT content,role,created_at FROM assistant_messages
     WHERE id=? AND conversation_id=? AND created_at=?`,
  ).bind(
    job.source_message_id, job.conversation_id, job.source_created_at,
  ).first<{ content: string; role: string; created_at: string }>();
  if (!message) throw new AssistantConversationInputError(
    "MEMORY_SOURCE_MISSING", "历史记忆规范消息不存在",
  );
  const normalized = await normalizedCompletion(input, message.content);
  const entryId = `memory-entry:${job.index_version}:${job.source_message_id}`;
  const entryStatement = binding.prepare(
    `INSERT INTO assistant_memory_entries (
       id,source_job_id,owner_id,conversation_id,source_message_id,source_role,
       source_created_at,source_content_sha256,index_version,term_count,indexed_at
     ) SELECT ?,id,owner_id,conversation_id,source_message_id,?,source_created_at,?,?,?,?
       FROM assistant_memory_index_jobs
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
     RETURNING *`,
  ).bind(
    entryId, message.role, normalized.sourceContentSha256, job.index_version,
    normalized.terms.length, timestamp, id, leaseToken, timestamp,
  );
  const termStatements = normalized.terms.map(term => binding.prepare(
    `INSERT INTO assistant_memory_terms (entry_id,owner_id,term,source_created_at)
     SELECT id,owner_id,?,source_created_at FROM assistant_memory_entries WHERE id=?`,
  ).bind(term, entryId));
  const results = await binding.batch<Record<string, unknown>>([
    entryStatement,
    ...termStatements,
    binding.prepare(
      `UPDATE assistant_memory_index_jobs SET status='COMPLETED',source_content_sha256=?,
       term_count=?,failure_code=NULL,completed_at=?,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','COMPLETED','occurred_at',?,'attempt',attempt_count,
           'term_count',?,'source_content_sha256',?)),${leaseCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      normalized.sourceContentSha256, normalized.terms.length, timestamp,
      timestamp, normalized.terms.length, normalized.sourceContentSha256,
      id, leaseToken, timestamp,
    ),
  ]);
  const entry = results[0]?.results?.[0];
  const completed = results.at(-1)?.results?.[0];
  if (!entry || !completed) return null;
  return {
    job_id: id,
    status: "COMPLETED" as const,
    source_message_id: job.source_message_id,
    source_content_sha256: normalized.sourceContentSha256,
    term_count: normalized.terms.length,
    index_version: job.index_version,
  };
}

export async function failAssistantMemoryIndexJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const failureCode = String(input.failure_code ?? "MEMORY_INDEX_FAILED").trim().toUpperCase();
  if (!/^[A-Z0-9_]{3,64}$/.test(failureCode)) {
    throw new AssistantConversationInputError("INVALID_FAILURE_CODE", "失败代码无效");
  }
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_memory_index_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<AssistantMemoryIndexJobRow>();
  if (!job) return null;
  const terminal = Number(job.attempt_count) >= Number(job.max_attempts);
  const delaySeconds = Math.min(60, 10 * (2 ** Math.max(0, Number(job.attempt_count) - 1)));
  const availableAt = terminal
    ? timestamp
    : new Date(now.getTime() + delaySeconds * 1_000).toISOString();
  const row = await binding.prepare(
    `UPDATE assistant_memory_index_jobs SET status=?,available_at=?,failure_code=?,
     completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
       json_object('event','FAILED','occurred_at',?,'attempt',attempt_count,
         'failure_code',?,'terminal',?)),${leaseCleanup}
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
     RETURNING *`,
  ).bind(
    terminal ? "FAILED" : "PENDING", availableAt, failureCode,
    terminal ? timestamp : null, timestamp, failureCode, terminal ? 1 : 0,
    id, leaseToken, timestamp,
  ).first<Record<string, unknown>>();
  return row ? { job_id: id, status: String(row.status) } : null;
}

export async function retrieveAssistantHistoricalMemory(
  binding: D1Database,
  input: {
    ownerId: string;
    conversationId: string;
    currentUser: { id: string; content: string; created_at: string };
    tokenBudget: number;
    estimateTokens: TokenEstimator;
  },
) {
  // Cross-conversation UUIDs cannot prove ordering inside one millisecond.
  // Exclude equal timestamps entirely so historical recall remains forward-only.
  const cutoffSql = "source_created_at<?";
  const cutoffBindings = [input.currentUser.created_at];
  const counts = await binding.prepare(
    `SELECT count(*) AS total_messages,
       COALESCE(sum(CASE WHEN status='COMPLETED' AND EXISTS (
         SELECT 1 FROM assistant_memory_entries entry
         WHERE entry.source_job_id=job.id
       ) THEN 1 ELSE 0 END),0) AS indexed_messages,
       COALESCE(sum(CASE WHEN status='PENDING' THEN 1 ELSE 0 END),0) AS pending_messages,
       COALESCE(sum(CASE WHEN status='PROCESSING' THEN 1 ELSE 0 END),0) AS processing_messages,
       COALESCE(sum(CASE WHEN status='FAILED' THEN 1 ELSE 0 END),0) AS failed_messages
     FROM assistant_memory_index_jobs job
     WHERE owner_id=? AND index_version=? AND conversation_id!=? AND ${cutoffSql}`,
  ).bind(
    input.ownerId, ASSISTANT_MEMORY_INDEX_VERSION, input.conversationId, ...cutoffBindings,
  ).first<Record<string, unknown>>();
  const queryTerms = tokenizeAssistantMemory(
    input.currentUser.content, ASSISTANT_MEMORY_INDEX_LIMITS.maxQueryTerms,
  );
  const candidates: AssistantMemoryCandidateRow[] = [];
  if (queryTerms.length) {
    const placeholders = queryTerms.map(() => "?").join(",");
    const rows = await binding.prepare(
      `SELECT entry.*,message.content,message.provenance_json,
         count(DISTINCT memory_term.term) AS overlap_count
       FROM assistant_memory_terms memory_term
       JOIN assistant_memory_entries entry ON entry.id=memory_term.entry_id
       JOIN assistant_messages message ON message.id=entry.source_message_id
       JOIN assistant_conversations conversation ON conversation.id=entry.conversation_id
       WHERE memory_term.owner_id=? AND entry.owner_id=?
         AND conversation.owner_id=? AND entry.index_version=?
         AND entry.conversation_id!=? AND memory_term.term IN (${placeholders})
         AND entry.source_created_at<?
       GROUP BY entry.id
       ORDER BY overlap_count DESC,entry.source_created_at DESC,entry.source_message_id DESC
       LIMIT ?`,
    ).bind(
      input.ownerId, input.ownerId, input.ownerId, ASSISTANT_MEMORY_INDEX_VERSION,
      input.conversationId, ...queryTerms, ...cutoffBindings,
      ASSISTANT_MEMORY_INDEX_LIMITS.maxCandidates,
    ).all<AssistantMemoryCandidateRow>();
    candidates.push(...rows.results);
  }
  const totalMessages = Number(counts?.total_messages ?? 0);
  const indexedMessages = Number(counts?.indexed_messages ?? 0);
  const retrieval = {
    index_version: ASSISTANT_MEMORY_INDEX_VERSION,
    index_complete: totalMessages === indexedMessages,
    total_messages: totalMessages,
    indexed_messages: indexedMessages,
    pending_messages: Number(counts?.pending_messages ?? 0),
    processing_messages: Number(counts?.processing_messages ?? 0),
    failed_messages: Number(counts?.failed_messages ?? 0),
    query_term_count: queryTerms.length,
    matched_entries: candidates.length,
    selected_entries: 0,
    current_conversation_excluded: true,
    trust: "UNVERIFIED_CONVERSATION_TEXT" as const,
    integrity_failures: 0,
  };
  const items: Array<Record<string, unknown>> = [];
  let integrityFailures = 0;
  let tokenEstimate = input.estimateTokens({ retrieval, items });
  for (const candidate of candidates) {
    if (items.length >= ASSISTANT_MEMORY_INDEX_LIMITS.maxItems) break;
    if (await sha256Hex(candidate.content) !== candidate.source_content_sha256) {
      integrityFailures += 1;
      retrieval.integrity_failures = integrityFailures;
      continue;
    }
    const indexedTerms = new Set(tokenizeAssistantMemory(candidate.content));
    const item = {
      content: candidate.content,
      canonical_message_ids: [candidate.source_message_id],
      source_conversation_id: candidate.conversation_id,
      role: candidate.source_role,
      created_at: candidate.source_created_at,
      match_terms: queryTerms.filter(term => indexedTerms.has(term)),
      overlap_count: Number(candidate.overlap_count),
      trust: "UNVERIFIED_CONVERSATION_TEXT",
    };
    const nextRetrieval = { ...retrieval, selected_entries: items.length + 1 };
    const nextEstimate = input.estimateTokens({ retrieval: nextRetrieval, items: [...items, item] });
    if (nextEstimate <= input.tokenBudget) {
      items.push(item);
      retrieval.selected_entries = items.length;
      tokenEstimate = nextEstimate;
    }
  }
  if (integrityFailures) retrieval.index_complete = false;
  tokenEstimate = input.estimateTokens({ retrieval, items });
  while (items.length && tokenEstimate > input.tokenBudget) {
    items.pop();
    retrieval.selected_entries = items.length;
    tokenEstimate = input.estimateTokens({ retrieval, items });
  }
  return {
    items,
    tokenEstimate,
    retrieval,
  };
}
