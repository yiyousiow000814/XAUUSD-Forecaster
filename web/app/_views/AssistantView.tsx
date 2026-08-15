"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AssistantConversationRail from "../_components/AssistantConversationRail";
import AssistantTranscript from "../_components/AssistantTranscript";
import DashboardLink from "../_components/DashboardLink";
import MobileDashboardNav from "../_components/MobileDashboardNav";
import { AssistantEventSequence, type AssistantEventEnvelope } from
  "../api/_shared/assistant-events";
import {
  AssistantClientError,
  cancelAssistantTurn,
  fetchAssistantConversation,
  fetchAssistantConversations,
  isAssistantTurnTerminal,
  mergeAssistantMessages,
  replayAssistantEvents,
  submitAssistantTurn,
  updateAssistantConversation,
  type AssistantChatTurn,
  type AssistantConversation,
  type AssistantFetcher,
  type AssistantMessage,
  type AssistantMessageCursor,
} from "../_lib/assistant-chat-client";
import {
  assistantPreviewConversations,
  assistantPreviewCursor,
  assistantPreviewEvents,
  assistantPreviewMessages,
  assistantPreviewOlderMessages,
} from "../_lib/assistant-preview-fixture";

const replayDelay = (milliseconds: number, signal: AbortSignal) => new Promise<void>(
  (resolve, reject) => {
    const finish = () => {
      signal.removeEventListener("abort", abort);
      resolve();
    };
    const timer = window.setTimeout(finish, milliseconds);
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Assistant replay aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  },
);

const waitUntilVisible = (signal: AbortSignal) => {
  if (document.visibilityState !== "hidden") return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      document.removeEventListener("visibilitychange", visible);
      signal.removeEventListener("abort", abort);
    };
    const visible = () => {
      if (document.visibilityState === "hidden") return;
      cleanup();
      resolve();
    };
    const abort = () => {
      cleanup();
      reject(new DOMException("Assistant replay aborted", "AbortError"));
    };
    document.addEventListener("visibilitychange", visible);
    signal.addEventListener("abort", abort, { once: true });
  });
};

const errorMessage = (reason: unknown) => {
  if (reason instanceof AssistantClientError) {
    if (reason.status === 401) return "Assistant 身份尚未通过验证，请刷新并完成 Cloudflare Access 登录。";
    if (reason.status === 429) return "Assistant 当前繁忙，请稍后再试。";
    return reason.message;
  }
  return reason instanceof Error ? reason.message : "Assistant 暂时无法连接";
};

const activeTurnFrom = (turn: AssistantChatTurn) => (
  turn.status === "PENDING" || turn.status === "PROCESSING" ? {
    id: turn.id,
    status: turn.status,
    event_sequence: turn.event_sequence,
    created_at: turn.created_at,
  } : null
);

const provisionalConversation = (
  turn: AssistantChatTurn,
  message: string,
): AssistantConversation => ({
  id: turn.conversation_id,
  title: turn.conversation_title ?? `${Array.from(message).slice(0, 31).join("")}…`,
  title_source: "PROVISIONAL",
  created_at: turn.created_at,
  last_activity_at: turn.created_at,
  archived_at: null,
  summary_version: 0,
  status: "ACTIVE",
  title_job_status: null,
  active_turn: activeTurnFrom(turn),
});

export default function AssistantView() {
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [cursor, setCursor] = useState<AssistantMessageCursor | null>(null);
  const [events, setEvents] = useState<AssistantEventEnvelope[]>([]);
  const [traceTurnId, setTraceTurnId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [archived, setArchived] = useState(false);
  const [preview, setPreview] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listRevision, setListRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [monitorRevision, setMonitorRevision] = useState(0);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  const retrySubmission = useRef<{
    message: string;
    conversationId: string | null;
    key: string;
  } | null>(null);

  const selectedConversation = useMemo(() => (
    conversations.find(item => item.id === selectedId) ?? null
  ), [conversations, selectedId]);
  const activeTurn = selectedConversation?.active_turn ?? null;
  const activeTurnId = activeTurn?.id ?? null;
  const activeTurnCreatedAt = activeTurn?.created_at ?? null;
  const selectedConversationId = selectedConversation?.id ?? null;

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++listRequest.current;
    const fetcher: AssistantFetcher = (input, init) => fetch(input, {
      ...init,
      signal: controller.signal,
    });
    void fetchAssistantConversations(archived, fetcher).then(result => {
      if (requestId !== listRequest.current) return;
      const next = result.preview
        ? (archived ? [] : structuredClone(assistantPreviewConversations))
        : result.items;
      const currentSelected = selectedIdRef.current;
      const nextSelected = currentSelected && next.some(item => item.id === currentSelected)
        ? currentSelected : next[0]?.id ?? null;
      setPreview(result.preview);
      setConversations(next);
      setSelectedId(nextSelected);
      if (nextSelected !== currentSelected) {
        setMessages(result.preview && nextSelected
          ? assistantPreviewMessages(nextSelected) : []);
        setCursor(result.preview && nextSelected
          ? assistantPreviewCursor[nextSelected] ?? null : null);
        setEvents(result.preview && nextSelected === "conversation-preview-rates"
          ? structuredClone(assistantPreviewEvents) : []);
        setTraceTurnId(result.preview && nextSelected === "conversation-preview-rates"
          ? "turn-preview-rates"
          : next.find(item => item.id === nextSelected)?.active_turn?.id ?? null);
        setDetailLoading(Boolean(nextSelected && !result.preview));
      }
      setError(null);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      setError(errorMessage(reason));
      setConversations([]);
      setSelectedId(null);
      setMessages([]);
      setCursor(null);
      setEvents([]);
      setTraceTurnId(null);
    }).finally(() => {
      if (requestId === listRequest.current) setListLoading(false);
    });
    return () => controller.abort();
  }, [archived, listRevision]);

  useEffect(() => {
    if (!selectedId || preview) return;
    const controller = new AbortController();
    const requestId = ++detailRequest.current;
    const fetcher: AssistantFetcher = (input, init) => fetch(input, {
      ...init,
      signal: controller.signal,
    });
    void fetchAssistantConversation(selectedId, null, fetcher).then(detail => {
      if (requestId !== detailRequest.current) return;
      setMessages(detail.items);
      setCursor(detail.next_cursor);
      setConversations(current => current.map(item => (
        item.id === detail.conversation.id ? detail.conversation : item
      )));
      if (detail.conversation.active_turn) {
        setTraceTurnId(detail.conversation.active_turn.id);
      }
      setError(null);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    }).finally(() => {
      if (requestId === detailRequest.current) setDetailLoading(false);
    });
    return () => controller.abort();
  }, [detailRevision, preview, selectedId]);

  useEffect(() => {
    if (!activeTurnId || !activeTurnCreatedAt || !selectedConversationId || preview) return;
    const controller = new AbortController();
    const sequence = new AssistantEventSequence();
    const turnId = activeTurnId;
    const conversationId = selectedConversationId;
    const deadline = Date.parse(activeTurnCreatedAt) + 31 * 60 * 1_000;

    const monitor = async () => {
      let after = 0;
      let consecutiveErrors = 0;
      while (!controller.signal.aborted && Date.now() <= deadline) {
        try {
          await waitUntilVisible(controller.signal);
          if (Date.now() > deadline) break;
          const page = await replayAssistantEvents(
            turnId, after, controller.signal,
          );
          for (const event of page.events) {
            if (event.user_turn_id !== turnId || event.conversation_id !== conversationId) {
              throw new AssistantClientError(
                "EVENT_OWNERSHIP_MISMATCH", "Assistant 事件身份发生变化",
              );
            }
            sequence.append(event);
          }
          after = page.next_sequence;
          consecutiveErrors = 0;
          setEvents(sequence.events);
          setError(null);
          if (isAssistantTurnTerminal(page.turn_status) || sequence.terminal) {
            setListRevision(value => value + 1);
            setListLoading(true);
            setDetailRevision(value => value + 1);
            setDetailLoading(true);
            return;
          }
          if (!page.has_more) await replayDelay(1_200, controller.signal);
        } catch (reason) {
          if (controller.signal.aborted) return;
          consecutiveErrors += 1;
          if (consecutiveErrors >= 4) {
            setError(`事件连接已暂停：${errorMessage(reason)}`);
            return;
          }
          try {
            await replayDelay(
              Math.min(4_000, consecutiveErrors * 1_000), controller.signal,
            );
          } catch {
            return;
          }
        }
      }
      if (!controller.signal.aborted) {
        setError("本轮已超过浏览器自动重连窗口，请重新连接确认最终状态。");
      }
    };
    void monitor();
    return () => controller.abort();
  }, [activeTurnCreatedAt, activeTurnId, monitorRevision, preview, selectedConversationId]);

  useEffect(() => {
    if (!railOpen) return;
    const close = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setRailOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [railOpen]);

  const selectConversation = useCallback((conversation: AssistantConversation) => {
    setSelectedId(conversation.id);
    setMessages(preview ? assistantPreviewMessages(conversation.id) : []);
    setCursor(preview ? assistantPreviewCursor[conversation.id] ?? null : null);
    setEvents(preview && conversation.id === "conversation-preview-rates"
      ? structuredClone(assistantPreviewEvents) : []);
    setTraceTurnId(preview && conversation.id === "conversation-preview-rates"
      ? "turn-preview-rates" : conversation.active_turn?.id ?? null);
    setDetailLoading(!preview);
    setError(null);
    setRailOpen(false);
  }, [preview]);

  const startNew = useCallback(() => {
    setArchived(false);
    setSelectedId(null);
    setMessages([]);
    setCursor(null);
    setEvents([]);
    setTraceTurnId(null);
    setDetailLoading(false);
    setError(null);
    setRailOpen(false);
  }, []);

  const changeDraft = useCallback((value: string) => {
    setDraft(value);
    const retry = retrySubmission.current;
    if (retry && (retry.message !== value || retry.conversationId !== selectedId)) {
      retrySubmission.current = null;
    }
  }, [selectedId]);

  const send = useCallback(async () => {
    const message = draft.trim();
    if (!message || sending || preview || activeTurn) return;
    const conversationId = selectedConversation?.id ?? null;
    const retry = retrySubmission.current;
    const key = retry?.message === message && retry.conversationId === conversationId
      ? retry.key : `assistant-turn:${crypto.randomUUID()}`;
    retrySubmission.current = { message, conversationId, key };
    setSending(true);
    setError(null);
    try {
      const turn = await submitAssistantTurn({
        message,
        conversation_id: conversationId,
        idempotency_key: key,
      });
      retrySubmission.current = null;
      setDraft("");
      setArchived(false);
      setSelectedId(turn.conversation_id);
      setTraceTurnId(turn.id);
      setEvents([]);
      const conversation = provisionalConversation(turn, message);
      setConversations(current => {
        const without = current.filter(item => item.id !== conversation.id);
        return [conversation, ...without];
      });
      setMessages(current => mergeAssistantMessages(current, [{
        id: turn.user_message_id,
        conversation_id: turn.conversation_id,
        role: "USER",
        content: message,
        content_document: null,
        created_at: turn.created_at,
        provenance: { kind: "PENDING_SERVER_CONFIRMATION", turn_id: turn.id },
      }]));
      setListRevision(value => value + 1);
      setListLoading(true);
      setDetailRevision(value => value + 1);
      setDetailLoading(true);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSending(false);
    }
  }, [activeTurn, draft, preview, selectedConversation?.id, sending]);

  const cancel = useCallback(async () => {
    if (!activeTurn || cancelling || preview) return;
    setCancelling(true);
    setError(null);
    try {
      await cancelAssistantTurn(activeTurn.id);
      setListRevision(value => value + 1);
      setListLoading(true);
      setDetailRevision(value => value + 1);
      setDetailLoading(true);
      setMonitorRevision(value => value + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setCancelling(false);
    }
  }, [activeTurn, cancelling, preview]);

  const loadOlder = useCallback(async () => {
    if (!selectedId || !cursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      if (preview) {
        setMessages(current => mergeAssistantMessages(
          current,
          structuredClone(assistantPreviewOlderMessages[selectedId] ?? []),
        ));
        setCursor(null);
      } else {
        const detail = await fetchAssistantConversation(selectedId, cursor);
        setMessages(current => mergeAssistantMessages(current, detail.items));
        setCursor(detail.next_cursor);
      }
      setError(null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoadingOlder(false);
    }
  }, [cursor, loadingOlder, preview, selectedId]);

  const mutateConversation = useCallback(async (
    action: "RENAME" | "ARCHIVE" | "UNARCHIVE" | "REGENERATE_TITLE",
    title?: string,
  ) => {
    if (!selectedConversation || preview || mutating) return;
    setMutating(true);
    setError(null);
    try {
      await updateAssistantConversation({
        conversation_id: selectedConversation.id,
        action,
        title,
        ...(action === "REGENERATE_TITLE"
          ? { idempotency_key: `assistant-title:${crypto.randomUUID()}` }
          : {}),
      });
      if (action === "ARCHIVE" || action === "UNARCHIVE") {
        setSelectedId(null);
        setMessages([]);
        setCursor(null);
        setEvents([]);
        setTraceTurnId(null);
      }
      setListRevision(value => value + 1);
      setListLoading(true);
      setDetailRevision(value => value + 1);
      setDetailLoading(
        action !== "ARCHIVE" && action !== "UNARCHIVE" && Boolean(selectedConversation),
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setMutating(false);
    }
  }, [mutating, preview, selectedConversation]);

  const retry = useCallback(() => {
    setError(null);
    setListRevision(value => value + 1);
    setListLoading(true);
    setDetailRevision(value => value + 1);
    setDetailLoading(Boolean(selectedId));
    setMonitorRevision(value => value + 1);
  }, [selectedId]);

  return <main className="assistant-main">
    <div className="grain" />
    <header className="topbar assistant-topbar">
      <DashboardLink className="brand audit-brand brand-button" href="/" replace>
        <span className="brand-mark">AU</span>
        <div><strong>Aurum Assistant</strong><small>可追溯分析 · 私有会话</small></div>
      </DashboardLink>
      <div className="top-actions">
        <DashboardLink className="audit-link" href="/audit?view=news">新闻与证据</DashboardLink>
        <DashboardLink className="audit-link" href="/status">系统状态</DashboardLink>
        <DashboardLink className="audit-link" href="/" replace>← 返回实时室</DashboardLink>
        <span className={`assistant-owner-pill${preview ? " is-preview" : ""}`}>
          <i aria-hidden="true" /> {preview ? "PREVIEW" : "PRIVATE"}
        </span>
      </div>
      <MobileDashboardNav current="assistant" />
    </header>

    <section className="assistant-workbench">
      <AssistantConversationRail
        archived={archived}
        conversations={conversations}
        loading={listLoading}
        onClose={() => setRailOpen(false)}
        onNew={startNew}
        onSelect={selectConversation}
        onToggleArchived={value => {
          setArchived(value);
          setSelectedId(null);
          setMessages([]);
          setCursor(null);
          setEvents([]);
          setTraceTurnId(null);
          setListLoading(true);
          setDetailLoading(false);
          setRailOpen(false);
        }}
        open={railOpen}
        preview={preview}
        selectedId={selectedId}
      />
      <AssistantTranscript
        key={selectedId ?? "assistant-new-thread"}
        cancelling={cancelling}
        conversation={selectedConversation}
        cursor={cursor}
        draft={draft}
        error={error}
        events={traceTurnId ? events : []}
        loading={detailLoading}
        loadingOlder={loadingOlder}
        messages={messages}
        mutating={mutating}
        onArchive={() => void mutateConversation(
          selectedConversation?.status === "ARCHIVED" ? "UNARCHIVE" : "ARCHIVE",
        )}
        onCancel={() => void cancel()}
        onDraftChange={changeDraft}
        onLoadOlder={() => void loadOlder()}
        onOpenRail={() => setRailOpen(true)}
        onRegenerateTitle={() => void mutateConversation("REGENERATE_TITLE")}
        onRename={title => void mutateConversation("RENAME", title)}
        onRetry={retry}
        onSend={() => void send()}
        preview={preview}
        sending={sending}
      />
    </section>
  </main>;
}
