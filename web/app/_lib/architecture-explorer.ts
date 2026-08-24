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
  code_paths: string[]; test_paths: string[]; document_paths: string[]; tags: string[]; subsystem_view?: string;
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
  data: { node: ArchitectureNode; laneId: string; laneLabel: string; hasIncomingEdge: boolean; hasOutgoingEdge: boolean };
};
export type ArchitectureGraphLane = {
  id: string; position: { x: number; y: number }; width: number; height: number;
  data: { label: string; direction: "LR" | "TB" };
};
export type ArchitectureGraphEdge = ArchitectureEdge & { source: string; target: string };
export type ArchitecturePoint = { x: number; y: number };

export const ARCHITECTURE_LANE_GAP = { LR: 24, TB: 20 } as const;

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
      && [node.code_paths, node.test_paths, node.document_paths, node.tags].every(isStringArray))) return null;
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

function laneBounds(nodes: ArchitectureGraphNode[], lane: ArchitectureLane, direction: "LR" | "TB"): ArchitectureGraphLane {
  const laneNodes = nodes.filter(node => lane.node_ids.includes(node.id));
  const padding = direction === "TB" ? { x: 18, top: 34, bottom: 18 } : { x: 22, top: 34, bottom: 20 };
  const left = Math.min(...laneNodes.map(node => node.position.x));
  const top = Math.min(...laneNodes.map(node => node.position.y));
  const right = Math.max(...laneNodes.map(node => node.position.x + node.width));
  const bottom = Math.max(...laneNodes.map(node => node.position.y + node.height));
  return {
    id: `lane-${lane.id}`,
    position: { x: left - padding.x, y: top - padding.top },
    width: right - left + padding.x * 2,
    height: bottom - top + padding.top + padding.bottom,
    data: { label: lane.label, direction },
  };
}

function separateArchitectureLanes(nodes: ArchitectureGraphNode[], lanes: ArchitectureLane[], direction: "LR" | "TB") {
  const placed: ArchitectureGraphLane[] = [];
  const gap = ARCHITECTURE_LANE_GAP[direction];
  for (const lane of lanes) {
    let bounds = laneBounds(nodes, lane, direction);
    for (let attempt = 0; attempt < placed.length; attempt += 1) {
      const conflicting = placed.filter(previous => {
        const xSeparated = bounds.position.x >= previous.position.x + previous.width + gap
          || previous.position.x >= bounds.position.x + bounds.width + gap;
        const ySeparated = bounds.position.y >= previous.position.y + previous.height + gap
          || previous.position.y >= bounds.position.y + bounds.height + gap;
        return !xSeparated && !ySeparated;
      });
      if (!conflicting.length) break;
      const delta = direction === "LR"
        ? Math.max(...conflicting.map(previous => previous.position.y + previous.height + gap - bounds.position.y))
        : Math.max(...conflicting.map(previous => previous.position.x + previous.width + gap - bounds.position.x));
      for (const node of nodes) {
        if (!lane.node_ids.includes(node.id)) continue;
        if (direction === "LR") node.position.y += delta;
        else node.position.x += delta;
      }
      bounds = laneBounds(nodes, lane, direction);
    }
    placed.push(bounds);
  }
  return placed;
}

export function architecturePortVisibility(edges: Pick<ArchitectureGraphEdge, "source" | "target">[], nodeId: string) {
  return {
    hasIncomingEdge: edges.some(edge => edge.target === nodeId),
    hasOutgoingEdge: edges.some(edge => edge.source === nodeId),
  };
}

function segmentIntersectsNode(a: ArchitecturePoint, b: ArchitecturePoint, node: ArchitectureGraphNode, clearance = 8) {
  const left = node.position.x - clearance; const right = node.position.x + node.width + clearance;
  const top = node.position.y - clearance; const bottom = node.position.y + node.height + clearance;
  if (a.x === b.x) return a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom;
  if (a.y === b.y) return a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right;
  return true;
}

export function architectureRouteCrossesUnrelatedNode(
  points: ArchitecturePoint[], nodes: ArchitectureGraphNode[], sourceId: string, targetId: string,
) {
  const obstacles = nodes.filter(node => node.id !== sourceId && node.id !== targetId);
  return points.slice(1).some((point, index) => obstacles.some(node => segmentIntersectsNode(points[index], point, node)));
}

export function architectureEdgeRoute(
  nodes: ArchitectureGraphNode[], edge: Pick<ArchitectureGraphEdge, "id" | "source" | "target">, direction: "LR" | "TB",
) {
  const source = nodes.find(node => node.id === edge.source)!; const target = nodes.find(node => node.id === edge.target)!;
  const start = direction === "LR"
    ? { x: source.position.x + source.width, y: source.position.y + source.height / 2 }
    : { x: source.position.x + source.width / 2, y: source.position.y + source.height };
  const end = direction === "LR"
    ? { x: target.position.x, y: target.position.y + target.height / 2 }
    : { x: target.position.x + target.width / 2, y: target.position.y };
  const middle = direction === "LR" ? (start.x + end.x) / 2 : (start.y + end.y) / 2;
  const direct = direction === "LR"
    ? [start, { x: middle, y: start.y }, { x: middle, y: end.y }, end]
    : [start, { x: start.x, y: middle }, { x: end.x, y: middle }, end];
  if (!architectureRouteCrossesUnrelatedNode(direct, nodes, edge.source, edge.target)) return direct;

  const ordinal = [...edge.id].reduce((total, char) => total + char.charCodeAt(0), 0) % 5;
  if (direction === "LR") {
    const above = Math.min(...nodes.map(node => node.position.y)) - 24 - ordinal * 8;
    const below = Math.max(...nodes.map(node => node.position.y + node.height)) + 24 + ordinal * 8;
    const stubStart = start.x + 18; const stubEnd = end.x - 18;
    for (const y of [above, below]) {
      const route = [start, { x: stubStart, y: start.y }, { x: stubStart, y }, { x: stubEnd, y }, { x: stubEnd, y: end.y }, end];
      if (!architectureRouteCrossesUnrelatedNode(route, nodes, edge.source, edge.target)) return route;
    }
  } else {
    const left = Math.min(...nodes.map(node => node.position.x)) - 24 - ordinal * 8;
    const right = Math.max(...nodes.map(node => node.position.x + node.width)) + 24 + ordinal * 8;
    const stubStart = start.y + 18; const stubEnd = end.y - 18;
    for (const x of [left, right]) {
      const route = [start, { x: start.x, y: stubStart }, { x, y: stubStart }, { x, y: stubEnd }, { x: end.x, y: stubEnd }, end];
      if (!architectureRouteCrossesUnrelatedNode(route, nodes, edge.source, edge.target)) return route;
    }
  }
  return direct;
}

export function architectureRoutePath(points: ArchitecturePoint[]) {
  return points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
}

export function architectureRouteLabelPoint(points: ArchitecturePoint[]) {
  const segments = points.slice(1).map((point, index) => ({
    a: points[index], b: point, length: Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y),
  }));
  const longest = segments.reduce((best, item) => item.length > best.length ? item : best, segments[0]);
  return { x: (longest.a.x + longest.b.x) / 2, y: (longest.a.y + longest.b.y) / 2 };
}

export function buildArchitectureGraph(manifest: ArchitectureManifest, viewId: string, direction?: "LR" | "TB") {
  const view = manifest.views.find(item => item.id === viewId) ?? manifest.views[0];
  const rankdir = direction ?? view.layout_direction;
  const width = rankdir === "TB" ? 238 : 190; const height = rankdir === "TB" ? 92 : 90;
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir, ranker: "network-simplex", nodesep: rankdir === "TB" ? 24 : 30, ranksep: rankdir === "TB" ? 72 : 66, marginx: 24, marginy: 24 });
  const nodeById = new Map(manifest.nodes.map(item => [item.id, item]));
  const laneByNode = new Map(view.lanes.flatMap(lane => lane.node_ids.map(nodeId => [nodeId, lane] as const)));
  view.node_ids.forEach(nodeId => graph.setNode(nodeId, { width, height, rank: view.lanes.findIndex(lane => lane.node_ids.includes(nodeId)) }));
  const visibleEdges = view.edge_ids.map(id => manifest.edges.find(item => item.id === id)!).filter(Boolean);
  visibleEdges.forEach(item => graph.setEdge(item.from, item.to, { id: item.id }));
  dagre.layout(graph);
  const nodes: ArchitectureGraphNode[] = view.node_ids.map(id => {
    const dagrePosition = graph.node(id) as { x: number; y: number }; const lane = laneByNode.get(id)!;
    const position = { x: dagrePosition.x - width / 2, y: dagrePosition.y - height / 2 };
    const ports = architecturePortVisibility(visibleEdges.map(item => ({ source: item.from, target: item.to })), id);
    return { id, position, width, height,
      data: { node: nodeById.get(id)!, laneId: lane.id, laneLabel: lane.label, ...ports } };
  });
  const laneBoxes = separateArchitectureLanes(nodes, view.lanes, rankdir);
  const edges: ArchitectureGraphEdge[] = visibleEdges.map(item => ({ ...item, source: item.from, target: item.to }));
  return { view, nodes, laneBoxes, edges, direction: rankdir };
}
