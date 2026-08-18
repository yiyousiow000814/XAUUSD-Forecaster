"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type RetryMode = "KEEP_ORIGINAL" | "IMMEDIATE" | "DELAY_15_MIN" | "DELAY_1_HOUR" | "IDLE_CAPACITY" | "CUSTOM_TIME";
type RetryJob = {
  job_id: string; task_type: string; title: string; state: string; priority: string;
  available_at: string; attempt_count: number; last_error: string | null;
  last_failure_at: string | null; lease_expires_at: string | null;
  override_mode: RetryMode | null; original_available_at: string;
};

const actions: Array<{ mode: RetryMode; label: string }> = [
  { mode: "IMMEDIATE", label: "立即重试" },
  { mode: "DELAY_15_MIN", label: "15 分钟后" },
  { mode: "DELAY_1_HOUR", label: "1 小时后" },
  { mode: "IDLE_CAPACITY", label: "系统空闲时" },
  { mode: "CUSTOM_TIME", label: "指定时间" },
  { mode: "KEEP_ORIGINAL", label: "保留原计划" },
];
const taskLabels: Record<string, string> = {
  ACTIVE_ANNOTATION: "新闻语义分析",
  ACTIVE_IMPACT: "影响评估",
  TITLE_TRANSLATION: "标题翻译",
};
const previewJobs: RetryJob[] = [{
  job_id: "a".repeat(64), task_type: "ACTIVE_IMPACT", title: "黄金重新成为货币抵押品的市场讨论",
  state: "BACKING_OFF", priority: "NORMAL", available_at: "2026-08-19T06:47:00.000Z",
  attempt_count: 3, last_error: "ConnectionResetError", last_failure_at: "2026-08-19T00:46:00.000Z",
  lease_expires_at: null, override_mode: null, original_available_at: "2026-08-19T06:47:00.000Z",
}, {
  job_id: "b".repeat(64), task_type: "ACTIVE_ANNOTATION", title: "美联储官员就通胀路径发表最新讲话",
  state: "BACKING_OFF", priority: "FAST", available_at: "2026-08-19T04:05:00.000Z",
  attempt_count: 2, last_error: "MODEL_OUTPUT_CONTRACT_FAILED", last_failure_at: "2026-08-19T00:51:00.000Z",
  lease_expires_at: null, override_mode: "DELAY_1_HOUR", original_available_at: "2026-08-19T06:58:00.000Z",
}];

const localTime = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Kuala_Lumpur" })
  : "—";
const localInput = () => {
  const local = new Date(Date.now() + 8 * 60 * 60_000 + 15 * 60_000);
  return local.toISOString().slice(0, 16);
};
const utcFromKualaLumpurInput = (value: string) => {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  return new Date(`${value}:00+08:00`).toISOString();
};

export default function RetryQueue() {
  const [jobs, setJobs] = useState<RetryJob[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
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
        throw new Error("需要通过现有操作员登录后才能查看和调整重试任务。");
      }
      if (!response.ok) throw new Error("重试任务暂时无法读取。");
      const payload = await response.json() as { items?: RetryJob[]; preview?: boolean };
      setJobs(payload.preview ? previewJobs : payload.items ?? []);
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
      setMessage(`已提交 ${payload.accepted ?? 0} 个调度调整${rejected ? `，${rejected} 个任务因当前状态未接受` : ""}。同步进程应用后会刷新。`);
      setPending(null); setReason(""); setSelected(new Set());
      await load(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交失败");
    } finally { setLoading(false); }
  };

  return <section className="retry-queue" aria-label="重试任务">
    <header>
      <div><p className="eyebrow">OPERATOR RETRY SCHEDULING</p><h2>重试任务</h2></div>
      <p>只调整当前可领取时间；自动退避与失败证据保持不变。</p>
    </header>
    {preview ? <p className="retry-queue-notice">PR Preview 为只读，使用空的演示队列，不连接生产调度器。</p> : null}
    {message ? <p className="retry-queue-notice" role="status">{message}</p> : null}
    {authRequired ? <p className="retry-queue-notice"><a href="/assistant">登录现有操作员入口</a>，然后返回系统页面。</p> : null}
    <div className="retry-bulk-bar">
      <label><input type="checkbox" checked={eligible.length > 0 && selected.size === eligible.length} onChange={event => setSelected(event.target.checked ? new Set(eligible.map(job => job.job_id)) : new Set())} />选择全部可调整任务</label>
      <strong>已选 {selected.size} 个</strong>
      <div>{actions.filter(action => action.mode !== "KEEP_ORIGINAL").map(action => <button key={action.mode} type="button" disabled={!selected.size || loading} onClick={() => choose(action.mode, [...selected])}>{action.label}</button>)}</div>
    </div>
    {loading && !jobs.length ? <p className="retry-queue-empty">正在读取权威调度状态…</p> : null}
    {!loading && !jobs.length ? <p className="retry-queue-empty">当前没有排队、退避或正在执行的重试任务。</p> : null}
    <div className="retry-job-list">{jobs.map(job => {
      const mutable = job.state === "QUEUED" || job.state === "BACKING_OFF";
      const overridden = Boolean(job.override_mode);
      return <article className="retry-job-card" key={job.job_id}>
        <div className="retry-job-select"><input aria-label={`选择 ${job.title}`} type="checkbox" disabled={!mutable} checked={selected.has(job.job_id)} onChange={event => setSelected(current => { const next = new Set(current); if (event.target.checked) next.add(job.job_id); else next.delete(job.job_id); return next; })} /></div>
        <div className="retry-job-main">
          <p><b>{taskLabels[job.task_type] ?? job.task_type}</b><span>{job.state === "LEASED" ? "正在执行，不能调整" : job.state === "BACKING_OFF" ? "自动重试中" : "等待领取"}</span></p>
          <h3>{job.title}</h3>
          <dl>
            <div><dt>上次失败</dt><dd>{job.last_error ?? "—"}</dd></div>
            <div><dt>失败时间</dt><dd>{localTime(job.last_failure_at)}</dd></div>
            <div><dt>当前计划</dt><dd>{localTime(job.available_at)}</dd></div>
            <div><dt>尝试次数</dt><dd>{job.attempt_count}</dd></div>
            <div><dt>来源</dt><dd>{overridden ? "人工调整" : "自动退避"}</dd></div>
            {overridden ? <div><dt>原计划</dt><dd>{localTime(job.original_available_at)}</dd></div> : null}
          </dl>
          <code title={job.job_id}>{job.job_id.slice(0, 12)}…</code>
        </div>
        <div className="retry-job-actions">{actions.map(action => <button key={action.mode} type="button" disabled={!mutable || loading} onClick={() => choose(action.mode, [job.job_id])}>{action.label}</button>)}</div>
      </article>;
    })}</div>
    {pending ? <div className="retry-confirmation" role="dialog" aria-modal="false" aria-label="确认重试调度调整">
      <div><b>确认调整 {pending.ids.length} 个任务</b><p>{pending.mode === "IMMEDIATE" ? "任务会变成可领取状态，但仍受 provider quota 与 scheduler 控制。" : "只更新当前调度时间；任务仍由 scheduler 领取和执行。"}</p></div>
      {pending.mode === "CUSTOM_TIME" ? <label>UTC+8 日期时间<input type="datetime-local" value={customTime} onChange={event => setCustomTime(event.target.value)} /></label> : null}
      <label>调整原因<input value={reason} maxLength={500} onChange={event => setReason(event.target.value)} placeholder="例如：修复已部署，重新验证失败阶段" /></label>
      <div><button type="button" onClick={() => setPending(null)}>取消</button><button type="button" disabled={loading || preview} onClick={() => void submit()}>{preview ? "Preview 不提交" : "确认提交"}</button></div>
    </div> : null}
  </section>;
}
