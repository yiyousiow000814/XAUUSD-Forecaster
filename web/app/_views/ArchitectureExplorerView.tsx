"use client";

import "@xyflow/react/dist/style.css";
import {
  Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, MiniMap, Position,
  ReactFlow, ReactFlowProvider, getSmoothStepPath, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  architectureCanvasHeight, architectureCommitSha, architectureFailureImpact, architectureFitOptions, architectureGithubHref, architectureRelations,
  bestViewForNode, buildArchitectureGraph, bundledArchitectureManifest, searchArchitectureNodes,
  type ArchitectureEdge, type ArchitectureFailureImpact, type ArchitectureManifest, type ArchitectureNode,
} from "../_lib/architecture-explorer";
import styles from "./ArchitectureExplorerView.module.css";

const DIMENSIONS = [
  ["ownership", "所有权 · Owner"], ["boundary", "执行边界 · Boundary"], ["critical_path", "关键路径 · Critical Path"],
  ["bounded_work", "有界工作 · Bounded Work"], ["incremental", "增量进度 · Incremental"], ["failure_isolation", "故障隔离 · Failure Isolation"],
] as const;
const STATES = ["ALL", "CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"] as const;
const KIND_SYMBOL: Record<string, string> = {
  PROCESS: "▶", THREAD: "≋", STORE: "▤", WORKER: "☁", CONTROL: "◆", EXTERNAL: "↗",
  COMPONENT: "□", REQUEST_HANDLER: "⇄", STATIC: "◇", SUBSYSTEM: "▣",
};

type FailureStatus = "AFFECTED" | "CONTINUES" | null;
type FlowNodeData = Record<string, unknown> & {
  node: ArchitectureNode; laneLabel: string; selected: boolean; dimmed: boolean; highlighted: boolean;
  direction: "LR" | "TB";
  failureStatus: FailureStatus; onSelect: (id: string) => void; onHover: (id: string | null) => void;
  onDrill: (id: string) => void; onNavigate: (id: string, direction: number) => void;
};
type FlowEdgeData = Record<string, unknown> & { edge: ArchitectureEdge; highlighted: boolean; dimmed: boolean; guided: boolean; showLabel: boolean };
type FlowLaneData = Record<string, unknown> & { label: string; direction: "LR" | "TB" };
type ArchitectureFlowNode = Node<FlowNodeData, "architecture">;
type ArchitectureLaneNode = Node<FlowLaneData, "lane">;
type ArchitectureCanvasNode = ArchitectureFlowNode | ArchitectureLaneNode;
type ArchitectureFlowEdge = Edge<FlowEdgeData, "architecture">;

function useMobileGraph() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const update = () => setMobile(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return mobile;
}

const ArchitectureGraphNode = memo(function ArchitectureGraphNode({ data }: NodeProps<ArchitectureFlowNode>) {
  const { node, laneLabel, selected, dimmed, highlighted, failureStatus, onSelect, onHover, onDrill, onNavigate } = data;
  const className = [styles.graphNode, styles[`kind${node.kind}`], selected ? styles.selected : "",
    highlighted ? styles.highlighted : "", dimmed ? styles.dimmed : "", failureStatus ? styles[`failure${failureStatus}`] : ""].filter(Boolean).join(" ");
  return <article className={className} data-failure-status={failureStatus ?? undefined} data-node-id={node.id}>
    <Handle className={styles.handle} isConnectable={false} position={data.direction === "TB" ? Position.Top : Position.Left} type="target" />
    <button aria-label={`${node.label}, ${node.kind}, ${node.runtime_state}`} aria-pressed={selected} title={node.summary}
      onClick={() => onSelect(node.id)} onDoubleClick={() => onDrill(node.id)}
      onFocus={() => onHover(node.id)} onBlur={() => onHover(null)}
      onMouseEnter={() => onHover(node.id)} onMouseLeave={() => onHover(null)}
      onKeyDown={event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node.id); }
        if (["ArrowRight", "ArrowDown"].includes(event.key)) { event.preventDefault(); onNavigate(node.id, 1); }
        if (["ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); onNavigate(node.id, -1); }
      }} type="button">
      <span className={styles.nodeTopline}><b aria-hidden="true">{KIND_SYMBOL[node.kind] ?? "□"}</b><span>{node.kind}</span><i>{node.runtime_state}</i></span>
      <strong>{node.short_label}</strong>
      <small>{laneLabel}</small>
      {failureStatus ? <em>{failureStatus}</em> : null}
    </button>
    <Handle className={styles.handle} isConnectable={false} position={data.direction === "TB" ? Position.Bottom : Position.Right} type="source" />
  </article>;
});

const ArchitectureGraphEdge = memo(function ArchitectureGraphEdge(props: EdgeProps<ArchitectureFlowEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath(props);
  const data = props.data!;
  const className = [styles.graphEdge, styles[`edge${data.edge.criticality}`], data.highlighted ? styles.edgeHighlighted : "",
    data.dimmed ? styles.edgeDimmed : "", data.guided ? styles.edgeGuided : ""].filter(Boolean).join(" ");
  return <>
    <BaseEdge id={props.id} markerEnd={props.markerEnd} path={path} className={className} />
    {data.showLabel ? <EdgeLabelRenderer><span className={`${styles.edgeLabel} ${data.dimmed ? styles.edgeLabelDimmed : ""}`}
      style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }} title={data.edge.description}>
      {data.edge.label}<small>{data.edge.kind}</small>
    </span></EdgeLabelRenderer> : null}
  </>;
});

const ArchitectureLaneRegion = memo(function ArchitectureLaneRegion({ data }: NodeProps<ArchitectureLaneNode>) {
  return <section aria-hidden="true" className={styles.laneRegion} data-lane-direction={data.direction}>
    <span>{data.label}</span>
  </section>;
});

const nodeTypes = { architecture: ArchitectureGraphNode, lane: ArchitectureLaneRegion };
const edgeTypes = { architecture: ArchitectureGraphEdge };

function SourceLinks({ manifest, node, sha, kind }: { manifest: ArchitectureManifest; node: ArchitectureNode; sha: string | null; kind: "code" | "test" | "docs" }) {
  const paths = kind === "code" ? node.code_paths : kind === "test" ? node.test_paths : node.document_paths;
  return paths.length ? <ul className={styles.sourceLinks}>{paths.map(path => {
    const href = architectureGithubHref(manifest, path, sha);
    return <li key={path}>{href ? <a href={href} rel="noreferrer" target="_blank">{path}<span aria-hidden="true"> ↗</span></a> : <span>{path} · SHA unavailable</span>}</li>;
  })}</ul> : <p className={styles.emptyCopy}>None</p>;
}

function Inspector({ manifest, node, impact, sha, onClose, onDrill }: {
  manifest: ArchitectureManifest; node: ArchitectureNode; impact: ArchitectureFailureImpact | null; sha: string | null;
  onClose: () => void; onDrill: (id: string) => void;
}) {
  const [tab, setTab] = useState<"code" | "test" | "docs">("code");
  const relations = architectureRelations(manifest, node.id);
  const names = (ids: string[]) => ids.map(id => manifest.nodes.find(item => item.id === id)?.short_label).filter(Boolean).join(" · ") || "无 · None";
  const unavailableImpact = "该节点没有显式 failure impact contract；不会推断其他节点安全。";
  return <aside aria-labelledby="architecture-inspector-title" className={styles.inspector}>
    <header><div><span>{node.kind} · {node.runtime_state}</span><h2 id="architecture-inspector-title">{node.label}</h2></div>
      <button aria-label="关闭详情" onClick={onClose} type="button">×</button></header>
    <div className={styles.inspectorBody}>
      <dl className={styles.beginnerDetails}>
        <div><dt>它是什么？</dt><dd>{node.summary}</dd></div>
        <div><dt>为什么需要它？</dt><dd>{node.purpose}</dd></div>
        <div><dt>谁负责它？</dt><dd className={styles.ownerAnswer}><strong>{node.owner}</strong><span>{node.architecture.ownership}</span></dd></div>
        <div><dt>输入来自哪里？</dt><dd>{names(relations.directUpstream)}</dd></div>
        <div><dt>输出到哪里？</dt><dd>{names(relations.directDownstream)}</dd></div>
        <div><dt>它坏了会停止什么？</dt><dd>{impact?.affected.map(item => item.message).join(" ") ?? unavailableImpact}</dd></div>
        <div><dt>什么仍会继续？</dt><dd>{impact?.continues.map(item => item.message).join(" ") ?? unavailableImpact}</dd></div>
      </dl>
      {node.subsystem_view ? <button className={styles.drillButton} onClick={() => onDrill(node.id)} type="button">打开子系统 <span aria-hidden="true">· Open subsystem →</span></button> : null}
      <section className={styles.dimensions} aria-label="Architecture dimensions">{DIMENSIONS.map(([key, label]) => <details key={key}>
        <summary>{label}</summary><p>{node.architecture[key]}</p>
      </details>)}</section>
      <section className={styles.sourcePanel}><nav aria-label="Source evidence">
        {(["code", "test", "docs"] as const).map(item => <button aria-selected={tab === item} key={item} onClick={() => setTab(item)} role="tab" type="button">{item.toUpperCase()}</button>)}
      </nav><SourceLinks kind={tab} manifest={manifest} node={node} sha={sha} /></section>
    </div>
  </aside>;
}

function ExplorerGraph({ manifest }: { manifest: ArchitectureManifest }) {
  const mobile = useMobileGraph();
  const flow = useReactFlow();
  const [viewId, setViewId] = useState("system-overview");
  const [viewHistory, setViewHistory] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [state, setState] = useState<(typeof STATES)[number]>("ALL");
  const [scenarioId, setScenarioId] = useState("");
  const [scenarioStep, setScenarioStep] = useState(0);
  const [failureMode, setFailureMode] = useState(false);
  const graph = useMemo(() => buildArchitectureGraph(manifest, viewId, mobile ? "TB" : undefined), [manifest, mobile, viewId]);
  const canvasHeight = architectureCanvasHeight(graph.view.node_ids.length, mobile);
  const fitGraph = useCallback((duration = 280) => {
    flow.fitView({ ...architectureFitOptions(graph.view.node_ids.length, mobile), duration });
  }, [flow, graph.view.node_ids.length, mobile]);
  const focusGraphNode = useCallback((item: { position: { x: number; y: number }; width: number; height: number }, duration: number) => {
    const zoom = mobile ? .9 : 1;
    if (mobile) {
      const canvasWidth = Math.max(0, window.innerWidth - 26);
      flow.setViewport({
        x: (canvasWidth - item.width * zoom) / 2 - item.position.x * zoom,
        y: 64 - item.position.y * zoom,
        zoom,
      }, { duration });
      return;
    }
    flow.setCenter(item.position.x + item.width / 2, item.position.y + item.height / 2, { zoom, duration });
  }, [flow, mobile]);
  const selected = selectedId ? manifest.nodes.find(item => item.id === selectedId) ?? null : null;
  const scenario = manifest.scenarios.find(item => item.id === scenarioId) ?? null;
  const activeImpact = failureMode ? architectureFailureImpact(manifest, selectedId) : null;
  const searchMatches = useMemo(() => searchArchitectureNodes(manifest, query, state), [manifest, query, state]);
  const focusId = hoveredId ?? selectedId;
  const relations = focusId ? architectureRelations(manifest, focusId, viewId) : null;
  const scenarioNodes = new Set(scenario ? scenario.node_ids.slice(0, scenarioStep + 1) : []);
  const scenarioEdges = new Set(scenario ? scenario.edge_ids.slice(0, scenarioStep) : []);
  const stateNodes = state === "ALL" ? [] : graph.nodes.filter(item => item.data.node.runtime_state === state).map(item => item.id);
  const highlightedNodes = new Set(focusId ? [focusId, ...(hoveredId ? relations?.directUpstream ?? [] : relations?.upstream ?? []), ...(hoveredId ? relations?.directDownstream ?? [] : relations?.downstream ?? [])] : scenario ? scenarioNodes : stateNodes);
  const directEdges = focusId ? graph.edges.filter(edge => edge.from === focusId || edge.to === focusId).map(edge => edge.id) : [];
  const stateEdges = state === "ALL" ? [] : graph.edges.filter(edge => stateNodes.includes(edge.from) && stateNodes.includes(edge.to)).map(edge => edge.id);
  const highlightedEdges = new Set(focusId ? hoveredId ? directEdges : relations?.connectedEdges ?? [] : scenario ? scenarioEdges : stateEdges);
  const affected = new Set(activeImpact?.affected.map(item => item.node_id) ?? []);
  const continues = new Set(activeImpact?.continues.map(item => item.node_id) ?? []);
  const hasFocus = Boolean(focusId || scenario || state !== "ALL");
  const sha = architectureCommitSha();

  const changeView = useCallback((next: string, remember = false) => {
    if (remember && next !== viewId) setViewHistory(items => [...items, viewId]);
    setViewId(next); setSelectedId(null); setHoveredId(null); setHoveredEdgeId(null); setFailureMode(false);
    const nodeCount = manifest.views.find(view => view.id === next)?.node_ids.length ?? 0;
    window.setTimeout(() => flow.fitView({ ...architectureFitOptions(nodeCount, mobile), duration: 260 }), 180);
  }, [flow, manifest.views, mobile, viewId]);
  const selectNode = useCallback((id: string) => {
    setSelectedId(id); setFailureMode(false);
    if (mobile) return;
    const item = graph.nodes.find(node => node.id === id);
    if (item) window.setTimeout(() => flow.setCenter(
      item.position.x + item.width / 2, item.position.y + item.height / 2,
      { zoom: flow.getZoom(), duration: 220 },
    ), 230);
  }, [flow, graph.nodes, mobile]);
  const closeInspector = useCallback(() => {
    setSelectedId(null); setFailureMode(false);
    window.setTimeout(() => fitGraph(220), 230);
  }, [fitGraph]);
  const drill = useCallback((id: string) => {
    const item = manifest.nodes.find(node => node.id === id);
    if (item?.subsystem_view && item.subsystem_view !== viewId) changeView(item.subsystem_view, true);
  }, [changeView, manifest.nodes, viewId]);
  const navigateNode = useCallback((id: string, direction: number) => {
    const index = graph.view.node_ids.indexOf(id); const next = graph.view.node_ids[(index + direction + graph.view.node_ids.length) % graph.view.node_ids.length];
    selectNode(next); window.requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-node-id="${next}"] button`)?.focus());
  }, [graph.view.node_ids, selectNode]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") closeInspector(); };
    window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close);
  }, [closeInspector]);
  useEffect(() => {
    const timeout = window.setTimeout(() => fitGraph(260), 300);
    return () => window.clearTimeout(timeout);
  }, [fitGraph, graph.direction, graph.view.id]);

  const flowNodes: ArchitectureFlowNode[] = graph.nodes.map(item => ({
    ...item, type: "architecture", draggable: false, selectable: true,
    data: { ...item.data, selected: item.id === selectedId, highlighted: highlightedNodes.has(item.id), dimmed: hasFocus && !highlightedNodes.has(item.id),
      direction: graph.direction, failureStatus: affected.has(item.id) ? "AFFECTED" : continues.has(item.id) ? "CONTINUES" : null,
      onSelect: selectNode, onHover: setHoveredId, onDrill: drill, onNavigate: navigateNode },
  }));
  const laneNodes: ArchitectureLaneNode[] = graph.laneBoxes.map(item => ({
    ...item, type: "lane", draggable: false, selectable: false, focusable: false, connectable: false,
    zIndex: -1, data: item.data,
  }));
  const flowEdges: ArchitectureFlowEdge[] = graph.edges.map(item => ({
    id: item.id, source: item.source, target: item.target, type: "architecture", label: item.label,
    markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: item.criticality === "CRITICAL" ? "#137d74" : "#607278" },
    animated: scenarioEdges.has(item.id), data: {
      edge: item,
      highlighted: highlightedEdges.has(item.id),
      dimmed: hasFocus && !highlightedEdges.has(item.id),
      guided: scenarioEdges.has(item.id),
      showLabel: item.criticality === "CRITICAL"
        || (item.criticality === "CONTROL_PLANE" && viewId === "runtime-release")
        || graph.edges.length <= 4
        || highlightedEdges.has(item.id)
        || scenarioEdges.has(item.id)
        || hoveredEdgeId === item.id,
    },
  }));

  const runScenario = (id: string) => {
    const next = manifest.scenarios.find(item => item.id === id); setScenarioId(id); setScenarioStep(0); setSelectedId(null); setFailureMode(false);
    if (next) {
      changeView(next.view_id, next.view_id !== viewId);
      if (next.failure_node_id) { setSelectedId(next.failure_node_id); setFailureMode(true); }
      else {
        const target = buildArchitectureGraph(manifest, next.view_id, mobile ? "TB" : undefined).nodes.find(node => node.id === next.node_ids[0]);
        if (target) window.setTimeout(() => focusGraphNode(target, 300), 420);
      }
    }
  };
  const moveScenario = (direction: number) => {
    if (!scenario) return;
    const nextStep = Math.max(0, Math.min(scenario.steps.length - 1, scenarioStep + direction));
    setScenarioStep(nextStep);
    const target = graph.nodes.find(node => node.id === scenario.node_ids[nextStep]);
    if (target) window.setTimeout(() => focusGraphNode(target, 260), 80);
  };
  const selectSearch = (node: ArchitectureNode) => {
    const targetView = bestViewForNode(manifest, node, viewId); if (targetView !== viewId) setViewId(targetView);
    setSelectedId(node.id); setQuery("");
    const target = buildArchitectureGraph(manifest, targetView, mobile ? "TB" : undefined).nodes.find(item => item.id === node.id);
    if (target) window.setTimeout(() => focusGraphNode(target, 260), targetView === viewId ? 80 : 360);
  };
  const impactForSelected = architectureFailureImpact(manifest, selectedId);
  const announced = scenario ? `${scenario.label}: step ${scenarioStep + 1} of ${scenario.steps.length}. ${scenario.steps[scenarioStep]?.message}`
    : selected ? `${selected.label} selected. Upstream ${relations?.upstream.length ?? 0}; downstream ${relations?.downstream.length ?? 0}.` : "No architecture node selected.";

  return <main className={styles.main}>
    <header className={styles.header}>
      <div><span>PRIVATE · BUILD {sha?.slice(0, 8) ?? "UNVERIFIED"}</span><h1>系统架构</h1><p>{graph.view.summary}</p></div>
      <nav aria-label="Architecture breadcrumb" className={styles.breadcrumbs}>
        <button onClick={() => { setViewHistory([]); setScenarioId(""); changeView("system-overview"); }} type="button">System Overview</button>
        {viewHistory.length ? <><span aria-hidden="true">›</span><button onClick={() => { const previous = viewHistory.at(-1)!; setViewHistory(items => items.slice(0, -1)); changeView(previous); }} type="button">Back</button></> : null}
        {viewId !== "system-overview" ? <><span aria-hidden="true">›</span><strong>{graph.view.label}</strong></> : null}
        {selected ? <><span aria-hidden="true">›</span><em>{selected.short_label}</em></> : null}
      </nav>
    </header>
    <section className={styles.toolbar} aria-label="Architecture graph toolbar">
      <label className={styles.search}><span>Search owner, purpose, file, test, or change target</span><input aria-label="Search architecture" onChange={event => setQuery(event.currentTarget.value)} placeholder="training, release, dashboard, retry…" type="search" value={query} />
        {query ? <div className={styles.searchResults} role="listbox">{searchMatches.slice(0, 7).map(node => <button aria-selected="false" key={node.id} onClick={() => selectSearch(node)} role="option" type="button"><strong>{node.short_label}</strong><span>{node.owner}</span></button>)}{!searchMatches.length ? <p>No matching change target.</p> : null}</div> : null}
      </label>
      <label><span>View</span><select aria-label="Architecture view" onChange={event => { setScenarioId(""); changeView(event.currentTarget.value); }} value={viewId}>{manifest.views.map(view => <option key={view.id} value={view.id}>{view.label}</option>)}</select></label>
      <label><span>State</span><select aria-label="Runtime state" onChange={event => setState(event.currentTarget.value as typeof state)} value={state}>{STATES.map(item => <option key={item}>{item}</option>)}</select></label>
      <label><span>Scenario</span><select aria-label="Guided scenario" onChange={event => runScenario(event.currentTarget.value)} value={scenarioId}><option value="">Explore freely</option>{manifest.scenarios.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <button aria-describedby={selected && !impactForSelected ? "failure-contract-status" : undefined} aria-pressed={failureMode} className={styles.failureButton} disabled={!impactForSelected} onClick={() => setFailureMode(value => !value)} type="button">故障影响</button>
      <button className={styles.fitButton} onClick={() => fitGraph()} type="button">适配画布 · Fit</button>
    </section>
    {selected && !impactForSelected ? <p className={styles.failureAvailability} id="failure-contract-status">此节点没有显式 failure impact contract，故障按钮已禁用。</p> : null}
    {graph.view.relationship_note ? <aside className={styles.dependencyNotice} aria-label="Package dependency meaning">
      <strong>{graph.view.relationship_note}</strong>
      {graph.view.prohibited_directions?.length ? <details><summary>禁止的反向依赖 · Prohibited reverse directions</summary><ul>{graph.view.prohibited_directions.map(item => <li key={item}>{item}</li>)}</ul></details> : null}
    </aside> : null}
    {scenario ? <section className={styles.guide} aria-label="Guided architecture scenario"><div><b>{scenario.label}</b><span>{scenario.steps[scenarioStep]?.message}</span><small>{scenarioStep + 1} / {scenario.steps.length}</small></div>
      <button disabled={scenarioStep === 0} onClick={() => moveScenario(-1)} type="button">Previous</button>
      <button disabled={scenarioStep === scenario.steps.length - 1} onClick={() => moveScenario(1)} type="button">Next</button>
      <button aria-label="Close scenario" onClick={() => { setScenarioId(""); setScenarioStep(0); }} type="button">×</button></section> : null}
    <section className={`${styles.stage} ${selected ? styles.withInspector : ""}`} style={{ minHeight: canvasHeight }}>
      <div className={styles.canvas} data-graph-direction={graph.direction} data-testid="architecture-graph" style={{ height: canvasHeight }}>
        <ReactFlow<ArchitectureCanvasNode, ArchitectureFlowEdge> nodes={[...laneNodes, ...flowNodes]} edges={flowEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          elementsSelectable fitView minZoom={0.25} maxZoom={1.6} nodesConnectable={false} nodesDraggable={false}
          onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(edge.id)} onEdgeMouseLeave={() => setHoveredEdgeId(null)}
          panOnDrag zoomOnPinch zoomOnScroll proOptions={{ hideAttribution: true }}>
          <Background color="#b7c3c5" gap={22} size={1} />
          <Controls position="bottom-right" showInteractive={false} />
          {!mobile ? <MiniMap aria-label="Architecture minimap" pannable zoomable nodeColor={node => {
            const architectureNode = node.data.node as ArchitectureNode | undefined;
            if (!architectureNode) return "#d7e2e0";
            return architectureNode.runtime_state === "PAUSED" ? "#a88b55" : "#137d74";
          }} /> : null}
        </ReactFlow>
        <section className={styles.legend} aria-label="Graph legend"><span><i className={styles.criticalLine} /> Critical</span><span><i className={styles.backgroundLine} /> Background</span><span><i className={styles.optionalLine} /> Optional</span><span><i className={styles.controlLine} /> Control plane</span></section>
        <span className={styles.keyboardHint}>Tab nodes · Enter/Space select · Arrow keys navigate · Esc close</span>
      </div>
      {selected ? <Inspector impact={activeImpact} manifest={manifest} node={selected} onClose={closeInspector} onDrill={drill} sha={sha} /> : null}
    </section>
    {activeImpact ? <section className={styles.failureSummary} aria-label="Explicit failure impact"><h2>{activeImpact.label}</h2><div><h3>AFFECTED</h3>{activeImpact.affected.map(item => <p key={item.node_id}>{item.message}</p>)}</div><div><h3>CONTINUES</h3>{activeImpact.continues.map(item => <p key={item.node_id}>{item.message}</p>)}</div></section> : null}
    <details className={styles.textFallback}><summary>关系文字版 · Relationship text fallback</summary><div>{graph.edges.map(edge => <p key={edge.id}><b>{manifest.nodes.find(node => node.id === edge.from)?.short_label}</b><span>{edge.label} · {edge.kind} · {edge.criticality}</span><b>{manifest.nodes.find(node => node.id === edge.to)?.short_label}</b></p>)}</div></details>
    <p aria-live="polite" className={styles.srOnly}>{announced}</p>
  </main>;
}

export default function ArchitectureExplorerView() {
  const manifest = bundledArchitectureManifest();
  if (!manifest) return <main className={`${styles.main} ${styles.unavailable}`}><span>ARCHITECTURE MANIFEST</span><h1>System architecture unavailable</h1><p>The bounded build manifest is invalid. No runtime fallback request was attempted.</p></main>;
  return <ReactFlowProvider><ExplorerGraph manifest={manifest} /></ReactFlowProvider>;
}
