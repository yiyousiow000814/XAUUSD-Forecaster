"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardLink from "../_components/DashboardLink";
import { ADMIN_API_PREFIX, adminErrorPresentation, adminResponseError, type AdminErrorPresentation } from "../_lib/admin-client";
import { ASSISTANT_ACCEPTING_TURNS } from "../_lib/assistant-availability";
import { assistantHealthPresentation, type AssistantHealthPayload } from "../_lib/assistant-health-presentation";
import {
  operatorRetryPreviewJobs,
  operatorRetryPreviewRequests,
  summarizeOperatorRetryQueue,
  type OperatorRetryJob,
  type OperatorRetryRequest,
} from "../_lib/operator-retry-client";

type RetrySummary = ReturnType<typeof summarizeOperatorRetryQueue>;

const emptySummary: RetrySummary = {
  total: 0, waiting: 0, overridden: 0, applying: 0, conflict: 0,
};

export default function AdminOverviewView() {
  const [retrySummary, setRetrySummary] = useState<RetrySummary>(emptySummary);
  const [preview, setPreview] = useState(false);
  const [retryError, setRetryError] = useState<AdminErrorPresentation | null>(null);
  const [assistantHealth, setAssistantHealth] = useState<AssistantHealthPayload | null>(null);
  const [assistantError, setAssistantError] = useState<AdminErrorPresentation | null>(null);

  const loadRetrySummary = useCallback(async () => {
    try {
      const response = await fetch(`${ADMIN_API_PREFIX}/operator-retry`, { cache: "no-store" });
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!response.ok || response.redirected || contentType.includes("text/html")) {
        throw adminResponseError(response, "重试任务状态暂不可用");
      }
      const payload = await response.json() as {
        items?: OperatorRetryJob[];
        requests?: OperatorRetryRequest[];
        preview?: boolean;
      };
      const jobs = payload.preview ? operatorRetryPreviewJobs : payload.items ?? [];
      const requests = payload.preview ? operatorRetryPreviewRequests : payload.requests ?? [];
      setRetrySummary(summarizeOperatorRetryQueue(jobs, requests));
      setPreview(current => current || Boolean(payload.preview));
      setRetryError(null);
    } catch (reason) {
      setRetrySummary(emptySummary);
      setRetryError(adminErrorPresentation(reason, "重试任务状态暂不可用"));
    }
  }, []);

  const loadAssistantHealth = useCallback(async () => {
    try {
      const response = await fetch(`${ADMIN_API_PREFIX}/assistant-health`, { cache: "no-store" });
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!response.ok || response.redirected || contentType.includes("text/html")) {
        throw adminResponseError(response, "Assistant 运行状态暂不可用");
      }
      const payload = await response.json() as AssistantHealthPayload;
      setAssistantHealth(payload);
      setPreview(current => current || payload.current === false);
      setAssistantError(null);
    } catch (reason) {
      setAssistantHealth(null);
      setAssistantError(adminErrorPresentation(reason, "Assistant 运行状态暂不可用"));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadRetrySummary();
      void loadAssistantHealth();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadAssistantHealth, loadRetrySummary]);

  const authRequired = retryError?.kind === "AUTH_REQUIRED" || assistantError?.kind === "AUTH_REQUIRED";

  return <main className="admin-overview-main">
    <header className="admin-overview-hero">
      <div><p>OWNER OPERATIONS</p><h1>管理后台</h1></div>
      <p>一个登录会话内查看私有工具与需要处理的运维状态。</p>
    </header>
    {preview ? <p className="admin-preview-notice">PR Preview 使用合成只读 Admin 数据，不代表 Cloudflare Access 已完成登录，也不具有生产操作权限。</p> : null}
    {authRequired ? <p className="admin-preview-notice is-auth">需要管理员登录。 <a href="/admin">管理员登录</a></p> : null}
    <section className="admin-overview-grid" aria-label="管理概览">
      <DashboardLink className="admin-overview-card" href="/admin/assistant">
        <span>ASSISTANT</span><h2>Assistant</h2>
        <strong>{ASSISTANT_ACCEPTING_TURNS ? "可接受新对话" : "已暂停"}</strong>
        <small className="admin-overview-health">{assistantError?.message ?? assistantHealthPresentation(assistantHealth)}</small>
        <b>打开 Assistant →</b>
      </DashboardLink>
      <DashboardLink className="admin-overview-card is-retry" href="/admin/retry-jobs">
        <span>SCHEDULER</span><h2>重试任务</h2>
        {retryError ? <strong>{retryError.message}</strong> : <dl>
          <div><dt>总任务</dt><dd>{retrySummary.total}</dd></div>
          <div><dt>等待应用</dt><dd>{retrySummary.applying}</dd></div>
          <div><dt>冲突</dt><dd>{retrySummary.conflict}</dd></div>
        </dl>}
        <b>打开重试队列 →</b>
      </DashboardLink>
      <DashboardLink className="admin-overview-card" href="/admin/ai-usage">
        <span>PROVIDER CAPACITY</span><h2>AI 模型用量</h2>
        <strong>权威配额账本</strong>
        <b>打开用量状态 →</b>
      </DashboardLink>
    </section>
  </main>;
}
