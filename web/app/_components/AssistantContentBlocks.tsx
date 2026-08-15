import type {
  AssistantContentBlock,
  AssistantContentDocument,
  AssistantNewsCardBlock,
  AssistantTableCell,
} from "../api/_shared/assistant-content";

const timestamp = (value: string | null) => value
  ? new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(new Date(value))
  : "未记录";

const cellText = (value: AssistantTableCell) => {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
};

function MarkdownBlock({ block }: { block: Extract<AssistantContentBlock, { type: "markdown" }> }) {
  return <div className="assistant-content-markdown">
    {block.data.text.split(/\n{2,}/u).map((paragraph, index) => (
      <p key={`${block.id}:paragraph:${index}`}>{paragraph}</p>
    ))}
  </div>;
}

function NewsCard({ block }: { block: AssistantNewsCardBlock }) {
  const data = block.data;
  return <details className="assistant-news-card">
    <summary>
      <span><b>{data.source || "已收录来源"}</b><time>{timestamp(data.published_at)}</time></span>
      <strong>{data.headline}</strong>
      {data.summary ? <span className="assistant-news-summary">{data.summary}</span> : null}
      <small>
        <i>{data.category || "NEWS"}</i>
        {data.impact ? <i>影响已记录</i> : null}
        <em>展开凭据</em>
      </small>
    </summary>
    <div className="assistant-news-card-detail">
      <dl>
        <div><dt>发布时间</dt><dd>{timestamp(data.published_at)}</dd></div>
        <div><dt>系统收到</dt><dd>{timestamp(data.received_at)}</dd></div>
        <div><dt>证据 ID</dt><dd>{data.evidence_id}</dd></div>
        {data.impact ? <div><dt>影响</dt><dd>{data.impact}</dd></div> : null}
        {data.relevance ? <div><dt>相关性</dt><dd>{data.relevance}</dd></div> : null}
      </dl>
      {data.source_url ? <a href={data.source_url} rel="noopener noreferrer" target="_blank">
        查看来源 <span aria-hidden="true">↗</span>
      </a> : <span>来源链接未进入当前证据包</span>}
    </div>
  </details>;
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
    {block.data.detail ? <p>{block.data.detail}</p> : null}
  </article>;
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
