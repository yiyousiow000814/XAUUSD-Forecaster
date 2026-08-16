import type { AssistantEventEnvelope } from "../api/_shared/assistant-events";
import type {
  AssistantConversation,
  AssistantMessage,
  AssistantMessageCursor,
} from "./assistant-chat-client";

export const ASSISTANT_PREVIEW_FIXTURE_LABEL =
  "交互预览样本 · 管理与发送仅生产可用 · 不调用模型";

const modelProvenance = {
  kind: "ASSISTANT_CHAT",
  agent: {
    model_versions: ["gemma-4-31b-it-preview-fixture"],
    model_routing: [{ reasoning_class: "TOOL_HEAVY" }],
    tool_execution: [[{
      name: "search_news_v1",
      status: "SUCCEEDED",
      evidence_ids: ["preview-evidence-1", "preview-evidence-2"],
    }]],
    evidence_ids: ["preview-evidence-1", "preview-evidence-2"],
  },
};

export const assistantPreviewConversations: AssistantConversation[] = [
  {
    id: "conversation-preview-rates",
    title: "美联储利率与黄金重定价",
    title_source: "AI",
    created_at: "2026-08-15T09:30:00.000Z",
    last_activity_at: "2026-08-15T10:03:00.000Z",
    archived_at: null,
    summary_version: 1,
    status: "ACTIVE",
    title_job_status: null,
    active_turn: null,
    latest_turn: null,
  },
  {
    id: "conversation-preview-opening",
    title: "周末开盘前的证据清单",
    title_source: "USER",
    created_at: "2026-08-14T14:00:00.000Z",
    last_activity_at: "2026-08-14T14:04:00.000Z",
    archived_at: null,
    summary_version: 0,
    status: "ACTIVE",
    title_job_status: null,
    active_turn: null,
    latest_turn: null,
  },
  {
    id: "conversation-preview-inflation",
    title: "CPI 公布后的反常上涨",
    title_source: "PROVISIONAL",
    created_at: "2026-08-13T12:30:00.000Z",
    last_activity_at: "2026-08-13T12:33:00.000Z",
    archived_at: null,
    summary_version: 0,
    status: "ACTIVE",
    title_job_status: "PENDING",
    active_turn: null,
    latest_turn: null,
  },
  {
    id: "conversation-preview-recovery",
    title: "页面返回后的失败恢复",
    title_source: "USER",
    created_at: "2026-08-12T11:00:00.000Z",
    last_activity_at: "2026-08-12T11:01:00.000Z",
    archived_at: null,
    summary_version: 0,
    status: "ACTIVE",
    title_job_status: null,
    active_turn: null,
    latest_turn: {
      id: "turn-preview-recovery",
      user_message_id: "message-preview-user-recovery",
      status: "FAILED",
      failure_code: "NO_MODEL_CAPACITY",
      event_sequence: 1,
      created_at: "2026-08-12T11:00:00.000Z",
      completed_at: "2026-08-12T11:01:00.000Z",
    },
  },
];

const previewMessages: Record<string, AssistantMessage[]> = {
  "conversation-preview-rates": [
    {
      id: "message-preview-user-1",
      conversation_id: "conversation-preview-rates",
      role: "USER",
      content: "请结合最新已收录新闻，解释为什么实际利率变化会影响黄金。",
      content_document: null,
      created_at: "2026-08-15T10:00:00.000Z",
      provenance: { kind: "PREVIEW_FIXTURE" },
    },
    {
      id: "message-preview-assistant-1",
      conversation_id: "conversation-preview-rates",
      role: "ASSISTANT",
      content: "实际利率是持有无息黄金的机会成本。已收录证据显示，市场在重新评估政策利率维持高位的时间；当实际利率预期上升时，美元与短端收益率通常同步走强，黄金因此承压。\n\n这不是交易指令。当前更值得观察的是实际利率、美元与黄金是否继续同向重定价，以及新的政策证据是否在决策时间前已经被系统收到。",
      content_document: {
        protocol: "assistant.content.v1",
        blocks: [
          {
            id: "block:answer",
            type: "markdown",
            version: "v1",
            data: { text: "实际利率是持有无息黄金的机会成本。已收录证据显示，市场在重新评估政策利率维持高位的时间；当实际利率预期上升时，美元与短端收益率通常同步走强，黄金因此承压。\n\n这不是交易指令。当前更值得观察的是实际利率、美元与黄金是否继续同向重定价，以及新的政策证据是否在决策时间前已经被系统收到。" },
            content_sha256: "81e514f062f5fa982676a14badc2f66b4bf44056e22e8e00382d11b332340f07",
          },
          {
            id: "block:metric:evidence",
            type: "metric",
            version: "v1",
            data: {
              label: "本轮检索证据",
              value: "2",
              unit: "条",
              trend: "UNKNOWN",
              detail: "检索截止 2026-08-15T10:00:00.000Z",
            },
            content_sha256: "543be2dc3569e5f5b3e772f427bdcf6e8a20c38f841c5efccae77080d78fd032",
          },
          {
            id: "block:news:1:2b374918875f",
            type: "news_card",
            version: "v1",
            data: {
              evidence_id: "preview-evidence-1",
              source: "Reuters",
              published_at: "2026-08-15T09:42:00.000Z",
              received_at: "2026-08-15T09:43:00.000Z",
              headline: "Fed officials signal rates may remain restrictive",
              summary: "政策官员继续强调利率可能维持高位，市场重新评估实际利率路径。",
              category: "MONETARY POLICY",
              impact: "实际利率与美元走强会提高持有无息黄金的机会成本。",
              relevance: null,
              source_url: "https://example.com/preview-evidence-1",
            },
            content_sha256: "094c8c1ef1eb6e121fe857dd269b50676d401432d8c6ecd4a90fb58b36581def",
          },
          {
            id: "block:news:2:2bc04c7b4ddb",
            type: "news_card",
            version: "v1",
            data: {
              evidence_id: "preview-evidence-2",
              source: "Aurum Evidence",
              published_at: "2026-08-15T09:10:00.000Z",
              received_at: "2026-08-15T09:12:00.000Z",
              headline: "Dollar and short yields rise together",
              summary: "美元与短端收益率同步走强，黄金价格承压。",
              category: "MARKET RESPONSE",
              impact: "跨市场同步变化为黄金重定价提供背景。",
              relevance: null,
              source_url: "https://example.com/preview-evidence-2",
            },
            content_sha256: "6cd69015de2b79309a8043a363f0dc9fba350a29d1b4986bef80bb16cd53cf42",
          },
          {
            id: "block:table:evidence-times",
            type: "table",
            version: "v1",
            data: {
              caption: "本轮检索证据时间",
              columns: [
                { key: "source", label: "来源", align: "left" },
                { key: "published", label: "发布时间", align: "left" },
                { key: "received", label: "系统收到", align: "left" },
              ],
              rows: [
                ["Reuters", "2026-08-15T09:42:00.000Z", "2026-08-15T09:43:00.000Z"],
                ["Aurum Evidence", "2026-08-15T09:10:00.000Z", "2026-08-15T09:12:00.000Z"],
              ],
            },
            content_sha256: "8876338f53a291719f4dbb35f0848a3cb332dd8827db4b08b2b5ed88573ef336",
          },
          {
            id: "block:boundary",
            type: "callout",
            version: "v1",
            data: {
              tone: "BOUNDARY",
              title: "决策支持边界",
              body: "该回答不会下单、执行交易或自动晋升模型；请按证据时间与来源自行判断。",
            },
            content_sha256: "2068814e4d606974d4b080e7c16b478d2728ee033b44d1a73276935844a040ca",
          },
        ],
        document_sha256: "34ffe46b719d66b1a711a7b051c2905eafe3ead0e6b0104ccc4809bd8300123a",
      },
      created_at: "2026-08-15T10:03:00.000Z",
      provenance: modelProvenance,
    },
  ],
  "conversation-preview-opening": [
    {
      id: "message-preview-user-2",
      conversation_id: "conversation-preview-opening",
      role: "USER",
      content: "周末开盘前，我应该优先核对哪些证据？",
      content_document: null,
      created_at: "2026-08-14T14:00:00.000Z",
      provenance: { kind: "PREVIEW_FIXTURE" },
    },
    {
      id: "message-preview-assistant-2",
      conversation_id: "conversation-preview-opening",
      role: "ASSISTANT",
      content: "优先核对三组有明确时间戳的证据：最新地缘事件是否有独立来源确认、美元与实际利率是否在休市前已经重定价、以及开盘后点差是否恢复到可比较范围。信息不足时应保持 WAIT，而不是把周末叙事当成已经可交易的事实。",
      content_document: null,
      created_at: "2026-08-14T14:04:00.000Z",
      provenance: modelProvenance,
    },
  ],
  "conversation-preview-inflation": [
    {
      id: "message-preview-user-3",
      conversation_id: "conversation-preview-inflation",
      role: "USER",
      content: "CPI 高于预期，为什么黄金当时仍然上涨？",
      content_document: null,
      created_at: "2026-08-13T12:30:00.000Z",
      provenance: { kind: "PREVIEW_FIXTURE" },
    },
    {
      id: "message-preview-assistant-3",
      conversation_id: "conversation-preview-inflation",
      role: "ASSISTANT",
      content: "单一数据方向不足以解释价格。需要同时检查市场此前定价、分项修订、美元反应、实际利率与避险需求；如果这些链路没有同一决策时点的完整证据，就只能标记为解释不足。",
      content_document: null,
      created_at: "2026-08-13T12:33:00.000Z",
      provenance: modelProvenance,
    },
  ],
  "conversation-preview-recovery": [
    {
      id: "message-preview-user-recovery",
      conversation_id: "conversation-preview-recovery",
      role: "USER",
      content: "请说明当前新闻对黄金方向的影响，并列出依据。",
      content_document: null,
      created_at: "2026-08-12T11:00:00.000Z",
      provenance: { kind: "PREVIEW_FIXTURE" },
    },
  ],
};

export const assistantPreviewOlderMessages: Record<string, AssistantMessage[]> = {
  "conversation-preview-rates": [
    {
      id: "message-preview-old-user",
      conversation_id: "conversation-preview-rates",
      role: "USER",
      content: "先记住：只使用系统在决策时间前已经收到的证据。",
      content_document: null,
      created_at: "2026-08-15T09:30:00.000Z",
      provenance: { kind: "PREVIEW_FIXTURE" },
    },
    {
      id: "message-preview-old-assistant",
      conversation_id: "conversation-preview-rates",
      role: "ASSISTANT",
      content: "明白。后续分析会保留收到时间、来源与证据编号，不会用事后材料回填。",
      content_document: null,
      created_at: "2026-08-15T09:31:00.000Z",
      provenance: modelProvenance,
    },
  ],
};

export const assistantPreviewCursor: Record<string, AssistantMessageCursor | null> = {
  "conversation-preview-rates": {
    before_created_at: "2026-08-15T10:00:00.000Z",
    before_id: "message-preview-user-1",
  },
  "conversation-preview-opening": null,
  "conversation-preview-inflation": null,
  "conversation-preview-recovery": null,
};

export function assistantPreviewMessages(conversationId: string) {
  return structuredClone(previewMessages[conversationId] ?? []);
}

const eventBase = {
  protocol: "assistant.event.v1" as const,
  conversation_id: "conversation-preview-rates",
  user_turn_id: "turn-preview-rates",
  message_id: null,
};

export const assistantPreviewEvents: AssistantEventEnvelope[] = [
  {
    ...eventBase,
    event_id: "event-preview-1",
    sequence: 1,
    type: "conversation.started",
    occurred_at: "2026-08-15T10:00:00.000Z",
    payload: {},
  },
  {
    ...eventBase,
    event_id: "event-preview-2",
    sequence: 2,
    type: "reasoning.started",
    occurred_at: "2026-08-15T10:00:00.010Z",
    payload: { reasoning_class: "TOOL_HEAVY" },
  },
  {
    ...eventBase,
    event_id: "event-preview-3",
    sequence: 3,
    type: "tool.started",
    occurred_at: "2026-08-15T10:00:00.020Z",
    payload: {
      call_id: "call-preview-news",
      tool_name: "search_news_v1",
      tool_version: "v1",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-4",
    sequence: 4,
    type: "tool.completed",
    occurred_at: "2026-08-15T10:00:00.030Z",
    payload: {
      call_id: "call-preview-news",
      tool_name: "search_news_v1",
      status: "SUCCEEDED",
      result_sha256: "a".repeat(64),
      evidence_count: 2,
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-5",
    sequence: 5,
    type: "answer.started",
    occurred_at: "2026-08-15T10:00:00.040Z",
    payload: {},
  },
  {
    ...eventBase,
    event_id: "event-preview-6",
    sequence: 6,
    type: "answer.delta",
    occurred_at: "2026-08-15T10:00:00.050Z",
    payload: { text: "实际利率是持有无息黄金的机会成本。" },
  },
  {
    ...eventBase,
    event_id: "event-preview-7",
    sequence: 7,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.060Z",
    payload: {
      block_id: "block:answer",
      block_type: "markdown",
      block_version: "v1",
      content_sha256: "81e514f062f5fa982676a14badc2f66b4bf44056e22e8e00382d11b332340f07",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-8",
    sequence: 8,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.070Z",
    payload: {
      block_id: "block:metric:evidence",
      block_type: "metric",
      block_version: "v1",
      content_sha256: "543be2dc3569e5f5b3e772f427bdcf6e8a20c38f841c5efccae77080d78fd032",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-9",
    sequence: 9,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.080Z",
    payload: {
      block_id: "block:news:1:2b374918875f",
      block_type: "news_card",
      block_version: "v1",
      content_sha256: "094c8c1ef1eb6e121fe857dd269b50676d401432d8c6ecd4a90fb58b36581def",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-10",
    sequence: 10,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.090Z",
    payload: {
      block_id: "block:news:2:2bc04c7b4ddb",
      block_type: "news_card",
      block_version: "v1",
      content_sha256: "6cd69015de2b79309a8043a363f0dc9fba350a29d1b4986bef80bb16cd53cf42",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-11",
    sequence: 11,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.100Z",
    payload: {
      block_id: "block:table:evidence-times",
      block_type: "table",
      block_version: "v1",
      content_sha256: "8876338f53a291719f4dbb35f0848a3cb332dd8827db4b08b2b5ed88573ef336",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-12",
    sequence: 12,
    type: "content.block",
    occurred_at: "2026-08-15T10:00:00.110Z",
    payload: {
      block_id: "block:boundary",
      block_type: "callout",
      block_version: "v1",
      content_sha256: "2068814e4d606974d4b080e7c16b478d2728ee033b44d1a73276935844a040ca",
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-13",
    message_id: "message-preview-assistant-1",
    sequence: 13,
    type: "answer.completed",
    occurred_at: "2026-08-15T10:03:00.000Z",
    payload: {
      content_sha256: "b".repeat(64),
      evidence_ids: ["preview-evidence-1", "preview-evidence-2"],
    },
  },
  {
    ...eventBase,
    event_id: "event-preview-14",
    sequence: 14,
    type: "conversation.completed",
    occurred_at: "2026-08-15T10:03:00.010Z",
    payload: {},
  },
];
