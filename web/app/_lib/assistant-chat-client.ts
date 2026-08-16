import {
  ASSISTANT_EVENT_PROTOCOL_VERSION,
  MAX_ASSISTANT_EVENTS_PER_TURN,
  parseAssistantEvent,
  type AssistantEventEnvelope,
} from "../api/_shared/assistant-events";
import {
  parseAssistantContentDocument,
  type AssistantContentDocument,
} from "../api/_shared/assistant-content";

export const ASSISTANT_ACTIVE_STATUSES = ["PENDING", "PROCESSING"] as const;
export const ASSISTANT_TERMINAL_STATUSES = [
  "ANSWERED", "FAILED", "REJECTED", "EXPIRED", "CANCELLED",
] as const;

export type AssistantTurnStatus =
  | typeof ASSISTANT_ACTIVE_STATUSES[number]
  | typeof ASSISTANT_TERMINAL_STATUSES[number];

export type AssistantActiveTurn = {
  id: string;
  status: typeof ASSISTANT_ACTIVE_STATUSES[number];
  event_sequence: number;
  created_at: string;
};

export type AssistantConversation = {
  id: string;
  title: string;
  title_source: "PROVISIONAL" | "AI" | "USER";
  created_at: string;
  last_activity_at: string;
  archived_at: string | null;
  summary_version: number;
  status: "ACTIVE" | "ARCHIVED";
  title_job_status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED" | "CANCELLED" | null;
  active_turn: AssistantActiveTurn | null;
};

export type AssistantMessage = {
  id: string;
  conversation_id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  content_document: AssistantContentDocument | null;
  created_at: string;
  provenance: Record<string, unknown>;
};

export type AssistantMessageCursor = {
  before_created_at: string;
  before_id: string;
};

export type AssistantChatTurn = {
  id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  conversation_title: string | null;
  status: AssistantTurnStatus;
  event_sequence: number;
  attempt_count: number;
  failure_code: string | null;
  created_at: string;
  completed_at: string | null;
};

export type AssistantConversationDetail = {
  conversation: AssistantConversation;
  items: AssistantMessage[];
  next_cursor: AssistantMessageCursor | null;
  preview: boolean;
};

export type AssistantReplayPage = {
  events: AssistantEventEnvelope[];
  turn_status: AssistantTurnStatus | null;
  next_sequence: number;
  has_more: boolean;
  preview: boolean;
};

export type AssistantProgressItem = {
  id: string;
  label: string;
  detail: string | null;
  state: "ACTIVE" | "COMPLETED" | "FAILED" | "QUEUED";
};

export type AssistantConversationSelectionPlan =
  | "REFRESH_CURRENT"
  | "LOAD_PREVIEW"
  | "LOAD_REMOTE";

export type AssistantFetcher = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export function planAssistantConversationSelection(
  currentConversationId: string | null,
  nextConversationId: string,
  preview: boolean,
): AssistantConversationSelectionPlan {
  if (currentConversationId === nextConversationId) return "REFRESH_CURRENT";
  return preview ? "LOAD_PREVIEW" : "LOAD_REMOTE";
}

const identifier = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const canonicalTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const titleSources = new Set(["PROVISIONAL", "AI", "USER"]);
const conversationStatuses = new Set(["ACTIVE", "ARCHIVED"]);
const titleJobStatuses = new Set([
  "PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED",
]);
const activeStatuses = new Set<string>(ASSISTANT_ACTIVE_STATUSES);
const turnStatuses = new Set<string>([
  ...ASSISTANT_ACTIVE_STATUSES, ...ASSISTANT_TERMINAL_STATUSES,
]);

export class AssistantClientError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

const fail = (code: string, message: string): never => {
  throw new AssistantClientError(code, message);
};

const objectValue = (value: unknown, label: string) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("INVALID_ASSISTANT_RESPONSE", `${label} 响应无效`);
  }
  return value as Record<string, unknown>;
};

const stringValue = (value: unknown, label: string, maximum = 32_000) => {
  if (typeof value !== "string" || !value || new TextEncoder().encode(value).length > maximum) {
    fail("INVALID_ASSISTANT_RESPONSE", `${label} 响应无效`);
  }
  return value;
};

const idValue = (value: unknown, label: string) => {
  const id = stringValue(value, label, 256);
  if (!identifier.test(id)) fail("INVALID_ASSISTANT_RESPONSE", `${label} 响应无效`);
  return id;
};

const timeValue = (value: unknown, label: string) => {
  const timestamp = stringValue(value, label, 64);
  if (!canonicalTime.test(timestamp) || new Date(timestamp).toISOString() !== timestamp) {
    fail("INVALID_ASSISTANT_RESPONSE", `${label} 响应无效`);
  }
  return timestamp;
};

const integerValue = (value: unknown, label: string, maximum = Number.MAX_SAFE_INTEGER) => {
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > maximum) {
    fail("INVALID_ASSISTANT_RESPONSE", `${label} 响应无效`);
  }
  return Number(value);
};

const nullableString = (value: unknown, label: string) => (
  value === null ? null : stringValue(value, label)
);

export function parseAssistantActiveTurn(value: unknown): AssistantActiveTurn | null {
  if (value === null || value === undefined) return null;
  const raw = objectValue(value, "active turn");
  if (typeof raw.status !== "string" || !activeStatuses.has(raw.status)) {
    fail("INVALID_ASSISTANT_RESPONSE", "active turn 状态无效");
  }
  return {
    id: idValue(raw.id, "active turn id"),
    status: raw.status as AssistantActiveTurn["status"],
    event_sequence: integerValue(
      raw.event_sequence, "active turn event sequence", MAX_ASSISTANT_EVENTS_PER_TURN,
    ),
    created_at: timeValue(raw.created_at, "active turn created_at"),
  };
}

export function parseAssistantConversation(value: unknown): AssistantConversation {
  const raw = objectValue(value, "conversation");
  if (typeof raw.title_source !== "string" || !titleSources.has(raw.title_source)
    || typeof raw.status !== "string" || !conversationStatuses.has(raw.status)
    || (raw.title_job_status !== null
      && (typeof raw.title_job_status !== "string"
        || !titleJobStatuses.has(raw.title_job_status)))) {
    fail("INVALID_ASSISTANT_RESPONSE", "conversation 状态无效");
  }
  return {
    id: idValue(raw.id, "conversation id"),
    title: stringValue(raw.title, "conversation title", 512),
    title_source: raw.title_source as AssistantConversation["title_source"],
    created_at: timeValue(raw.created_at, "conversation created_at"),
    last_activity_at: timeValue(raw.last_activity_at, "conversation last_activity_at"),
    archived_at: raw.archived_at === null
      ? null : timeValue(raw.archived_at, "conversation archived_at"),
    summary_version: integerValue(raw.summary_version, "conversation summary version"),
    status: raw.status as AssistantConversation["status"],
    title_job_status: raw.title_job_status as AssistantConversation["title_job_status"],
    active_turn: parseAssistantActiveTurn(raw.active_turn),
  };
}

export function parseAssistantMessage(value: unknown): AssistantMessage {
  const raw = objectValue(value, "message");
  if (raw.role !== "USER" && raw.role !== "ASSISTANT") {
    fail("INVALID_ASSISTANT_RESPONSE", "message role 无效");
  }
  const content = stringValue(raw.content, "message content");
  const provenance = objectValue(raw.provenance, "message provenance");
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
  const contentDocument = raw.content_document === null
    ? null
    : parseAssistantContentDocument(raw.content_document, { answer: content, evidenceIds });
  if (raw.role === "USER" && contentDocument !== null) {
    fail("INVALID_ASSISTANT_RESPONSE", "user message 不能携带 Assistant blocks");
  }
  return {
    id: idValue(raw.id, "message id"),
    conversation_id: idValue(raw.conversation_id, "message conversation id"),
    role: raw.role,
    content,
    content_document: contentDocument,
    created_at: timeValue(raw.created_at, "message created_at"),
    provenance,
  };
}

export function parseAssistantTurn(value: unknown): AssistantChatTurn {
  const raw = objectValue(value, "turn");
  if (typeof raw.status !== "string" || !turnStatuses.has(raw.status)) {
    fail("INVALID_ASSISTANT_RESPONSE", "turn 状态无效");
  }
  return {
    id: idValue(raw.id, "turn id"),
    conversation_id: idValue(raw.conversation_id, "turn conversation id"),
    user_message_id: idValue(raw.user_message_id, "turn user message id"),
    assistant_message_id: raw.assistant_message_id === null
      ? null : idValue(raw.assistant_message_id, "turn assistant message id"),
    conversation_title: nullableString(raw.conversation_title, "turn title"),
    status: raw.status as AssistantTurnStatus,
    event_sequence: integerValue(
      raw.event_sequence, "turn event sequence", MAX_ASSISTANT_EVENTS_PER_TURN,
    ),
    attempt_count: integerValue(raw.attempt_count, "turn attempt count", 3),
    failure_code: nullableString(raw.failure_code, "turn failure code"),
    created_at: timeValue(raw.created_at, "turn created_at"),
    completed_at: raw.completed_at === null
      ? null : timeValue(raw.completed_at, "turn completed_at"),
  };
}

const previewResponse = (response: Response) => (
  response.headers.get("x-aurum-preview") === "synthetic-empty-assistant"
);

const rejectAccessLoginResponse = (response: Response) => {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (response.redirected || contentType.includes("text/html")) {
    throw new AssistantClientError(
      "ACCESS_LOGIN_REQUIRED", "需要完成 Cloudflare Access 登录", 401,
    );
  }
};

async function jsonResponse(response: Response) {
  rejectAccessLoginResponse(response);
  const text = await response.text();
  let parsed: unknown = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    throw new AssistantClientError(
      "INVALID_ASSISTANT_RESPONSE", "Assistant 返回了无效响应", response.status,
    );
  }
  if (!response.ok) {
    const body = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown> : {};
    throw new AssistantClientError(
      typeof body.code === "string" ? body.code : `HTTP_${response.status}`,
      typeof body.error === "string" ? body.error : "Assistant 暂不可用",
      response.status,
    );
  }
  return parsed;
}

const requestInit = (init: RequestInit = {}): RequestInit => ({
  ...init,
  cache: "no-store",
  credentials: "same-origin",
  headers: {
    Accept: "application/json",
    ...init.headers,
  },
});

export async function fetchAssistantConversations(
  archived = false,
  fetcher: AssistantFetcher = fetch,
) {
  const response = await fetcher(
    `/api/assistant-conversations?limit=50&archived=${archived}`,
    requestInit(),
  );
  const body = objectValue(await jsonResponse(response), "conversation list");
  if (!Array.isArray(body.items)) fail("INVALID_ASSISTANT_RESPONSE", "会话列表无效");
  return {
    items: body.items.map(parseAssistantConversation),
    preview: previewResponse(response) || body.preview === true,
  };
}

export async function fetchAssistantConversation(
  conversationId: string,
  cursor: AssistantMessageCursor | null = null,
  fetcher: AssistantFetcher = fetch,
): Promise<AssistantConversationDetail> {
  const params = new URLSearchParams({ id: conversationId, message_limit: "30" });
  if (cursor) {
    params.set("before_created_at", cursor.before_created_at);
    params.set("before_id", cursor.before_id);
  }
  const response = await fetcher(
    `/api/assistant-conversations?${params.toString()}`,
    requestInit(),
  );
  const body = objectValue(await jsonResponse(response), "conversation detail");
  if (!Array.isArray(body.items)) fail("INVALID_ASSISTANT_RESPONSE", "消息列表无效");
  let nextCursor: AssistantMessageCursor | null = null;
  if (body.next_cursor !== null && body.next_cursor !== undefined) {
    const rawCursor = objectValue(body.next_cursor, "message cursor");
    nextCursor = {
      before_created_at: timeValue(rawCursor.before_created_at, "cursor time"),
      before_id: idValue(rawCursor.before_id, "cursor id"),
    };
  }
  return {
    conversation: parseAssistantConversation(body.conversation),
    items: body.items.map(parseAssistantMessage),
    next_cursor: nextCursor,
    preview: previewResponse(response) || body.preview === true,
  };
}

export async function submitAssistantTurn(
  input: { message: string; conversation_id: string | null; idempotency_key: string },
  fetcher: AssistantFetcher = fetch,
) {
  const response = await fetcher("/api/assistant-chat", requestInit({
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": input.idempotency_key,
    },
    body: JSON.stringify({
      message: input.message,
      ...(input.conversation_id ? { conversation_id: input.conversation_id } : {}),
    }),
  }));
  return parseAssistantTurn(await jsonResponse(response));
}

export async function cancelAssistantTurn(
  turnId: string,
  fetcher: AssistantFetcher = fetch,
) {
  const response = await fetcher("/api/assistant-chat", requestInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "CANCEL", turn_id: turnId }),
  }));
  return parseAssistantTurn(await jsonResponse(response));
}

export async function updateAssistantConversation(
  input: {
    conversation_id: string;
    action: "RENAME" | "ARCHIVE" | "UNARCHIVE" | "REGENERATE_TITLE";
    title?: string;
    idempotency_key?: string;
  },
  fetcher: AssistantFetcher = fetch,
) {
  const response = await fetcher("/api/assistant-conversations", requestInit({
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(input.idempotency_key
        ? { "Idempotency-Key": input.idempotency_key }
        : {}),
    },
    body: JSON.stringify({
      action: input.action,
      conversation_id: input.conversation_id,
      ...(input.title ? { title: input.title } : {}),
    }),
  }));
  return jsonResponse(response);
}

export function parseAssistantSse(value: string): AssistantEventEnvelope[] {
  if (typeof value !== "string") fail("INVALID_ASSISTANT_STREAM", "事件流无效");
  const normalized = value.replace(/\r\n?/g, "\n");
  const events: AssistantEventEnvelope[] = [];
  for (const block of normalized.split(/\n\n+/)) {
    const lines = block.split("\n").filter(line => line && !line.startsWith(":"));
    if (lines.length === 0) continue;
    let id: string | null = null;
    let eventName: string | null = null;
    const data: string[] = [];
    for (const line of lines) {
      const separator = line.indexOf(":");
      const field = separator === -1 ? line : line.slice(0, separator);
      const raw = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
      if (field === "id" && id === null) id = raw;
      else if (field === "event" && eventName === null) eventName = raw;
      else if (field === "data") data.push(raw);
      else fail("INVALID_ASSISTANT_STREAM", "事件流字段无效");
    }
    if (data.length === 0) fail("INVALID_ASSISTANT_STREAM", "事件流缺少数据");
    let parsed: unknown;
    try {
      parsed = JSON.parse(data.join("\n"));
    } catch {
      fail("INVALID_ASSISTANT_STREAM", "事件流 JSON 无效");
    }
    const event = parseAssistantEvent(parsed);
    if (id !== String(event.sequence) || eventName !== event.type) {
      fail("INVALID_ASSISTANT_STREAM", "事件流身份不一致");
    }
    events.push(event);
  }
  for (let index = 1; index < events.length; index += 1) {
    if (events[index].sequence !== events[index - 1].sequence + 1) {
      fail("INVALID_ASSISTANT_STREAM", "事件流序号不连续");
    }
  }
  return events;
}

export async function replayAssistantEvents(
  turnId: string,
  after: number,
  signal: AbortSignal,
  fetcher: AssistantFetcher = fetch,
): Promise<AssistantReplayPage> {
  const params = new URLSearchParams({
    mode: "events",
    id: turnId,
    after: String(after),
    limit: "100",
  });
  const response = await fetcher(`/api/assistant-chat?${params.toString()}`, {
    ...requestInit({
      headers: {
        Accept: "text/event-stream",
        "Last-Event-ID": String(after),
      },
    }),
    signal,
  });
  rejectAccessLoginResponse(response);
  if (!response.ok) await jsonResponse(response);
  if (response.headers.get("x-assistant-event-protocol")
    !== ASSISTANT_EVENT_PROTOCOL_VERSION) {
    throw new AssistantClientError(
      "UNSUPPORTED_EVENT_PROTOCOL", "Assistant 事件协议不一致", response.status,
    );
  }
  const events = parseAssistantSse(await response.text());
  if (events[0] && events[0].sequence !== after + 1) {
    fail("INVALID_ASSISTANT_STREAM", "事件流没有从请求游标继续");
  }
  const expectedNext = events.at(-1)?.sequence ?? after;
  const rawNext = response.headers.get("x-assistant-next-sequence");
  const next = rawNext === null ? expectedNext : Number(rawNext);
  if (!Number.isSafeInteger(next) || next !== expectedNext
    || next < after || next > MAX_ASSISTANT_EVENTS_PER_TURN) {
    fail("INVALID_ASSISTANT_STREAM", "事件流游标无效");
  }
  const rawStatus = response.headers.get("x-assistant-turn-status");
  if (rawStatus !== null && !turnStatuses.has(rawStatus)) {
    fail("INVALID_ASSISTANT_STREAM", "事件流状态无效");
  }
  const rawHasMore = response.headers.get("x-assistant-has-more");
  if (rawHasMore !== null && rawHasMore !== "true" && rawHasMore !== "false") {
    fail("INVALID_ASSISTANT_STREAM", "事件流分页状态无效");
  }
  return {
    events,
    turn_status: rawStatus as AssistantTurnStatus | null,
    next_sequence: next,
    has_more: rawHasMore === "true",
    preview: previewResponse(response),
  };
}

export function mergeAssistantMessages(
  current: AssistantMessage[],
  incoming: AssistantMessage[],
) {
  const byId = new Map(current.map(message => [message.id, message]));
  for (const message of incoming) {
    const existing = byId.get(message.id);
    if (existing && JSON.stringify(existing) !== JSON.stringify(message)) {
      fail("IMMUTABLE_MESSAGE_CHANGED", "历史消息内容发生冲突");
    }
    byId.set(message.id, message);
  }
  return [...byId.values()].sort((left, right) => (
    left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id)
  ));
}

const reasoningLabel: Record<string, string> = {
  SIMPLE: "正在整理直接回答",
  ANALYTICAL: "正在核对上下文与证据",
  TOOL_HEAVY: "正在规划证据检索",
};
const reasoningCompletedLabel: Record<string, string> = {
  SIMPLE: "直接回答已整理",
  ANALYTICAL: "上下文与证据已核对",
  TOOL_HEAVY: "证据检索规划已完成",
};

const toolLabel = (name: unknown) => (
  name === "search_news_v1" || name === "news_retrieval_v1"
    ? "相关新闻检索"
    : "只读分析工具"
);

export function assistantAnswerDraft(events: AssistantEventEnvelope[]) {
  let started = false;
  let failed = false;
  const chunks: string[] = [];
  for (const event of events) {
    if (event.type === "answer.started") started = true;
    else if (started && event.type === "answer.delta") {
      chunks.push(String(event.payload.text));
    } else if (event.type === "error" || event.type === "cancelled") {
      failed = true;
    }
  }
  return started && !failed ? chunks.join("") : null;
}

export function assistantProgressItems(
  events: AssistantEventEnvelope[],
): AssistantProgressItem[] {
  const items: AssistantProgressItem[] = [];
  const tools = new Map<string, number>();
  let reasoningIndex: number | null = null;
  let reasoningClass = "";
  let answerIndex: number | null = null;
  const closeStates = (
    states: AssistantProgressItem["state"][],
    next: AssistantProgressItem["state"],
  ) => {
    for (let index = 0; index < items.length; index += 1) {
      if (states.includes(items[index].state)) items[index] = { ...items[index], state: next };
    }
  };
  const completeReasoning = () => {
    if (reasoningIndex === null) return;
    items[reasoningIndex] = {
      ...items[reasoningIndex],
      label: reasoningCompletedLabel[reasoningClass] ?? "公开分析阶段已完成",
      state: "COMPLETED",
    };
  };
  const completeAnswer = () => {
    if (answerIndex === null) return;
    items[answerIndex] = {
      ...items[answerIndex],
      label: "回答整理已完成",
      detail: "最终回答已通过持久化门槛",
      state: "COMPLETED",
    };
  };
  for (const event of events) {
    if (event.type === "conversation.started") {
      items.push({
        id: event.event_id,
        label: "问题已进入安全队列",
        detail: "等待 Windows Assistant worker",
        state: "QUEUED",
      });
    } else if (event.type === "reasoning.started") {
      closeStates(["QUEUED"], "COMPLETED");
      reasoningIndex = items.length;
      reasoningClass = String(event.payload.reasoning_class);
      items.push({
        id: event.event_id,
        label: reasoningLabel[reasoningClass] ?? "正在分析",
        detail: "仅显示公开策略阶段，不展示私有思维过程",
        state: "ACTIVE",
      });
    } else if (event.type === "tool.started") {
      completeReasoning();
      tools.set(String(event.payload.call_id), items.length);
      items.push({
        id: event.event_id,
        label: `正在运行${toolLabel(event.payload.tool_name)}…`,
        detail: String(event.payload.tool_version),
        state: "ACTIVE",
      });
    } else if (event.type === "tool.completed" || event.type === "tool.failed") {
      const index = tools.get(String(event.payload.call_id));
      if (index === undefined) continue;
      items[index] = {
        ...items[index],
        label: event.type === "tool.completed"
          ? `${toolLabel(event.payload.tool_name)}已完成`
          : `${toolLabel(event.payload.tool_name)}暂不可用`,
        detail: event.type === "tool.completed"
          ? `取得 ${Number(event.payload.evidence_count)} 条证据`
          : `公开错误码 ${String(event.payload.error_code)}`,
        state: event.type === "tool.completed" ? "COMPLETED" : "FAILED",
      };
    } else if (event.type === "retrieval.started") {
      completeReasoning();
      tools.set(String(event.payload.operation_id), items.length);
      items.push({
        id: event.event_id,
        label: "正在搜索相关新闻…",
        detail: null,
        state: "ACTIVE",
      });
    } else if (event.type === "retrieval.completed") {
      const index = tools.get(String(event.payload.operation_id));
      if (index === undefined) continue;
      items[index] = {
        ...items[index],
        label: "相关新闻检索已完成",
        detail: `取得 ${Number(event.payload.evidence_count)} 条证据`,
        state: "COMPLETED",
      };
    } else if (event.type === "answer.started") {
      completeReasoning();
      closeStates(["ACTIVE", "QUEUED"], "COMPLETED");
      answerIndex = items.length;
      items.push({
        id: event.event_id,
        label: "正在整理回答…",
        detail: "完成后才会写入正式会话记录",
        state: "ACTIVE",
      });
    } else if (event.type === "answer.completed") {
      completeAnswer();
    } else if (event.type === "conversation.completed") {
      completeReasoning();
      completeAnswer();
      closeStates(["ACTIVE", "QUEUED"], "COMPLETED");
      items.push({
        id: event.event_id,
        label: "回答已写入会话",
        detail: "可审计的最终消息已经持久化",
        state: "COMPLETED",
      });
    } else if (event.type === "error") {
      closeStates(["ACTIVE", "QUEUED"], "FAILED");
      items.push({
        id: event.event_id,
        label: "本轮未能完成",
        detail: `公开错误码 ${String(event.payload.code)}`,
        state: "FAILED",
      });
    } else if (event.type === "cancelled") {
      closeStates(["ACTIVE", "QUEUED"], "FAILED");
      items.push({
        id: event.event_id,
        label: "本轮已取消",
        detail: null,
        state: "FAILED",
      });
    }
  }
  return items;
}

export function isAssistantTurnTerminal(status: AssistantTurnStatus | null) {
  return status !== null && new Set<string>(ASSISTANT_TERMINAL_STATUSES).has(status);
}
