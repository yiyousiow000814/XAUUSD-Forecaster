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
  manifest.views = manifest.views.map(value => {
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
    return { ...view, node_ids: nodeIds, ...(layoutHints === undefined ? {} : { layout_hints: layoutHints }) };
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
  return manifest;
}

export function loadArchitectureManifest(root = resolve("..")) {
  const path = resolve(root, "architecture/manifest.json");
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
