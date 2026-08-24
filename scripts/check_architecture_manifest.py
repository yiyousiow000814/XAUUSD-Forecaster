"""Validate the bounded Architecture Explorer manifest using stdlib only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "architecture-explorer-v1"
KINDS = {
    "SUBSYSTEM", "PROCESS", "THREAD", "CONTROL", "WORKER",
    "REQUEST_HANDLER", "STORE", "STATIC", "COMPONENT", "EXTERNAL",
}
RUNTIME_STATES = {"CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"}
IMPLEMENTATION_STATES = {"CURRENT_PATH", "PENDING_PATH", "LEGACY_SHIM", "TARGET_PATH"}
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

    views = manifest.get("views")
    if not isinstance(views, list):
        errors.append("views must be a list")
        views = []
    view_ids = {view.get("id") for view in views if isinstance(view, dict)}
    for view in views:
        view_id = view.get("id", "<missing>")
        if view.get("drill_down") not in view_ids:
            errors.append(f"{view_id}: missing drill-down view {view.get('drill_down')}")
        for node_id in view.get("node_ids", []):
            if node_id not in known_nodes:
                errors.append(f"{view_id}: missing view node {node_id}")

    for edge in manifest.get("edges", []):
        if edge.get("from") not in known_nodes or edge.get("to") not in known_nodes:
            errors.append(f"bad edge endpoint: {edge!r}")

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
        f"{len(manifest['views'])} views, {size} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
