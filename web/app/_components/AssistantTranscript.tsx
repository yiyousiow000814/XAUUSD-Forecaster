"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { AssistantEventEnvelope } from "../api/_shared/assistant-events";
import {
  assistantAnswerDraft,
  assistantProgressItems,
  type AssistantConversation,
  type AssistantMessage,
  type AssistantMessageCursor,
} from "../_lib/assistant-chat-client";
import { ASSISTANT_PREVIEW_FIXTURE_LABEL } from "../_lib/assistant-preview-fixture";

const timeLabel = (value: string) => new Intl.DateTimeFormat("zh-CN", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
}).format(new Date(value));

const byteLength = (value: string) => new TextEncoder().encode(value).length;

function messageAudit(message: AssistantMessage) {
  if (message.role !== "ASSISTANT") return null;
  const provenance = message.provenance;
  const agent = provenance.agent && typeof provenance.agent === "object"
    && !Array.isArray(provenance.agent)
    ? provenance.agent as Record<string, unknown>
    : null;
  const toolRounds = Array.isArray(agent?.tool_execution)
    ? agent.tool_execution : [];
  const toolCount = toolRounds.reduce((total, round) => (
    total + (Array.isArray(round) ? round.length : 0)
  ), 0);
  const evidenceCount = Array.isArray(agent?.evidence_ids)
    ? agent.evidence_ids.length : 0;
  const model = typeof provenance.model_version === "string"
    ? provenance.model_version : "未记录模型";
  return { model, toolCount, evidenceCount };
}

function AssistantMessageCard({ message, index }: { message: AssistantMessage; index: number }) {
  const audit = messageAudit(message);
  return <article className={`assistant-message is-${message.role.toLowerCase()}`}>
    <header>
      <span>{message.role === "USER" ? "YOU / REQUEST" : "AURUM / RESPONSE"}</span>
      <span>MSG {String(index + 1).padStart(2, "0")} · {timeLabel(message.created_at)}</span>
    </header>
    <p>{message.content}</p>
    {audit ? <details className="assistant-message-audit">
      <summary>查看回答凭据</summary>
      <dl>
        <div><dt>模型</dt><dd>{audit.model}</dd></div>
        <div><dt>只读工具调用</dt><dd>{audit.toolCount}</dd></div>
        <div><dt>证据引用</dt><dd>{audit.evidenceCount}</dd></div>
      </dl>
    </details> : null}
  </article>;
}

export default function AssistantTranscript({
  conversation,
  messages,
  cursor,
  events,
  draft,
  preview,
  error,
  loading,
  sending,
  loadingOlder,
  mutating,
  cancelling,
  onDraftChange,
  onSend,
  onCancel,
  onLoadOlder,
  onRename,
  onArchive,
  onRegenerateTitle,
  onOpenRail,
  onRetry,
}: {
  conversation: AssistantConversation | null;
  messages: AssistantMessage[];
  cursor: AssistantMessageCursor | null;
  events: AssistantEventEnvelope[];
  draft: string;
  preview: boolean;
  error: string | null;
  loading: boolean;
  sending: boolean;
  loadingOlder: boolean;
  mutating: boolean;
  cancelling: boolean;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onLoadOlder: () => void;
  onRename: (title: string) => void;
  onArchive: () => void;
  onRegenerateTitle: () => void;
  onOpenRail: () => void;
  onRetry: () => void;
}) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState("");
  const messageScroll = useRef<HTMLDivElement>(null);
  const progress = useMemo(() => assistantProgressItems(events), [events]);
  const activeTurn = conversation?.active_turn ?? null;
  const answerDraft = useMemo(
    () => activeTurn ? assistantAnswerDraft(events) : null,
    [activeTurn, events],
  );
  const draftBytes = byteLength(draft);
  const invalidDraft = draftBytes === 0 || draftBytes > 16_000;
  const lastMessageId = messages.at(-1)?.id;

  useEffect(() => {
    const element = messageScroll.current;
    if (!element) return;
    const frame = window.requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [conversation?.id, lastMessageId]);

  useEffect(() => {
    const element = messageScroll.current;
    if (!element || element.scrollHeight - element.scrollTop - element.clientHeight > 180) return;
    const frame = window.requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [events.length]);

  const submitOnEnter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!invalidDraft && !sending && !activeTurn && !preview) onSend();
  };

  return <section className="assistant-transcript" aria-label="Assistant 对话">
    <header className="assistant-transcript-head">
      <button className="assistant-open-rail" onClick={onOpenRail} type="button">
        <span aria-hidden="true">☰</span> 会话
      </button>
      <div className="assistant-thread-heading">
        <span>{conversation ? "ACTIVE THREAD" : "NEW THREAD"}</span>
        {editingTitle && conversation ? <form onSubmit={event => {
          event.preventDefault();
          setEditingTitle(false);
          onRename(title);
        }}>
          <label className="sr-only" htmlFor="assistant-title-input">会话标题</label>
          <input
            id="assistant-title-input"
            onChange={event => setTitle(event.currentTarget.value)}
            value={title}
          />
          <button disabled={mutating || title.trim().length < 2} type="submit">保存</button>
          <button onClick={() => setEditingTitle(false)} type="button">取消</button>
        </form> : <h1>{conversation?.title ?? "向证据提问"}</h1>}
        <small>
          {conversation
            ? `${conversation.title_source === "AI" ? "AI 标题" : conversation.title_source === "USER" ? "自定义标题" : "临时标题"} · ${timeLabel(conversation.last_activity_at)}`
            : "新会话会在消息被认证并接收后立即建立"}
        </small>
      </div>
      {conversation ? <div className="assistant-thread-actions">
        <button disabled={mutating || preview} onClick={() => {
          setTitle(conversation.title);
          setEditingTitle(true);
        }} type="button">重命名</button>
        <button disabled={mutating || preview || messages.every(message => message.role !== "ASSISTANT")} onClick={onRegenerateTitle} type="button">重写标题</button>
        <button disabled={mutating || preview || Boolean(activeTurn)} onClick={onArchive} type="button">
          {conversation.status === "ARCHIVED" ? "恢复" : "归档"}
        </button>
      </div> : null}
    </header>

    <div className="assistant-transcript-banners">
      {preview ? <div className="assistant-preview-notice" role="note">
        <b>PREVIEW FIXTURE</b><span>{ASSISTANT_PREVIEW_FIXTURE_LABEL}</span>
      </div> : null}
      {error ? <div className="assistant-chat-error" role="alert">
        <span>{error}</span><button onClick={onRetry} type="button">重新连接</button>
      </div> : null}
    </div>

    <div className="assistant-message-scroll" aria-busy={loading} ref={messageScroll}>
      {cursor ? <button className="assistant-load-older" disabled={loadingOlder} onClick={onLoadOlder} type="button">
        {loadingOlder ? "正在读取…" : "↑ 加载更早消息"}
      </button> : null}
      {loading ? <div className="assistant-empty-state"><i /><p>正在读取可恢复的会话记录…</p></div> : null}
      {!loading && messages.length === 0 ? <div className="assistant-empty-state">
        <span>AU</span>
        <h2>{conversation ? "这个会话还没有消息" : "不是交易终端，是一张可追溯的分析桌"}</h2>
        <p>可以询问新闻证据、价格背景或系统已经知道什么。回答只提供 XAUUSD 决策支持，不会下单、交易或自动晋升模型。</p>
        <div><b>POINT-IN-TIME</b><b>READ-ONLY TOOLS</b><b>SHADOW ONLY</b></div>
      </div> : null}
      {messages.map((message, index) => <AssistantMessageCard
        index={index}
        key={message.id}
        message={message}
      />)}

      {answerDraft !== null ? <article className="assistant-message is-assistant is-streaming">
        <header><span>AURUM / PROVISIONAL</span><span>LIVE · 尚未写入历史</span></header>
        <p>{answerDraft || "正在建立安全的回答输出…"}</p>
      </article> : null}

      {progress.length > 0 ? <details className="assistant-progress" open={Boolean(activeTurn)}>
        <summary>{activeTurn ? "本轮正在进行" : "查看本轮分析过程"}<span>{progress.length} 个公开阶段</span></summary>
        <ol>{progress.map(item => <li className={`is-${item.state.toLowerCase()}`} key={item.id}>
          <i aria-hidden="true" />
          <div><b>{item.label}</b>{item.detail ? <small>{item.detail}</small> : null}</div>
        </li>)}</ol>
        <footer>这里只展示真实 backend/tool 事件，不包含私有 chain-of-thought。</footer>
      </details> : null}
      <div aria-live="polite" className="sr-only">
        {activeTurn ? "Assistant 正在处理当前问题" : ""}
      </div>
    </div>

    <div className="assistant-composer-shell">
      {activeTurn ? <div className="assistant-active-turn">
        <span><i aria-hidden="true" /> {activeTurn.status === "PENDING" ? "等待安全容量" : "正在生成回答"}</span>
        <button disabled={cancelling || preview} onClick={onCancel} type="button">{cancelling ? "取消中…" : "取消本轮"}</button>
      </div> : null}
      {conversation?.status === "ARCHIVED" ? <p className="assistant-archived-note">此会话已归档；恢复后才能继续提问。</p> : <form onSubmit={event => {
        event.preventDefault();
        if (!invalidDraft && !sending && !activeTurn && !preview) onSend();
      }}>
        <label htmlFor="assistant-message-input">给 Aurum Assistant 的问题</label>
        <textarea
          disabled={Boolean(activeTurn) || preview}
          id="assistant-message-input"
          onChange={event => onDraftChange(event.currentTarget.value)}
          onKeyDown={submitOnEnter}
          placeholder={preview ? "Preview 只读；生产环境认证后可发送" : "例如：最新已收录的政策证据如何影响黄金？"}
          rows={3}
          value={draft}
        />
        <div className="assistant-composer-meta">
          <span>Enter 发送 · Shift + Enter 换行 · {draftBytes.toLocaleString("zh-CN")}/16,000 bytes</span>
          <button disabled={invalidDraft || sending || Boolean(activeTurn) || preview} type="submit">
            <span>{sending ? "发送中" : preview ? "只读预览" : "发送问题"}</span><b aria-hidden="true">↗</b>
          </button>
        </div>
      </form>}
      <small>回答属于决策支持，不会进入预测训练，也不会触发交易。</small>
    </div>
  </section>;
}
