"use client";

import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { SYSTEM_MAPS, systemMap, mapRanks, type MapNode, type MapSource, type SystemMap } from '../_lib/system-map';
import { architectureCommitSha } from '../_lib/architecture-explorer';
import styles from './SystemArchitectureView.module.css';

const SourceIndex = lazy(() => import('./ArchitectureExplorerView'));


function SourceLink({ source }: { source: MapSource }) {
  const sha = architectureCommitSha();
  if (!sha) return <code>{source.path}</code>;
  return <a href={`https://github.com/yiyousiow000814/XAUUSD-Forecaster/blob/${sha}/${source.path}`} target="_blank" rel="noreferrer">{source.path} ↗</a>;
}

function FlowDiagram({ view, selected, choose }: { view: SystemMap; selected: string | null; choose: (node: MapNode) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<Array<{ id: string; path: string; x: number; y: number; label: string }>>([]);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const measure = () => {
      const bounds = element.getBoundingClientRect();
      setLines(view.edges.flatMap(edge => {
        const from = element.querySelector<HTMLElement>(`[data-map-node="${edge.from}"]`)?.getBoundingClientRect();
        const to = element.querySelector<HTMLElement>(`[data-map-node="${edge.to}"]`)?.getBoundingClientRect();
        if (!from || !to) return [];
        const x1 = from.x + from.width / 2 - bounds.x, y1 = from.bottom - bounds.y;
        const x2 = to.x + to.width / 2 - bounds.x, y2 = to.top - bounds.y;
        const mid = (y1 + y2) / 2;
        return [{ id: `${edge.from}-${edge.to}`, path: `M${x1},${y1} V${mid} H${x2} V${y2 - 5}`, x: (x1 + x2) / 2, y: mid - 8, label: edge.label }];
      }));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [view]);
  return <div className={styles.diagram} ref={root} aria-label={`${view.title}流程图`}>
    <svg className={styles.connections} aria-hidden="true"><defs><marker id="system-map-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
      {lines.map(line => <g key={line.id}><path d={line.path} markerEnd="url(#system-map-arrow)" /><text x={line.x} y={line.y} textAnchor="middle">{line.label}</text></g>)}
    </svg>
    {mapRanks(view).map((row, index) => <div className={styles.row} key={index}>{row.map(node => <div className={styles.nodeGroup} key={node.id}>
      <button data-map-node={node.id} aria-pressed={selected === node.id} className={styles.node} onClick={() => choose(node)} type="button">
        <strong>{node.title}</strong><span>{node.detail}</span><small>{node.child ? '展开流程 →' : '查看依据 →'}</small>
      </button>
      <div className={styles.mobileEdges}>{view.edges.filter(edge => edge.from === node.id).map(edge => <button key={edge.to} type="button" onClick={() => choose(view.nodes.find(item => item.id === edge.to)!)}>↓ {edge.label} → {view.nodes.find(item => item.id === edge.to)?.title}</button>)}</div>
    </div>)}</div>)}
  </div>;
}

export default function SystemArchitectureView() {
  const [history, setHistory] = useState(['system']);
  const [selected, setSelected] = useState<string | null>(null);
  const [sourceIndex, setSourceIndex] = useState(false);
  const detail = useRef<HTMLElement>(null);
  const heading = useRef<HTMLElement>(null);
  const lastView = useRef('system');
  const view = systemMap(history[history.length - 1]);
  useEffect(() => {
    if (lastView.current !== view.id) {
      heading.current?.focus({ preventScroll: true });
      heading.current?.scrollIntoView({ block: 'start' });
    }
    lastView.current = view.id;
  }, [view.id]);
  const node = view.nodes.find(item => item.id === selected);
  const open = (target: MapNode) => {
    if (target.child) { setHistory(current => [...current, target.child!]); setSelected(null); }
    else setSelected(target.id);
  };
  useEffect(() => { if (selected) detail.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [selected]);
  if (sourceIndex) return <div className={styles.sourceIndex}><button type="button" onClick={() => setSourceIndex(false)}>← 返回系统流程图</button><Suspense fallback={<p>正在加载源码索引…</p>}><SourceIndex /></Suspense></div>;
  return <main className={styles.main}>
    <header ref={heading} tabIndex={-1} className={styles.header}><h1>系统架构</h1><p>从业务流程逐层查看实现</p><button type="button" onClick={() => setSourceIndex(true)}>源码索引</button></header>
    <nav className={styles.breadcrumbs} aria-label="架构层级">{history.map((id, index) => <span key={`${id}-${index}`}>{index > 0 ? <span aria-hidden="true">›</span> : null}<button type="button" aria-current={index === history.length - 1 ? 'page' : undefined} onClick={() => { setHistory(history.slice(0, index + 1)); setSelected(null); }}>{systemMap(id).title}</button></span>)}</nav>
    <section className={styles.flow} aria-label={view.title}><div className={styles.caption}><h2>{view.title}</h2><p>{view.summary}</p></div><FlowDiagram key={view.id} view={view} selected={selected} choose={open} /></section>
    {node ? <section className={styles.details} ref={detail} aria-label={`${node.title}源码依据`}><header><h2>{node.title}</h2><button type="button" onClick={() => setSelected(null)}>关闭详情</button></header><p>{node.detail}</p><SourceLink source={node.source} /><p className={styles.witness}>核对位置：<code>{node.source.witness}</code></p>
      <ul>{view.edges.filter(edge => edge.from === node.id || edge.to === node.id).map(edge => <li key={`${edge.from}-${edge.to}`}><strong>{view.nodes.find(item => item.id === edge.from)?.title} → {view.nodes.find(item => item.id === edge.to)?.title}</strong><p>{edge.label}</p><SourceLink source={edge.source} /><code>{edge.source.witness}</code></li>)}</ul>
    </section> : null}
    <footer className={styles.footer}>依据当前源码核对连接关系；实时运行情况请点击顶部状态。<details><summary>查看范围与依据</summary><p>此图覆盖行情、新闻、预测、训练及网页同步的主流程。每条连接保留实现位置；源码索引用于查看函数引用，不等同于运行轨迹。</p><ul>{SYSTEM_MAPS.filter(item => item.id !== 'system').map(item => <li key={item.id}><button type="button" onClick={() => { setHistory(['system', item.id]); setSelected(null); }}>{item.title}</button></li>)}</ul></details></footer>
  </main>;
}
