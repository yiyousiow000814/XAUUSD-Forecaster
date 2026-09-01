from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "scripts" / "release_runtime_read_model.ps1"
POWERSHELLS = [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)]


def _run(body: str, powershell: str = "powershell.exe") -> str:
    command = f". '{OWNER}'; {body}"
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"release read-model contract failed under {powershell}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _identity(letter: str, worker: str, *, kind: str = "PRODUCTION_CANDIDATE") -> dict:
    return {
        "git_sha": letter * 40,
        "worker_git_sha": letter * 40,
        "worker_version_id": worker,
        "windows_revision": letter * 40,
        "artifact_kind": kind,
        "validation_key": f"{worker}:{letter * 40}",
    }


def _projection_script(state: dict, *, active: dict | None, health: str) -> str:
    state_json = json.dumps(state, separators=(",", ":"))
    active_json = json.dumps(active, separators=(",", ":")) if active else "null"
    previous = state.get("previous_stable")
    worker = {
        "status": "AVAILABLE" if previous else "NOT_APPLICABLE",
        "reason": "EXACT_WORKER_VERSION_AVAILABLE" if previous else "PREVIOUS_IDENTITY_UNAVAILABLE",
    }
    windows = {
        "status": "AVAILABLE" if previous else "NOT_APPLICABLE",
        "reason": "REVISION_OWNED_LAUNCH_CONTRACT_AVAILABLE" if previous else "PREVIOUS_IDENTITY_UNAVAILABLE",
    }
    reverse = {
        "status": "READY" if previous and health == "HEALTHY" else "BLOCKED",
        "can_reverse": bool(previous and health == "HEALTHY"),
        "reason": "READY" if previous and health == "HEALTHY" else "ACTIVE_HEALTH_DEGRADED",
        "recovery_observation_status": "NOT_OBSERVED",
    }
    return (
        f"$state='{state_json}'|ConvertFrom-Json;"
        f"$active='{active_json}'|ConvertFrom-Json;"
        "$windows=if($active){[pscustomobject]@{revision=[string]$active.windows_revision}}else{$null};"
        f"$health=[pscustomobject]@{{status='{health}';reason='TEST_HEALTH'}};"
        f"$worker='{json.dumps(worker)}'|ConvertFrom-Json;"
        f"$previousWindows='{json.dumps(windows)}'|ConvertFrom-Json;"
        f"$reverse='{json.dumps(reverse)}'|ConvertFrom-Json;"
        "$model=New-ReleaseRuntimeReadModel -PersistedState $state "
        "-ActiveWorkerObservation $active -ActiveWindowsObservation $windows "
        "-HealthObservation $health -PreviousWorkerArtifact $worker "
        "-PreviousWindowsArtifact $previousWindows -ReversePrecheck $reverse "
        "-ObservedAt ([DateTimeOffset]::Parse('2026-09-01T12:00:00+00:00'));"
        "$model|ConvertTo-Json -Depth 8 -Compress"
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_v3_projection_separates_active_committed_previous_and_target(powershell: str) -> None:
    stable = _identity("a", "stable")
    previous = _identity("c", "previous")
    candidate = _identity("b", "candidate")
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": previous,
        "candidate": candidate,
        "transaction": None,
        "deployment_status": "READY",
    }
    active = {
        "version_id": stable["worker_version_id"],
        "git_sha": stable["git_sha"],
        "windows_revision": stable["windows_revision"],
        "traffic_percent": 100,
        "previous_is_member": False,
        "previous_traffic_percent": None,
    }
    model = json.loads(_run(_projection_script(state, active=active, health="HEALTHY"), powershell))

    assert model["schema_version"] == "release-runtime-read-model-v1"
    assert model["active_matches_committed"] is True
    assert model["drift_status"] == "MATCHED"
    assert model["phase"] == "VERIFY"
    assert model["active"]["health"] == "HEALTHY"
    assert model["last_known_good"]["worker_version_id"] == "stable"
    assert model["target"]["worker_version_id"] == "candidate"
    assert model["previous"]["worker_artifact"]["status"] == "AVAILABLE"
    assert model["previous"]["worker_is_current_traffic_member"] is False
    assert model["previous"]["reverse_precheck"]["can_reverse"] is True
    assert model["previous"]["reverse_precheck"]["recovery_observation_status"] == "NOT_OBSERVED"


@pytest.mark.parametrize(
    ("health", "active_letter", "expected_drift", "expected_health"),
    (
        ("DEGRADED", "a", "MATCHED", "DEGRADED"),
        ("HEALTHY", "d", "DRIFT", "HEALTHY"),
    ),
)
def test_active_health_and_identity_drift_are_independent(
    health: str, active_letter: str, expected_drift: str, expected_health: str,
) -> None:
    stable = _identity("a", "stable")
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": _identity("c", "previous"),
        "candidate": None,
        "transaction": None,
        "deployment_status": "READY",
    }
    active = {
        "version_id": "stable" if active_letter == "a" else "drifted",
        "git_sha": active_letter * 40,
        "windows_revision": active_letter * 40,
        "traffic_percent": 100,
        "previous_is_member": False,
    }
    model = json.loads(_run(_projection_script(state, active=active, health=health)))
    assert model["drift_status"] == expected_drift
    assert model["active"]["health"] == expected_health
    assert model["last_known_good"]["git_sha"] == "a" * 40


@pytest.mark.parametrize("phase", ["CUTOVER", "OBSERVING", "REVERSE_OBSERVING"])
def test_switch_and_observe_keep_old_committed_as_lkg(phase: str) -> None:
    stable = _identity("a", "stable")
    target = _identity("b", "candidate")
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": _identity("c", "previous"),
        "candidate": target,
        "transaction": {"type": "PROMOTE", "phase": phase, "target": target},
        "deployment_status": "OBSERVING" if "OBSERV" in phase else "PROMOTING",
    }
    active_identity = target if "OBSERV" in phase else stable
    active = {
        "version_id": active_identity["worker_version_id"],
        "git_sha": active_identity["git_sha"],
        "windows_revision": active_identity["windows_revision"],
        "traffic_percent": 100,
        "previous_is_member": False,
    }
    model = json.loads(_run(_projection_script(state, active=active, health="HEALTHY")))
    assert model["phase"] == ("OBSERVE" if "OBSERV" in phase else "SWITCH")
    assert model["last_known_good"]["git_sha"] == stable["git_sha"]
    assert model["target"]["git_sha"] == target["git_sha"]


def test_unknown_old_state_does_not_invent_lkg_or_health() -> None:
    state = {
        "schema_version": "unknown-release-state",
        "stable": _identity("a", "stable"),
        "previous_stable": None,
        "candidate": None,
        "transaction": None,
        "deployment_status": "READY",
    }
    model = json.loads(_run(_projection_script(state, active=None, health="UNKNOWN")))
    assert model["last_known_good"] is None
    assert model["last_known_good_source"] == "UNKNOWN"
    assert model["drift_status"] == "UNKNOWN"
    assert model["active"]["health"] == "UNKNOWN"


def test_unavailable_required_active_observation_cannot_report_healthy() -> None:
    stable = _identity("a", "stable")
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": None,
        "candidate": None,
        "transaction": None,
        "deployment_status": "READY",
    }
    active = {
        "status": "UNKNOWN",
        "version_id": stable["worker_version_id"],
        "git_sha": stable["git_sha"],
        "windows_revision": stable["windows_revision"],
        "traffic_percent": 100,
    }
    model = json.loads(_run(_projection_script(state, active=active, health="HEALTHY")))
    assert model["active"]["observation_status"] == "UNKNOWN"
    assert model["active"]["health"] == "UNKNOWN"


def _worker_version(target: dict, *, version_id: str | None = None, message: str | None = None) -> dict:
    return {
        "id": version_id or target["worker_version_id"],
        "metadata": {"source": "wrangler"},
        "annotations": {
            "workers/message": message
            or f"release:{target['git_sha']} branch:main artifact_kind:PRODUCTION_CANDIDATE"
        },
        "resources": {"script": {"handlers": ["fetch"]}, "bindings": []},
    }


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_exact_artifact_availability_is_independent_from_traffic_membership(
    powershell: str,
) -> None:
    target = _identity("a", "11111111-1111-4111-8111-111111111111")
    version = _worker_version(target)
    deployment = {
        "versions": [{"version_id": "active", "percentage": 100}],
    }
    body = (
        f"$target='{json.dumps(target)}'|ConvertFrom-Json;"
        f"$version='{json.dumps(version)}'|ConvertFrom-Json;"
        f"$deployment='{json.dumps(deployment)}'|ConvertFrom-Json;"
        "$artifact=New-ReleaseWorkerArtifactObservation -Target $target "
        "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $true;"
        "$traffic=$deployment.versions|?{$_.version_id -eq $target.worker_version_id};"
        '[pscustomobject]@{status=$artifact.status;member=[bool]$traffic}|ConvertTo-Json -Compress'
    )
    result = json.loads(_run(body, powershell))
    assert result == {"status": "AVAILABLE", "member": False}


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_reason"),
    (
        ("$version=$null", "UNKNOWN", "WORKER_VERSION_RESPONSE_MALFORMED"),
        ("$version.metadata=$null", "UNKNOWN", "WORKER_VERSION_RESPONSE_MALFORMED"),
        ("$version.id='wrong'", "MISMATCH", "WORKER_VERSION_IDENTITY_MISMATCH"),
        ("$version.resources=[pscustomobject]@{}", "UNKNOWN", "WORKER_VERSION_RESPONSE_MALFORMED"),
        ("$version.annotations.'workers/message'='release:" + "b" * 40 + " artifact_kind:PRODUCTION_CANDIDATE'", "MISMATCH", "WORKER_VERSION_PROVENANCE_MISMATCH"),
    ),
)
def test_worker_artifact_malformed_or_wrong_identity_fails_closed(
    mutation: str, expected_status: str, expected_reason: str,
) -> None:
    target = _identity("a", "11111111-1111-4111-8111-111111111111")
    version = _worker_version(target)
    body = (
        f"$target='{json.dumps(target)}'|ConvertFrom-Json;"
        f"$version='{json.dumps(version)}'|ConvertFrom-Json;{mutation};"
        "$result=New-ReleaseWorkerArtifactObservation -Target $target "
        "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $true;"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["status"] == expected_status
    assert result["reason"] == expected_reason


def test_worker_artifact_provider_scope_must_be_exact() -> None:
    target = _identity("a", "11111111-1111-4111-8111-111111111111")
    version = _worker_version(target)
    body = (
        f"$target='{json.dumps(target)}'|ConvertFrom-Json;"
        f"$version='{json.dumps(version)}'|ConvertFrom-Json;"
        "$result=New-ReleaseWorkerArtifactObservation -Target $target "
        "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $false;"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "WORKER_PROVIDER_SCOPE_UNVERIFIED"


@pytest.mark.parametrize(
    ("worker", "windows", "bundle", "transaction", "lock", "owners", "health", "ready"),
    (
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "HEALTHY", True),
        ("UNKNOWN", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "HEALTHY", False),
        ("AVAILABLE", "UNAVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "HEALTHY", False),
        ("AVAILABLE", "AVAILABLE", "UNAVAILABLE", False, False, "SINGLE_OWNER", "HEALTHY", False),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", True, False, "SINGLE_OWNER", "HEALTHY", False),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, True, "SINGLE_OWNER", "HEALTHY", False),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "INVALID", "HEALTHY", False),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "DEGRADED", False),
    ),
)
def test_reverse_precheck_is_live_fail_closed_composition(
    worker: str, windows: str, bundle: str, transaction: bool, lock: bool,
    owners: str, health: str, ready: bool,
) -> None:
    body = (
        "$previous=[pscustomobject]@{worker_version_id='previous';windows_revision=('a'*40)};"
        f"$worker=[pscustomobject]@{{status='{worker}';reason='WORKER_{worker}'}};"
        f"$windows=[pscustomobject]@{{status='{windows}';reason='WINDOWS_{windows}'}};"
        "$result=New-ReleaseReversePrecheck -Previous $previous -WorkerArtifact $worker "
        f"-WindowsArtifact $windows -ControlBundleStatus '{bundle}' "
        f"-TransactionActive ${str(transaction).lower()} -ReleaseLockActive ${str(lock).lower()} "
        f"-OwnershipStatus '{owners}' -ActiveHealthStatus '{health}';"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["can_reverse"] is ready
    assert result["status"] == ("READY" if ready else "BLOCKED")


def test_read_model_is_bounded_and_does_not_copy_validation_or_history() -> None:
    stable = _identity("a", "stable")
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": _identity("c", "previous"),
        "candidate": {**_identity("b", "candidate"), "validation": {"routes": ["x"] * 200}},
        "transaction": None,
        "deployment_status": "READY",
        "history": ["x"] * 200,
    }
    active = {
        "version_id": "stable", "git_sha": "a" * 40,
        "windows_revision": "a" * 40, "traffic_percent": 100,
        "previous_is_member": False,
    }
    raw = _run(_projection_script(state, active=active, health="HEALTHY"))
    assert len(raw.encode("utf-8")) < 6000
    assert "routes" not in raw
    assert "history" not in raw


def test_owner_dot_source_declares_functions_without_io_or_filesystem_mutation(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    result = _run(
        f"Set-Location '{tmp_path}';"
        "Write-Output ((Get-Command New-ReleaseRuntimeReadModel).CommandType)"
    )
    assert result == "Function"
    assert list(tmp_path.iterdir()) == before
