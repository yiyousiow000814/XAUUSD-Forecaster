"""Receipt-backed migration of model locators into the runtime state root."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_paths import canonicalize_artifact_path, require_runtime_artifact_path
from .forward_ledger import canonical_hash
from .ridge import RidgeArtifact


UTC = timezone.utc
SCHEMA = "xauusd.runtime-artifact-path-migration.v1"
MIGRATION_VERSION = "runtime-artifact-path-v1"


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _artifact_hash(identity: str, path: Path) -> str:
    if identity in {"FULL", "BROAD_FULL"}:
        return canonical_hash(json.loads(path.read_text(encoding="utf-8")))
    return RidgeArtifact.read(path).artifact_hash


def _database_identity(connection: sqlite3.Connection, database: Path) -> dict:
    stat = database.stat()
    metadata = {
        "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "application_id": int(connection.execute("PRAGMA application_id").fetchone()[0]),
    }
    epoch = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    return {
        "path": str(database.resolve()),
        "device": int(stat.st_dev),
        "file_id": int(stat.st_ino),
        "forward_epoch": str(epoch[0]) if epoch else None,
        **metadata,
    }


def _assert_database_authority(
    connection: sqlite3.Connection, receipt: dict,
) -> None:
    expected = receipt["database_identity"]
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None:
        raise RuntimeError("ARTIFACT_PATH_MIGRATION_DATABASE_UNAVAILABLE")
    database = Path(str(row[2])).resolve()
    observed = _database_identity(connection, database)
    for field in ("path", "device", "file_id", "forward_epoch"):
        if observed[field] != expected[field]:
            raise RuntimeError(
                f"ARTIFACT_PATH_MIGRATION_DATABASE_IDENTITY_MISMATCH:{field}"
            )


def _record(
    *, table: str, model_version: str, model_identity: str,
    old_value: str, new_value: str, stored_hash: str,
    observed_hash: str, source_family: str, disposition: str,
) -> dict:
    return {
        "table": table,
        "model_version": model_version,
        "model_identity": model_identity,
        "old_value": old_value,
        "new_value": new_value,
        "stored_artifact_hash": stored_hash,
        "target_observed_hash": observed_hash,
        "source_family": source_family,
        "disposition": disposition,
    }


def build_artifact_path_migration_plan(
    connection: sqlite3.Connection,
    *,
    database: Path,
    runtime_forward_root: Path,
) -> dict:
    connection.row_factory = sqlite3.Row
    active = connection.execute(
        """SELECT generation_id FROM news_model_generation_activations_v1
        ORDER BY activated_at DESC,activation_id DESC LIMIT 1"""
    ).fetchone()
    if active is None:
        raise RuntimeError("ACTIVE_GENERATION_MISSING")
    active_generation = str(active["generation_id"])
    active_versions = {
        str(row["model_version"])
        for row in connection.execute(
            """SELECT model_version FROM news_model_generation_members_v1
            WHERE generation_id=? UNION ALL
            SELECT model_version FROM news_model_generation_aux_members_v1
            WHERE generation_id=?""",
            (active_generation, active_generation),
        )
    }
    records: list[dict] = []
    manifest_locators: list[dict] = []
    for row in connection.execute(
        """SELECT model_version,model_identity,artifact_path,artifact_hash
        FROM model_updates_v2 ORDER BY model_version"""
    ):
        resolution = canonicalize_artifact_path(
            row["artifact_path"], runtime_forward_root=runtime_forward_root,
        )
        target = require_runtime_artifact_path(
            row["artifact_path"], runtime_forward_root=runtime_forward_root,
        )
        observed_hash = _artifact_hash(str(row["model_identity"]), target)
        if observed_hash != str(row["artifact_hash"]):
            raise RuntimeError(f"ARTIFACT_HASH_MISMATCH:{row['model_version']}")
        active_required = str(row["model_version"]) in active_versions
        disposition = (
            "ALREADY_CANONICAL"
            if resolution.source_family == "ALREADY_CANONICAL"
            else "ACTIVE_REQUIRED_MAPPED"
            if active_required
            else "RETAINED_HISTORICAL_MAPPED"
        )
        records.append(_record(
            table="model_updates_v2", model_version=str(row["model_version"]),
            model_identity=str(row["model_identity"]),
            old_value=str(row["artifact_path"]), new_value=str(target),
            stored_hash=str(row["artifact_hash"]), observed_hash=observed_hash,
            source_family=resolution.source_family, disposition=disposition,
        ))
        if str(row["model_identity"]) in {"FULL", "BROAD_FULL"}:
            manifest = json.loads(target.read_text(encoding="utf-8"))
            for field in ("market_artifact_path", "news_artifact_path"):
                child = require_runtime_artifact_path(
                    manifest[field], runtime_forward_root=runtime_forward_root,
                )
                child_resolution = canonicalize_artifact_path(
                    manifest[field], runtime_forward_root=runtime_forward_root,
                )
                hash_field = field.replace("_path", "_hash")
                expected_child_hash = str(manifest.get(hash_field) or "")
                observed_child_hash = RidgeArtifact.read(child).artifact_hash
                if not expected_child_hash or observed_child_hash != expected_child_hash:
                    raise RuntimeError(
                        f"MANIFEST_CHILD_HASH_MISMATCH:{row['model_version']}:{field}"
                    )
                manifest_locators.append({
                    "model_version": str(row["model_version"]),
                    "field": field,
                    "immutable_manifest_path": str(target),
                    "original": str(manifest[field]),
                    "resolved": str(child),
                    "expected_artifact_hash": expected_child_hash,
                    "source_family": child_resolution.source_family,
                    "ownership": "IMMUTABLE_MANIFEST_RESOLVED_AT_RUNTIME",
                })
    if len(active_versions) != 6 or sum(
        record["disposition"] == "ACTIVE_REQUIRED_MAPPED"
        or (
            record["disposition"] == "ALREADY_CANONICAL"
            and record["model_version"] in active_versions
        )
        for record in records
    ) != 6:
        raise RuntimeError("ACTIVE_GENERATION_INCOMPLETE")

    for row in connection.execute(
        """SELECT model_version,model_identity,artifact_path,artifact_hash
        FROM execution_model_updates_v1 ORDER BY model_version"""
    ):
        resolution = canonicalize_artifact_path(
            row["artifact_path"], runtime_forward_root=runtime_forward_root,
        )
        target = require_runtime_artifact_path(
            row["artifact_path"], runtime_forward_root=runtime_forward_root,
        )
        observed_hash = RidgeArtifact.read(target).artifact_hash
        if observed_hash != str(row["artifact_hash"]):
            raise RuntimeError(f"ARTIFACT_HASH_MISMATCH:{row['model_version']}")
        records.append(_record(
            table="execution_model_updates_v1",
            model_version=str(row["model_version"]),
            model_identity=str(row["model_identity"]),
            old_value=str(row["artifact_path"]), new_value=str(target),
            stored_hash=str(row["artifact_hash"]), observed_hash=observed_hash,
            source_family=resolution.source_family,
            disposition=("ALREADY_CANONICAL" if resolution.source_family ==
                         "ALREADY_CANONICAL" else "RETAINED_HISTORICAL_MAPPED"),
        ))

    for row in connection.execute(
        """SELECT model_version,model_identity,artifact_paths_json,artifact_hash
        FROM execution_model_updates_v2 ORDER BY model_version"""
    ):
        old_paths = json.loads(str(row["artifact_paths_json"]))
        new_paths: dict[str, str] = {}
        observed_hashes: dict[str, str] = {}
        source_families: set[str] = set()
        for key, value in old_paths.items():
            resolution = canonicalize_artifact_path(
                value, runtime_forward_root=runtime_forward_root,
            )
            target = require_runtime_artifact_path(
                value, runtime_forward_root=runtime_forward_root,
            )
            new_paths[str(key)] = str(target)
            observed_hashes[str(key)] = RidgeArtifact.read(target).artifact_hash
            source_families.add(resolution.source_family)
        observed_hash = canonical_hash(observed_hashes)
        if observed_hash != str(row["artifact_hash"]):
            raise RuntimeError(f"ARTIFACT_HASH_MISMATCH:{row['model_version']}")
        old_value = json.dumps(old_paths, sort_keys=True)
        new_value = json.dumps(new_paths, sort_keys=True)
        records.append(_record(
            table="execution_model_updates_v2",
            model_version=str(row["model_version"]),
            model_identity=str(row["model_identity"]), old_value=old_value,
            new_value=new_value, stored_hash=str(row["artifact_hash"]),
            observed_hash=observed_hash,
            source_family="+".join(sorted(source_families)),
            disposition=("ALREADY_CANONICAL" if old_value == new_value else
                         "RETAINED_HISTORICAL_MAPPED"),
        ))

    canonical_records = [
        {
            "table": item["table"],
            "model_version": item["model_version"],
            "value": item["old_value"],
        }
        for item in records
    ]
    after_records = [
        {**item, "value": record["new_value"]}
        for item, record in zip(canonical_records, records, strict=True)
    ]
    primary = [record for record in records if record["table"] == "model_updates_v2"]
    return {
        "schema": SCHEMA,
        "migration_version": MIGRATION_VERSION,
        "planned_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "database_identity": _database_identity(connection, database),
        "runtime_forward_root": str(runtime_forward_root.resolve()),
        "source_roots": {
            "former_checkout": str(
                Path(r"C:\Users\yiyou\XAUUSD-Forecaster\.local\forward")
            ),
            "older_automated_trading": str(Path(
                r"C:\Users\yiyou\automated-trading\src\XAUUSD-Forecaster\.local\forward"
            )),
            "known_relative_variants": True,
        },
        "active_generation_id": active_generation,
        "active_member_count": len(active_versions),
        "before_set_digest": _json_digest(canonical_records),
        "after_set_digest": _json_digest(after_records),
        "records": records,
        "manifest_locators": manifest_locators,
        "old_stable_compatibility_alias": {
            "path": str(Path(
                r"C:\Users\yiyou\XAUUSD-Forecaster\.local\forward\models-v2"
            )),
            "target": str((runtime_forward_root / "models-v2").resolve()),
            "created_by_migration": False,
        },
        "model_updates_v2_dispositions": {
            name: sum(record["disposition"] == name for record in primary)
            for name in (
                "ACTIVE_REQUIRED_MAPPED", "RETAINED_HISTORICAL_MAPPED",
                "HISTORICAL_ARTIFACT_NOT_RETAINED", "ALREADY_CANONICAL",
                "INVALID_OR_UNKNOWN",
            )
        },
        "transaction_result": "PLANNED",
    }


def receipt_digest(receipt: dict) -> str:
    return _json_digest({
        key: value for key, value in receipt.items() if key != "receipt_digest"
    })


def _validated_receipt_path(path: Path, runtime_forward_root: Path) -> Path:
    root = runtime_forward_root.resolve()
    candidate = path.resolve()
    try:
        common = Path(os.path.commonpath((root, candidate)))
    except ValueError as exc:
        raise ValueError("ARTIFACT_PATH_MIGRATION_RECEIPT_OUTSIDE_RUNTIME_ROOT") from exc
    if os.path.normcase(str(common)) != os.path.normcase(str(root)):
        raise ValueError("ARTIFACT_PATH_MIGRATION_RECEIPT_OUTSIDE_RUNTIME_ROOT")
    if candidate.suffix.lower() != ".json":
        raise ValueError("ARTIFACT_PATH_MIGRATION_RECEIPT_JSON_REQUIRED")
    return candidate


def write_migration_receipt(
    path: Path, receipt: dict, *, runtime_forward_root: Path,
) -> None:
    path = _validated_receipt_path(path, runtime_forward_root)
    payload = dict(receipt)
    payload["receipt_digest"] = receipt_digest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_migration_receipt(
    path: Path, *, runtime_forward_root: Path,
) -> dict:
    path = _validated_receipt_path(path, runtime_forward_root)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    observed = str(receipt.pop("receipt_digest", ""))
    if observed != receipt_digest(receipt):
        raise RuntimeError("ARTIFACT_PATH_MIGRATION_RECEIPT_TAMPERED")
    receipt["receipt_digest"] = observed
    return receipt


def _current_set_digest(connection: sqlite3.Connection, records: list[dict]) -> str:
    values = []
    for record in records:
        column = (
            "artifact_paths_json"
            if record["table"] == "execution_model_updates_v2"
            else "artifact_path"
        )
        row = connection.execute(
            f"SELECT {column} FROM {record['table']} WHERE model_version=?",
            (record["model_version"],),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"ARTIFACT_PATH_MIGRATION_ROW_MISSING:{record['model_version']}"
            )
        values.append({
            "table": record["table"],
            "model_version": record["model_version"],
            "value": str(row[0]),
        })
    return _json_digest(values)


def apply_artifact_path_migration(
    connection: sqlite3.Connection, receipt: dict,
) -> str:
    _assert_database_authority(connection, receipt)
    before = _current_set_digest(connection, receipt["records"])
    if before == receipt["after_set_digest"]:
        return "NO_CHANGE"
    if before != receipt["before_set_digest"]:
        raise RuntimeError("ARTIFACT_PATH_MIGRATION_SOURCE_DRIFT")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for record in receipt["records"]:
            column = (
                "artifact_paths_json"
                if record["table"] == "execution_model_updates_v2"
                else "artifact_path"
            )
            cursor = connection.execute(
                f"UPDATE {record['table']} SET {column}=? "
                f"WHERE model_version=? AND {column}=?",
                (
                    record["new_value"], record["model_version"],
                    record["old_value"],
                ),
            )
            if record["old_value"] != record["new_value"] and cursor.rowcount != 1:
                raise RuntimeError(
                    f"ARTIFACT_PATH_MIGRATION_WRITE_CONFLICT:{record['model_version']}"
                )
        if _current_set_digest(connection, receipt["records"]) != receipt["after_set_digest"]:
            raise RuntimeError("ARTIFACT_PATH_MIGRATION_AFTER_DIGEST_MISMATCH")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return "APPLIED"


def rollback_artifact_path_migration(
    connection: sqlite3.Connection, receipt: dict,
) -> str:
    _assert_database_authority(connection, receipt)
    current = _current_set_digest(connection, receipt["records"])
    if current == receipt["before_set_digest"]:
        return "NO_CHANGE"
    if current != receipt["after_set_digest"]:
        raise RuntimeError("ARTIFACT_PATH_MIGRATION_ROLLBACK_SOURCE_DRIFT")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for record in receipt["records"]:
            column = (
                "artifact_paths_json"
                if record["table"] == "execution_model_updates_v2"
                else "artifact_path"
            )
            connection.execute(
                f"UPDATE {record['table']} SET {column}=? "
                f"WHERE model_version=? AND {column}=?",
                (
                    record["old_value"], record["model_version"],
                    record["new_value"],
                ),
            )
        if _current_set_digest(connection, receipt["records"]) != receipt["before_set_digest"]:
            raise RuntimeError("ARTIFACT_PATH_MIGRATION_ROLLBACK_DIGEST_MISMATCH")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return "ROLLED_BACK"


def verify_artifact_path_migration(
    connection: sqlite3.Connection, receipt: dict,
) -> str:
    _assert_database_authority(connection, receipt)
    current = _current_set_digest(connection, receipt["records"])
    if current != receipt["after_set_digest"]:
        raise RuntimeError("ARTIFACT_PATH_MIGRATION_NOT_APPLIED")
    for record in receipt["records"]:
        if record["table"] == "execution_model_updates_v2":
            paths = json.loads(record["new_value"])
            observed = {
                key: RidgeArtifact.read(Path(value)).artifact_hash
                for key, value in paths.items()
            }
            observed_hash = canonical_hash(observed)
        else:
            observed_hash = _artifact_hash(
                record["model_identity"], Path(record["new_value"]),
            )
        if observed_hash != record["stored_artifact_hash"]:
            raise RuntimeError(
                f"ARTIFACT_PATH_MIGRATION_VERIFY_HASH_MISMATCH:"
                f"{record['model_version']}"
            )
    for locator in receipt["manifest_locators"]:
        observed = RidgeArtifact.read(Path(locator["resolved"])).artifact_hash
        if observed != locator["expected_artifact_hash"]:
            raise RuntimeError(
                "ARTIFACT_PATH_MIGRATION_MANIFEST_CHILD_HASH_MISMATCH:"
                f"{locator['model_version']}:{locator['field']}"
            )
    return "VERIFIED"


def ensure_old_stable_compatibility_alias(
    receipt: dict,
    *,
    alias_path: Path | None = None,
) -> str:
    contract = receipt["old_stable_compatibility_alias"]
    alias = (alias_path or Path(str(contract["path"]))).absolute()
    target = Path(str(contract["target"])).resolve()
    if not target.is_dir():
        raise RuntimeError("OLD_STABLE_COMPATIBILITY_TARGET_MISSING")
    if alias.exists():
        try:
            if alias.resolve() == target:
                return "ALREADY_PRESENT"
        except OSError:
            pass
        raise RuntimeError("OLD_STABLE_COMPATIBILITY_PATH_CONFLICT")
    alias.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["XAUUSD_ARTIFACT_ALIAS_PATH"] = str(alias)
    environment["XAUUSD_ARTIFACT_ALIAS_TARGET"] = str(target)
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction "
            "-Path $env:XAUUSD_ARTIFACT_ALIAS_PATH "
            "-Target $env:XAUUSD_ARTIFACT_ALIAS_TARGET "
            "-ErrorAction Stop | Out-Null",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not alias.exists() or alias.resolve() != target:
        raise RuntimeError("OLD_STABLE_COMPATIBILITY_ALIAS_FAILED")
    contract["path"] = str(alias)
    contract["created_by_migration"] = True
    return "CREATED"


def remove_old_stable_compatibility_alias(receipt: dict) -> str:
    contract = receipt["old_stable_compatibility_alias"]
    if not bool(contract.get("created_by_migration")):
        return "NOT_OWNED"
    alias = Path(str(contract["path"])).absolute()
    target = Path(str(contract["target"])).resolve()
    if not alias.exists():
        return "ALREADY_ABSENT"
    if not alias.is_symlink() and not os.path.isjunction(alias):
        raise RuntimeError("OLD_STABLE_COMPATIBILITY_ALIAS_OWNERSHIP_LOST")
    if alias.resolve() != target:
        raise RuntimeError("OLD_STABLE_COMPATIBILITY_ALIAS_TARGET_CHANGED")
    alias.rmdir()
    return "REMOVED"
