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
export type ArchitectureSemanticGroup = { id: string; node_ids: string[] };
export type ArchitectureConvergence = { target: string; sources: string[] };
export type ArchitectureLayoutHints = {
  mode: "SEMANTIC_GRID";
  rank_groups: ArchitectureSemanticGroup[];
  track_groups: ArchitectureSemanticGroup[];
  convergences: ArchitectureConvergence[];
  auto_place_unlisted: true;
};
export type ArchitectureViewRole = "OVERVIEW" | "SUBSYSTEM" | "ADVANCED" | "CAMPAIGN";
export type ArchitectureAudience = "BEGINNER" | "ADVANCED";
export type ArchitectureDisclosureMode = "PRIMARY_PATH" | "VIEW_RELATIONSHIPS" | "SELECTED_NODE" | "SELECTED_PACKAGE";
export type ArchitectureNavigation = { role: ArchitectureViewRole; audience: ArchitectureAudience; parent_view?: string };
export type ArchitectureDisclosure = {
  default_mode: ArchitectureDisclosureMode; always_visible_edge_ids: string[]; secondary_edge_ids: string[]; allow_show_all: boolean;
};
export type ArchitectureView = {
  id: string; label: string; summary: string; layout_direction: "LR" | "TB";
  node_ids: string[]; edge_ids: string[]; entry_node: string; primary_path: string[]; lanes: ArchitectureLane[];
  relationship_note?: string; prohibited_directions?: string[]; layout_hints?: ArchitectureLayoutHints;
  navigation: ArchitectureNavigation; disclosure: ArchitectureDisclosure;
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
  data: {
    node: ArchitectureNode; laneId: string; laneLabel: string; hasIncomingEdge: boolean; hasOutgoingEdge: boolean;
    incomingPorts: ArchitecturePortSlot[]; outgoingPorts: ArchitecturePortSlot[];
  };
};
export type ArchitectureGraphLane = {
  id: string; position: { x: number; y: number }; width: number; height: number;
  data: { label: string; direction: "LR" | "TB" };
};
export type ArchitectureGraphEdge = ArchitectureEdge & {
  source: string; target: string; sourceAnchor: ArchitecturePoint; targetAnchor: ArchitecturePoint;
  routeSlot: number; routeCount: number;
};
export type ArchitecturePoint = { x: number; y: number };
export type ArchitecturePortSlot = { edgeId: string; offset: number; anchor: ArchitecturePoint };
export type ArchitectureGraphBounds = { x: number; y: number; width: number; height: number };

export const ARCHITECTURE_LANE_GAP = { LR: 24, TB: 20 } as const;
export const ARCHITECTURE_EDGE_OVERLAP_TOLERANCE = 6;
export const ARCHITECTURE_MOBILE_NODE_WIDTH_FLOOR = 168;
export const ARCHITECTURE_SEMANTIC_LAYOUT_PASSES = 8;

const STATES = new Set<ArchitectureState>(["CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"]);
const PATH_STATES = new Set<ArchitecturePathState>(["CURRENT_PATH", "PENDING_PATH", "LEGACY_SHIM", "TARGET_PATH"]);
const NODE_KINDS = new Set<ArchitectureNodeKind>(["SUBSYSTEM", "PROCESS", "THREAD", "CONTROL", "WORKER", "REQUEST_HANDLER", "STORE", "STATIC", "COMPONENT", "EXTERNAL"]);
const EDGE_KINDS = new Set<ArchitectureEdgeKind>(["DATA", "READ", "WRITE", "CONTROL", "MODEL", "MIRROR", "OPTIONAL", "DEPENDENCY"]);
const CRITICALITIES = new Set<ArchitectureCriticality>(["CRITICAL", "BACKGROUND", "OPTIONAL", "CONTROL_PLANE"]);
const DIMENSIONS: ArchitectureDimension[] = ["ownership", "boundary", "critical_path", "bounded_work", "incremental", "failure_isolation"];
const VIEW_ROLES = new Set<ArchitectureViewRole>(["OVERVIEW", "SUBSYSTEM", "ADVANCED", "CAMPAIGN"]);
const VIEW_AUDIENCES = new Set<ArchitectureAudience>(["BEGINNER", "ADVANCED"]);
const DISCLOSURE_MODES = new Set<ArchitectureDisclosureMode>(["PRIMARY_PATH", "VIEW_RELATIONSHIPS", "SELECTED_NODE", "SELECTED_PACKAGE"]);

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string");
}

function isContinuous(nodeIds: string[], edgeIds: string[], edgeById: Map<string, ArchitectureEdge>) {
  return edgeIds.length === nodeIds.length - 1 && edgeIds.every((edgeId, index) => {
    const edge = edgeById.get(edgeId);
    return edge?.from === nodeIds[index] && edge.to === nodeIds[index + 1];
  });
}

function isValidLayoutHints(value: unknown, visible: Set<string>) {
  if (value === undefined) return true;
  if (!value || typeof value !== "object") return false;
  const hints = value as Partial<ArchitectureLayoutHints>;
  if (Object.keys(hints).some(key => !["mode", "rank_groups", "track_groups", "convergences", "auto_place_unlisted"].includes(key))) return false;
  if (hints.mode !== "SEMANTIC_GRID" || hints.auto_place_unlisted !== true
      || !Array.isArray(hints.rank_groups) || !Array.isArray(hints.track_groups)
      || !Array.isArray(hints.convergences)) return false;
  const groups = [...hints.rank_groups, ...hints.track_groups];
  if (!groups.length && !hints.convergences.length) return false;
  if (!groups.every(group => group && Object.keys(group).length === 2 && Object.hasOwn(group, "id") && Object.hasOwn(group, "node_ids")
      && typeof group.id === "string" && group.id.trim()
      && isStringArray(group.node_ids) && group.node_ids.length > 0
      && new Set(group.node_ids).size === group.node_ids.length
      && group.node_ids.every(id => visible.has(id)))) return false;
  if (new Set(groups.map(group => group.id)).size !== groups.length) return false;
  for (const family of [hints.rank_groups, hints.track_groups]) {
    const members = family.flatMap(group => group.node_ids);
    if (new Set(members).size !== members.length) return false;
  }
  const rankByNode = new Map(hints.rank_groups.flatMap(group => group.node_ids.map(id => [id, group.id] as const)));
  const trackByNode = new Map(hints.track_groups.flatMap(group => group.node_ids.map(id => [id, group.id] as const)));
  const occupied = new Set<string>();
  for (const id of visible) {
    const key = rankByNode.has(id) && trackByNode.has(id) ? `${rankByNode.get(id)}\u0000${trackByNode.get(id)}` : null;
    if (key && occupied.has(key)) return false;
    if (key) occupied.add(key);
  }
  const convergenceTracks = new Set<string>();
  const convergenceTargets = new Set<string>();
  return hints.convergences.every(item => {
    if (!item || Object.keys(item).length !== 2 || !Object.hasOwn(item, "target") || !Object.hasOwn(item, "sources")
        || typeof item.target !== "string" || !visible.has(item.target) || convergenceTargets.has(item.target)
        || !isStringArray(item.sources) || item.sources.length < 2
        || new Set(item.sources).size !== item.sources.length
        || item.sources.includes(item.target) || !item.sources.every(id => visible.has(id))) return false;
    const targetTrack = trackByNode.get(item.target);
    if (targetTrack && (convergenceTracks.has(targetTrack)
        || item.sources.some(source => trackByNode.get(source) === targetTrack))) return false;
    if (targetTrack) convergenceTracks.add(targetTrack);
    convergenceTargets.add(item.target);
    return true;
  });
}

function isValidViewMetadata(view: Partial<ArchitectureView>, viewIds: Set<string>) {
  const navigation = view.navigation;
  const disclosure = view.disclosure;
  if (!navigation || !VIEW_ROLES.has(navigation.role) || !VIEW_AUDIENCES.has(navigation.audience)
      || (navigation.parent_view !== undefined && !viewIds.has(navigation.parent_view))) return false;
  if (!disclosure || !DISCLOSURE_MODES.has(disclosure.default_mode)
      || !isStringArray(disclosure.always_visible_edge_ids) || !isStringArray(disclosure.secondary_edge_ids)
      || typeof disclosure.allow_show_all !== "boolean") return false;
  const classified = [...disclosure.always_visible_edge_ids, ...disclosure.secondary_edge_ids];
  return new Set(classified).size === classified.length && classified.every(id => view.edge_ids?.includes(id));
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
        || (view.prohibited_directions !== undefined && !isStringArray(view.prohibited_directions))
        || !isValidViewMetadata(view, viewIds)) return false;
    const visible = new Set(view.node_ids);
    if (!isValidLayoutHints(view.layout_hints, visible)) return false;
    const laneNodes = view.lanes.flatMap(lane => lane.node_ids);
    const primaryEdges = view.primary_path.slice(0, -1).map((from, index) => view.edge_ids.find(edgeId => {
      const edge = edgeById.get(edgeId); return edge?.from === from && edge.to === view.primary_path[index + 1];
    }) ?? "");
    return visible.has(view.entry_node) && laneNodes.length === visible.size && new Set(laneNodes).size === visible.size
      && laneNodes.every(id => visible.has(id))
      && view.edge_ids.every(id => { const edge = edgeById.get(id); return edge && visible.has(edge.from) && visible.has(edge.to); })
      && isContinuous(view.primary_path, primaryEdges, edgeById);
  })) return null;
  const overviews = candidate.views.filter(view => view.navigation.role === "OVERVIEW");
  if (overviews.length !== 1 || overviews[0].id !== "system-overview" || overviews[0].navigation.audience !== "BEGINNER") return null;
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

export type ArchitectureDisclosureSelection = {
  selectedNodeId?: string | null; scenarioEdgeIds?: Iterable<string>; showAll?: boolean; referenceMode?: boolean;
};

export function architectureDisclosedEdgeIds(
  manifest: ArchitectureManifest, viewId: string, selection: ArchitectureDisclosureSelection = {},
) {
  const view = manifest.views.find(item => item.id === viewId) ?? manifest.views[0];
  if (selection.referenceMode || (selection.showAll && view.disclosure.allow_show_all)) return new Set(view.edge_ids);
  const visible = new Set(view.disclosure.always_visible_edge_ids);
  for (const edgeId of selection.scenarioEdgeIds ?? []) if (view.edge_ids.includes(edgeId)) visible.add(edgeId);
  if (selection.selectedNodeId) {
    for (const edge of manifest.edges) if (view.edge_ids.includes(edge.id)
        && (edge.from === selection.selectedNodeId || edge.to === selection.selectedNodeId)) visible.add(edge.id);
  }
  return visible;
}

export function architectureDisclosedGraph<T extends ReturnType<typeof buildArchitectureGraph>>(graph: T, visibleEdgeIds: Set<string>): T {
  const edges = graph.edges.filter(edge => visibleEdgeIds.has(edge.id));
  const nodes = graph.nodes.map(node => {
    const incomingPorts = node.data.incomingPorts.filter(port => visibleEdgeIds.has(port.edgeId));
    const outgoingPorts = node.data.outgoingPorts.filter(port => visibleEdgeIds.has(port.edgeId));
    return { ...node, data: { ...node.data, incomingPorts, outgoingPorts,
      hasIncomingEdge: incomingPorts.length > 0, hasOutgoingEdge: outgoingPorts.length > 0 } };
  });
  return { ...graph, nodes, edges };
}

export function architectureFailureImpact(manifest: ArchitectureManifest, nodeId: string | null) {
  return nodeId ? manifest.failure_impacts.find(item => item.node_id === nodeId) ?? null : null;
}

export function architectureFitOptions(nodeCount: number, mobile: boolean) {
  const maxZoom = nodeCount <= 5 ? (mobile ? 1.12 : 1.3) : nodeCount <= 9 ? (mobile ? 1 : 1.12) : (mobile ? .9 : 1);
  return { padding: mobile ? .08 : .07, maxZoom, duration: 280 } as const;
}

export function architectureGraphBounds(nodes: ArchitectureGraphNode[], lanes: ArchitectureGraphLane[]): ArchitectureGraphBounds {
  const boxes = lanes.length ? lanes : nodes;
  const left = Math.min(...boxes.map(item => item.position.x));
  const top = Math.min(...boxes.map(item => item.position.y));
  const right = Math.max(...boxes.map(item => item.position.x + item.width));
  const bottom = Math.max(...boxes.map(item => item.position.y + item.height));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export function architectureCanvasHeight(viewportWidth: number, viewportHeight: number, mobile: boolean) {
  if (!mobile) return 650;
  if (viewportWidth > viewportHeight) return Math.min(360, Math.max(280, Math.round(viewportHeight * .72)));
  return Math.min(720, Math.max(480, Math.round(viewportHeight * .68)));
}

export function architectureMobileViewport(
  nodes: ArchitectureGraphNode[], lanes: ArchitectureGraphLane[], canvasWidth: number, canvasHeight: number,
) {
  const bounds = architectureGraphBounds(nodes, lanes);
  const minimumNodeWidth = Math.min(...nodes.map(node => node.width));
  const readabilityZoom = ARCHITECTURE_MOBILE_NODE_WIDTH_FLOOR / minimumNodeWidth;
  const fitZoom = Math.min((canvasWidth - 28) / bounds.width, (canvasHeight - 96) / bounds.height, 1);
  const zoom = Math.max(readabilityZoom, fitZoom);
  const boundsCenterX = bounds.x + bounds.width / 2;
  const convergenceNodes = nodes.filter(node => node.data.incomingPorts.length > 1);
  const convergenceCenterX = convergenceNodes.length
    ? convergenceNodes.reduce((total, node) => total + node.position.x + node.width / 2, 0) / convergenceNodes.length
    : boundsCenterX;
  const usefulCenterX = boundsCenterX * .73 + convergenceCenterX * .27;
  return {
    x: canvasWidth / 2 - usefulCenterX * zoom,
    y: 48 - bounds.y * zoom,
    zoom,
  };
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

function architectureLaneBoxesConflict(lanes: ArchitectureGraphLane[], direction: "LR" | "TB") {
  const gap = ARCHITECTURE_LANE_GAP[direction];
  for (let left = 0; left < lanes.length; left += 1) for (let right = left + 1; right < lanes.length; right += 1) {
    const a = lanes[left]; const b = lanes[right];
    const xSeparated = a.position.x >= b.position.x + b.width + gap || b.position.x >= a.position.x + a.width + gap;
    const ySeparated = a.position.y >= b.position.y + b.height + gap || b.position.y >= a.position.y + a.height + gap;
    if (!xSeparated && !ySeparated) return true;
  }
  return false;
}

function architectureNodesOverlap(left: ArchitectureGraphNode, right: ArchitectureGraphNode, gap = 12) {
  return left.position.x < right.position.x + right.width + gap
    && right.position.x < left.position.x + left.width + gap
    && left.position.y < right.position.y + right.height + gap
    && right.position.y < left.position.y + left.height + gap;
}

function applySemanticArchitectureLayout(
  nodes: ArchitectureGraphNode[], view: ArchitectureView, edges: ArchitectureEdge[], direction: "LR" | "TB",
) {
  const hints = view.layout_hints!;
  const fallback = new Map(nodes.map(node => [node.id, { ...node.position }]));
  const rankByNode = new Map(hints.rank_groups.flatMap((group, rank) => group.node_ids.map(id => [id, rank] as const)));
  const trackByNode = new Map(hints.track_groups.flatMap((group, track) => group.node_ids.map(id => [id, track] as const)));
  const primarySize = direction === "LR" ? nodes[0].width : nodes[0].height;
  const crossSize = direction === "LR" ? nodes[0].height : nodes[0].width;
  const fallbackPrimaryCenter = (node: ArchitectureGraphNode) => {
    const position = fallback.get(node.id)!;
    return direction === "LR" ? position.x + node.width / 2 : position.y + node.height / 2;
  };
  const fallbackCrossCenter = (node: ArchitectureGraphNode) => {
    const position = fallback.get(node.id)!;
    return direction === "LR" ? position.y + node.height / 2 : position.x + node.width / 2;
  };
  const primaryOrigin = Math.min(...nodes.map(fallbackPrimaryCenter));
  const crossOrigin = Math.min(...nodes.map(fallbackCrossCenter));

  for (let pass = 0; pass < ARCHITECTURE_SEMANTIC_LAYOUT_PASSES; pass += 1) {
    const rankStep = primarySize + (direction === "LR" ? 80 : 84) + pass * 28;
    const trackStep = crossSize + (direction === "LR" ? 84 : 66) + pass * 28;
    const resolvedRanks = new Map(rankByNode);
    const pending = nodes.filter(node => !resolvedRanks.has(node.id)).sort((left, right) => left.id.localeCompare(right.id));
    for (let attempt = 0; attempt < nodes.length && pending.some(node => !resolvedRanks.has(node.id)); attempt += 1) {
      for (const node of pending) {
        if (resolvedRanks.has(node.id)) continue;
        const incoming = edges.filter(edge => edge.to === node.id).map(edge => resolvedRanks.get(edge.from)).filter(Number.isFinite) as number[];
        const outgoing = edges.filter(edge => edge.from === node.id).map(edge => resolvedRanks.get(edge.to)).filter(Number.isFinite) as number[];
        const estimates = [...incoming.map(rank => rank + 1), ...outgoing.map(rank => rank - 1)];
        if (estimates.length) resolvedRanks.set(node.id, Math.round(estimates.reduce((sum, rank) => sum + rank, 0) / estimates.length));
      }
    }
    for (const node of pending) if (!resolvedRanks.has(node.id)) {
      resolvedRanks.set(node.id, Math.round((fallbackPrimaryCenter(node) - primaryOrigin) / rankStep));
    }

    const crossCenters = new Map<string, number>();
    nodes.forEach(node => crossCenters.set(node.id, trackByNode.has(node.id)
      ? crossOrigin + trackByNode.get(node.id)! * trackStep : fallbackCrossCenter(node)));
    for (const node of nodes.filter(item => !trackByNode.has(item.id)).sort((left, right) => left.id.localeCompare(right.id))) {
      const neighbours = edges.filter(edge => edge.from === node.id || edge.to === node.id)
        .map(edge => edge.from === node.id ? edge.to : edge.from)
        .map(id => crossCenters.get(id)).filter(Number.isFinite) as number[];
      if (neighbours.length) crossCenters.set(node.id, neighbours.reduce((sum, value) => sum + value, 0) / neighbours.length);
    }
    for (const convergence of hints.convergences) {
      const midpoint = convergence.sources.reduce((sum, id) => sum + crossCenters.get(id)!, 0) / convergence.sources.length;
      const targetTrack = trackByNode.get(convergence.target);
      if (targetTrack === undefined) crossCenters.set(convergence.target, midpoint);
      else hints.track_groups[targetTrack].node_ids.forEach(id => crossCenters.set(id, midpoint));
    }

    nodes.forEach(node => {
      const primary = primaryOrigin + resolvedRanks.get(node.id)! * rankStep;
      const cross = crossCenters.get(node.id)!;
      node.position = direction === "LR"
        ? { x: primary - node.width / 2, y: cross - node.height / 2 }
        : { x: cross - node.width / 2, y: primary - node.height / 2 };
    });
    for (const node of nodes.filter(item => !trackByNode.has(item.id)).sort((left, right) => left.id.localeCompare(right.id))) {
      const baseCross = crossCenters.get(node.id)!;
      for (let slot = 0; slot <= nodes.length + 2; slot += 1) {
        const ordinal = slot === 0 ? 0 : Math.ceil(slot / 2) * (slot % 2 ? 1 : -1);
        const cross = baseCross + ordinal * trackStep;
        node.position = direction === "LR"
          ? { ...node.position, y: cross - node.height / 2 }
          : { ...node.position, x: cross - node.width / 2 };
        if (!nodes.some(other => other.id !== node.id && architectureNodesOverlap(node, other))) break;
      }
    }
    const laneBoxes = view.lanes.map(lane => laneBounds(nodes, lane, direction));
    if (!architectureLaneBoxesConflict(laneBoxes, direction)) return laneBoxes;
  }
  throw new Error(`Semantic layout could not separate lanes for ${view.id} in ${direction}`);
}

export function architecturePortVisibility(edges: Pick<ArchitectureGraphEdge, "source" | "target">[], nodeId: string) {
  return {
    hasIncomingEdge: edges.some(edge => edge.target === nodeId),
    hasOutgoingEdge: edges.some(edge => edge.source === nodeId),
  };
}

function portOffset(size: number, index: number, count: number) {
  if (count === 1) return size / 2;
  const margin = Math.min(18, size / 4);
  return margin + index * ((size - margin * 2) / (count - 1));
}

export function architectureEdgeAnchors(
  nodes: ArchitectureGraphNode[], edges: Array<Pick<ArchitectureGraphEdge, "id" | "source" | "target">>, direction: "LR" | "TB",
) {
  const anchors = new Map<string, { sourceAnchor: ArchitecturePoint; targetAnchor: ArchitecturePoint }>();
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const crossCenter = (nodeId: string) => {
    const node = nodeById.get(nodeId)!;
    return direction === "LR" ? node.position.y + node.height / 2 : node.position.x + node.width / 2;
  };
  const assign = (node: ArchitectureGraphNode, side: "source" | "target") => {
    const incident = edges.filter(edge => side === "source" ? edge.source === node.id : edge.target === node.id)
      .sort((left, right) => {
        const leftOther = side === "source" ? left.target : left.source;
        const rightOther = side === "source" ? right.target : right.source;
        return crossCenter(leftOther) - crossCenter(rightOther) || left.id.localeCompare(right.id);
      });
    incident.forEach((edge, index) => {
      const size = direction === "LR" ? node.height : node.width;
      const offset = portOffset(size, index, incident.length);
      const anchor = direction === "LR"
        ? { x: node.position.x + (side === "source" ? node.width : 0), y: node.position.y + offset }
        : { x: node.position.x + offset, y: node.position.y + (side === "source" ? node.height : 0) };
      const current = anchors.get(edge.id) ?? {} as { sourceAnchor: ArchitecturePoint; targetAnchor: ArchitecturePoint };
      current[side === "source" ? "sourceAnchor" : "targetAnchor"] = anchor;
      anchors.set(edge.id, current);
    });
  };
  nodes.forEach(node => { assign(node, "target"); assign(node, "source"); });
  return anchors;
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
  const anchors = "sourceAnchor" in edge && "targetAnchor" in edge
    ? edge as Pick<ArchitectureGraphEdge, "sourceAnchor" | "targetAnchor">
    : architectureEdgeAnchors(nodes, [edge], direction).get(edge.id)!;
  const start = anchors?.sourceAnchor ?? (direction === "LR"
    ? { x: source.position.x + source.width, y: source.position.y + source.height / 2 }
    : { x: source.position.x + source.width / 2, y: source.position.y + source.height });
  const end = anchors?.targetAnchor ?? (direction === "LR"
    ? { x: target.position.x, y: target.position.y + target.height / 2 }
    : { x: target.position.x + target.width / 2, y: target.position.y });
  const hashOrdinal = [...edge.id].reduce((total, char) => total + char.charCodeAt(0), 0) % 9 - 4;
  const routeSlot = "routeSlot" in edge ? Number(edge.routeSlot) : hashOrdinal + 4;
  const routeCount = "routeCount" in edge ? Number(edge.routeCount) : 9;
  const channelOffset = (routeSlot - (routeCount - 1) / 2) * 3;
  const middle = (direction === "LR" ? (start.x + end.x) / 2 : (start.y + end.y) / 2) + channelOffset;
  const direct = direction === "LR"
    ? [start, { x: middle, y: start.y }, { x: middle, y: end.y }, end]
    : [start, { x: start.x, y: middle }, { x: end.x, y: middle }, end];
  if (!architectureRouteCrossesUnrelatedNode(direct, nodes, edge.source, edge.target)) return direct;

  const detourOrdinal = routeSlot;
  if (direction === "LR") {
    const above = Math.min(...nodes.map(node => node.position.y)) - 24 - detourOrdinal * 4;
    const below = Math.max(...nodes.map(node => node.position.y + node.height)) + 24 + detourOrdinal * 4;
    const stubStart = start.x + 14 + detourOrdinal * 1.5; const stubEnd = end.x - 14 - detourOrdinal * 1.5;
    for (const y of [above, below]) {
      const route = [start, { x: stubStart, y: start.y }, { x: stubStart, y }, { x: stubEnd, y }, { x: stubEnd, y: end.y }, end];
      if (!architectureRouteCrossesUnrelatedNode(route, nodes, edge.source, edge.target)) return route;
    }
  } else {
    const left = Math.min(...nodes.map(node => node.position.x)) - 24 - detourOrdinal * 4;
    const right = Math.max(...nodes.map(node => node.position.x + node.width)) + 24 + detourOrdinal * 4;
    const stubStart = start.y + 14 + detourOrdinal * 1.5; const stubEnd = end.y - 14 - detourOrdinal * 1.5;
    for (const x of [left, right]) {
      const route = [start, { x: start.x, y: stubStart }, { x, y: stubStart }, { x, y: stubEnd }, { x: end.x, y: stubEnd }, end];
      if (!architectureRouteCrossesUnrelatedNode(route, nodes, edge.source, edge.target)) return route;
    }
  }
  return direct;
}

export function architectureSharedCollinearLength(left: ArchitecturePoint[], right: ArchitecturePoint[]) {
  let longest = 0;
  for (let leftIndex = 1; leftIndex < left.length; leftIndex += 1) {
    const a = left[leftIndex - 1]; const b = left[leftIndex];
    for (let rightIndex = 1; rightIndex < right.length; rightIndex += 1) {
      const c = right[rightIndex - 1]; const d = right[rightIndex];
      if (a.x === b.x && c.x === d.x && a.x === c.x) {
        longest = Math.max(longest, Math.max(0, Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y))
          - Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y))));
      } else if (a.y === b.y && c.y === d.y && a.y === c.y) {
        longest = Math.max(longest, Math.max(0, Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x))
          - Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x))));
      }
    }
  }
  return longest;
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
      data: { node: nodeById.get(id)!, laneId: lane.id, laneLabel: lane.label, ...ports, incomingPorts: [], outgoingPorts: [] } };
  });
  const laneBoxes = view.layout_hints
    ? applySemanticArchitectureLayout(nodes, view, visibleEdges, rankdir)
    : separateArchitectureLanes(nodes, view.lanes, rankdir);
  const edgeShapes = visibleEdges.map(item => ({ ...item, source: item.from, target: item.to }));
  const anchorByEdge = architectureEdgeAnchors(nodes, edgeShapes, rankdir);
  const routeOrder = [...edgeShapes].map(edge => edge.id).sort();
  const edges: ArchitectureGraphEdge[] = edgeShapes.map(item => ({
    ...item, ...anchorByEdge.get(item.id)!, routeSlot: routeOrder.indexOf(item.id), routeCount: routeOrder.length,
  }));
  nodes.forEach(node => {
    node.data.incomingPorts = edges.filter(edge => edge.target === node.id).map(edge => ({
      edgeId: edge.id,
      offset: rankdir === "LR" ? edge.targetAnchor.y - node.position.y : edge.targetAnchor.x - node.position.x,
      anchor: edge.targetAnchor,
    }));
    node.data.outgoingPorts = edges.filter(edge => edge.source === node.id).map(edge => ({
      edgeId: edge.id,
      offset: rankdir === "LR" ? edge.sourceAnchor.y - node.position.y : edge.sourceAnchor.x - node.position.x,
      anchor: edge.sourceAnchor,
    }));
  });
  return { view, nodes, laneBoxes, edges, direction: rankdir };
}
