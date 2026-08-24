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
EDGE_KINDS = {"DATA", "READ", "WRITE", "CONTROL", "MODEL", "MIRROR", "OPTIONAL", "DEPENDENCY"}
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
CANONICAL_PACKAGE_DEPENDENCIES = {
    "foundational": set(),
    "ai": {"foundational"},
    "evidence": {"foundational", "ai"},
    "news": {"foundational", "ai", "evidence"},
    "training": {"foundational", "ai", "evidence", "news"},
    "decision": {"foundational", "ai", "evidence", "news", "training"},
    "runtime": {"foundational"},
    "assistant": {"foundational", "ai", "evidence", "news"},
    "dashboard": {"foundational", "ai", "evidence", "news", "training", "decision", "runtime", "assistant"},
}
REQUIRED_FAILURE_IMPACTS = {
    "training", "cloudflare", "decision", "evidence", "news", "dashboard-sync", "d1", "control-plane",
}
SECRET_PATTERN = re.compile(
    r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|(?:api|secret|token|password)[_-]?key\s*[:=])",
    re.IGNORECASE,
)
NODE_FIELDS = [
    "id", "label", "short_label", "kind", "runtime_state", "implementation_state",
    "owner", "summary", "architecture", "code_paths", "test_paths", "document_paths",
    "tags", "purpose", "subsystem_view",
]
EDGE_FIELDS = ["id", "from", "to", "label", "kind", "criticality", "description"]


def _validate_layout_hints(view_id: str, hints: Any, node_set: set[str]) -> list[str]:
    if hints is None:
        return []
    owner = f"{view_id}: layout_hints"
    if not isinstance(hints, dict):
        return [f"{owner} must be an object"]
    errors: list[str] = []
    absolute_fields = {"x", "y", "position", "coordinate", "coordinates"}

    def find_absolute(value: Any) -> bool:
        if isinstance(value, dict):
            return any(str(key).lower() in absolute_fields or find_absolute(item) for key, item in value.items())
        if isinstance(value, list):
            return any(find_absolute(item) for item in value)
        return False

    if find_absolute(hints):
        errors.append(f"{owner} must not contain absolute coordinate fields")
    if set(hints) - {"mode", "rank_groups", "track_groups", "convergences", "auto_place_unlisted"}:
        errors.append(f"{owner} contains unsupported fields")
    if hints.get("mode") != "SEMANTIC_GRID":
        errors.append(f"{owner} has an invalid semantic mode")
    if hints.get("auto_place_unlisted") is not True:
        errors.append(f"{owner} must auto-place unlisted nodes")
    rank_groups = hints.get("rank_groups")
    track_groups = hints.get("track_groups")
    convergences = hints.get("convergences")
    if not all(isinstance(value, list) for value in (rank_groups, track_groups, convergences)):
        return errors + [f"{owner} groups and convergences must be lists"]
    if not rank_groups and not track_groups and not convergences:
        errors.append(f"{owner} semantic mode has no usable groups")

    all_groups = rank_groups + track_groups
    group_ids = [group.get("id") for group in all_groups if isinstance(group, dict)]
    if len(group_ids) != len(set(group_ids)):
        errors.append(f"{owner} has duplicate semantic group IDs")
    memberships: dict[str, dict[str, str]] = {"rank": {}, "track": {}}
    for family_name, groups in (("rank", rank_groups), ("track", track_groups)):
        for group in groups:
            if not isinstance(group, dict) or set(group) != {"id", "node_ids"}:
                errors.append(f"{owner} has an invalid {family_name} group")
                continue
            group_id = group.get("id")
            members = group.get("node_ids")
            if (not isinstance(group_id, str) or not group_id.strip() or not isinstance(members, list) or not members
                    or not all(isinstance(node_id, str) for node_id in members)):
                errors.append(f"{owner} has an invalid {family_name} group")
                continue
            if len(members) != len(set(members)):
                errors.append(f"{owner} {group_id} has duplicate node IDs")
            for node_id in members:
                if node_id not in node_set:
                    errors.append(f"{owner} {group_id} references unknown node {node_id}")
                if node_id in memberships[family_name]:
                    errors.append(f"{owner} node {node_id} belongs to multiple {family_name} groups")
                memberships[family_name][node_id] = group_id

    occupied: dict[tuple[str, str], str] = {}
    for node_id in node_set:
        if node_id not in memberships["rank"] or node_id not in memberships["track"]:
            continue
        cell = (memberships["rank"][node_id], memberships["track"][node_id])
        if cell in occupied:
            errors.append(f"{owner} has contradictory constraints for {occupied[cell]} and {node_id}")
        occupied[cell] = node_id

    convergence_tracks: set[str] = set()
    convergence_targets: set[str] = set()
    for convergence in convergences:
        if not isinstance(convergence, dict) or set(convergence) != {"target", "sources"}:
            errors.append(f"{owner} has an invalid convergence")
            continue
        target = convergence.get("target")
        sources = convergence.get("sources")
        if not isinstance(target, str) or target not in node_set:
            errors.append(f"{owner} convergence target is missing from the view")
        elif target in convergence_targets:
            errors.append(f"{owner} has contradictory convergence targets")
        else:
            convergence_targets.add(target)
        if not isinstance(sources, list) or len(sources) < 2 or not all(isinstance(source, str) for source in sources):
            errors.append(f"{owner} convergence must have at least two sources")
            continue
        if len(sources) != len(set(sources)) or target in sources:
            errors.append(f"{owner} convergence has contradictory sources")
        for source in sources:
            if not isinstance(source, str) or source not in node_set:
                errors.append(f"{owner} convergence source {source} is missing from the view")
        target_track = memberships["track"].get(target)
        if target_track and (
            target_track in convergence_tracks
            or any(memberships["track"].get(source) == target_track for source in sources)
        ):
            errors.append(f"{owner} convergence contradicts track {target_track}")
        if target_track:
            convergence_tracks.add(target_track)
    return errors


def expand_compact_manifest(source: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(source)
    for row_key, field_key, expected_fields in (
        ("nodes", "node_fields", NODE_FIELDS),
        ("edges", "edge_fields", EDGE_FIELDS),
    ):
        fields = manifest.get(field_key)
        rows = manifest.get(row_key)
        if fields != expected_fields or not isinstance(rows, list):
            raise ValueError(f"invalid compact {row_key} contract")
        if any(not isinstance(row, list) or len(row) != len(fields) for row in rows):
            raise ValueError(f"invalid compact {row_key} row width")
        manifest[row_key] = [dict(zip(fields, row, strict=True)) for row in rows]
    edges = manifest["edges"]
    views = manifest.get("views")
    scenarios = manifest.get("scenarios")
    if not isinstance(views, list) or not isinstance(scenarios, list):
        raise ValueError("invalid compact graph contract")
    view_metadata = manifest.get("view_metadata")
    if not isinstance(view_metadata, list) or len(view_metadata) != len(views):
        raise ValueError("invalid compact view metadata contract")
    manifest["views"] = []
    role_codes = {"O": "OVERVIEW", "S": "SUBSYSTEM", "A": "ADVANCED", "C": "CAMPAIGN"}
    audience_codes = {"B": "BEGINNER", "A": "ADVANCED"}
    disclosure_codes = {"P": "PRIMARY_PATH", "V": "VIEW_RELATIONSHIPS", "N": "SELECTED_NODE", "K": "SELECTED_PACKAGE"}
    for view_index, view in enumerate(views):
        lanes = view.get("lanes") if isinstance(view, dict) else None
        if not isinstance(lanes, list) or any(not isinstance(lane.get("node_ids"), list) for lane in lanes if isinstance(lane, dict)):
            raise ValueError("invalid compact view lanes")
        if any(not isinstance(lane, dict) for lane in lanes):
            raise ValueError("invalid compact view lane")
        layout_hints = view.get("layout_hints")
        if isinstance(layout_hints, list):
            if len(layout_hints) != 5:
                raise ValueError("invalid compact semantic layout contract")
            mode, rank_rows, track_rows, convergence_rows, auto_place = layout_hints
            if any(not isinstance(rows, list) for rows in (rank_rows, track_rows, convergence_rows)):
                raise ValueError("invalid compact semantic layout rows")
            if any(not isinstance(row, list) or len(row) != 2 for rows in (rank_rows, track_rows, convergence_rows) for row in rows):
                raise ValueError("invalid compact semantic layout row width")
            layout_hints = {
                "mode": mode,
                "rank_groups": [{"id": row[0], "node_ids": row[1]} for row in rank_rows],
                "track_groups": [{"id": row[0], "node_ids": row[1]} for row in track_rows],
                "convergences": [{"target": row[0], "sources": row[1]} for row in convergence_rows],
                "auto_place_unlisted": auto_place,
            }
        metadata = view_metadata[view_index]
        if not isinstance(metadata, list) or len(metadata) != 7:
            raise ValueError("invalid compact view metadata row")
        role, audience, parent_index, default_mode, always_rows, secondary_rows, allow_show_all = metadata
        if role not in role_codes or audience not in audience_codes or default_mode not in disclosure_codes:
            raise ValueError("invalid compact view metadata enum")
        if parent_index is not None and (not isinstance(parent_index, int) or parent_index < 0 or parent_index >= len(views)):
            raise ValueError("invalid compact parent view")
        if not isinstance(always_rows, list) or not isinstance(secondary_rows, list) or not isinstance(allow_show_all, bool):
            raise ValueError("invalid compact disclosure contract")
        def expand_edge_rows(rows: list[Any], excluded: list[str] | None = None) -> list[str]:
            expanded: list[str] = []
            for edge_id in rows:
                if edge_id == "$all":
                    expanded.extend(view.get("edge_ids", []))
                elif edge_id == "$rest":
                    expanded.extend(item for item in view.get("edge_ids", []) if item not in (excluded or []))
                elif edge_id == "$primary":
                    for left, right in zip(view.get("primary_path", []), view.get("primary_path", [])[1:]):
                        matches = [edge["id"] for edge in edges if edge.get("id") in view.get("edge_ids", []) and edge.get("from") == left and edge.get("to") == right]
                        if len(matches) != 1:
                            raise ValueError("ambiguous compact view primary path")
                        expanded.append(matches[0])
                elif isinstance(edge_id, str):
                    expanded.append(edge_id)
                else:
                    raise ValueError("invalid compact disclosure edge")
            return list(dict.fromkeys(expanded))
        always_edges = expand_edge_rows(always_rows)
        expanded_view = {
            **view,
            "node_ids": [node_id for lane in lanes for node_id in lane["node_ids"]],
            "navigation": {
                "role": role_codes[role],
                "audience": audience_codes[audience],
                **({"parent_view": views[parent_index]["id"]} if parent_index is not None else {}),
            },
            "disclosure": {
                "default_mode": disclosure_codes[default_mode],
                "always_visible_edge_ids": always_edges,
                "secondary_edge_ids": expand_edge_rows(secondary_rows, always_edges),
                "allow_show_all": allow_show_all,
            },
        }
        if layout_hints is not None:
            expanded_view["layout_hints"] = layout_hints
        manifest["views"].append(expanded_view)
    manifest["scenarios"] = []
    for scenario in scenarios:
        steps = scenario.get("steps") if isinstance(scenario, dict) else None
        if not isinstance(steps, list) or any(not isinstance(step, dict) or not isinstance(step.get("node_id"), str) for step in steps):
            raise ValueError("invalid compact scenario steps")
        node_ids = [step["node_id"] for step in steps]
        edge_ids = []
        for left, right in zip(node_ids, node_ids[1:]):
            matches = [edge["id"] for edge in edges if edge.get("from") == left and edge.get("to") == right]
            if len(matches) != 1:
                raise ValueError("ambiguous compact scenario path")
            edge_ids.append(matches[0])
        manifest["scenarios"].append({**scenario, "node_ids": node_ids, "edge_ids": edge_ids})
    manifest.pop("node_fields", None)
    manifest.pop("edge_fields", None)
    manifest.pop("view_metadata", None)
    return manifest


def load_manifest(root: Path) -> tuple[dict[str, Any], int]:
    path = root / "architecture" / "manifest.json"
    raw = path.read_bytes()
    return expand_compact_manifest(json.loads(raw)), len(raw)


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
        for field in ("owner", "summary", "purpose"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                errors.append(f"{node_id}: {field} must be a non-empty string")
        architecture = node.get("architecture")
        if not isinstance(architecture, dict) or set(architecture) != ARCHITECTURE_FIELDS:
            errors.append(f"{node_id}: architecture must contain all six dimensions")
        elif any(not isinstance(architecture[key], str) or not architecture[key].strip() for key in ARCHITECTURE_FIELDS):
            errors.append(f"{node_id}: architecture dimensions must be non-empty strings")
        for field in ("code_paths", "test_paths", "document_paths", "tags"):
            if not isinstance(node.get(field), list):
                errors.append(f"{node_id}: {field} must be a list")
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
        navigation = view.get("navigation")
        disclosure = view.get("disclosure")
        if not isinstance(navigation, dict) or navigation.get("role") not in {"OVERVIEW", "SUBSYSTEM", "ADVANCED", "CAMPAIGN"} \
                or navigation.get("audience") not in {"BEGINNER", "ADVANCED"} \
                or (navigation.get("parent_view") is not None and navigation.get("parent_view") not in view_ids):
            errors.append(f"{view_id}: invalid navigation metadata")
        if not isinstance(disclosure, dict) or disclosure.get("default_mode") not in {"PRIMARY_PATH", "VIEW_RELATIONSHIPS", "SELECTED_NODE", "SELECTED_PACKAGE"} \
                or not isinstance(disclosure.get("always_visible_edge_ids"), list) \
                or not isinstance(disclosure.get("secondary_edge_ids"), list) \
                or not isinstance(disclosure.get("allow_show_all"), bool):
            errors.append(f"{view_id}: invalid disclosure metadata")
        if view.get("layout_direction") not in LAYOUT_DIRECTIONS:
            errors.append(f"{view_id}: invalid layout direction")
        if view.get("relationship_note") is not None and (not isinstance(view["relationship_note"], str) or not view["relationship_note"].strip()):
            errors.append(f"{view_id}: relationship_note must be a non-empty string")
        if view.get("prohibited_directions") is not None and (
            not isinstance(view["prohibited_directions"], list)
            or not all(isinstance(item, str) and item.strip() for item in view["prohibited_directions"])
        ):
            errors.append(f"{view_id}: prohibited_directions must contain non-empty strings")
        view_nodes = view.get("node_ids")
        view_edges = view.get("edge_ids")
        if not isinstance(view_nodes, list) or not isinstance(view_edges, list):
            errors.append(f"{view_id}: node_ids and edge_ids must be lists")
            continue
        node_set = set(view_nodes)
        edge_set = set(view_edges)
        if isinstance(disclosure, dict):
            disclosed = disclosure.get("always_visible_edge_ids", []) + disclosure.get("secondary_edge_ids", [])
            if len(disclosed) != len(set(disclosed)) or any(edge_id not in edge_set for edge_id in disclosed):
                errors.append(f"{view_id}: disclosure edges must be unique view edges")
        errors.extend(_validate_layout_hints(view_id, view.get("layout_hints"), node_set))
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

    overview_views = [view for view in views if isinstance(view, dict) and view.get("navigation", {}).get("role") == "OVERVIEW"]
    if len(overview_views) != 1 or overview_views[0].get("id") != "system-overview" \
            or overview_views[0].get("navigation", {}).get("audience") != "BEGINNER":
        errors.append("views must contain exactly one beginner System Overview")
    expected_taxonomy = {
        "decision-evidence": ("SUBSYSTEM", "BEGINNER"), "training-models": ("SUBSYSTEM", "BEGINNER"),
        "news-ai": ("SUBSYSTEM", "BEGINNER"), "dashboard-sync": ("SUBSYSTEM", "BEGINNER"),
        "web-cloudflare": ("SUBSYSTEM", "BEGINNER"), "assistant": ("SUBSYSTEM", "BEGINNER"),
        "execution-topology": ("ADVANCED", "ADVANCED"), "package-dependencies": ("ADVANCED", "ADVANCED"),
        "runtime-release": ("ADVANCED", "ADVANCED"), "modularization-campaign": ("CAMPAIGN", "ADVANCED"),
    }
    for view_id, expected in expected_taxonomy.items():
        navigation = view_by_id.get(view_id, {}).get("navigation", {})
        if (navigation.get("role"), navigation.get("audience")) != expected:
            errors.append(f"{view_id}: navigation taxonomy must be {expected!r}")

    package_view = view_by_id.get("package-dependencies", {})
    expected_package_nodes = {f"package-{name}" for name in CANONICAL_PACKAGE_DEPENDENCIES}
    expected_dependency_pairs = {
        (f"package-{source}", f"package-{target}")
        for source, targets in CANONICAL_PACKAGE_DEPENDENCIES.items()
        for target in targets
    }
    dependency_edges = [edge for edge in edges if isinstance(edge, dict) and edge.get("kind") == "DEPENDENCY"]
    actual_dependency_pairs = {(edge.get("from"), edge.get("to")) for edge in dependency_edges}
    if set(package_view.get("node_ids", [])) != expected_package_nodes:
        errors.append("package-dependencies: canonical package nodes do not match contract")
    if set(package_view.get("edge_ids", [])) != {edge.get("id") for edge in dependency_edges}:
        errors.append("package-dependencies: view must contain every dependency edge and no runtime edge")
    if actual_dependency_pairs != expected_dependency_pairs:
        errors.append("package-dependencies: dependency directions do not match contract")
    if package_view.get("relationship_note") != "A → B means A may import or depend on B.":
        errors.append("package-dependencies: import direction explanation is missing")
    if not package_view.get("prohibited_directions"):
        errors.append("package-dependencies: prohibited reverse directions are missing")

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
    if not REQUIRED_FAILURE_IMPACTS.issubset(impact_ids):
        errors.append(f"missing required failure impacts: {sorted(REQUIRED_FAILURE_IMPACTS - impact_ids)!r}")
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
        if any("AFFECTED" not in entry.get("message", "") for entry in affected if isinstance(entry, dict)):
            errors.append(f"{impact.get('node_id')}: affected messages must be explicit")
        if any("CONTINUES" not in entry.get("message", "") for entry in continues if isinstance(entry, dict)):
            errors.append(f"{impact.get('node_id')}: continues messages must be explicit")
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
    except (OSError, json.JSONDecodeError, ValueError) as error:
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
