"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  latestOperatorRetryRequests,
  operatorRetryPreviewJobs,
  operatorRetryPreviewRequests,
  operatorRetryCommandPresentation,
  shouldPollOperatorRetryRequests,
  summarizeOperatorRetryQueue,
  type OperatorRetryJob,
  type OperatorRetryRequest,
} from "../_lib/operator-retry-client";

type RetryMode = "KEEP_ORIGINAL" | "IMMEDIATE" | "DELAY_15_MIN" | "DELAY_1_HOUR" | "IDLE_CAPACITY" | "CUSTOM_TIME";
type RetryJob = OperatorRetryJob & { override_mode: RetryMode | null };

const actions: Array<{ mode: RetryMode; label: string; help: string }> = [
  { mode: "IMMEDIATE", label: "立即可领取", help: "立即进入可领取状态，仍受配额与 scheduler 控制。" },
  { mode: "DELAY_15_MIN", label: "15 分钟后", help: "从提交时刻延后 15 分钟。" },
  { mode: "DELAY_1_HOUR", label: "1 小时后", help: "从提交时刻延后 1 小时。" },
  { mode: "IDLE_CAPACITY", label: "让位 30 分钟", help: "30 分钟内让位给同池其他可领取任务，之后恢复原有排序。" },
  { mode: "CUSTOM_TIME", label: "指定时间", help: "按 Dashboard 固定 UTC+8 时区指定。" },
  { mode: "KEEP_ORIGINAL", label: "恢复自动计划", help: "撤销当前人工调整，恢复保留的自动计划。" },
];
const taskLabels: Record<string, string> = {
  ACTIVE_ANNOTATION: "新闻语义分析",
  ACTIVE_IMPACT: "影响评估",
  TITLE_TRANSLATION: "标题翻译",
};

const localTime = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" })
  : "—";
const compactTime = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-CN", {
      hour12: false, timeZone: "Asia/Kuala_Lumpur",
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    })
  : "—";
const localInput = () => {
  const local = new Date(Date.now() + 8 * 60 * 60_000 + 15 * 60_000);
  return local.toISOString().slice(0, 16);
};
const utcFromKualaLumpurInput = (value: string) => {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  return new Date(`${value}:00+08:00`).toISOString();
};
const stateLabel = (state: string) => state === "LEASED"
  ? "正在执行，不能调整" : state === "BACKING_OFF" ? "等待重试" : "等待领取";

export default function RetryQueue() {
  const [jobs, setJobs] = useState<RetryJob[]>([]);
  const [requests, setRequests] = useState<OperatorRetryRequest[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkMode, setBulkMode] = useState<RetryMode>("IMMEDIATE");
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [customTime, setCustomTime] = useState(localInput);
  const [pending, setPending] = useState<{ mode: RetryMode; ids: string[] } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);

  const load = useCallback(async (clearMessage = true) => {
    try {
      const response = await fetch("/api/operator-retry", { cache: "no-store" });
      if (response.status === 401 || !response.headers.get("content-type")?.includes("application/json")) {
        setAuthRequired(true);
        throw new Error("需要完成 Dashboard Operator 身份验证后才能查看运维证据和调整计划。");
      }
      if (!response.ok) throw new Error("重试任务暂时无法读取。");
      const payload = await response.json() as {
        items?: RetryJob[]; requests?: OperatorRetryRequest[]; preview?: boolean;
      };
      setJobs(payload.preview ? operatorRetryPreviewJobs as RetryJob[] : payload.items ?? []);
      setRequests(payload.preview ? operatorRetryPreviewRequests : payload.requests ?? []);
      setPreview(Boolean(payload.preview));
      setAuthRequired(false);
      if (clearMessage) setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重试任务暂时无法读取。");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const latestRequests = useMemo(() => latestOperatorRetryRequests(requests), [requests]);
  const summary = useMemo(() => summarizeOperatorRetryQueue(jobs, requests), [jobs, requests]);
  useEffect(() => {
    if (preview || !shouldPollOperatorRetryRequests(requests)) return;
    const timer = window.setTimeout(() => void load(false), 1_500);
    return () => window.clearTimeout(timer);
  }, [load, preview, requests]);

  const eligible = useMemo(
    () => jobs.filter(job => job.state === "QUEUED" || job.state === "BACKING_OFF"),
    [jobs],
  );
  const choose = (mode: RetryMode, ids: string[]) => {
    if (!ids.length) { setMessage("请先选择可调整的任务。"); return; }
    setPending({ mode, ids });
    setMessage(null);
  };
  const submit = async () => {
    if (!pending) return;
    if (preview) { setMessage("PR Preview 只演示交互，不会提交调度命令。"); return; }
    if (!reason.trim()) { setMessage("请填写这次调整的原因。"); return; }
    const requested = pending.mode === "CUSTOM_TIME" ? utcFromKualaLumpurInput(customTime) : null;
    if (pending.mode === "CUSTOM_TIME" && !requested) { setMessage("指定日期时间无效。"); return; }
    setLoading(true);
    try {
      const response = await fetch("/api/operator-retry", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          job_ids: pending.ids, mode: pending.mode, reason: reason.trim(),
          requested_available_at: requested,
        }),
      });
      const payload = await response.json() as { accepted?: number; error?: string; items?: Array<{ status: string }> };
      if (!response.ok && response.status !== 207) throw new Error(payload.error ?? "提交失败");
      const rejected = (payload.items ?? []).filter(item => item.status === "REJECTED" || item.status === "CONFLICT").length;
      setMessage(`云端已接受 ${payload.accepted ?? 0} 个命令，尚不代表 Windows scheduler 已应用${rejected ? `；另有 ${rejected} 个未被接受` : ""}。`);
      setPending(null); setReason(""); setSelected(new Set()); setExpandedJobId(null);
      await load(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交失败");
    } finally { setLoading(false); }
  };

  return <section className="retry-queue" id="retry-jobs" aria-label="重试任务">
    <header className="retry-queue-header">
      <div><p className="eyebrow">PRIVATE OPERATOR QUEUE</p><h1>重试任务</h1><p>云端接受、Windows 应用与实际执行分阶段记录。</p></div>
      <dl className="retry-queue-summary" aria-label="重试队列摘要">
        <div><dt>总任务</dt><dd>{summary.total}</dd></div>
        <div><dt>等待重试</dt><dd>{summary.waiting}</dd></div>
        <div><dt>人工调整</dt><dd>{summary.overridden}</dd></div>
        <div><dt>等待应用</dt><dd>{summary.applying}</dd></div>
        <div><dt>冲突</dt><dd>{summary.conflict}</dd></div>
      </dl>
    </header>
    {preview ? <p className="retry-queue-notice">PR Preview 使用合成演示任务，仅用于检查界面与交互，不连接或修改生产调度器。</p> : null}
    {message ? <p className="retry-queue-notice" role="status">{message}</p> : null}
    {authRequired ? <p className="retry-queue-notice"><a href="/admin">返回管理员登录</a>。登录一次后，全部 Admin 工具共用同一 Access 会话。</p> : null}
    <div className={`retry-bulk-bar ${selected.size ? "is-active" : ""}`}>
      <label className="retry-checkbox-target"><input type="checkbox" aria-label="选择全部可调整任务" checked={eligible.length > 0 && selected.size === eligible.length} onChange={event => setSelected(event.target.checked ? new Set(eligible.map(job => job.job_id)) : new Set())} /><span>选择全部可调整任务</span></label>
      <strong>{selected.size ? `已选 ${selected.size} 个` : "选择任务后可批量调整"}</strong>
      {selected.size ? <div className="retry-bulk-action">
        <label>批量计划<select value={bulkMode} onChange={event => setBulkMode(event.target.value as RetryMode)}>{actions.filter(action => action.mode !== "KEEP_ORIGINAL").map(action => <option key={action.mode} value={action.mode}>{action.label}</option>)}</select></label>
        <button type="button" disabled={!selected.size || loading} onClick={() => choose(bulkMode, [...selected])}>调整选中任务</button>
      </div> : null}
    </div>
    {loading && !jobs.length ? <p className="retry-queue-empty">正在读取权威调度状态…</p> : null}
    {!loading && !jobs.length ? <p className="retry-queue-empty">当前没有排队、退避或正在执行的重试任务。</p> : null}
    <div className="retry-job-list">{jobs.map(job => {
      const mutable = job.state === "QUEUED" || job.state === "BACKING_OFF";
      const overridden = Boolean(job.override_mode);
      const latest = latestRequests.get(job.job_id);
      const command = latest ? operatorRetryCommandPresentation(latest) : null;
      const expanded = expandedJobId === job.job_id;
      return <article className={`retry-job-row ${expanded ? "is-expanded" : ""}`} key={job.job_id}>
        <label className="retry-checkbox-target retry-job-select"><input aria-label={`选择 ${job.title}`} type="checkbox" disabled={!mutable} checked={selected.has(job.job_id)} onChange={event => setSelected(current => { const next = new Set(current); if (event.target.checked) next.add(job.job_id); else next.delete(job.job_id); return next; })} /><span className="sr-only">选择 {job.title}</span></label>
        <div className="retry-job-identity">
          <p className="retry-job-kicker"><b>{taskLabels[job.task_type] ?? job.task_type}</b><span>{stateLabel(job.state)}</span></p>
          <h3>{job.title}</h3>
          <p className="retry-failure-summary"><b>{job.last_error ?? "暂无失败代码"}</b><span>失败 {compactTime(job.last_failure_at)}</span><span>第 {job.attempt_count} 次</span></p>
        </div>
        <p className="retry-job-schedule"><span>当前计划</span><b>{compactTime(job.available_at)}</b><em>{overridden ? "人工调整" : "自动计划"}</em>{overridden ? <small>原自动计划 {compactTime(job.original_available_at)}</small> : null}</p>
        {latest && command ? <p className={`retry-command-state is-${command.tone}`} title={command.label}><span>最近操作</span><b>{command.shortLabel}</b><time>{compactTime(latest.completed_at ?? latest.requested_at)}</time></p> : <p className="retry-command-state is-empty"><span>最近操作</span><b>暂无命令</b></p>}
        <div className="retry-job-control">
          <button type="button" aria-expanded={expanded} aria-controls={`retry-plan-${job.job_id}`} disabled={!mutable || loading} onClick={() => setExpandedJobId(expanded ? null : job.job_id)}>{expanded ? "收起计划" : "调整计划"}</button>
        </div>
        {expanded ? <div className="retry-job-plan" id={`retry-plan-${job.job_id}`}><header><div><b>选择新的领取时间</b><span>只改变可领取时间，不直接调用 provider。</span></div><code title={job.job_id}>任务 {job.job_id.slice(0, 12)}…</code></header><div>{actions.filter(action => action.mode !== "KEEP_ORIGINAL" || overridden).map(action => <button key={action.mode} type="button" disabled={!mutable || loading} onClick={() => choose(action.mode, [job.job_id])}><b>{action.label}</b><span>{action.help}</span></button>)}</div>{latest && command ? <p>最近命令：<b>{command.label}</b> · {localTime(latest.completed_at ?? latest.requested_at)}</p> : null}</div> : null}
      </article>;
    })}</div>
    {pending ? <div className="retry-confirmation" role="dialog" aria-modal="false" aria-label="确认重试调度调整">
      <div><b>确认调整 {pending.ids.length} 个任务</b><p>{actions.find(action => action.mode === pending.mode)?.help} 命令先由云端接受，再由 Windows scheduler 应用；最终执行仍是独立阶段。</p></div>
      {pending.mode === "CUSTOM_TIME" ? <label>UTC+8 日期时间<input type="datetime-local" value={customTime} onChange={event => setCustomTime(event.target.value)} /></label> : null}
      <label>调整原因<input value={reason} maxLength={500} onChange={event => setReason(event.target.value)} placeholder="例如：修复已部署，重新验证失败阶段" /></label>
      <div><button type="button" onClick={() => setPending(null)}>取消</button><button type="button" disabled={loading || preview} onClick={() => void submit()}>{preview ? "Preview 不提交" : "确认提交"}</button></div>
    </div> : null}
  </section>;
}
