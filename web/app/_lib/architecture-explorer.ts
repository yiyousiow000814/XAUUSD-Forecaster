import dagre from "@dagrejs/dagre";

declare const __AURUM_ARCHITECTURE_MANIFEST__: unknown;
declare const __AURUM_DEPLOYMENT__: { commit_sha?: string };

export type ArchitectureState = "CURRENT" | "PENDING" | "TARGET" | "PAUSED" | "RETAINED";
export type ArchitecturePathState = "CURRENT_PATH" | "PENDING_PATH" | "LEGACY_SHIM" | "TARGET_PATH";
export type ArchitectureNodeKind = "SUBSYSTEM" | "PROCESS" | "THREAD" | "CONTROL" | "WORKER" | "REQUEST_HANDLER" | "STORE" | "STATIC" | "COMPONENT" | "EXTERNAL";
export type ArchitectureEdgeKind = "DATA" | "READ" | "WRITE" | "CONTROL" | "MODEL" | "MIRROR" | "OPTIONAL" | "DEPENDENCY";
export type ArchitectureCriticality = "CRITICAL" | "BACKGROUND" | "OPTIONAL" | "CONTROL_PLANE";
export type ArchitectureDimension = "ownership" | "boundary" | "critical_path" | "bounded_work" | "incremental" | "failure_isolation";
export type ArchitectureNode = {
  id: string; label: string; short_label: string; kind: ArchitectureNodeKind;
  runtime_state: ArchitectureState; implementation_state: ArchitecturePathState;
  owner: string; summary: string; purpose: string; architecture: Record<ArchitectureDimension, string>;
  inputs: string[]; outputs: string[]; code_paths: string[]; test_paths: string[];
  document_paths: string[]; tags: string[]; subsystem_view?: string;
};
export type ArchitectureEdge = {
  id: string; from: string; to: string; label: string; kind: ArchitectureEdgeKind;
  criticality: ArchitectureCriticality; description: string;
};
export type ArchitectureLane = { id: string; label: string; node_ids: string[] };
export type ArchitectureView = {
  id: string; label: string; summary: string; layout_direction: "LR" | "TB";
  node_ids: string[]; edge_ids: string[]; entry_node: string; primary_path: string[]; lanes: ArchitectureLane[];
  relationship_note?: string; prohibited_directions?: string[];
};
export type ArchitectureScenario = {
  id: string; label: string; description: string; view_id: string; node_ids: string[]; edge_ids: string[];
  steps: Array<{ node_id: string; message: string }>; failure_node_id?: string;
};
export type ArchitectureFailureImpact = {
  node_id: string; label: string;
  affected: Array<{ node_id: string; message: string }>;
  continues: Array<{ node_id: string; message: string }>;
};
export type ArchitectureManifest = {
  schema: "architecture-explorer-v2"; repository: string; byte_limit: number;
  views: ArchitectureView[]; nodes: ArchitectureNode[]; edges: ArchitectureEdge[];
  scenarios: ArchitectureScenario[]; failure_impacts: ArchitectureFailureImpact[];
  campaign: Array<{ id: string; label: string; branch: string; pr: number | null; state: "PENDING" }>;
};
export type ArchitectureGraphNode = {
  id: string; position: { x: number; y: number }; width: number; height: number;
  data: { node: ArchitectureNode; laneId: string; laneLabel: string };
};
export type ArchitectureGraphLane = {
  id: string; position: { x: number; y: number }; width: number; height: number;
  data: { label: string; direction: "LR" | "TB" };
};
export type ArchitectureGraphEdge = ArchitectureEdge & { source: string; target: string };

const STATES = new Set<ArchitectureState>(["CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"]);
const PATH_STATES = new Set<ArchitecturePathState>(["CURRENT_PATH", "PENDING_PATH", "LEGACY_SHIM", "TARGET_PATH"]);
const NODE_KINDS = new Set<ArchitectureNodeKind>(["SUBSYSTEM", "PROCESS", "THREAD", "CONTROL", "WORKER", "REQUEST_HANDLER", "STORE", "STATIC", "COMPONENT", "EXTERNAL"]);
const EDGE_KINDS = new Set<ArchitectureEdgeKind>(["DATA", "READ", "WRITE", "CONTROL", "MODEL", "MIRROR", "OPTIONAL", "DEPENDENCY"]);
const CRITICALITIES = new Set<ArchitectureCriticality>(["CRITICAL", "BACKGROUND", "OPTIONAL", "CONTROL_PLANE"]);
const DIMENSIONS: ArchitectureDimension[] = ["ownership", "boundary", "critical_path", "bounded_work", "incremental", "failure_isolation"];

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string");
}

function isContinuous(nodeIds: string[], edgeIds: string[], edgeById: Map<string, ArchitectureEdge>) {
  return edgeIds.length === nodeIds.length - 1 && edgeIds.every((edgeId, index) => {
    const edge = edgeById.get(edgeId);
    return edge?.from === nodeIds[index] && edge.to === nodeIds[index + 1];
  });
}

export function parseArchitectureManifest(value: unknown): ArchitectureManifest | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ArchitectureManifest>;
  if (candidate.schema !== "architecture-explorer-v2" || typeof candidate.repository !== "string"
      || !Array.isArray(candidate.views) || !Array.isArray(candidate.nodes) || !Array.isArray(candidate.edges)
      || !Array.isArray(candidate.scenarios) || !Array.isArray(candidate.failure_impacts)) return null;
  if (!candidate.nodes.every(node => node && typeof node.id === "string" && NODE_KINDS.has(node.kind)
      && STATES.has(node.runtime_state) && PATH_STATES.has(node.implementation_state)
      && typeof node.summary === "string" && node.summary.trim().length > 0
      && typeof node.purpose === "string" && node.purpose.trim().length > 0
      && typeof node.owner === "string" && node.owner.trim().length > 0
      && DIMENSIONS.every(key => typeof node.architecture?.[key] === "string")
      && [node.inputs, node.outputs, node.code_paths, node.test_paths, node.document_paths, node.tags].every(isStringArray))) return null;
  const nodeIds = new Set(candidate.nodes.map(node => node.id));
  if (nodeIds.size !== candidate.nodes.length) return null;
  if (!candidate.edges.every(edge => edge && typeof edge.id === "string" && typeof edge.label === "string"
      && typeof edge.description === "string" && nodeIds.has(edge.from) && nodeIds.has(edge.to)
      && EDGE_KINDS.has(edge.kind) && CRITICALITIES.has(edge.criticality))) return null;
  const edgeById = new Map(candidate.edges.map(edge => [edge.id, edge]));
  if (edgeById.size !== candidate.edges.length) return null;
  const viewIds = new Set(candidate.views.map(view => view.id));
  const viewById = new Map(candidate.views.map(view => [view.id, view]));
  if (viewIds.size !== candidate.views.length || !candidate.views.every(view => {
    if (!view || !["LR", "TB"].includes(view.layout_direction) || !isStringArray(view.node_ids)
        || !isStringArray(view.edge_ids) || !isStringArray(view.primary_path) || !Array.isArray(view.lanes)
        || (view.relationship_note !== undefined && typeof view.relationship_note !== "string")
        || (view.prohibited_directions !== undefined && !isStringArray(view.prohibited_directions))) return false;
    const visible = new Set(view.node_ids);
    const laneNodes = view.lanes.flatMap(lane => lane.node_ids);
    const primaryEdges = view.primary_path.slice(0, -1).map((from, index) => view.edge_ids.find(edgeId => {
      const edge = edgeById.get(edgeId); return edge?.from === from && edge.to === view.primary_path[index + 1];
    }) ?? "");
    return visible.has(view.entry_node) && laneNodes.length === visible.size && new Set(laneNodes).size === visible.size
      && laneNodes.every(id => visible.has(id))
      && view.edge_ids.every(id => { const edge = edgeById.get(id); return edge && visible.has(edge.from) && visible.has(edge.to); })
      && isContinuous(view.primary_path, primaryEdges, edgeById);
  })) return null;
  const scenarioIds = new Set(candidate.scenarios.map(scenario => scenario.id));
  if (scenarioIds.size !== candidate.scenarios.length || !candidate.scenarios.every(scenario => scenario && viewIds.has(scenario.view_id)
      && isStringArray(scenario.node_ids) && isStringArray(scenario.edge_ids)
      && isContinuous(scenario.node_ids, scenario.edge_ids, edgeById)
      && scenario.node_ids.every(id => viewById.get(scenario.view_id)?.node_ids.includes(id))
      && scenario.edge_ids.every(id => viewById.get(scenario.view_id)?.edge_ids.includes(id))
      && Array.isArray(scenario.steps) && scenario.steps.length === scenario.node_ids.length
      && scenario.steps.every((step, index) => step.node_id === scenario.node_ids[index] && typeof step.message === "string"))) return null;
  if (!candidate.failure_impacts.every(impact => impact && nodeIds.has(impact.node_id)
      && Array.isArray(impact.affected) && impact.affected.length > 0
      && Array.isArray(impact.continues) && impact.continues.length > 0
      && [...impact.affected, ...impact.continues].every(item => nodeIds.has(item.node_id) && typeof item.message === "string"))) return null;
  return candidate as ArchitectureManifest;
}

export function bundledArchitectureManifest(): ArchitectureManifest | null {
  return parseArchitectureManifest(typeof __AURUM_ARCHITECTURE_MANIFEST__ === "undefined" ? null : __AURUM_ARCHITECTURE_MANIFEST__);
}
export function architectureCommitSha(): string | null {
  const sha = typeof __AURUM_DEPLOYMENT__ === "undefined" ? "" : __AURUM_DEPLOYMENT__.commit_sha ?? "";
  return /^[0-9a-f]{40}$/i.test(sha) ? sha : null;
}
export function architectureGithubHref(manifest: Pick<ArchitectureManifest, "repository">, path: string, sha: string | null): string | null {
  if (!sha || !/^[0-9a-f]{40}$/i.test(sha) || path.includes("..") || path.startsWith("/")) return null;
  return `https://github.com/${manifest.repository}/blob/${sha}/${path}`;
}

export function architectureRelations(manifest: ArchitectureManifest, nodeId: string, viewId?: string) {
  const view = viewId ? manifest.views.find(item => item.id === viewId) : null;
  const scoped = view ? manifest.edges.filter(edge => view.edge_ids.includes(edge.id)) : manifest.edges;
  const directUpstream = scoped.filter(edge => edge.to === nodeId).map(edge => edge.from);
  const directDownstream = scoped.filter(edge => edge.from === nodeId).map(edge => edge.to);
  const walk = (direction: "up" | "down") => {
    const found = new Set<string>([nodeId]); const queue = [nodeId];
    while (queue.length) {
      const current = queue.shift()!;
      const next = scoped.filter(edge => direction === "up" ? edge.to === current : edge.from === current)
        .map(edge => direction === "up" ? edge.from : edge.to);
      for (const id of next) if (!found.has(id)) { found.add(id); queue.push(id); }
    }
    found.delete(nodeId); return [...found];
  };
  const upstream = walk("up"); const downstream = walk("down");
  const connectedEdges = scoped.filter(edge => upstream.includes(edge.from) && (edge.to === nodeId || upstream.includes(edge.to))
    || downstream.includes(edge.to) && (edge.from === nodeId || downstream.includes(edge.from))
    || edge.from === nodeId || edge.to === nodeId).map(edge => edge.id);
  return { directUpstream, directDownstream, upstream, downstream, connectedEdges };
}

export function searchArchitectureNodes(manifest: ArchitectureManifest, query: string, state: string) {
  const normalized = query.trim().toLocaleLowerCase();
  return manifest.nodes.filter(node => {
    if (state !== "ALL" && node.runtime_state !== state) return false;
    if (!normalized) return true;
    return [node.label, node.short_label, node.owner, node.summary, node.purpose, ...node.code_paths, ...node.test_paths, ...node.document_paths, ...node.tags]
      .some(value => value.toLocaleLowerCase().includes(normalized));
  });
}
export function bestViewForNode(manifest: ArchitectureManifest, node: ArchitectureNode, currentViewId: string) {
  if (manifest.views.find(view => view.id === currentViewId)?.node_ids.includes(node.id)) return currentViewId;
  if (node.subsystem_view && manifest.views.some(view => view.id === node.subsystem_view)) return node.subsystem_view;
  return manifest.views.find(view => view.node_ids.includes(node.id))?.id ?? manifest.views[0].id;
}
export function architectureFailureImpact(manifest: ArchitectureManifest, nodeId: string | null) {
  return nodeId ? manifest.failure_impacts.find(item => item.node_id === nodeId) ?? null : null;
}

export function architectureFitOptions(nodeCount: number, mobile: boolean) {
  const maxZoom = nodeCount <= 5 ? (mobile ? 1.12 : 1.3) : nodeCount <= 9 ? (mobile ? 1 : 1.12) : (mobile ? .9 : 1);
  return { padding: mobile ? .08 : .07, maxZoom, duration: 280 } as const;
}

export function architectureCanvasHeight(nodeCount: number, mobile: boolean) {
  return mobile ? Math.min(1600, Math.max(650, nodeCount * 135)) : 650;
}

export function buildArchitectureGraph(manifest: ArchitectureManifest, viewId: string, direction?: "LR" | "TB") {
  const view = manifest.views.find(item => item.id === viewId) ?? manifest.views[0];
  const rankdir = direction ?? view.layout_direction;
  const width = rankdir === "TB" ? 238 : 190; const height = rankdir === "TB" ? 92 : 90;
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir, ranker: "network-simplex", nodesep: rankdir === "TB" ? 24 : 30, ranksep: rankdir === "TB" ? 68 : 66, marginx: 24, marginy: 24 });
  const nodeById = new Map(manifest.nodes.map(item => [item.id, item]));
  const laneByNode = new Map(view.lanes.flatMap(lane => lane.node_ids.map(nodeId => [nodeId, lane] as const)));
  view.node_ids.forEach(nodeId => graph.setNode(nodeId, { width, height, rank: view.lanes.findIndex(lane => lane.node_ids.includes(nodeId)) }));
  const visibleEdges = view.edge_ids.map(id => manifest.edges.find(item => item.id === id)!).filter(Boolean);
  visibleEdges.forEach(item => graph.setEdge(item.from, item.to, { id: item.id }));
  dagre.layout(graph);
  const compactMobilePositions = new Map<string, { x: number; y: number }>();
  if (rankdir === "TB") {
    let cursorY = 24;
    for (const lane of view.lanes) {
      for (const nodeId of lane.node_ids) {
        compactMobilePositions.set(nodeId, { x: 24, y: cursorY });
        cursorY += height + 36;
      }
      cursorY += 28;
    }
  }
  const nodes: ArchitectureGraphNode[] = view.node_ids.map(id => {
    const dagrePosition = graph.node(id) as { x: number; y: number }; const lane = laneByNode.get(id)!;
    const position = compactMobilePositions.get(id) ?? { x: dagrePosition.x - width / 2, y: dagrePosition.y - height / 2 };
    return { id, position, width, height,
      data: { node: nodeById.get(id)!, laneId: lane.id, laneLabel: lane.label } };
  });
  const lanePadding = rankdir === "TB" ? { x: 18, top: 34, bottom: 18 } : { x: 22, top: 34, bottom: 20 };
  const laneBoxes: ArchitectureGraphLane[] = view.lanes.map(lane => {
    const laneNodes = nodes.filter(node => lane.node_ids.includes(node.id));
    const left = Math.min(...laneNodes.map(node => node.position.x));
    const top = Math.min(...laneNodes.map(node => node.position.y));
    const right = Math.max(...laneNodes.map(node => node.position.x + node.width));
    const bottom = Math.max(...laneNodes.map(node => node.position.y + node.height));
    return {
      id: `lane-${lane.id}`,
      position: { x: left - lanePadding.x, y: top - lanePadding.top },
      width: right - left + lanePadding.x * 2,
      height: bottom - top + lanePadding.top + lanePadding.bottom,
      data: { label: lane.label, direction: rankdir },
    };
  });
  const edges: ArchitectureGraphEdge[] = visibleEdges.map(item => ({ ...item, source: item.from, target: item.to }));
  return { view, nodes, laneBoxes, edges, direction: rankdir };
}
