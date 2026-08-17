import { AssistantConversationInputError } from "./assistant-conversations";
import { retrieveAssistantHistoricalMemory } from "./assistant-memory-index";
import { parseAssistantRoutingProvenance } from "./assistant-routing";
import { ASSISTANT_CONTEXT_LIMIT_TOKENS } from "../../_lib/assistant-runtime-limits";

export const ASSISTANT_COMPACTION_PROMPT_VERSION = "assistant-compaction-v1";

export const ASSISTANT_PIN_KINDS = [
  "CONSTRAINT",
  "UNRESOLVED",
  "DECISION",
  "TASK_SCOPE",
  "EVIDENCE_REF",
  "TOOL_ARTIFACT",
  "IMPORTANT_TIMESTAMP",
  "TOPIC",
] as const;

export type AssistantPinKind = typeof ASSISTANT_PIN_KINDS[number];
export type AssistantCapacityState = "GREEN" | "YELLOW" | "RED";

export type AssistantContextProfile = {
  id: string;
  contextLimitTokens: number;
  greenThresholdRatio: number;
  yellowThresholdRatio: number;
  reservedSystemTokens: number;
  reservedToolDefinitionTokens: number;
  reservedReasoningTokens: number;
  reservedOutputTokens: number;
  pinnedTokenBudget: number;
  summaryTokenBudget: number;
  historicalMemoryTokenBudget: number;
  recentTurnsTokenBudget: number;
  currentUserTokenBudget: number;
  toolEvidenceTokenBudget: number;
  recentTurnLimit: number;
  recentMessageLimit: number;
  compactionMessageLimit: number;
  compactionSourceTokenBudget: number;
};

// Operational safety data for the current bounded memory worker. The later
// model router may select a different profile; conversation rows never do.
export const DEFAULT_ASSISTANT_CONTEXT_PROFILE: AssistantContextProfile = {
  id: "assistant-context-256k-v2",
  contextLimitTokens: ASSISTANT_CONTEXT_LIMIT_TOKENS,
  greenThresholdRatio: 0.60,
  yellowThresholdRatio: 0.82,
  reservedSystemTokens: 2_048,
  reservedToolDefinitionTokens: 2_048,
  reservedReasoningTokens: 16_384,
  reservedOutputTokens: 8_192,
  pinnedTokenBudget: 8_192,
  summaryTokenBudget: 16_384,
  historicalMemoryTokenBudget: 16_384,
  recentTurnsTokenBudget: 98_304,
  currentUserTokenBudget: 16_384,
  toolEvidenceTokenBudget: 32_768,
  recentTurnLimit: 24,
  recentMessageLimit: 96,
  compactionMessageLimit: 48,
  compactionSourceTokenBudget: 98_304,
};

export const ASSISTANT_MEMORY_LIMITS = {
  compactionLeaseMs: 3 * 60 * 1_000,
  compactionMaxAttempts: 3,
  maxPinnedEntries: 64,
  maxGeneratedPins: 24,
  maxPinContentCharacters: 1_200,
  maxSummaryCharacters: 8_000,
  maxReferenceItems: 64,
  maxAnchorItems: 512,
  activeCompactionsPerOwner: 2,
  activeCompactionsGlobal: 20,
} as const;

type AssistantMessageRow = Record<string, unknown> & {
  id: string;
  conversation_id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  created_at: string;
  provenance_json: string;
};

type AssistantSummaryRow = Record<string, unknown> & {
  id: string;
  conversation_id: string;
  version: number;
  content: string;
  anchors_json: string;
  estimated_tokens: number;
  covered_through_message_id: string;
  covered_through_created_at: string;
};

type AssistantCompactionJobRow = Record<string, unknown> & {
  id: string;
  conversation_id: string;
  prior_summary_version: number;
  output_summary_version: number;
  source_message_ids_json: string;
  source_message_count: number;
  first_source_message_id: string;
  last_source_message_id: string;
  pinned_snapshot_json: string;
  context_profile_id: string;
  capacity_state: AssistantCapacityState;
  estimated_context_tokens: number;
  status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
  lease_token: string | null;
  lease_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
  prompt_version: string;
};

type AssistantPinnedEntry = {
  id: string;
  kind: AssistantPinKind;
  content: string;
  origin_message_ids: string[];
  evidence_ids: string[];
  source_refs: string[];
  important_timestamps: string[];
  tool_refs: string[];
  artifact_refs: string[];
  created_at: string;
};

type CompactionPinInput = Omit<AssistantPinnedEntry, "id" | "created_at">;

const pinKinds = new Set<string>(ASSISTANT_PIN_KINDS);
const referencePattern = /^.{1,256}$/su;
const validReference = (value: string) => referencePattern.test(value)
  && !Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });

const parsedJson = (value: unknown, fallback: unknown) => {
  if (typeof value !== "string") return fallback;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return fallback;
  }
};

const orderedUniqueStrings = (
  value: unknown,
  field: string,
  maximum = ASSISTANT_MEMORY_LIMITS.maxReferenceItems,
) => {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", `${field} 无效`);
  }
  const result: string[] = [];
  const seen = new Set<string>();
  for (const raw of value) {
    const item = String(raw ?? "").normalize("NFKC").trim();
    if (!validReference(item)) {
      throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", `${field} 无效`);
    }
    if (!seen.has(item)) {
      seen.add(item);
      result.push(item);
    }
  }
  return result;
};

const canonicalText = (value: unknown, maximum: number, field: string) => {
  const text = String(value ?? "").normalize("NFKC").trim();
  if (!text || text.length > maximum) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", `${field} 无效`);
  }
  return text;
};

export const conservativeAssistantTokenEstimate = (value: unknown) => {
  const serialized = typeof value === "string" ? value : JSON.stringify(value ?? null);
  return Math.max(1, new TextEncoder().encode(serialized).length + 8);
};

const validateProfile = (profile: AssistantContextProfile) => {
  const integerFields: (keyof AssistantContextProfile)[] = [
    "contextLimitTokens", "reservedSystemTokens", "reservedToolDefinitionTokens",
    "reservedReasoningTokens", "reservedOutputTokens", "pinnedTokenBudget",
    "summaryTokenBudget", "historicalMemoryTokenBudget", "recentTurnsTokenBudget",
    "currentUserTokenBudget", "toolEvidenceTokenBudget", "recentTurnLimit",
    "recentMessageLimit",
    "compactionMessageLimit", "compactionSourceTokenBudget",
  ];
  if (
    !/^[A-Za-z0-9._:-]{3,96}$/.test(profile.id)
    || integerFields.some(field => !Number.isSafeInteger(Number(profile[field])) || Number(profile[field]) <= 0)
    || !(profile.greenThresholdRatio > 0 && profile.greenThresholdRatio < profile.yellowThresholdRatio)
    || !(profile.yellowThresholdRatio < 1)
  ) throw new Error("invalid Assistant context profile");
  const reserved = profile.reservedSystemTokens + profile.reservedToolDefinitionTokens
    + profile.reservedReasoningTokens + profile.reservedOutputTokens;
  if (reserved >= profile.contextLimitTokens) {
    throw new Error("Assistant context profile reserves the complete context window");
  }
  return profile;
};

export function classifyAssistantContextCapacity(
  estimatedTokens: number,
  profile = DEFAULT_ASSISTANT_CONTEXT_PROFILE,
): AssistantCapacityState {
  validateProfile(profile);
  const ratio = Math.max(0, estimatedTokens) / profile.contextLimitTokens;
  if (ratio < profile.greenThresholdRatio) return "GREEN";
  if (ratio < profile.yellowThresholdRatio) return "YELLOW";
  return "RED";
}

const parsePinnedEntry = (row: Record<string, unknown>): AssistantPinnedEntry => ({
  id: String(row.id),
  kind: String(row.kind) as AssistantPinKind,
  content: String(row.content),
  origin_message_ids: parsedJson(row.origin_message_ids_json, []) as string[],
  evidence_ids: parsedJson(row.evidence_ids_json, []) as string[],
  source_refs: parsedJson(row.source_refs_json, []) as string[],
  important_timestamps: parsedJson(row.important_timestamps_json, []) as string[],
  tool_refs: parsedJson(row.tool_refs_json, []) as string[],
  artifact_refs: parsedJson(row.artifact_refs_json, []) as string[],
  created_at: String(row.created_at),
});

async function listPinnedEntries(
  binding: D1Database,
  conversationId: string,
  cutoff?: { created_at: string; id: string },
) {
  const rows = await binding.prepare(
    `SELECT pinned.* FROM assistant_pinned_entries pinned WHERE pinned.conversation_id=?
       ${cutoff ? `AND NOT EXISTS (
         SELECT 1 FROM json_each(pinned.origin_message_ids_json) origin
         JOIN assistant_messages message ON message.id=origin.value
         WHERE message.created_at>? OR (message.created_at=? AND message.id>?)
       )` : ""}
     ORDER BY pinned.created_at,pinned.id LIMIT ?`,
  ).bind(
    conversationId,
    ...(cutoff ? [cutoff.created_at, cutoff.created_at, cutoff.id] : []),
    ASSISTANT_MEMORY_LIMITS.maxPinnedEntries + 1,
  )
    .all<Record<string, unknown>>();
  if (rows.results.length > ASSISTANT_MEMORY_LIMITS.maxPinnedEntries) {
    throw new AssistantConversationInputError(
      "PINNED_STATE_EXCEEDS_BUDGET", "固定状态超过安全上限",
    );
  }
  return rows.results.map(parsePinnedEntry);
}

async function currentSummary(
  binding: D1Database,
  conversationId: string,
  version: number,
) {
  if (version === 0) return null;
  const summary = await binding.prepare(
    "SELECT * FROM assistant_summaries WHERE conversation_id=? AND version=?",
  ).bind(conversationId, version).first<AssistantSummaryRow>();
  if (!summary) {
    throw new AssistantConversationInputError("SUMMARY_STATE_INVALID", "会话摘要状态无效");
  }
  return summary;
}

const orderingAfter = (alias: string, createdAt: string | null, id: string | null) => (
  createdAt && id
    ? `(${alias}.created_at>? OR (${alias}.created_at=? AND ${alias}.id>?))`
    : "1=1"
);

const orderingBefore = (alias: string) => (
  `(${alias}.created_at<? OR (${alias}.created_at=? AND ${alias}.id<?))`
);

const profileReservedTokens = (profile: AssistantContextProfile) => (
  profile.reservedSystemTokens + profile.reservedToolDefinitionTokens
  + profile.reservedReasoningTokens + profile.reservedOutputTokens
);

const trimRecentWholeTurns = (
  messages: AssistantMessageRow[],
  budget: number,
  totalAvailable: number,
) => {
  let selected = [...messages];
  const maximum = Math.min(budget, totalAvailable);
  const estimate = () => conservativeAssistantTokenEstimate(selected.map(message => ({
    id: message.id,
    role: message.role,
    content: message.content,
    created_at: message.created_at,
  })));
  while (selected.length && estimate() > maximum) {
    const nextUser = selected.findIndex((message, index) => index > 0 && message.role === "USER");
    selected = nextUser < 0 ? [] : selected.slice(nextUser);
  }
  return { messages: selected, tokens: selected.length ? estimate() : 0 };
};

export async function buildAssistantContext(
  binding: D1Database,
  input: {
    ownerId: string;
    conversationId: string;
    currentUserMessageId: string;
    toolEvidence?: Array<{ evidence_id: string; content: unknown }>;
    semanticMatches?: Array<{ id: string; score: number }>;
    semanticAvailable?: boolean;
  },
  profile = DEFAULT_ASSISTANT_CONTEXT_PROFILE,
) {
  validateProfile(profile);
  const conversation = await binding.prepare(
    "SELECT * FROM assistant_conversations WHERE owner_id=? AND id=?",
  ).bind(input.ownerId, input.conversationId).first<Record<string, unknown>>();
  if (!conversation) return null;
  const currentUser = await binding.prepare(
    `SELECT m.* FROM assistant_messages m
     JOIN assistant_conversations c ON c.id=m.conversation_id
     WHERE c.owner_id=? AND c.id=? AND m.id=? AND m.role='USER'`,
  ).bind(input.ownerId, input.conversationId, input.currentUserMessageId)
    .first<AssistantMessageRow>();
  if (!currentUser) {
    throw new AssistantConversationInputError(
      "CURRENT_USER_MESSAGE_NOT_FOUND", "当前用户消息不属于这个会话",
    );
  }

  const summary = await binding.prepare(
    `SELECT * FROM assistant_summaries WHERE conversation_id=?
       AND (covered_through_created_at<?
         OR (covered_through_created_at=? AND covered_through_message_id<?))
     ORDER BY version DESC LIMIT 1`,
  ).bind(
    input.conversationId, currentUser.created_at, currentUser.created_at, currentUser.id,
  ).first<AssistantSummaryRow>();
  const pins = await listPinnedEntries(binding, input.conversationId, currentUser);
  const pinnedPayload = pins.map(({ id, kind, content, origin_message_ids, evidence_ids,
    source_refs, important_timestamps, tool_refs, artifact_refs }) => ({
    id, kind, content, origin_message_ids, evidence_ids, source_refs,
    important_timestamps, tool_refs, artifact_refs,
  }));
  const summaryPayload = summary ? {
    id: summary.id,
    version: Number(summary.version),
    content: summary.content,
    anchors: parsedJson(summary.anchors_json, {}),
    covered_through_message_id: summary.covered_through_message_id,
  } : null;
  const toolEvidence = input.toolEvidence ?? [];
  if (toolEvidence.length > 32) {
    throw new AssistantConversationInputError("CONTEXT_LAYER_EXCEEDS_BUDGET", "上下文资料过多");
  }
  const historicalMemory = await retrieveAssistantHistoricalMemory(binding, {
    ownerId: input.ownerId,
    conversationId: input.conversationId,
    currentUser: {
      id: currentUser.id,
      content: currentUser.content,
      created_at: currentUser.created_at,
    },
    tokenBudget: profile.historicalMemoryTokenBudget,
    estimateTokens: conservativeAssistantTokenEstimate,
    semanticMatches: input.semanticMatches,
    semanticAvailable: input.semanticAvailable,
  });
  const normalizedEvidence = toolEvidence.map(item => ({
    evidence_id: orderedUniqueStrings([item.evidence_id], "evidence_id", 1)[0],
    content: item.content,
  }));

  const summaryCreatedAt = summary?.covered_through_created_at ?? null;
  const summaryMessageId = summary?.covered_through_message_id ?? null;
  const afterSummarySql = orderingAfter("m", summaryCreatedAt, summaryMessageId);
  const afterSummaryBindings = summaryCreatedAt && summaryMessageId
    ? [summaryCreatedAt, summaryCreatedAt, summaryMessageId]
    : [];
  const atOrBeforeCurrentSql = "(m.created_at<? OR (m.created_at=? AND m.id<=?))";
  const currentCutoffBindings = [currentUser.created_at, currentUser.created_at, currentUser.id];
  const recentBoundary = await binding.prepare(
    `SELECT m.id,m.created_at FROM assistant_messages m
     WHERE m.conversation_id=? AND ${afterSummarySql}
       AND ${atOrBeforeCurrentSql} AND m.role='USER'
     ORDER BY m.created_at DESC,m.id DESC LIMIT 1 OFFSET ?`,
  ).bind(
    input.conversationId, ...afterSummaryBindings, ...currentCutoffBindings,
    profile.recentTurnLimit - 1,
  ).first<{ id: string; created_at: string }>();
  if (recentBoundary) {
    const gap = await binding.prepare(
      `SELECT count(*) AS count FROM assistant_messages m
       WHERE m.conversation_id=? AND ${afterSummarySql}
         AND ${atOrBeforeCurrentSql} AND ${orderingBefore("m")}`,
    ).bind(
      input.conversationId, ...afterSummaryBindings, ...currentCutoffBindings,
      recentBoundary.created_at, recentBoundary.created_at, recentBoundary.id,
    ).first<{ count: number }>();
    if (Number(gap?.count ?? 0) > 0) {
      throw new AssistantConversationInputError(
        "COMPACTION_REQUIRED", "旧消息仍在等待安全压缩，暂时不能省略",
      );
    }
  }
  const boundarySql = recentBoundary
    ? "AND (m.created_at>? OR (m.created_at=? AND m.id>=?))"
    : "";
  const recentRows = await binding.prepare(
    `SELECT m.* FROM assistant_messages m WHERE m.conversation_id=?
       AND ${afterSummarySql} AND ${atOrBeforeCurrentSql} ${boundarySql}
     ORDER BY m.created_at,m.id LIMIT ?`,
  ).bind(
    input.conversationId, ...afterSummaryBindings, ...currentCutoffBindings,
    ...(recentBoundary
      ? [recentBoundary.created_at, recentBoundary.created_at, recentBoundary.id]
      : []),
    profile.recentMessageLimit + 1,
  ).all<AssistantMessageRow>();
  if (recentRows.results.length > profile.recentMessageLimit) {
    throw new AssistantConversationInputError(
      "RECENT_TURNS_EXCEED_BUDGET", "最近逐字对话超过安全消息上限",
    );
  }
  const recentCandidates = recentRows.results
    .filter(message => message.id !== currentUser.id);

  const pinnedTokens = conservativeAssistantTokenEstimate(pinnedPayload);
  const summaryTokens = summaryPayload ? conservativeAssistantTokenEstimate(summaryPayload) : 0;
  const historicalTokens = historicalMemory.tokenEstimate;
  const currentUserTokens = conservativeAssistantTokenEstimate({
    id: currentUser.id, content: currentUser.content, created_at: currentUser.created_at,
  });
  const evidenceTokens = normalizedEvidence.length
    ? conservativeAssistantTokenEstimate(normalizedEvidence) : 0;
  const layerChecks: Array<[number, number, string]> = [
    [pinnedTokens, profile.pinnedTokenBudget, "PINNED_STATE_EXCEEDS_BUDGET"],
    [summaryTokens, profile.summaryTokenBudget, "SUMMARY_EXCEEDS_BUDGET"],
    [historicalTokens, profile.historicalMemoryTokenBudget, "HISTORICAL_MEMORY_EXCEEDS_BUDGET"],
    [currentUserTokens, profile.currentUserTokenBudget, "CURRENT_USER_EXCEEDS_BUDGET"],
    [evidenceTokens, profile.toolEvidenceTokenBudget, "TOOL_EVIDENCE_EXCEEDS_BUDGET"],
  ];
  for (const [actual, maximum, code] of layerChecks) {
    if (actual > maximum) {
      throw new AssistantConversationInputError(code, "必要上下文超过模型安全预算");
    }
  }
  const requiredTokens = profileReservedTokens(profile) + pinnedTokens + summaryTokens
    + historicalTokens + currentUserTokens + evidenceTokens;
  const availableRecent = profile.contextLimitTokens - requiredTokens;
  if (availableRecent < 0) {
    throw new AssistantConversationInputError(
      "REQUIRED_CONTEXT_EXCEEDS_BUDGET", "必要上下文超过模型窗口，无法安全继续",
    );
  }
  const recent = trimRecentWholeTurns(
    recentCandidates, profile.recentTurnsTokenBudget, availableRecent,
  );
  const estimatedTokens = requiredTokens + recent.tokens;
  return {
    profile_id: profile.id,
    capacity_state: classifyAssistantContextCapacity(estimatedTokens, profile),
    estimated_tokens: estimatedTokens,
    context_limit_tokens: profile.contextLimitTokens,
    reserved_tokens: profileReservedTokens(profile),
    layers: [
      { type: "PINNED_STATE", token_estimate: pinnedTokens, items: pinnedPayload },
      { type: "ROLLING_SUMMARY", token_estimate: summaryTokens, item: summaryPayload },
      { type: "HISTORICAL_MEMORY", token_estimate: historicalTokens,
        items: historicalMemory.items, retrieval: historicalMemory.retrieval },
      { type: "RECENT_VERBATIM_TURNS", token_estimate: recent.tokens, items: recent.messages.map(
        message => ({ id: message.id, role: message.role, content: message.content,
          created_at: message.created_at }),
      ) },
      { type: "CURRENT_USER_MESSAGE", token_estimate: currentUserTokens, item: {
        id: currentUser.id, role: currentUser.role, content: currentUser.content,
        created_at: currentUser.created_at,
      } },
      { type: "TOOL_EVIDENCE", token_estimate: evidenceTokens, items: normalizedEvidence },
    ],
  };
}

const parsePinInput = (value: unknown, allowedOrigins?: Set<string>): CompactionPinInput => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", "固定状态无效");
  }
  const raw = value as Record<string, unknown>;
  const kind = String(raw.kind ?? "").toUpperCase();
  if (!pinKinds.has(kind)) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", "固定状态类型无效");
  }
  const origins = orderedUniqueStrings(raw.origin_message_ids, "origin_message_ids");
  if (!origins.length || (allowedOrigins && origins.some(id => !allowedOrigins.has(id)))) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", "固定状态来源无效");
  }
  return {
    kind: kind as AssistantPinKind,
    content: canonicalText(
      raw.content, ASSISTANT_MEMORY_LIMITS.maxPinContentCharacters, "pinned content",
    ),
    origin_message_ids: origins,
    evidence_ids: orderedUniqueStrings(raw.evidence_ids ?? [], "evidence_ids"),
    source_refs: orderedUniqueStrings(raw.source_refs ?? [], "source_refs"),
    important_timestamps: orderedUniqueStrings(
      raw.important_timestamps ?? [], "important_timestamps",
    ),
    tool_refs: orderedUniqueStrings(raw.tool_refs ?? [], "tool_refs"),
    artifact_refs: orderedUniqueStrings(raw.artifact_refs ?? [], "artifact_refs"),
  };
};

export async function createAssistantPinnedEntry(
  binding: D1Database,
  input: {
    ownerId: string;
    conversationId: string;
    idempotencyKey: string;
    entry: unknown;
    now?: Date;
  },
) {
  const conversation = await binding.prepare(
    "SELECT id FROM assistant_conversations WHERE owner_id=? AND id=?",
  ).bind(input.ownerId, input.conversationId).first<{ id: string }>();
  if (!conversation) return null;
  const replay = await binding.prepare(
    "SELECT * FROM assistant_pinned_entries WHERE conversation_id=? AND idempotency_key=?",
  ).bind(input.conversationId, input.idempotencyKey).first<Record<string, unknown>>();
  if (replay) return parsePinnedEntry(replay);
  const entry = parsePinInput(input.entry);
  const placeholders = entry.origin_message_ids.map(() => "?").join(",");
  const origins = await binding.prepare(
    `SELECT count(*) AS count FROM assistant_messages
     WHERE conversation_id=? AND id IN (${placeholders})`,
  ).bind(input.conversationId, ...entry.origin_message_ids).first<{ count: number }>();
  if (Number(origins?.count ?? 0) !== entry.origin_message_ids.length) {
    throw new AssistantConversationInputError("INVALID_PIN_ORIGIN", "固定状态来源无效");
  }
  const existingPins = await listPinnedEntries(binding, input.conversationId);
  if (
    existingPins.length >= ASSISTANT_MEMORY_LIMITS.maxPinnedEntries
    || conservativeAssistantTokenEstimate([...existingPins, entry])
      > DEFAULT_ASSISTANT_CONTEXT_PROFILE.pinnedTokenBudget
  ) throw new AssistantConversationInputError(
    "PINNED_STATE_EXCEEDS_BUDGET", "固定状态超过安全上限",
  );
  const timestamp = (input.now ?? new Date()).toISOString();
  const id = crypto.randomUUID();
  const row = await binding.prepare(
    `INSERT INTO assistant_pinned_entries (
       id,conversation_id,idempotency_key,kind,content,origin_message_ids_json,
       evidence_ids_json,source_refs_json,important_timestamps_json,tool_refs_json,
       artifact_refs_json,created_by,created_at
     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'SYSTEM',?)
     ON CONFLICT(conversation_id,idempotency_key) DO NOTHING RETURNING *`,
  ).bind(
    id, input.conversationId, input.idempotencyKey, entry.kind, entry.content,
    JSON.stringify(entry.origin_message_ids), JSON.stringify(entry.evidence_ids),
    JSON.stringify(entry.source_refs), JSON.stringify(entry.important_timestamps),
    JSON.stringify(entry.tool_refs), JSON.stringify(entry.artifact_refs), timestamp,
  ).first<Record<string, unknown>>();
  const result = row ?? await binding.prepare(
    "SELECT * FROM assistant_pinned_entries WHERE conversation_id=? AND idempotency_key=?",
  ).bind(input.conversationId, input.idempotencyKey).first<Record<string, unknown>>();
  return result ? parsePinnedEntry(result) : null;
}

export type CompactionScheduleOutcome =
  | { kind: "CREATED" | "EXISTING"; job_id: string; status: string; capacity_state: string }
  | { kind: "NOT_NEEDED" | "NOT_FOUND" | "DEFERRED_CAPACITY"
    | "BLOCKED_PINNED_STATE" | "BLOCKED_SOURCE_TOO_LARGE" };

export async function scheduleAssistantCompaction(
  binding: D1Database,
  conversationId: string,
  options: { now?: Date; profile?: AssistantContextProfile } = {},
): Promise<CompactionScheduleOutcome> {
  const profile = validateProfile(options.profile ?? DEFAULT_ASSISTANT_CONTEXT_PROFILE);
  const conversation = await binding.prepare(
    "SELECT * FROM assistant_conversations WHERE id=?",
  ).bind(conversationId).first<Record<string, unknown>>();
  if (!conversation) return { kind: "NOT_FOUND" };
  if (conversation.pending_compaction_job_id) {
    const existing = await binding.prepare(
      "SELECT id,status,capacity_state FROM assistant_compaction_jobs WHERE id=?",
    ).bind(conversation.pending_compaction_job_id).first<Record<string, unknown>>();
    if (existing) return {
      kind: "EXISTING", job_id: String(existing.id), status: String(existing.status),
      capacity_state: String(existing.capacity_state),
    };
  }
  const summaryVersion = Number(conversation.summary_version ?? 0);
  const summary = await currentSummary(binding, conversationId, summaryVersion);
  const pins = await listPinnedEntries(binding, conversationId);
  const pinnedTokens = conservativeAssistantTokenEstimate(pins);
  if (pinnedTokens > profile.pinnedTokenBudget) return { kind: "BLOCKED_PINNED_STATE" };
  const afterSql = orderingAfter(
    "m", summary?.covered_through_created_at ?? null, summary?.covered_through_message_id ?? null,
  );
  const afterBindings = summary
    ? [summary.covered_through_created_at, summary.covered_through_created_at,
      summary.covered_through_message_id]
    : [];
  const aggregate = await binding.prepare(
    `SELECT count(*) AS count,
       COALESCE(sum(length(CAST(m.content AS BLOB)) + 24),0) AS tokens
     FROM assistant_messages m WHERE m.conversation_id=? AND ${afterSql}`,
  ).bind(conversationId, ...afterBindings).first<{ count: number; tokens: number }>();
  const estimatedContextTokens = profileReservedTokens(profile) + pinnedTokens
    + Number(summary?.estimated_tokens ?? 0) + Number(aggregate?.tokens ?? 0);
  const capacityState = classifyAssistantContextCapacity(estimatedContextTokens, profile);

  const recentBoundary = await binding.prepare(
    `SELECT id,created_at FROM assistant_messages
     WHERE conversation_id=? AND role='USER'
     ORDER BY created_at DESC,id DESC LIMIT 1 OFFSET ?`,
  ).bind(conversationId, profile.recentTurnLimit - 1)
    .first<{ id: string; created_at: string }>();
  if (!recentBoundary) return { kind: "NOT_NEEDED" };
  const candidates = await binding.prepare(
    `SELECT m.* FROM assistant_messages m WHERE m.conversation_id=?
       AND ${afterSql} AND ${orderingBefore("m")}
     ORDER BY m.created_at,m.id LIMIT ?`,
  ).bind(
    conversationId, ...afterBindings,
    recentBoundary.created_at, recentBoundary.created_at, recentBoundary.id,
    profile.compactionMessageLimit,
  ).all<AssistantMessageRow>();
  const selected: AssistantMessageRow[] = [];
  let sourceTokens = 0;
  for (const candidate of candidates.results) {
    const tokens = conservativeAssistantTokenEstimate({
      id: candidate.id, role: candidate.role, content: candidate.content,
      created_at: candidate.created_at,
    });
    if (!selected.length && tokens > profile.compactionSourceTokenBudget) {
      return { kind: "BLOCKED_SOURCE_TOO_LARGE" };
    }
    if (sourceTokens + tokens > profile.compactionSourceTokenBudget) break;
    selected.push(candidate);
    sourceTokens += tokens;
  }
  if (!selected.length) return { kind: "NOT_NEEDED" };

  const timestamp = (options.now ?? new Date()).toISOString();
  const jobId = crypto.randomUUID();
  const nextVersion = summaryVersion + 1;
  const priorRequestVersion = Number(conversation.compaction_request_version ?? 0);
  const requestVersion = priorRequestVersion + 1;
  const sourceIds = selected.map(message => message.id);
  const pinnedSnapshot = pins.map(pin => ({
    id: pin.id, kind: pin.kind, content: pin.content,
    origin_message_ids: pin.origin_message_ids, evidence_ids: pin.evidence_ids,
    source_refs: pin.source_refs, important_timestamps: pin.important_timestamps,
    tool_refs: pin.tool_refs, artifact_refs: pin.artifact_refs,
  }));
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_conversations SET pending_compaction_job_id=?,compaction_request_version=?
       WHERE id=? AND summary_version=? AND compaction_request_version=?
         AND pending_compaction_job_id IS NULL
       RETURNING *`,
    ).bind(jobId, requestVersion, conversationId, summaryVersion, priorRequestVersion),
    binding.prepare(
      `INSERT INTO assistant_compaction_jobs (
       id,conversation_id,input_version,prior_summary_version,output_summary_version,
       source_message_ids_json,source_message_count,first_source_message_id,
       last_source_message_id,pinned_snapshot_json,context_profile_id,capacity_state,
       estimated_context_tokens,status,available_at,attempt_count,max_attempts,
       prompt_version,created_at
       )
       SELECT ?,id,compaction_request_version,?,?, ?,?,?,?, ?,?,?,?,'PENDING',?,0,?,?,?
       FROM assistant_conversations
       WHERE id=? AND summary_version=? AND compaction_request_version=?
         AND pending_compaction_job_id=?
         AND (SELECT count(*) FROM assistant_compaction_jobs active
              WHERE active.status IN ('PENDING','PROCESSING')) < ?
         AND (SELECT count(*) FROM assistant_compaction_jobs active
              JOIN assistant_conversations owned
                ON owned.id=active.conversation_id
              WHERE active.status IN ('PENDING','PROCESSING')
                AND owned.owner_id=assistant_conversations.owner_id) < ?
       RETURNING *`,
    ).bind(
      jobId, summaryVersion, nextVersion,
      JSON.stringify(sourceIds), sourceIds.length, sourceIds[0], sourceIds[sourceIds.length - 1],
      JSON.stringify(pinnedSnapshot), profile.id, capacityState, estimatedContextTokens,
      timestamp, ASSISTANT_MEMORY_LIMITS.compactionMaxAttempts,
      ASSISTANT_COMPACTION_PROMPT_VERSION, timestamp,
      conversationId, summaryVersion, requestVersion, jobId,
      ASSISTANT_MEMORY_LIMITS.activeCompactionsGlobal,
      ASSISTANT_MEMORY_LIMITS.activeCompactionsPerOwner,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET pending_compaction_job_id=NULL
       WHERE id=? AND pending_compaction_job_id=?
         AND NOT EXISTS (SELECT 1 FROM assistant_compaction_jobs WHERE id=?)`,
    ).bind(conversationId, jobId, jobId),
  ]);
  const job = results[1]?.results?.[0];
  if (job) return {
    kind: "CREATED", job_id: String(job.id), status: String(job.status),
    capacity_state: String(job.capacity_state),
  };
  const replay = await binding.prepare(
    `SELECT id,status,capacity_state FROM assistant_compaction_jobs
     WHERE conversation_id=? AND output_summary_version=?
       AND status IN ('PENDING','PROCESSING')
     ORDER BY input_version DESC LIMIT 1`,
  ).bind(conversationId, nextVersion).first<Record<string, unknown>>();
  return replay ? {
    kind: "EXISTING", job_id: String(replay.id), status: String(replay.status),
    capacity_state: String(replay.capacity_state),
  } : { kind: "DEFERRED_CAPACITY" };
}

const compactionLeaseCleanup = "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL";

export async function claimAssistantCompactionJob(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(
    now.getTime() + ASSISTANT_MEMORY_LIMITS.compactionLeaseMs,
  ).toISOString();
  const results = await binding.batch<AssistantCompactionJobRow>([
    binding.prepare(
      `UPDATE assistant_conversations SET pending_compaction_job_id=NULL
       WHERE pending_compaction_job_id IN (
         SELECT id FROM assistant_compaction_jobs WHERE status='PROCESSING'
           AND lease_expires_at<=? AND attempt_count>=max_attempts
       )`,
    ).bind(timestamp),
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status='FAILED',failure_code='LEASE_EXPIRED',
       completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',1)),${compactionLeaseCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count>=max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status='PENDING',failure_code='LEASE_EXPIRED',
       available_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','LEASE_EXPIRED','occurred_at',?,'attempt',attempt_count,
           'terminal',0)),${compactionLeaseCleanup}
       WHERE status='PROCESSING' AND lease_expires_at<=? AND attempt_count<max_attempts`,
    ).bind(timestamp, timestamp, timestamp),
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status='PROCESSING',lease_owner=?,lease_token=?,
       lease_expires_at=?,attempt_count=attempt_count+1,failure_code=NULL,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CLAIMED','occurred_at',?,'attempt',attempt_count+1,
           'worker_id',?))
       WHERE id=(SELECT id FROM assistant_compaction_jobs
         WHERE status='PENDING' AND available_at<=?
         ORDER BY created_at,id LIMIT 1)
       RETURNING *`,
    ).bind(workerId, leaseToken, leaseExpiresAt, timestamp, workerId, timestamp),
  ]);
  const job = results.at(-1)?.results?.[0];
  if (!job) return null;
  const sourceIds = parsedJson(job.source_message_ids_json, []) as string[];
  const placeholders = sourceIds.map(() => "?").join(",");
  const messages = await binding.prepare(
    `SELECT * FROM assistant_messages WHERE conversation_id=? AND id IN (${placeholders})
     ORDER BY created_at,id`,
  ).bind(job.conversation_id, ...sourceIds).all<AssistantMessageRow>();
  if (
    messages.results.length !== sourceIds.length
    || messages.results.some((message, index) => message.id !== sourceIds[index])
  ) {
    await failAssistantCompactionJob(binding, {
      id: job.id, lease_token: job.lease_token, failure_code: "COMPACTION_CONTEXT_MISSING",
    }, now);
    return null;
  }
  const priorSummary = await currentSummary(
    binding, job.conversation_id, Number(job.prior_summary_version),
  );
  return {
    id: job.id,
    conversation_id: job.conversation_id,
    lease_token: String(job.lease_token),
    lease_expires_at: String(job.lease_expires_at),
    attempt_count: Number(job.attempt_count),
    prompt_version: String(job.prompt_version),
    context_profile_id: String(job.context_profile_id),
    prior_summary: priorSummary ? {
      id: priorSummary.id,
      version: Number(priorSummary.version),
      content: priorSummary.content,
      anchors: parsedJson(priorSummary.anchors_json, {}),
    } : null,
    pinned_state: parsedJson(job.pinned_snapshot_json, []),
    source_messages: messages.results.map(message => ({
      id: message.id,
      role: message.role,
      content: message.content,
      created_at: message.created_at,
    })),
  };
}

const anchorKeys = [
  "evidence_ids", "source_refs", "important_timestamps", "tool_refs", "artifact_refs",
] as const;
type AnchorKey = typeof anchorKeys[number];
type SummaryAnchors = Record<AnchorKey, string[]>;

const emptyAnchors = (): SummaryAnchors => ({
  evidence_ids: [], source_refs: [], important_timestamps: [], tool_refs: [], artifact_refs: [],
});

const addAnchor = (anchors: SummaryAnchors, key: AnchorKey, raw: unknown) => {
  if (typeof raw !== "string") return;
  const value = raw.normalize("NFKC").trim();
  if (validReference(value) && !anchors[key].includes(value)) anchors[key].push(value);
};

const collectProvenanceAnchors = (value: unknown, anchors: SummaryAnchors, parent = "") => {
  if (Array.isArray(value)) {
    if (anchorKeys.includes(parent as AnchorKey)) {
      for (const item of value) addAnchor(anchors, parent as AnchorKey, item);
    } else {
      for (const item of value) collectProvenanceAnchors(item, anchors, parent);
    }
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (key === "canonical_evidence_ids" || key === "evidence_ids") {
      for (const item of Array.isArray(child) ? child : []) addAnchor(anchors, "evidence_ids", item);
    } else if (key === "evidence_id") {
      addAnchor(anchors, "evidence_ids", child);
    } else if (["source_ref", "source_url", "source_id", "question_id"].includes(key)) {
      addAnchor(anchors, "source_refs", child);
    } else if (key === "tool_ref") {
      addAnchor(anchors, "tool_refs", child);
    } else if (key === "artifact_ref") {
      addAnchor(anchors, "artifact_refs", child);
    } else if ((key.endsWith("_at") || key === "cutoff") && typeof child === "string") {
      addAnchor(anchors, "important_timestamps", child);
    } else {
      collectProvenanceAnchors(child, anchors, key);
    }
  }
};

const mergedAnchors = (
  prior: unknown,
  messages: AssistantMessageRow[],
  pins: CompactionPinInput[],
) => {
  const anchors = emptyAnchors();
  if (prior && typeof prior === "object" && !Array.isArray(prior)) {
    for (const key of anchorKeys) {
      const values = (prior as Record<string, unknown>)[key];
      for (const item of Array.isArray(values) ? values : []) addAnchor(anchors, key, item);
    }
  }
  for (const message of messages) collectProvenanceAnchors(
    parsedJson(message.provenance_json, {}), anchors,
  );
  for (const pin of pins) {
    for (const item of pin.evidence_ids) addAnchor(anchors, "evidence_ids", item);
    for (const item of pin.source_refs) addAnchor(anchors, "source_refs", item);
    for (const item of pin.important_timestamps) addAnchor(anchors, "important_timestamps", item);
    for (const item of pin.tool_refs) addAnchor(anchors, "tool_refs", item);
    for (const item of pin.artifact_refs) addAnchor(anchors, "artifact_refs", item);
  }
  if (anchorKeys.some(key => anchors[key].length > ASSISTANT_MEMORY_LIMITS.maxAnchorItems)) {
    throw new AssistantConversationInputError(
      "SUMMARY_ANCHORS_EXCEED_BUDGET", "摘要来源索引超过安全预算",
    );
  }
  return anchors;
};

export async function completeAssistantCompactionJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
  profile = DEFAULT_ASSISTANT_CONTEXT_PROFILE,
) {
  validateProfile(profile);
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_compaction_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<AssistantCompactionJobRow>();
  if (!job) return null;
  if (
    String(input.prompt_version ?? "") !== job.prompt_version
    || String(input.context_profile_id ?? "") !== job.context_profile_id
    || job.context_profile_id !== profile.id
  ) throw new AssistantConversationInputError(
    "INVALID_COMPACTION_PROVENANCE", "摘要规则或上下文 profile 无效",
  );
  const modelVersion = canonicalText(input.model_version, 120, "model version");
  let routing;
  try {
    routing = parseAssistantRoutingProvenance(input.routing, "CONTEXT_COMPACTION");
  } catch {
    throw new AssistantConversationInputError(
      "INVALID_COMPACTION_PROVENANCE", "摘要模型路由来源无效",
    );
  }
  const summaryContent = canonicalText(
    input.summary, ASSISTANT_MEMORY_LIMITS.maxSummaryCharacters, "summary",
  );
  const sourceIds = parsedJson(job.source_message_ids_json, []) as string[];
  const coveredIds = orderedUniqueStrings(input.covered_message_ids, "covered_message_ids", 128);
  if (JSON.stringify(coveredIds) !== JSON.stringify(sourceIds)) {
    throw new AssistantConversationInputError(
      "INCOMPLETE_COMPACTION_COVERAGE", "摘要没有确认全部冻结输入消息",
    );
  }
  const rawPins = input.pinned_entries;
  if (!Array.isArray(rawPins)
    || rawPins.length > ASSISTANT_MEMORY_LIMITS.maxGeneratedPins) {
    throw new AssistantConversationInputError("INVALID_COMPACTION_RESULT", "固定状态无效");
  }
  const sourceSet = new Set(sourceIds);
  const pins = rawPins.map(value => parsePinInput(value, sourceSet));
  const placeholders = sourceIds.map(() => "?").join(",");
  const messages = await binding.prepare(
    `SELECT * FROM assistant_messages WHERE conversation_id=? AND id IN (${placeholders})
     ORDER BY created_at,id`,
  ).bind(job.conversation_id, ...sourceIds).all<AssistantMessageRow>();
  if (
    messages.results.length !== sourceIds.length
    || messages.results.some((message, index) => message.id !== sourceIds[index])
  ) throw new AssistantConversationInputError("COMPACTION_CONTEXT_MISSING", "摘要输入已不完整");
  const priorSummary = await currentSummary(
    binding, job.conversation_id, Number(job.prior_summary_version),
  );
  const snapshotPins = parsedJson(job.pinned_snapshot_json, []) as unknown[];
  const normalizedSnapshotPins = snapshotPins.map(pin => parsePinInput(pin));
  if (
    normalizedSnapshotPins.length + pins.length > ASSISTANT_MEMORY_LIMITS.maxPinnedEntries
    || conservativeAssistantTokenEstimate([...normalizedSnapshotPins, ...pins])
      > profile.pinnedTokenBudget
  ) throw new AssistantConversationInputError(
    "PINNED_STATE_EXCEEDS_BUDGET", "新增固定状态超过安全预算",
  );
  const anchors = mergedAnchors(
    priorSummary ? parsedJson(priorSummary.anchors_json, {}) : {},
    messages.results,
    [...normalizedSnapshotPins, ...pins],
  );
  const estimatedTokens = conservativeAssistantTokenEstimate({
    content: summaryContent, anchors,
  });
  if (estimatedTokens > profile.summaryTokenBudget) {
    throw new AssistantConversationInputError("SUMMARY_EXCEEDS_BUDGET", "摘要超过安全预算");
  }
  const summaryId = crypto.randomUUID();
  const pinStatements = pins.map((pin, index) => binding.prepare(
    `INSERT INTO assistant_pinned_entries (
       id,conversation_id,idempotency_key,kind,content,origin_message_ids_json,
       evidence_ids_json,source_refs_json,important_timestamps_json,tool_refs_json,
       artifact_refs_json,created_by,source_job_id,created_at
     ) SELECT ?,conversation_id,?,?,?,?,?,?,?,?,?,'COMPACTION',id,?
       FROM assistant_compaction_jobs WHERE id=? AND status='COMPLETED'
         AND completed_at=? AND model_version=?
         AND EXISTS (SELECT 1 FROM assistant_summaries WHERE id=?)`,
  ).bind(
    crypto.randomUUID(), `compaction:${id}:${index}`, pin.kind, pin.content,
    JSON.stringify(pin.origin_message_ids), JSON.stringify(pin.evidence_ids),
    JSON.stringify(pin.source_refs), JSON.stringify(pin.important_timestamps),
    JSON.stringify(pin.tool_refs), JSON.stringify(pin.artifact_refs), timestamp, id,
    timestamp, modelVersion, summaryId,
  ));
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status='COMPLETED',model_version=?,completed_at=?,
       failure_code=NULL,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','COMPLETED','occurred_at',?,'attempt',attempt_count,
           'routing',json(?))),
       ${compactionLeaseCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      modelVersion, timestamp, timestamp, JSON.stringify(routing),
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `INSERT INTO assistant_summaries (
       id,conversation_id,version,prior_summary_id,source_job_id,
       first_source_message_id,covered_through_message_id,covered_through_created_at,
       source_message_count,content,anchors_json,estimated_tokens,context_profile_id,
       prompt_version,model_version,created_at
       ) SELECT ?,conversation_id,output_summary_version,?,id,first_source_message_id,
         last_source_message_id,?,source_message_count,?,?,?,?,?,?,?
       FROM assistant_compaction_jobs WHERE id=? AND status='COMPLETED'
         AND completed_at=? AND model_version=?
       RETURNING *`,
    ).bind(
      summaryId, priorSummary?.id ?? null, messages.results.at(-1)?.created_at,
      summaryContent, JSON.stringify(anchors), estimatedTokens, profile.id,
      job.prompt_version, modelVersion, timestamp, id, timestamp, modelVersion,
    ),
    ...pinStatements,
    binding.prepare(
      `UPDATE assistant_conversations SET summary_version=?,pending_compaction_job_id=NULL
       WHERE id=? AND summary_version=? AND pending_compaction_job_id=?
       RETURNING *`,
    ).bind(
      job.output_summary_version, job.conversation_id, job.prior_summary_version, id,
    ),
  ]);
  const completedJob = results[0]?.results?.[0];
  const summary = results[1]?.results?.[0];
  const conversation = results.at(-1)?.results?.[0];
  if (!completedJob || !summary || !conversation) return null;
  let nextCompaction: CompactionScheduleOutcome | { kind: "SCHEDULE_DEFERRED" };
  try {
    nextCompaction = await scheduleAssistantCompaction(binding, job.conversation_id, {
      now, profile,
    });
  } catch {
    nextCompaction = { kind: "SCHEDULE_DEFERRED" };
  }
  return {
    job_id: id,
    status: "COMPLETED" as const,
    summary_id: summaryId,
    summary_version: Number(job.output_summary_version),
    pinned_entries_created: pins.length,
    next_compaction: nextCompaction,
  };
}

export async function failAssistantCompactionJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const failureCode = String(input.failure_code ?? "COMPACTION_FAILED").trim().toUpperCase();
  if (!/^[A-Z0-9_]{3,64}$/.test(failureCode)) {
    throw new AssistantConversationInputError("INVALID_FAILURE_CODE", "失败代码无效");
  }
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_compaction_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<AssistantCompactionJobRow>();
  if (!job) return null;
  const terminal = Number(job.attempt_count) >= Number(job.max_attempts);
  const delaySeconds = Math.min(120, 30 * (2 ** Math.max(0, Number(job.attempt_count) - 1)));
  const availableAt = terminal
    ? timestamp
    : new Date(now.getTime() + delaySeconds * 1_000).toISOString();
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status=?,available_at=?,failure_code=?,
       completed_at=?,attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','FAILED','occurred_at',?,'attempt',attempt_count,
           'failure_code',?,'terminal',?)),${compactionLeaseCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      terminal ? "FAILED" : "PENDING", availableAt, failureCode,
      terminal ? timestamp : null, timestamp, failureCode, terminal ? 1 : 0,
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET pending_compaction_job_id=NULL
       WHERE pending_compaction_job_id=? AND ?=1`,
    ).bind(id, terminal ? 1 : 0),
  ]);
  const row = results[0]?.results?.[0];
  return row ? { job_id: id, status: String(row.status) } : null;
}

export async function deferAssistantCompactionJob(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const id = String(input.id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const job = await binding.prepare(
    `SELECT * FROM assistant_compaction_jobs
     WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?`,
  ).bind(id, leaseToken, timestamp).first<AssistantCompactionJobRow>();
  if (!job) return null;
  const terminal = Number(job.attempt_count) >= Number(job.max_attempts);
  const availableAt = terminal
    ? timestamp : new Date(now.getTime() + 60_000).toISOString();
  const results = await binding.batch<Record<string, unknown>>([
    binding.prepare(
      `UPDATE assistant_compaction_jobs SET status=?,available_at=?,
       failure_code='NO_MODEL_CAPACITY',completed_at=?,
       attempt_history_json=json_insert(attempt_history_json,'$[#]',
         json_object('event','CAPACITY_DEFERRED','occurred_at',?,
           'attempt',attempt_count,'failure_code','NO_MODEL_CAPACITY','terminal',?)),
       ${compactionLeaseCleanup}
       WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires_at>?
       RETURNING *`,
    ).bind(
      terminal ? "FAILED" : "PENDING", availableAt,
      terminal ? timestamp : null, timestamp, terminal ? 1 : 0,
      id, leaseToken, timestamp,
    ),
    binding.prepare(
      `UPDATE assistant_conversations SET pending_compaction_job_id=NULL
       WHERE pending_compaction_job_id=? AND ?=1`,
    ).bind(id, terminal ? 1 : 0),
  ]);
  const row = results[0]?.results?.[0];
  return row ? { job_id: id, status: String(row.status) } : null;
}
