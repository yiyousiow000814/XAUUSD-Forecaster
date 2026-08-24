"use client";

import { useMemo, useState } from "react";
import {
  architectureCommitSha,
  architectureGithubHref,
  architectureRelations,
  bundledArchitectureManifest,
  searchArchitectureNodes,
  type ArchitectureNode,
} from "../_lib/architecture-explorer";

const DIMENSIONS = [
  ["ownership", "所有权"], ["boundary", "执行边界"], ["critical_path", "关键路径"],
  ["bounded_work", "有界工作"], ["incremental", "增量机制"], ["failure_isolation", "故障隔离"],
] as const;
const STATES = ["ALL", "CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"] as const;

function LinkList({ title, paths, node, sha, repository }: {
  title: string; paths: string[]; node: ArchitectureNode; sha: string | null; repository: string;
}) {
  return <section className="architecture-link-list">
    <h3>{title}</h3>
    {paths.length ? <ul>{paths.map(path => {
      const href = architectureGithubHref({ repository }, path, sha);
      return <li key={`${node.id}:${path}`}>{href
        ? <a href={href} rel="noreferrer" target="_blank">{path}<span aria-hidden="true"> ↗</span></a>
        : <span>{path} <small>SHA unavailable</small></span>}</li>;
    })}</ul> : <p>无</p>}
  </section>;
}

export default function ArchitectureExplorerView() {
  const manifest = bundledArchitectureManifest();
  const [viewId, setViewId] = useState("system-overview");
  const [query, setQuery] = useState("");
  const [state, setState] = useState<(typeof STATES)[number]>("ALL");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const view = manifest?.views.find(item => item.id === viewId) ?? manifest?.views[0];
  const matches = useMemo(() => manifest ? searchArchitectureNodes(manifest, query, state) : [], [manifest, query, state]);
  const visible = useMemo(() => {
    if (!view) return [];
    const matchIds = new Set(matches.map(node => node.id));
    const scoped = query.trim() ? matches : manifest!.nodes.filter(node => view.node_ids.includes(node.id));
    return scoped.filter(node => matchIds.has(node.id));
  }, [manifest, matches, query, view]);
  const selected = visible.find(node => node.id === selectedId)
    ?? visible[0] ?? manifest?.nodes.find(node => node.id === selectedId) ?? manifest?.nodes[0];
  const relations = manifest && selected ? architectureRelations(manifest, selected.id) : null;
  const nodeById = (id: string) => manifest?.nodes.find(node => node.id === id);
  const sha = architectureCommitSha();

  if (!manifest || !view || !selected || !relations) {
    return <main className="architecture-main architecture-unavailable">
      <p>ARCHITECTURE MANIFEST</p><h1>系统架构暂不可用</h1>
      <span>构建中的架构清单无效；页面已关闭显示，不会回退到运行时请求。</span>
    </main>;
  }

  const selectView = (next: string) => {
    setViewId(next);
    setSelectedId(manifest.views.find(item => item.id === next)?.node_ids[0] ?? null);
  };

  return <main className="architecture-main">
    <header className="architecture-hero">
      <div><p>PRIVATE · BUILD {sha?.slice(0, 8) ?? "UNVERIFIED"}</p><h1>系统架构</h1></div>
      <p>从所有权、边界与故障域出发，定位“我想改什么”应该落在哪个真实 owner。</p>
    </header>
    <nav className="architecture-breadcrumbs" aria-label="架构路径">
      <button type="button" onClick={() => selectView("system-overview")}>系统架构</button>
      <span aria-hidden="true">/</span><button type="button" onClick={() => selectView(view.id)}>{view.label}</button>
      <span aria-hidden="true">/</span><strong>{selected.short_label}</strong>
    </nav>
    <section className="architecture-mobile-controls" aria-label="移动端架构控制">
      <label><span>视图</span><select aria-label="架构视图" value={view.id} onChange={event => selectView(event.currentTarget.value)}>
        {manifest.views.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}
      </select></label>
    </section>
    <section className="architecture-workbench">
      <aside className="architecture-rail">
        <p className="architecture-kicker">11 VIEWS</p>
        <nav aria-label="架构视图">{manifest.views.map(item => <button
          aria-selected={item.id === view.id} className={item.id === view.id ? "is-active" : ""}
          key={item.id} onClick={() => selectView(item.id)} role="option" type="button"
        ><span>{item.label}</span><small>{item.node_ids.length}</small></button>)}</nav>
      </aside>
      <section className="architecture-canvas">
        <header>
          <div><p className="architecture-kicker">{view.id.replaceAll("-", " ")}</p><h2>{view.label}</h2><span>{view.summary}</span></div>
          <button type="button" onClick={() => selectView(view.drill_down)}>深入 {manifest.views.find(item => item.id === view.drill_down)?.label} →</button>
        </header>
        <div className="architecture-search-row">
          <label><span>搜索 owner / 文件 / 测试 / 标签 / 改动目标</span><input aria-label="搜索架构"
            onChange={event => setQuery(event.currentTarget.value)} placeholder="例如：change release" type="search" value={query}
          /></label>
          <label><span>运行状态</span><select aria-label="运行状态" value={state} onChange={event => setState(event.currentTarget.value as typeof state)}>
            {STATES.map(item => <option key={item} value={item}>{item}</option>)}
          </select></label>
        </div>
        <div className="architecture-node-grid" role="listbox" aria-label={`${view.label} 组件`}>
          {visible.map((node, index) => <button
            aria-selected={node.id === selected.id} className={`architecture-node is-${node.runtime_state.toLowerCase()}`}
            key={node.id} onClick={() => setSelectedId(node.id)} role="option" type="button"
          >
            <span className="architecture-node-index">{String(index + 1).padStart(2, "0")}</span>
            <span className="architecture-node-badges"><b>{node.kind}</b><b>{node.runtime_state}</b><b>{node.implementation_state}</b></span>
            <strong>{node.label}</strong><small>{node.owner}</small>
            <i aria-hidden="true">{node.outputs.length ? "→" : "•"}</i>
          </button>)}
          {!visible.length ? <p className="architecture-empty">没有匹配这个 owner、路径、测试或标签的组件。</p> : null}
        </div>
        <section className="architecture-text-map" aria-label="关系文本等价视图">
          <div><h3>上游</h3><p>{relations.upstream.map(id => nodeById(id)?.short_label).filter(Boolean).join(" · ") || "无"}</p></div>
          <div><h3>下游</h3><p>{relations.downstream.map(id => nodeById(id)?.short_label).filter(Boolean).join(" · ") || "无"}</p></div>
          <div><h3>该故障不影响</h3><p>{relations.unaffected.slice(0, 8).map(id => nodeById(id)?.short_label).filter(Boolean).join(" · ") || "无"}</p></div>
        </section>
      </section>
      <aside className="architecture-detail" aria-live="polite">
        <header><p>{selected.kind} / {selected.runtime_state}</p><h2>{selected.label}</h2><span>{selected.summary}</span></header>
        <dl>{DIMENSIONS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{selected.architecture[key]}</dd></div>)}</dl>
        <LinkList title="CODE" paths={selected.code_paths} node={selected} sha={sha} repository={manifest.repository} />
        <LinkList title="TEST" paths={selected.test_paths} node={selected} sha={sha} repository={manifest.repository} />
        <LinkList title="DOC" paths={selected.document_paths} node={selected} sha={sha} repository={manifest.repository} />
      </aside>
    </section>
  </main>;
}
