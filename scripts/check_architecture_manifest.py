"""Validate the bounded Architecture Explorer manifest using stdlib only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "architecture-explorer-v2"
KINDS = {
    "SUBSYSTEM", "PROCESS", "THREAD", "CONTROL", "WORKER",
    "REQUEST_HANDLER", "STORE", "STATIC", "COMPONENT", "EXTERNAL",
}
RUNTIME_STATES = {"CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"}
IMPLEMENTATION_STATES = {"CURRENT_PATH", "PENDING_PATH", "LEGACY_SHIM", "TARGET_PATH"}
EDGE_KINDS = {"DATA", "READ", "WRITE", "CONTROL", "MODEL", "MIRROR", "OPTIONAL"}
EDGE_CRITICALITIES = {"CRITICAL", "BACKGROUND", "OPTIONAL", "CONTROL_PLANE"}
LAYOUT_DIRECTIONS = {"LR", "TB"}
ARCHITECTURE_FIELDS = {
    "ownership", "boundary", "critical_path", "bounded_work",
    "incremental", "failure_isolation",
}
CAMPAIGN_ORDER = [
    "latest-main", "phase-c", "phase-d", "phase-e",
    "architecture-explorer", "closure",
]
SECRET_PATTERN = re.compile(
    r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|(?:api|secret|token|password)[_-]?key\s*[:=])",
    re.IGNORECASE,
)


def load_manifest(root: Path) -> tuple[dict[str, Any], int]:
    path = root / "architecture" / "manifest.json"
    raw = path.read_bytes()
    return json.loads(raw), len(raw)


def validate_manifest(root: Path, manifest: dict[str, Any], byte_size: int) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    limit = manifest.get("byte_limit")
    if not isinstance(limit, int) or limit <= 0 or byte_size > limit:
        errors.append(f"manifest byte size {byte_size} exceeds valid limit {limit!r}")
    serialized = json.dumps(manifest, ensure_ascii=False)
    if SECRET_PATTERN.search(serialized):
        errors.append("manifest contains a secret-looking value")

    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        return errors + ["nodes must be a list"]
    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(set(node_ids)):
        errors.append("duplicate node id")
    known_nodes = set(node_ids)
    migrations = {
        item.get("node_id") for item in manifest.get("migration_map", [])
        if isinstance(item, dict)
    }
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("node must be an object")
            continue
        node_id = node.get("id", "<missing>")
        if node.get("kind") not in KINDS:
            errors.append(f"{node_id}: invalid kind {node.get('kind')!r}")
        if node.get("runtime_state") not in RUNTIME_STATES:
            errors.append(f"{node_id}: invalid runtime_state {node.get('runtime_state')!r}")
        if node.get("implementation_state") not in IMPLEMENTATION_STATES:
            errors.append(f"{node_id}: invalid implementation_state {node.get('implementation_state')!r}")
        architecture = node.get("architecture")
        if not isinstance(architecture, dict) or set(architecture) != ARCHITECTURE_FIELDS:
            errors.append(f"{node_id}: architecture must contain all six dimensions")
        elif any(not isinstance(architecture[key], str) or not architecture[key].strip() for key in ARCHITECTURE_FIELDS):
            errors.append(f"{node_id}: architecture dimensions must be non-empty strings")
        for field in ("inputs", "outputs", "code_paths", "test_paths", "document_paths", "tags"):
            if not isinstance(node.get(field), list):
                errors.append(f"{node_id}: {field} must be a list")
        for endpoint in [*node.get("inputs", []), *node.get("outputs", [])]:
            if endpoint not in known_nodes:
                errors.append(f"{node_id}: missing input/output endpoint {endpoint}")
        for path_value in node.get("code_paths", []):
            if path_value.startswith(("tests/", "web/tests/")):
                errors.append(f"{node_id}: code path is a test path: {path_value}")
            if node.get("implementation_state") in {"CURRENT_PATH", "PENDING_PATH"} and not (root / path_value).exists():
                errors.append(f"{node_id}: missing code path {path_value}")
        for path_value in node.get("test_paths", []):
            if not path_value.startswith(("tests/", "web/tests/")):
                errors.append(f"{node_id}: invalid test path {path_value}")
            if node.get("implementation_state") in {"CURRENT_PATH", "PENDING_PATH"} and not (root / path_value).exists():
                errors.append(f"{node_id}: missing test path {path_value}")
        for path_value in node.get("document_paths", []):
            if not (root / path_value).is_file():
                errors.append(f"{node_id}: missing document {path_value}")
        if node.get("implementation_state") == "LEGACY_SHIM" and node_id not in migrations:
            errors.append(f"{node_id}: legacy shim absent from migration_map")

    edges = manifest.get("edges")
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []
    edge_ids = [edge.get("id") for edge in edges if isinstance(edge, dict)]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("duplicate edge id")
    known_edges = {edge.get("id"): edge for edge in edges if isinstance(edge, dict)}
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("edge must be an object")
            continue
        edge_id = edge.get("id", "<missing>")
        if edge.get("from") not in known_nodes or edge.get("to") not in known_nodes:
            errors.append(f"bad edge endpoint: {edge!r}")
        if edge.get("kind") not in EDGE_KINDS:
            errors.append(f"{edge_id}: invalid edge kind {edge.get('kind')!r}")
        if edge.get("criticality") not in EDGE_CRITICALITIES:
            errors.append(f"{edge_id}: invalid edge criticality {edge.get('criticality')!r}")
        for field in ("id", "label", "description"):
            if not isinstance(edge.get(field), str) or not edge[field].strip():
                errors.append(f"{edge_id}: edge {field} must be a non-empty string")

    def validate_path(owner: str, node_path: Any, path_edges: Any, allowed_nodes: set[str], allowed_edges: set[str]) -> None:
        if not isinstance(node_path, list) or not node_path:
            errors.append(f"{owner}: path nodes must be a non-empty list")
            return
        if not isinstance(path_edges, list) or len(path_edges) != len(node_path) - 1:
            errors.append(f"{owner}: path edge count must equal node count minus one")
            return
        if any(node_id not in allowed_nodes for node_id in node_path):
            errors.append(f"{owner}: path references a node outside its scope")
        if any(edge_id not in allowed_edges for edge_id in path_edges):
            errors.append(f"{owner}: path references an edge outside its scope")
        for index, edge_id in enumerate(path_edges):
            edge = known_edges.get(edge_id)
            if edge and (edge.get("from"), edge.get("to")) != (node_path[index], node_path[index + 1]):
                errors.append(f"{owner}: path is not continuous at {edge_id}")

    views = manifest.get("views")
    if not isinstance(views, list):
        errors.append("views must be a list")
        views = []
    view_ids = {view.get("id") for view in views if isinstance(view, dict)}
    view_by_id = {view.get("id"): view for view in views if isinstance(view, dict)}
    for view in views:
        if not isinstance(view, dict):
            errors.append("view must be an object")
            continue
        view_id = view.get("id", "<missing>")
        if view.get("layout_direction") not in LAYOUT_DIRECTIONS:
            errors.append(f"{view_id}: invalid layout direction")
        view_nodes = view.get("node_ids")
        view_edges = view.get("edge_ids")
        if not isinstance(view_nodes, list) or not isinstance(view_edges, list):
            errors.append(f"{view_id}: node_ids and edge_ids must be lists")
            continue
        node_set = set(view_nodes)
        edge_set = set(view_edges)
        if len(node_set) != len(view_nodes) or len(edge_set) != len(view_edges):
            errors.append(f"{view_id}: duplicated view membership")
        for node_id in view_nodes:
            if node_id not in known_nodes:
                errors.append(f"{view_id}: missing view node {node_id}")
        for edge_id in view_edges:
            edge = known_edges.get(edge_id)
            if edge is None:
                errors.append(f"{view_id}: missing view edge {edge_id}")
            elif edge.get("from") not in node_set or edge.get("to") not in node_set:
                errors.append(f"{view_id}: edge {edge_id} leaves visible view nodes")
        incident = {
            endpoint for edge_id in view_edges for endpoint in
            ((known_edges.get(edge_id) or {}).get("from"), (known_edges.get(edge_id) or {}).get("to"))
            if endpoint is not None
        }
        if node_set - incident:
            errors.append(f"{view_id}: orphan view nodes {sorted(node_set - incident)!r}")
        if view.get("entry_node") not in node_set:
            errors.append(f"{view_id}: entry node must be visible")
        lanes = view.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            errors.append(f"{view_id}: lanes must be a non-empty list")
        else:
            lane_nodes = [node_id for lane in lanes if isinstance(lane, dict) for node_id in lane.get("node_ids", [])]
            if len(lane_nodes) != len(set(lane_nodes)) or set(lane_nodes) != node_set:
                errors.append(f"{view_id}: lane membership must cover each view node exactly once")
        primary_path = view.get("primary_path")
        primary_edges = []
        if isinstance(primary_path, list):
            for left, right in zip(primary_path, primary_path[1:]):
                match = next((edge_id for edge_id in view_edges if (known_edges.get(edge_id) or {}).get("from") == left and (known_edges.get(edge_id) or {}).get("to") == right), None)
                primary_edges.append(match)
        validate_path(f"{view_id} primary path", primary_path, primary_edges, node_set, edge_set)

    for item in nodes:
        if isinstance(item, dict) and item.get("subsystem_view") is not None and item.get("subsystem_view") not in view_ids:
            errors.append(f"{item.get('id')}: missing subsystem view {item.get('subsystem_view')}")

    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios must be a non-empty list")
        scenarios = []
    scenario_ids = [item.get("id") for item in scenarios if isinstance(item, dict)]
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("duplicate scenario id")
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            errors.append("scenario must be an object")
            continue
        scenario_id = scenario.get("id", "<missing>")
        if scenario.get("view_id") not in view_ids:
            errors.append(f"{scenario_id}: missing scenario view")
        scenario_nodes = scenario.get("node_ids")
        scenario_edges = scenario.get("edge_ids")
        validate_path(scenario_id, scenario_nodes, scenario_edges, known_nodes, set(known_edges))
        scenario_view = view_by_id.get(scenario.get("view_id"), {})
        if isinstance(scenario_nodes, list) and not set(scenario_nodes).issubset(set(scenario_view.get("node_ids", []))):
            errors.append(f"{scenario_id}: scenario nodes leave its view")
        if isinstance(scenario_edges, list) and not set(scenario_edges).issubset(set(scenario_view.get("edge_ids", []))):
            errors.append(f"{scenario_id}: scenario edges leave its view")
        steps = scenario.get("steps")
        if not isinstance(steps, list) or [step.get("node_id") for step in steps if isinstance(step, dict)] != scenario_nodes:
            errors.append(f"{scenario_id}: steps must match ordered scenario nodes")

    impacts = manifest.get("failure_impacts")
    if not isinstance(impacts, list) or not impacts:
        errors.append("failure_impacts must be a non-empty list")
        impacts = []
    impact_ids = {item.get("node_id") for item in impacts if isinstance(item, dict)}
    for impact in impacts:
        if not isinstance(impact, dict) or impact.get("node_id") not in known_nodes:
            errors.append(f"invalid failure impact owner: {impact!r}")
            continue
        affected = impact.get("affected")
        continues = impact.get("continues")
        if not isinstance(affected, list) or not isinstance(continues, list) or not affected or not continues:
            errors.append(f"{impact.get('node_id')}: failure impact needs affected and continues")
            continue
        references = [entry.get("node_id") for entry in [*affected, *continues] if isinstance(entry, dict)]
        if any(node_id not in known_nodes for node_id in references):
            errors.append(f"{impact.get('node_id')}: failure impact references missing node")
        if set(entry.get("node_id") for entry in affected) & set(entry.get("node_id") for entry in continues):
            errors.append(f"{impact.get('node_id')}: failure impact overlaps affected and continues")
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get("failure_node_id") is not None and scenario.get("failure_node_id") not in impact_ids:
            errors.append(f"{scenario.get('id')}: missing failure impact")

    campaign = manifest.get("campaign")
    campaign_ids = [item.get("id") for item in campaign] if isinstance(campaign, list) else []
    if campaign_ids != CAMPAIGN_ORDER:
        errors.append(f"campaign order must be {CAMPAIGN_ORDER!r}")
    if isinstance(campaign, list):
        for item in campaign:
            if item.get("state") != "PENDING" or not item.get("branch"):
                errors.append(f"stale campaign entry: {item!r}")
            if not isinstance(item.get("pr"), int):
                errors.append(f"campaign PR is missing: {item!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        manifest, size = load_manifest(root)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Architecture manifest invalid: {error}")
        return 1
    errors = validate_manifest(root, manifest, size)
    if errors:
        for error in errors:
            print(f"Architecture manifest invalid: {error}")
        return 1
    print(
        "Architecture manifest passed: "
        f"{len(manifest['nodes'])} nodes, {len(manifest['edges'])} edges, "
        f"{len(manifest['views'])} views, {len(manifest['scenarios'])} scenarios, {size} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
