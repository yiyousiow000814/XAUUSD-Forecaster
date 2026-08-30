from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path, PureWindowsPath

import pytest

from xauusd_forecaster.artifact_path_migration import (
    apply_artifact_path_migration,
    build_artifact_path_migration_plan,
    ensure_old_stable_compatibility_alias,
    read_migration_receipt,
    remove_old_stable_compatibility_alias,
    rollback_artifact_path_migration,
    write_migration_receipt,
)
from xauusd_forecaster.artifact_paths import canonicalize_artifact_path
from xauusd_forecaster.forward_ledger import canonical_hash
from xauusd_forecaster.news_contracts import CURRENT_NEWS_CONTRACT
from xauusd_forecaster.ridge import RidgeArtifact


MODULE_ROOT = Path(__file__).resolve().parents[1]
OLD_STABLE_REVISION = "783d25314b090dd7fbbf124777c3b8de517d2b85"
OLD_FORWARD = PureWindowsPath(
    r"C:\Users\yiyou\XAUUSD-Forecaster\.local\forward"
)
AUTO_FORWARD = PureWindowsPath(
    r"C:\Users\yiyou\automated-trading\src\XAUUSD-Forecaster\.local\forward"
)
POWERSHELLS = [
    shell for shell in ("powershell.exe", "pwsh.exe") if shutil.which(shell)
]


def _ridge(seed: str) -> RidgeArtifact:
    return RidgeArtifact(
        feature_names=("value",), means=(0.0,), scales=(1.0,),
        coefficients=(1.0,), intercept=0.0, alpha=1.0,
        training_dataset_hash=seed, residual_std=0.0, training_rows=1,
    )


def _write_ridge(root: Path, family: str, version: str) -> tuple[Path, str]:
    path = root / family / version / "model.json"
    artifact = _ridge(version)
    artifact.write(path)
    return path, artifact.artifact_hash


def _legacy(family: str, path: Path, runtime_forward: Path) -> str:
    relative = path.relative_to(runtime_forward / family)
    return str(OLD_FORWARD / family / PureWindowsPath(str(relative)))


def _database_fixture(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path, dict]:
    runtime_root = tmp_path / "production-runtime"
    forward = runtime_root / ".local" / "forward"
    database = forward / "forward-evidence.sqlite3"
    forward.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE runtime_metadata(key TEXT PRIMARY KEY,value TEXT,created_at TEXT);
        INSERT INTO runtime_metadata VALUES('FORWARD_EPOCH','2026-08-01T00:00:00+00:00','x');
        CREATE TABLE model_updates_v2(
          model_version TEXT PRIMARY KEY,model_identity TEXT,model_stage TEXT,
          artifact_path TEXT,artifact_hash TEXT);
        CREATE TABLE news_model_generation_activations_v1(
          activation_id TEXT,generation_id TEXT,previous_generation_id TEXT,
          activated_at TEXT,reason TEXT);
        CREATE TABLE news_model_generations_v1(
          generation_id TEXT PRIMARY KEY,model_stage TEXT,created_at TEXT,
          cutoff_time TEXT,policy_version TEXT,feature_version TEXT,
          eligibility_version TEXT,event_snapshot_hash TEXT,
          market_training_hash TEXT,core_training_hash TEXT,
          broad_training_hash TEXT,event_weighting_version TEXT,
          generation_members INTEGER,status TEXT);
        CREATE TABLE news_model_generation_members_v1(
          generation_id TEXT,model_identity TEXT,model_version TEXT);
        CREATE TABLE news_model_generation_aux_members_v1(
          generation_id TEXT,model_identity TEXT,model_version TEXT);
        CREATE TABLE execution_model_updates_v1(
          model_version TEXT PRIMARY KEY,model_identity TEXT,
          artifact_path TEXT,artifact_hash TEXT);
        CREATE TABLE execution_model_updates_v2(
          model_version TEXT PRIMARY KEY,model_identity TEXT,
          artifact_paths_json TEXT,artifact_hash TEXT);
        """
    )
    generation = "generation-active"
    connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            generation, "SHADOW", "2026-08-29T00:00:00+00:00",
            "2026-08-29T00:00:00+00:00", CURRENT_NEWS_CONTRACT.policy_version,
            CURRENT_NEWS_CONTRACT.feature_version,
            CURRENT_NEWS_CONTRACT.eligibility_version,
            "events", "market", "core", "broad", "weights", 6, "READY",
        ),
    )
    connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES(?,?,?,?,?)",
        ("activation", generation, None, "2026-08-29T00:00:00+00:00", "test"),
    )
    files: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for version in ("market", "core", "broad", "news-only"):
        files[version], hashes[version] = _write_ridge(
            forward, "models-v2", version,
        )
    manifests = {
        "full": {
            "schema": "xauusd.phase2f.core-full-model.v4",
            "market_artifact_path": _legacy("models-v2", files["market"], forward),
            "market_artifact_hash": hashes["market"],
            "news_artifact_path": _legacy("models-v2", files["core"], forward),
            "news_artifact_hash": hashes["core"],
        },
        "broad-full": {
            "schema": "xauusd.phase2f.broad-full-model.v2",
            "market_artifact_path": _legacy("models-v2", files["market"], forward),
            "market_artifact_hash": hashes["market"],
            "news_artifact_path": _legacy("models-v2", files["broad"], forward),
            "news_artifact_hash": hashes["broad"],
        },
    }
    for version, payload in manifests.items():
        path = forward / "models-v2" / version / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        files[version] = path
        hashes[version] = canonical_hash(payload)
    identities = {
        "market": "MARKET_ONLY", "core": "NEWS_RESIDUAL", "full": "FULL",
        "broad": "BROAD_NEWS_RESIDUAL", "broad-full": "BROAD_FULL",
        "news-only": "NEWS_ONLY",
    }
    for version, identity in identities.items():
        connection.execute(
            "INSERT INTO model_updates_v2 VALUES(?,?,?,?,?)",
            (
                version, identity, "SHADOW",
                _legacy("models-v2", files[version], forward), hashes[version],
            ),
        )
        table = (
            "news_model_generation_aux_members_v1"
            if identity == "NEWS_ONLY"
            else "news_model_generation_members_v1"
        )
        connection.execute(
            f"INSERT INTO {table} VALUES(?,?,?)", (generation, identity, version),
        )
    historical, historical_hash = _write_ridge(
        forward, "models-v2", "relative-history",
    )
    connection.execute(
        "INSERT INTO model_updates_v2 VALUES(?,?,?,?,?)",
        (
            "relative-history", "NEWS_RESIDUAL", "SHADOW",
            r"models-v2\relative-history\model.json", historical_hash,
        ),
    )
    execution_v1, execution_v1_hash = _write_ridge(
        forward, "execution-models-v1", "legacy-execution",
    )
    connection.execute(
        "INSERT INTO execution_model_updates_v1 VALUES(?,?,?,?)",
        (
            "legacy-execution", "LOT_RIDGE",
            str(AUTO_FORWARD / "execution-models-v1" /
                "legacy-execution" / "model.json"),
            execution_v1_hash,
        ),
    )
    execution_hashes = {}
    execution_paths = {}
    for size in ("0.5X", "1.0X", "2.0X"):
        path, digest = _write_ridge(
            forward, "execution-models-v2", f"lot/{size}",
        )
        execution_paths[size] = _legacy("execution-models-v2", path, forward)
        execution_hashes[size] = digest
    connection.execute(
        "INSERT INTO execution_model_updates_v2 VALUES(?,?,?,?)",
        (
            "lot", "LOT_RIDGE", json.dumps(execution_paths, sort_keys=True),
            canonical_hash(execution_hashes),
        ),
    )
    connection.commit()
    return connection, database, runtime_root, files


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _prepare_repair_entrypoint_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "candidate-code-checkout"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    manifest = json.loads(
        (MODULE_ROOT / "scripts" / "runtime-control-files.json").read_text(
            encoding="utf-8",
        )
    )
    for name in manifest["files"]:
        shutil.copy2(MODULE_ROOT / "scripts" / name, scripts / name)
    for name in (
        "repair_stable_runtime_artifact_paths.ps1",
        "migrate_runtime_artifact_paths.py",
    ):
        shutil.copy2(MODULE_ROOT / "scripts" / name, scripts / name)
    shutil.copytree(
        MODULE_ROOT / "xauusd_forecaster", repository / "xauusd_forecaster",
    )
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "repair-entrypoint@example.invalid")
    _git(repository, "config", "user.name", "Repair Entrypoint Contract")
    _git(repository, "add", "scripts", "xauusd_forecaster")
    _git(repository, "commit", "-m", "fixture")
    revision = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", revision)
    return repository, revision


def _run_repair_entrypoint(
    *, powershell: str, code_root: Path, runtime_root: Path,
    repository_root: Path, expected_revision: str, receipt: Path,
    working_directory: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File",
            str(code_root / "scripts" / "repair_stable_runtime_artifact_paths.ps1"),
            "-RuntimeRoot", str(runtime_root),
            "-RepositoryRoot", str(repository_root),
            "-ExpectedRevision", expected_revision,
            "-ReceiptPath", str(receipt),
            "-PreMutationPlanOnly",
        ],
        cwd=working_directory, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )


def test_plan_covers_path_family_hashes_manifests_and_active_generation(
    tmp_path: Path,
) -> None:
    connection, database, runtime, files = _database_fixture(tmp_path)
    before = {name: path.read_bytes() for name, path in files.items() if "full" in name}
    plan = build_artifact_path_migration_plan(
        connection, database=database,
        runtime_forward_root=runtime / ".local" / "forward",
    )
    assert plan["model_updates_v2_dispositions"] == {
        "ACTIVE_REQUIRED_MAPPED": 6,
        "RETAINED_HISTORICAL_MAPPED": 1,
        "HISTORICAL_ARTIFACT_NOT_RETAINED": 0,
        "ALREADY_CANONICAL": 0,
        "INVALID_OR_UNKNOWN": 0,
    }
    assert len(plan["records"]) == 9
    assert len(plan["manifest_locators"]) == 4
    assert all(item["ownership"] == "IMMUTABLE_MANIFEST_RESOLVED_AT_RUNTIME"
               for item in plan["manifest_locators"])
    assert before == {
        name: path.read_bytes() for name, path in files.items() if "full" in name
    }
    connection.close()


def test_migration_is_atomic_idempotent_receipted_and_reversible(
    tmp_path: Path,
) -> None:
    connection, database, runtime, _ = _database_fixture(tmp_path)
    plan = build_artifact_path_migration_plan(
        connection, database=database,
        runtime_forward_root=runtime / ".local" / "forward",
    )
    forward_root = runtime / ".local" / "forward"
    receipt_path = forward_root / "migration-receipt.json"
    write_migration_receipt(
        receipt_path, plan, runtime_forward_root=forward_root,
    )
    receipt = read_migration_receipt(
        receipt_path, runtime_forward_root=forward_root,
    )
    assert apply_artifact_path_migration(connection, receipt) == "APPLIED"
    assert apply_artifact_path_migration(connection, receipt) == "NO_CHANGE"
    assert rollback_artifact_path_migration(connection, receipt) == "ROLLED_BACK"
    assert rollback_artifact_path_migration(connection, receipt) == "NO_CHANGE"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["active_generation_id"] = "tampered"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="RECEIPT_TAMPERED"):
        read_migration_receipt(
            receipt_path, runtime_forward_root=forward_root,
        )
    connection.close()


def test_migrated_database_loads_through_exact_old_stable_boundary(
    tmp_path: Path,
) -> None:
    available = subprocess.run(
        ["git", "cat-file", "-e", f"{OLD_STABLE_REVISION}^{{commit}}"],
        cwd=MODULE_ROOT, capture_output=True, check=False,
    )
    if available.returncode:
        pytest.skip("exact Stable source is unavailable in this shallow checkout")
    connection, database, runtime, _ = _database_fixture(tmp_path)
    receipt = build_artifact_path_migration_plan(
        connection, database=database,
        runtime_forward_root=runtime / ".local" / "forward",
    )
    assert apply_artifact_path_migration(connection, receipt) == "APPLIED"
    connection.close()

    archive = subprocess.run(
        ["git", "archive", "--format=tar", OLD_STABLE_REVISION],
        cwd=MODULE_ROOT, capture_output=True, check=True,
    ).stdout
    old_source = tmp_path / "exact-old-stable"
    old_source.mkdir()
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(old_source, filter="data")
    probe = subprocess.run(
        [
            sys.executable, "-c",
            "import sqlite3,sys;sys.path.insert(0,sys.argv[1]);"
            "from xauusd_forecaster.training_v2 import "
            "require_current_contract_generation;"
            "c=sqlite3.connect(sys.argv[2]);c.row_factory=sqlite3.Row;"
            "print(require_current_contract_generation(c))",
            str(old_source), str(database),
        ],
        capture_output=True, text=True, check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "generation-active"


def test_receipt_is_bound_to_exact_database_identity(tmp_path: Path) -> None:
    connection, database, runtime, _ = _database_fixture(tmp_path)
    receipt = build_artifact_path_migration_plan(
        connection, database=database,
        runtime_forward_root=runtime / ".local" / "forward",
    )
    receipt["database_identity"]["file_id"] += 1
    with pytest.raises(RuntimeError, match="DATABASE_IDENTITY_MISMATCH:file_id"):
        apply_artifact_path_migration(connection, receipt)
    connection.close()


@pytest.mark.parametrize("name", ("outside.json", "inside.txt"))
def test_receipt_path_is_owned_by_runtime_root(
    tmp_path: Path, name: str,
) -> None:
    connection, database, runtime, _ = _database_fixture(tmp_path)
    forward_root = runtime / ".local" / "forward"
    receipt = build_artifact_path_migration_plan(
        connection, database=database, runtime_forward_root=forward_root,
    )
    path = tmp_path / name if name == "outside.json" else forward_root / name
    with pytest.raises(
        ValueError, match="RECEIPT_(OUTSIDE_RUNTIME_ROOT|JSON_REQUIRED)",
    ):
        write_migration_receipt(
            path, receipt, runtime_forward_root=forward_root,
        )
    connection.close()


def test_repair_orchestration_fences_all_sqlite_writers_and_preserves_bridge(
) -> None:
    repair = (
        MODULE_ROOT / "scripts" / "repair_stable_runtime_artifact_paths.ps1"
    ).read_text(encoding="utf-8")
    migrate = (
        MODULE_ROOT / "scripts" / "migrate_runtime_artifact_paths.py"
    ).read_text(encoding="utf-8")

    for service in ("collector", "annotator", "api"):
        assert f'$services | Where-Object Key -eq "{service}"' in repair
        assert f"Stop-ForecasterService -Service ${service}" in repair
        assert f"Start-ForecasterService -Service ${service}" in repair
    assert repair.index("-MigrationAction plan") < repair.index("-MigrationAction apply")
    assert repair.index("-MigrationAction apply") < repair.index("-MigrationAction verify")
    assert repair.index("-MigrationAction verify") < repair.index(
        "Start-ForecasterService -Service $collector"
    )
    assert '"PRESERVED_FOR_OLD_STABLE"' in migrate
    assert "remove_old_stable_compatibility_alias" not in migrate


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_real_repair_entrypoint_preserves_source_authority_and_fails_closed(
    tmp_path: Path, powershell: str,
) -> None:
    connection, database, runtime, _ = _database_fixture(tmp_path)
    connection.close()
    code_root, revision = _prepare_repair_entrypoint_repository(tmp_path)
    repository_root = tmp_path / "config-authority"
    repository_root.mkdir()
    working_directory = tmp_path / "unrelated-working-directory"
    working_directory.mkdir()
    runtime_marker = runtime / ".runtime-code-marker"
    runtime_marker.write_text("stable", encoding="utf-8")
    runtime_scripts = runtime / "scripts"
    runtime_scripts.mkdir()
    shutil.copy2(
        MODULE_ROOT / "scripts" / "windows-service-launch-contract.json",
        runtime_scripts / "windows-service-launch-contract.json",
    )
    _git(runtime, "init", "-b", "stable")
    _git(runtime, "config", "user.email", "runtime@example.invalid")
    _git(runtime, "config", "user.name", "Runtime Contract")
    _git(runtime, "add", runtime_marker.name, "scripts")
    _git(runtime, "commit", "-m", "runtime")
    database_before = database.read_bytes()

    mismatched_receipt = runtime / ".local" / "forward" / "mismatch.json"
    mismatched = _run_repair_entrypoint(
        powershell=powershell, code_root=code_root,
        runtime_root=runtime, repository_root=repository_root,
        expected_revision="0" * 40, receipt=mismatched_receipt,
        working_directory=working_directory,
    )
    assert mismatched.returncode != 0
    assert "ARTIFACT_REPAIR_EXACT_MAIN_REQUIRED" in (
        mismatched.stdout + mismatched.stderr
    )
    assert not mismatched_receipt.exists()
    assert not (runtime / ".local" / "forward" / "release-control.lock").exists()
    assert database.read_bytes() == database_before

    receipt = runtime / ".local" / "forward" / "entrypoint-plan.json"
    matched = _run_repair_entrypoint(
        powershell=powershell, code_root=code_root,
        runtime_root=runtime, repository_root=repository_root,
        expected_revision=revision, receipt=receipt,
        working_directory=working_directory,
    )
    assert matched.returncode == 0, matched.stderr
    evidence = json.loads([
        line for line in matched.stdout.splitlines() if line.startswith("{")
    ][-1])
    assert evidence["schema"] == (
        "xauusd.stable-artifact-repair-premutation-plan.v1"
    )
    assert evidence["status"] == "PLANNED"
    assert Path(evidence["repair_source_root"]) == code_root.resolve()
    assert evidence["source_revision"] == revision
    assert evidence["origin_main"] == revision
    assert Path(evidence["runtime_root"]) == runtime.resolve()
    assert Path(evidence["repository_root"]) == repository_root.resolve()
    assert Path(evidence["working_directory"]) == working_directory.resolve()
    assert receipt.exists()
    assert not (runtime / ".local" / "forward" / "release-control.lock").exists()
    assert database.read_bytes() == database_before
    assert runtime_marker.read_text(encoding="utf-8") == "stable"

    connection = sqlite3.connect(database)
    connection.execute(
        "DELETE FROM news_model_generation_members_v1 "
        "WHERE model_identity='MARKET_ONLY'"
    )
    connection.commit()
    connection.close()
    invalid_before = database.read_bytes()
    invalid_receipt = runtime / ".local" / "forward" / "invalid-plan.json"
    invalid = _run_repair_entrypoint(
        powershell=powershell, code_root=code_root,
        runtime_root=runtime, repository_root=repository_root,
        expected_revision=revision, receipt=invalid_receipt,
        working_directory=working_directory,
    )
    assert invalid.returncode != 0
    assert "ARTIFACT_PATH_MIGRATION_PLAN_FAILED" in (
        invalid.stdout + invalid.stderr
    )
    assert "ACTIVE_GENERATION_INCOMPLETE" in (
        invalid.stdout + invalid.stderr
    )
    assert not invalid_receipt.exists()
    assert not (runtime / ".local" / "forward" / "release-control.lock").exists()
    assert database.read_bytes() == invalid_before


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("unknown", "UNKNOWN_ARTIFACT_ROOT"),
        ("traversal", "ARTIFACT_PATH_OUTSIDE_RUNTIME_ROOT"),
        ("missing", "RUNTIME_ARTIFACT_MISSING"),
        ("hash", "ARTIFACT_HASH_MISMATCH"),
        ("incomplete", "ACTIVE_GENERATION_INCOMPLETE"),
    ),
)
def test_invalid_path_hash_and_generation_fail_before_mutation(
    tmp_path: Path, mutation: str, error: str,
) -> None:
    connection, database, runtime, _ = _database_fixture(tmp_path)
    if mutation == "unknown":
        connection.execute(
            "UPDATE model_updates_v2 SET artifact_path=? WHERE model_version='market'",
            (r"D:\unknown\model.json",),
        )
    elif mutation == "traversal":
        connection.execute(
            "UPDATE model_updates_v2 SET artifact_path=? WHERE model_version='market'",
            (r"models-v2\..\outside\model.json",),
        )
    elif mutation == "missing":
        connection.execute(
            "UPDATE model_updates_v2 SET artifact_path=? WHERE model_version='market'",
            (r"models-v2\missing\model.json",),
        )
    elif mutation == "hash":
        connection.execute(
            "UPDATE model_updates_v2 SET artifact_hash='bad' WHERE model_version='market'"
        )
    else:
        connection.execute(
            "DELETE FROM news_model_generation_members_v1 WHERE model_identity='MARKET_ONLY'"
        )
    connection.commit()
    with pytest.raises((RuntimeError, ValueError), match=error):
        build_artifact_path_migration_plan(
            connection, database=database,
            runtime_forward_root=runtime / ".local" / "forward",
        )
    connection.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_old_stable_compatibility_alias_is_exact_owned_and_reversible(
    tmp_path: Path,
) -> None:
    connection, database, runtime, files = _database_fixture(tmp_path)
    receipt = build_artifact_path_migration_plan(
        connection, database=database,
        runtime_forward_root=runtime / ".local" / "forward",
    )
    alias = tmp_path / "candidate-checkout" / ".local" / "forward" / "models-v2"
    assert ensure_old_stable_compatibility_alias(receipt, alias_path=alias) == "CREATED"
    manifest = json.loads(files["full"].read_text(encoding="utf-8"))
    old_child = PureWindowsPath(manifest["market_artifact_path"])
    child_through_alias = alias.joinpath(*old_child.parts[-2:])
    assert RidgeArtifact.read(child_through_alias).artifact_hash == _ridge("market").artifact_hash
    assert remove_old_stable_compatibility_alias(receipt) == "REMOVED"
    assert not alias.exists()
    connection.close()


def test_canonicalizer_rejects_unknown_absolute_roots_and_family_traversal(
    tmp_path: Path,
) -> None:
    runtime_forward = tmp_path / "runtime" / ".local" / "forward"
    with pytest.raises(ValueError, match="UNKNOWN_ARTIFACT_ROOT"):
        canonicalize_artifact_path(
            r"D:\foreign\models-v2\model.json",
            runtime_forward_root=runtime_forward,
        )
    with pytest.raises(ValueError, match="OUTSIDE_RUNTIME_ROOT"):
        canonicalize_artifact_path(
            r"models-v2\..\foreign\model.json",
            runtime_forward_root=runtime_forward,
        )
