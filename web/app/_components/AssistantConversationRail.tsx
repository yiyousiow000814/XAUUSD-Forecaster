"use client";

import type { AssistantConversation } from "../_lib/assistant-chat-client";

const localStamp = (value: string) => new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
}).format(new Date(value));

export default function AssistantConversationRail({
  conversations,
  selectedId,
  archived,
  loading,
  open,
  preview,
  onClose,
  onNew,
  onSelect,
  onToggleArchived,
}: {
  conversations: AssistantConversation[];
  selectedId: string | null;
  archived: boolean;
  loading: boolean;
  open: boolean;
  preview: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (conversation: AssistantConversation) => void;
  onToggleArchived: (archived: boolean) => void;
}) {
  return <>
    <button
      aria-label="关闭会话列表"
      className={`assistant-rail-scrim${open ? " is-open" : ""}`}
      onClick={onClose}
      type="button"
    />
    <aside
      aria-label="Assistant 会话列表"
      className={`assistant-conversation-rail${open ? " is-open" : ""}`}
    >
      <header>
        <div>
          <span>THREAD LEDGER</span>
          <strong>{archived ? "已归档会话" : "当前会话"}</strong>
        </div>
        <button aria-label="关闭会话列表" className="assistant-rail-close" onClick={onClose} type="button">×</button>
      </header>

      <button
        className="assistant-new-thread"
        disabled={archived}
        onClick={onNew}
        type="button"
      >
        <span aria-hidden="true">＋</span>
        <span><b>开始新问题</b><small>创建独立、可恢复的会话</small></span>
      </button>

      <div className="assistant-rail-mode" role="group" aria-label="会话范围">
        <button className={!archived ? "active" : ""} onClick={() => onToggleArchived(false)} type="button">进行中</button>
        <button className={archived ? "active" : ""} onClick={() => onToggleArchived(true)} type="button">已归档</button>
      </div>

      <div className="assistant-thread-list" aria-busy={loading}>
        {loading && conversations.length === 0
          ? <p className="assistant-rail-empty">正在读取私有会话…</p>
          : null}
        {!loading && conversations.length === 0
          ? <p className="assistant-rail-empty">{archived ? "没有已归档会话" : "还没有会话，从一个具体问题开始。"}</p>
          : null}
        {conversations.map((conversation, index) => <button
          aria-current={selectedId === conversation.id ? "page" : undefined}
          className={selectedId === conversation.id ? "active" : ""}
          key={conversation.id}
          onClick={() => onSelect(conversation)}
          type="button"
        >
          <span className="assistant-thread-index">{String(index + 1).padStart(2, "0")}</span>
          <span className="assistant-thread-copy">
            <b>{conversation.title}</b>
            <small>
              {conversation.active_turn ? <i>正在回答</i> : localStamp(conversation.last_activity_at)}
              {conversation.title_job_status === "PENDING" || conversation.title_job_status === "PROCESSING"
                ? " · 标题生成中" : ""}
            </small>
          </span>
          {conversation.active_turn ? <span className="assistant-thread-pulse" aria-hidden="true" /> : null}
        </button>)}
      </div>

      <footer>
        <span>OWNER-SCOPED</span>
        <span>{preview ? "FIXTURE / READ ONLY" : "D1 / PRIVATE"}</span>
      </footer>
    </aside>
  </>;
}
