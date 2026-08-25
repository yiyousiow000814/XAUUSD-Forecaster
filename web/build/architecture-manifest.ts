import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const MANIFEST_LIMIT = 65_536;

function expandRows(manifest: Record<string, unknown>, rowKey: "nodes" | "edges", fieldKey: "node_fields" | "edge_fields") {
  const fields = manifest[fieldKey];
  const rows = manifest[rowKey];
  if (!Array.isArray(fields) || !fields.every(field => typeof field === "string") || !Array.isArray(rows)) {
    throw new Error(`Architecture manifest has invalid ${rowKey} rows`);
  }
  return rows.map(row => {
    if (!Array.isArray(row) || row.length !== fields.length) throw new Error(`Architecture manifest has invalid ${rowKey} row width`);
    return Object.fromEntries(fields.map((field, index) => [field, row[index]]));
  });
}

function expandArchitectureManifest(source: Record<string, unknown>) {
  const manifest = { ...source };
  manifest.nodes = expandRows(manifest, "nodes", "node_fields");
  manifest.edges = expandRows(manifest, "edges", "edge_fields");
  const edges = manifest.edges as Array<Record<string, unknown>>;
  if (!Array.isArray(manifest.views) || !Array.isArray(manifest.scenarios)) {
    throw new Error("Architecture manifest has an invalid build contract");
  }
  const compactViews = manifest.views;
  const viewMetadata = manifest.view_metadata;
  if (!Array.isArray(viewMetadata) || viewMetadata.length !== compactViews.length) {
    throw new Error("Architecture manifest has invalid compact view metadata");
  }
  const roleCodes = { O: "OVERVIEW", S: "SUBSYSTEM", A: "ADVANCED", C: "CAMPAIGN" } as const;
  const audienceCodes = { B: "BEGINNER", A: "ADVANCED" } as const;
  const disclosureCodes = { P: "PRIMARY_PATH", V: "VIEW_RELATIONSHIPS", N: "SELECTED_NODE", K: "SELECTED_PACKAGE" } as const;
  manifest.views = compactViews.map((value, viewIndex) => {
    if (!value || typeof value !== "object") throw new Error("Architecture manifest has an invalid view");
    const view = value as Record<string, unknown>;
    if (!Array.isArray(view.lanes)) throw new Error("Architecture manifest view has invalid lanes");
    const nodeIds = view.lanes.flatMap(lane => {
      if (!lane || typeof lane !== "object" || !Array.isArray((lane as Record<string, unknown>).node_ids)) {
        throw new Error("Architecture manifest view has invalid lane membership");
      }
      return (lane as Record<string, unknown>).node_ids as unknown[];
    });
    let layoutHints = view.layout_hints;
    if (Array.isArray(layoutHints)) {
      if (layoutHints.length !== 5) throw new Error("Architecture manifest view has invalid compact semantic layout");
      const [mode, rankRows, trackRows, convergenceRows, autoPlace] = layoutHints;
      const expandGroups = (rows: unknown, idKey: "id" | "target", membersKey: "node_ids" | "sources") => {
        if (!Array.isArray(rows) || rows.some(row => !Array.isArray(row) || row.length !== 2)) {
          throw new Error("Architecture manifest view has invalid compact semantic layout rows");
        }
        return rows.map(row => ({ [idKey]: row[0], [membersKey]: row[1] }));
      };
      layoutHints = {
        mode,
        rank_groups: expandGroups(rankRows, "id", "node_ids"),
        track_groups: expandGroups(trackRows, "id", "node_ids"),
        convergences: expandGroups(convergenceRows, "target", "sources"),
        auto_place_unlisted: autoPlace,
      };
    }
    const metadata = viewMetadata[viewIndex];
    if (!Array.isArray(metadata) || metadata.length !== 7) throw new Error("Architecture manifest has invalid compact view metadata row");
    const [role, audience, parentIndex, defaultMode, alwaysRows, secondaryRows, allowShowAll] = metadata;
    if (!(typeof role === "string" && role in roleCodes) || !(typeof audience === "string" && audience in audienceCodes)
        || !(typeof defaultMode === "string" && defaultMode in disclosureCodes)
        || !Array.isArray(alwaysRows) || !Array.isArray(secondaryRows) || typeof allowShowAll !== "boolean"
        || (parentIndex !== null && (!Number.isInteger(parentIndex) || Number(parentIndex) < 0 || Number(parentIndex) >= compactViews.length))) {
      throw new Error("Architecture manifest has invalid compact view metadata values");
    }
    const expandEdgeRows = (rows: unknown[], excluded: string[] = []) => {
      const expanded: string[] = [];
      for (const edgeId of rows) {
        if (edgeId === "$all") expanded.push(...view.edge_ids as string[]);
        else if (edgeId === "$rest") expanded.push(...(view.edge_ids as string[]).filter(id => !excluded.includes(id)));
        else if (edgeId === "$primary") for (let index = 0; index < (view.primary_path as string[]).length - 1; index += 1) {
          const from = (view.primary_path as string[])[index]; const to = (view.primary_path as string[])[index + 1];
          const matches = edges.filter(edge => (view.edge_ids as string[]).includes(edge.id as string) && edge.from === from && edge.to === to);
          if (matches.length !== 1) throw new Error("Architecture manifest primary path is ambiguous");
          expanded.push(matches[0].id as string);
        } else if (typeof edgeId === "string") expanded.push(edgeId);
        else throw new Error("Architecture manifest has invalid disclosure edge");
      }
      return [...new Set(expanded)];
    };
    const alwaysEdges = expandEdgeRows(alwaysRows);
    return { ...view, node_ids: nodeIds,
      navigation: { role: roleCodes[role as keyof typeof roleCodes], audience: audienceCodes[audience as keyof typeof audienceCodes],
        ...(parentIndex === null ? {} : { parent_view: (compactViews[Number(parentIndex)] as Record<string, unknown>).id }) },
      disclosure: { default_mode: disclosureCodes[defaultMode as keyof typeof disclosureCodes], always_visible_edge_ids: alwaysEdges,
        secondary_edge_ids: expandEdgeRows(secondaryRows, alwaysEdges), allow_show_all: allowShowAll },
      ...(layoutHints === undefined ? {} : { layout_hints: layoutHints }) };
  });
  manifest.scenarios = manifest.scenarios.map(value => {
    if (!value || typeof value !== "object") throw new Error("Architecture manifest has an invalid scenario");
    const scenario = value as Record<string, unknown>;
    if (!Array.isArray(scenario.steps)) throw new Error("Architecture manifest scenario has invalid steps");
    const nodeIds = scenario.steps.map(step => (step as Record<string, unknown>).node_id);
    const edgeIds = nodeIds.slice(0, -1).map((from, index) => {
      const matches = edges.filter(edge => edge.from === from && edge.to === nodeIds[index + 1]);
      if (matches.length !== 1) throw new Error("Architecture manifest scenario path is ambiguous");
      return matches[0].id;
    });
    return { ...scenario, node_ids: nodeIds, edge_ids: edgeIds };
  });
  delete manifest.node_fields;
  delete manifest.edge_fields;
  delete manifest.view_metadata;
  return manifest;
}

export function loadArchitectureManifest(root = resolve("..")) {
  const path = resolve(root, "architecture/generated/explorer-manifest.json");
  const raw = readFileSync(path, "utf8");
  if (new TextEncoder().encode(raw).byteLength > MANIFEST_LIMIT) {
    throw new Error(`Architecture manifest exceeds ${MANIFEST_LIMIT} bytes`);
  }
  const manifest = expandArchitectureManifest(JSON.parse(raw) as Record<string, unknown>);
  if (manifest.schema !== "architecture-explorer-v2"
      || !Array.isArray(manifest.nodes)
      || !Array.isArray(manifest.edges)
      || !Array.isArray(manifest.views)
      || !Array.isArray(manifest.scenarios)
      || !Array.isArray(manifest.failure_impacts)) {
    throw new Error("Architecture manifest has an invalid build contract");
  }
  return manifest;
}
