"use client";

import { useRef, useState } from "react";
import type {
  AssistantContentBlock,
  AssistantContentDocument,
  AssistantNewsCardBlock,
  AssistantTableCell,
} from "../api/_shared/assistant-content";
import { loadDashboardResource } from "../_lib/dashboard-resource";

const evidenceIdPattern = /^[a-f0-9]{64}$/;
const canonicalTimePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

const timeParts = (value: string | null) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Kuala_Lumpur",
  }).formatToParts(date);
  return Object.fromEntries(parts.map(part => [part.type, part.value]));
};

const timestamp = (value: string | null, compact = false) => {
  const parts = timeParts(value);
  if (!parts) return "未记录";
  const date = compact
    ? `${parts.month}月${parts.day}日`
    : `${parts.year}年${parts.month}月${parts.day}日`;
  return `${date} ${parts.hour}:${parts.minute}${compact ? "" : "（GMT+8）"}`;
};

const timestampCell = (value: string) => canonicalTimePattern.test(value)
  ? timestamp(value) : value;

const metricDetail = (value: string) => {
  const match = /^检索截止 (.+)$/u.exec(value);
  return match && canonicalTimePattern.test(match[1])
    ? `检索截止：${timestamp(match[1])}` : value;
};

const cellText = (value: AssistantTableCell) => {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "string" ? timestampCell(value) : String(value);
};

function MarkdownBlock({ block }: { block: Extract<AssistantContentBlock, { type: "markdown" }> }) {
  return <div className="assistant-content-markdown">
    {block.data.text.split(/\n{2,}/u).map((paragraph, index) => (
      <p key={`${block.id}:paragraph:${index}`}>{paragraph}</p>
    ))}
  </div>;
}

type NewsDetailResponse = { payload?: Record<string, unknown> };

const boundedDetailText = (value: unknown, maximum: number) => (
  typeof value === "string" ? value.trim().slice(0, maximum) : ""
);

function NewsCard({ block }: { block: AssistantNewsCardBlock }) {
  const data = block.data;
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const open = () => {
    dialog.current?.showModal();
    if (detailState !== "idle" || !evidenceIdPattern.test(data.evidence_id)) return;
    setDetailState("loading");
    void loadDashboardResource<NewsDetailResponse>(
      `/api/news-content?key=${encodeURIComponent(data.evidence_id)}`,
      { maxAgeMs: Number.POSITIVE_INFINITY },
    ).then(response => {
      setDetail(response.payload ?? null);
      setDetailState(response.payload ? "ready" : "error");
    }).catch(() => setDetailState("error"));
  };
  const summary = boundedDetailText(detail?.summary_zh, 4_000) || data.summary;
  const impact = boundedDetailText(detail?.impact_reason_zh, 2_000) || data.impact;
  return <>
    <article className="assistant-news-card">
      <button aria-haspopup="dialog" className="assistant-news-card-trigger" onClick={open} ref={trigger} type="button">
        <span><b>{data.source || "已收录来源"}</b><time>{timestamp(data.published_at, true)}</time></span>
        <strong>{data.headline}</strong>
        {data.summary ? <span className="assistant-news-summary">{data.summary}</span> : null}
        <small>
          <i>{data.category || "NEWS"}</i>
          {data.impact ? <i>影响已记录</i> : null}
          <em>查看摘要与判断 <span aria-hidden="true">↗</span></em>
        </small>
      </button>
    </article>
    <dialog
      aria-labelledby={`${block.id}:title`}
      className="assistant-news-dialog"
      onClose={() => trigger.current?.focus()}
      ref={dialog}
    >
      <article>
        <header>
          <div><span>NEWS EVIDENCE</span><h2 id={`${block.id}:title`}>{data.headline}</h2></div>
          <button aria-label="关闭新闻证据详情" onClick={() => dialog.current?.close()} type="button">×</button>
        </header>
        <div className="assistant-news-dialog-body">
          <section className="assistant-news-insight is-summary">
            <span>GEMINI 中文摘要</span>
            <p>{summary || "这条证据尚未生成中文摘要。"}</p>
          </section>
          <section className="assistant-news-insight is-impact">
            <span>GEMMA 市场影响判断</span>
            <p>{impact || "这条证据尚未记录市场影响判断。"}</p>
          </section>
          {detailState === "loading" ? <p className="assistant-news-detail-state">正在读取完整新闻详情…</p> : null}
          {detailState === "error" ? <p className="assistant-news-detail-state">完整详情暂未到达；当前显示回答时冻结的证据内容。</p> : null}
          <dl>
            <div><dt>媒体发布时间</dt><dd>{timestamp(data.published_at)}</dd></div>
            <div><dt>系统收到时间</dt><dd>{timestamp(data.received_at)}</dd></div>
            <div><dt>证据分类</dt><dd>{data.category || "未分类"}</dd></div>
            <div><dt>证据 ID</dt><dd>{data.evidence_id}</dd></div>
          </dl>
        </div>
        <footer>
          {data.source_url ? <a href={data.source_url} rel="noopener noreferrer" target="_blank">
            阅读来源 <span aria-hidden="true">↗</span>
          </a> : <span>来源链接未进入当前证据包</span>}
        </footer>
      </article>
    </dialog>
  </>;
}

function TableBlock({ block }: { block: Extract<AssistantContentBlock, { type: "table" }> }) {
  return <figure className="assistant-content-table">
    {block.data.caption ? <figcaption>{block.data.caption}</figcaption> : null}
    {/* Horizontal evidence tables need an explicit keyboard scroll target. */}
    {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
    <div tabIndex={0} role="region" aria-label={block.data.caption ?? "Assistant 表格"}>
      <table>
        <thead><tr>{block.data.columns.map(column => (
          <th className={`is-${column.align}`} key={column.key} scope="col">{column.label}</th>
        ))}</tr></thead>
        <tbody>{block.data.rows.map((row, rowIndex) => <tr key={`${block.id}:row:${rowIndex}`}>
          {row.map((cell, cellIndex) => <td
            className={`is-${block.data.columns[cellIndex].align}`}
            key={`${block.id}:cell:${rowIndex}:${cellIndex}`}
          >{cellText(cell)}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </figure>;
}

function Block({ block }: { block: AssistantContentBlock }) {
  if (block.type === "markdown") return <MarkdownBlock block={block} />;
  if (block.type === "news_card") return <NewsCard block={block} />;
  if (block.type === "table") return <TableBlock block={block} />;
  if (block.type === "metric") return <article className="assistant-content-metric">
    <span>{block.data.label}</span>
    <strong>{block.data.value}{block.data.unit ? <small>{block.data.unit}</small> : null}</strong>
    {block.data.detail ? <p>{metricDetail(block.data.detail)}</p> : null}
  </article>;
  // Historical messages remain immutable; the obsolete generic boundary is hidden at render time.
  if (block.type === "callout" && block.data.tone === "BOUNDARY") return null;
  if (block.type === "callout") return <aside className={`assistant-content-callout is-${block.data.tone.toLowerCase()}`}>
    <strong>{block.data.title}</strong><p>{block.data.body}</p>
  </aside>;
  return null;
}

export default function AssistantContentBlocks({
  document,
}: {
  document: AssistantContentDocument;
}) {
  return <div className="assistant-content-blocks" data-content-protocol={document.protocol}>
    {document.blocks.map(block => <Block block={block} key={block.id} />)}
  </div>;
}
