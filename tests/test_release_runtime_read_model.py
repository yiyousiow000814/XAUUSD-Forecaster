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


WORKER_IDS = {
    "stable": "11111111-1111-4111-8111-111111111111",
    "candidate": "22222222-2222-4222-8222-222222222222",
    "previous": "33333333-3333-4333-8333-333333333333",
    "drifted": "44444444-4444-4444-8444-444444444444",
}


def _identity(letter: str, worker: str, *, kind: str = "PRODUCTION_CANDIDATE") -> dict:
    worker = WORKER_IDS.get(worker, worker)
    return {
        "git_sha": letter * 40,
        "worker_git_sha": letter * 40,
        "worker_version_id": worker,
        "windows_revision": letter * 40,
        "artifact_kind": kind,
        "branch": "main",
        "validation_key": f"{worker}:{letter * 40}",
    }


def _projection_script(state: dict, *, active: dict | None, health: str) -> str:
    state_json = json.dumps(state, separators=(",", ":"))
    active_payload = dict(active) if active else None
    if active_payload is not None:
        active_payload.setdefault("status", "AVAILABLE")
    active_json = json.dumps(active_payload, separators=(",", ":")) if active_payload else "null"
    previous = state.get("previous_stable")
    worker = {
        "status": "AVAILABLE" if previous else "NOT_APPLICABLE",
        "reason": "EXACT_WORKER_VERSION_AVAILABLE" if previous else "PREVIOUS_IDENTITY_UNAVAILABLE",
    }
    windows = {
        "status": "AVAILABLE" if previous else "NOT_APPLICABLE",
        "reason": "REVISION_OWNED_LAUNCH_CONTRACT_AVAILABLE" if previous else "PREVIOUS_IDENTITY_UNAVAILABLE",
    }
    active_matches = bool(active and state.get("stable") and
                          active.get("version_id") == state["stable"]["worker_version_id"] and
                          active.get("git_sha") == state["stable"]["git_sha"] and
                          active.get("windows_revision") == state["stable"]["windows_revision"])
    reverse = {
        "status": "READY" if previous and active_matches else "BLOCKED",
        "can_reverse": bool(previous and active_matches),
        "reason": "READY" if previous and active_matches else "ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE",
        "recovery_observation_status": "NOT_OBSERVED",
    }
    return (
        f"$state='{state_json}'|ConvertFrom-Json;"
        f"$active='{active_json}'|ConvertFrom-Json;"
        "$windows=if($active){[pscustomobject]@{status='AVAILABLE';revision=[string]$active.windows_revision}}else{$null};"
        f"$health=[pscustomobject]@{{status='{health}';business_health_status='{health}';"
        "business_health_reason='TEST_HEALTH';reason='TEST_HEALTH';ownership_status='SINGLE_OWNER'};"
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
    assert model["last_known_good"]["worker_version_id"] == WORKER_IDS["stable"]
    assert model["target"]["worker_version_id"] == WORKER_IDS["candidate"]
    assert model["previous"]["worker_artifact"]["status"] == "AVAILABLE"
    assert model["previous"]["worker_is_current_traffic_member"] is False
    assert model["previous"]["reverse_precheck"]["can_reverse"] is True
    assert model["previous"]["reverse_precheck"]["recovery_observation_status"] == "NOT_OBSERVED"


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_transaction_mode_is_backward_compatible_and_projects_recovery_metadata(
    powershell: str,
) -> None:
    stable = _identity("a", "stable")
    target = _identity("b", "candidate")
    active = {
        "version_id": stable["worker_version_id"],
        "git_sha": stable["git_sha"],
        "windows_revision": stable["windows_revision"],
        "traffic_percent": 100,
        "previous_membership_status": "NOT_ASSIGNED",
    }
    base_state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": stable,
        "previous_stable": _identity("c", "previous"),
        "candidate": target,
        "deployment_status": "PROMOTING",
    }

    legacy_state = {
        **base_state,
        "transaction": {"type": "PROMOTE", "phase": "CUTOVER", "target": target},
    }
    legacy = json.loads(_run(
        _projection_script(legacy_state, active=active, health="HEALTHY"), powershell,
    ))
    assert legacy["release_mode"] == "NORMAL"
    assert legacy["recovery_action"] is None

    recovery_state = {
        **base_state,
        "transaction": {
            "type": "PROMOTE",
            "phase": "CUTOVER",
            "mode": "RECOVERY_HOTFIX",
            "recovery_action": "APPLY_RECOVERY_HOTFIX",
            "recovery_reason": "ACTIVE_BUSINESS_HEALTH_DEGRADED",
            "target": target,
        },
    }
    recovery = json.loads(_run(
        _projection_script(recovery_state, active=active, health="DEGRADED"), powershell,
    ))
    assert recovery["release_mode"] == "RECOVERY_HOTFIX"
    assert recovery["recovery_action"] == "APPLY_RECOVERY_HOTFIX"
    assert recovery["recovery_reason"] == "ACTIVE_BUSINESS_HEALTH_DEGRADED"

    recovery_state["transaction"]["mode"] = "UNRECOGNIZED"
    malformed = json.loads(_run(
        _projection_script(recovery_state, active=active, health="DEGRADED"), powershell,
    ))
    assert malformed["release_mode"] == "UNKNOWN"


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
        "version_id": WORKER_IDS["stable" if active_letter == "a" else "drifted"],
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


@pytest.mark.parametrize(
    "mutation",
    (
        lambda identity: identity.clear(),
        lambda identity: identity.pop("worker_version_id"),
        lambda identity: identity.__setitem__("git_sha", "bad"),
        lambda identity: identity.__setitem__("windows_revision", "bad"),
        lambda identity: identity.__setitem__("artifact_kind", "UNKNOWN"),
    ),
)
def test_malformed_recognized_stable_cannot_become_lkg(mutation) -> None:
    stable = _identity("a", "stable")
    mutation(stable)
    state = {"schema_version": "stable-candidate-release-v3", "stable": stable,
             "previous_stable": None, "candidate": None, "transaction": None,
             "deployment_status": "READY"}
    model = json.loads(_run(_projection_script(state, active=None, health="UNKNOWN")))
    assert model["committed_stable"] is None
    assert model["committed_identity_status"] in {"INCOMPLETE", "MISMATCH"}
    assert model["last_known_good"] is None
    assert model["last_known_good_source"] == "UNKNOWN"


def test_explicit_narrow_legacy_identity_is_valid_lkg() -> None:
    revision = "783d25314b090dd7fbbf124777c3b8de517d2b85"
    legacy = _identity("a", "76d314fc-e484-4f50-8ace-3689e0896709",
                       kind="LEGACY_BOOTSTRAP_STABLE")
    legacy["git_sha"] = revision
    legacy["windows_revision"] = revision
    legacy["worker_git_sha"] = "NOT_RECORDED"
    legacy["provenance_state"] = "LEGACY_EXACT_WORKER_WINDOWS_PAIR"
    state = {"schema_version": "stable-candidate-release-v3", "stable": legacy,
             "previous_stable": None, "candidate": None, "transaction": None,
             "deployment_status": "READY"}
    model = json.loads(_run(_projection_script(state, active=None, health="UNKNOWN")))
    assert model["committed_identity_status"] == "COMPLETE"
    assert model["last_known_good"]["artifact_kind"] == "LEGACY_BOOTSTRAP_STABLE"


def test_current_identity_uses_git_as_worker_provenance_when_legacy_field_absent() -> None:
    stable = _identity("a", "stable")
    stable.pop("worker_git_sha")
    state = {"schema_version": "stable-candidate-release-v3", "stable": stable,
             "previous_stable": None, "candidate": None, "transaction": None,
             "deployment_status": "READY"}
    model = json.loads(_run(_projection_script(state, active=None, health="UNKNOWN")))
    assert model["committed_identity_status"] == "COMPLETE"
    assert model["last_known_good"]["git_sha"] == "a" * 40


def test_arbitrary_legacy_label_does_not_bypass_identity_contract() -> None:
    legacy = _identity("a", "stable", kind="LEGACY_BOOTSTRAP_STABLE")
    legacy["worker_git_sha"] = "NOT_RECORDED"
    legacy["provenance_state"] = "LEGACY_EXACT_WORKER_WINDOWS_PAIR"
    state = {"schema_version": "stable-candidate-release-v3", "stable": legacy,
             "previous_stable": None, "candidate": None, "transaction": None,
             "deployment_status": "READY"}
    model = json.loads(_run(_projection_script(state, active=None, health="UNKNOWN")))
    assert model["last_known_good"] is None


def test_active_observation_and_business_health_remain_independent() -> None:
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
    assert model["active"]["health"] == "HEALTHY"


@pytest.mark.parametrize(
    ("worker_status", "windows_status", "expected"),
    (
        (None, "AVAILABLE", "UNKNOWN"),
        ("AVAILABLE", None, "UNKNOWN"),
        ("ILLEGAL", "AVAILABLE", "UNKNOWN"),
        ("AVAILABLE", "OLD_ADAPTER", "UNKNOWN"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE"),
    ),
)
def test_observation_availability_requires_explicit_legal_status(
    worker_status: str | None, windows_status: str | None, expected: str,
) -> None:
    stable = _identity("a", "stable")
    state = {
        "schema_version": "stable-candidate-release-v3", "stable": stable,
        "previous_stable": None, "candidate": None, "transaction": None,
    }
    worker = {
        "version_id": stable["worker_version_id"], "git_sha": stable["git_sha"],
        "traffic_percent": 100,
    }
    windows = {"revision": stable["windows_revision"]}
    if worker_status is not None:
        worker["status"] = worker_status
    if windows_status is not None:
        windows["status"] = windows_status
    body = (
        f"$state='{json.dumps(state)}'|ConvertFrom-Json;"
        f"$worker='{json.dumps(worker)}'|ConvertFrom-Json;"
        f"$windows='{json.dumps(windows)}'|ConvertFrom-Json;"
        "$health=[pscustomobject]@{business_health_status='HEALTHY';"
        "business_health_reason='OK';ownership_status='SINGLE_OWNER'};"
        "$model=New-ReleaseRuntimeReadModel -PersistedState $state "
        "-ActiveWorkerObservation $worker -ActiveWindowsObservation $windows "
        "-HealthObservation $health -ObservedAt ([DateTimeOffset]::UtcNow);"
        "$model.active.observation_status"
    )
    assert _run(body) == expected


@pytest.mark.parametrize(
    ("business", "ownership", "overall"),
    (
        ("HEALTHY", "SINGLE_OWNER", "HEALTHY"),
        ("DEGRADED", "SINGLE_OWNER", "DEGRADED"),
        ("HEALTHY", "INVALID", "DEGRADED"),
        ("HEALTHY", "UNKNOWN", "UNKNOWN"),
    ),
)
def test_active_health_composes_business_health_and_ownership(
    business: str, ownership: str, overall: str,
) -> None:
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": _identity("a", "stable"), "previous_stable": None,
        "candidate": None, "transaction": None,
    }
    body = (
        f"$state='{json.dumps(state)}'|ConvertFrom-Json;"
        f"$health=[pscustomobject]@{{business_health_status='{business}';"
        f"business_health_reason='TEST';ownership_status='{ownership}'}};"
        "$model=New-ReleaseRuntimeReadModel -PersistedState $state "
        "-HealthObservation $health -ObservedAt ([DateTimeOffset]::UtcNow);"
        "$model.active|ConvertTo-Json -Compress"
    )
    active = json.loads(_run(body))
    assert active["business_health_status"] == business
    assert active["ownership_status"] == ownership
    assert active["health"] == overall


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
        "$resolution=Resolve-ReleaseRuntimeIdentity $target;"
        "$artifact=New-ReleaseWorkerArtifactObservation -IdentityResolution $resolution "
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
        "$resolution=Resolve-ReleaseRuntimeIdentity $target;"
        "$result=New-ReleaseWorkerArtifactObservation -IdentityResolution $resolution "
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
        "$resolution=Resolve-ReleaseRuntimeIdentity $target;"
        "$result=New-ReleaseWorkerArtifactObservation -IdentityResolution $resolution "
        "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $false;"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "WORKER_PROVIDER_SCOPE_UNVERIFIED"


@pytest.mark.parametrize(
    ("message", "status", "reason"),
    (
        (f"release:{'a' * 40} branch:main artifact_kind:PRODUCTION_CANDIDATE",
         "AVAILABLE", "EXACT_WORKER_VERSION_AVAILABLE"),
        (f"release:{'a' * 40} branch:feature artifact_kind:PRODUCTION_CANDIDATE",
         "MISMATCH", "WORKER_BRANCH_PROVENANCE_MISMATCH"),
        (f"release:{'a' * 40} artifact_kind:PRODUCTION_CANDIDATE",
         "MISMATCH", "WORKER_BRANCH_PROVENANCE_MISMATCH"),
    ),
)
def test_worker_artifact_requires_exact_main_branch(
    message: str, status: str, reason: str,
) -> None:
    target = _identity("a", "11111111-1111-4111-8111-111111111111")
    version = _worker_version(target, message=message)
    body = (f"$target='{json.dumps(target)}'|ConvertFrom-Json;"
            f"$version='{json.dumps(version)}'|ConvertFrom-Json;"
            "$resolution=Resolve-ReleaseRuntimeIdentity $target;"
            "$result=New-ReleaseWorkerArtifactObservation -IdentityResolution $resolution "
            "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $true;"
            "$result|ConvertTo-Json -Compress")
    result_obj = json.loads(_run(body))
    assert result_obj["status"] == status
    assert result_obj["reason"] == reason


LEGACY_REVISION = "783d25314b090dd7fbbf124777c3b8de517d2b85"
LEGACY_WORKER = "76d314fc-e484-4f50-8ace-3689e0896709"


def _legacy_identity() -> dict:
    return {
        "git_sha": LEGACY_REVISION,
        "worker_git_sha": "NOT_RECORDED",
        "worker_version_id": LEGACY_WORKER,
        "windows_revision": LEGACY_REVISION,
        "artifact_kind": "LEGACY_BOOTSTRAP_STABLE",
        "branch": "main",
        "validation_key": "legacy-bootstrap",
        "provenance_state": "LEGACY_EXACT_WORKER_WINDOWS_PAIR",
    }


@pytest.mark.parametrize(
    ("case", "mutation", "expected"),
    (
        ("exact_pair", lambda value: None, "AVAILABLE"),
        ("arbitrary_worker", lambda value: value.__setitem__(
            "worker_version_id", "11111111-1111-4111-8111-111111111111"), "MISMATCH"),
        ("wrong_revision", lambda value: (
            value.__setitem__("git_sha", "a" * 40),
            value.__setitem__("windows_revision", "a" * 40)), "MISMATCH"),
        ("missing_provenance", lambda value: value.pop("provenance_state"), "MISMATCH"),
        ("wrong_provenance", lambda value: value.__setitem__(
            "provenance_state", "ARBITRARY_LABEL"), "MISMATCH"),
        ("arbitrary_legacy_label", lambda value: value.update({
            "git_sha": "b" * 40,
            "windows_revision": "b" * 40,
            "worker_version_id": "22222222-2222-4222-8222-222222222222",
        }), "MISMATCH"),
        ("unknown_legacy_artifact", lambda value: value.__setitem__(
            "worker_git_sha", "UNKNOWN"), "MISMATCH"),
    ),
)
def test_narrow_legacy_worker_artifact_requires_exact_nonrecombinable_pair(
    case: str, mutation, expected: str,
) -> None:
    target = _legacy_identity()
    mutation(target)
    version = _worker_version(target, message="legacy artifact without branch")
    body = (f"$target='{json.dumps(target)}'|ConvertFrom-Json;"
            f"$version='{json.dumps(version)}'|ConvertFrom-Json;"
            "$resolution=Resolve-ReleaseRuntimeIdentity $target;"
            "$result=New-ReleaseWorkerArtifactObservation -IdentityResolution $resolution "
            "-VersionDetails $version -ProviderStatus AVAILABLE -ProviderScopeVerified $true;"
            "[pscustomobject]@{case='" + case + "';resolution=$resolution.status;"
            "artifact=$result.status}|ConvertTo-Json -Compress")
    result = json.loads(_run(body))
    assert result["artifact"] == expected
    assert result["resolution"] == ("COMPLETE" if expected == "AVAILABLE" else "MISMATCH")


@pytest.mark.parametrize(
    ("worker", "windows", "bundle", "transaction", "lock", "owners", "active_observation", "identity", "matches", "ready", "reason"),
    (
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, True, "READY"),
        ("UNKNOWN", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, False, "WORKER_UNKNOWN"),
        ("AVAILABLE", "UNAVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, False, "WINDOWS_UNAVAILABLE"),
        ("AVAILABLE", "AVAILABLE", "UNAVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, False, "CONTROL_BUNDLE_UNAVAILABLE"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", True, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, False, "RELEASE_TRANSACTION_ACTIVE"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, True, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", True, False, "RELEASE_LOCK_ACTIVE"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "INVALID", "AVAILABLE", "COMPLETE", True, False, "PRODUCTION_OWNERSHIP_INVALID"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "UNKNOWN", "COMPLETE", True, False, "ACTIVE_OBSERVATION_UNAVAILABLE"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "INCOMPLETE", False, False, "ACTIVE_IDENTITY_INCOMPLETE"),
        ("AVAILABLE", "AVAILABLE", "AVAILABLE", False, False, "SINGLE_OWNER", "AVAILABLE", "COMPLETE", False, False, "ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE"),
    ),
)
def test_reverse_precheck_is_live_fail_closed_composition(
    worker: str, windows: str, bundle: str, transaction: bool, lock: bool,
    owners: str, active_observation: str, identity: str, matches: bool,
    ready: bool, reason: str,
) -> None:
    body = (
        "$previous=[pscustomobject]@{worker_version_id='previous';windows_revision=('a'*40)};"
        f"$worker=[pscustomobject]@{{status='{worker}';reason='WORKER_{worker}'}};"
        f"$windows=[pscustomobject]@{{status='{windows}';reason='WINDOWS_{windows}'}};"
        "$result=New-ReleaseReversePrecheck -PreviousIdentity $previous "
        "-CommittedIdentityStatus COMPLETE -PreviousIdentityStatus COMPLETE "
        "-WorkerArtifact $worker "
        f"-WindowsArtifact $windows -ControlBundleStatus '{bundle}' "
        f"-TransactionActive ${str(transaction).lower()} -ReleaseLockActive ${str(lock).lower()} "
        f"-OwnershipStatus '{owners}' -ActiveObservationStatus '{active_observation}' "
        f"-ActiveIdentityStatus '{identity}' -ActiveMatchesCommitted ${str(matches).lower()};"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["can_reverse"] is ready
    assert result["status"] == ("READY" if ready else "BLOCKED")
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("committed", "previous", "expected"),
    (
        ("INCOMPLETE", "COMPLETE", "COMMITTED_IDENTITY_INVALID"),
        ("MISMATCH", "COMPLETE", "COMMITTED_IDENTITY_INVALID"),
        ("COMPLETE", "INCOMPLETE", "PREVIOUS_IDENTITY_INVALID"),
        ("COMPLETE", "UNKNOWN", "PREVIOUS_IDENTITY_INVALID"),
        ("COMPLETE", "MISMATCH", "PREVIOUS_IDENTITY_MISMATCH"),
    ),
)
def test_reverse_precheck_blocks_invalid_committed_or_previous_identity(
    committed: str, previous: str, expected: str,
) -> None:
    body = (
        "$identity=[pscustomobject]@{worker_version_id='previous'};"
        "$artifact=[pscustomobject]@{status='AVAILABLE';reason='AVAILABLE'};"
        "$result=New-ReleaseReversePrecheck -PreviousIdentity $identity "
        f"-CommittedIdentityStatus '{committed}' -PreviousIdentityStatus '{previous}' "
        "-WorkerArtifact $artifact -WindowsArtifact $artifact "
        "-ControlBundleStatus AVAILABLE -OwnershipStatus SINGLE_OWNER "
        "-ActiveObservationStatus AVAILABLE -ActiveIdentityStatus COMPLETE "
        "-ActiveMatchesCommitted $true;"
        "$result|ConvertTo-Json -Compress"
    )
    result = json.loads(_run(body))
    assert result["can_reverse"] is False
    assert result["reason"] == expected


@pytest.mark.parametrize("health", ("HEALTHY", "DEGRADED"))
def test_business_health_does_not_block_safe_reverse_authority(health: str) -> None:
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": _identity("a", "stable"),
        "previous_stable": _identity("c", "previous"),
        "candidate": None,
        "transaction": None,
        "deployment_status": "READY",
    }
    stable = state["stable"]
    active = {"version_id": stable["worker_version_id"], "git_sha": stable["git_sha"],
              "windows_revision": stable["windows_revision"], "traffic_percent": 100,
              "previous_membership_status": "NOT_ASSIGNED"}
    model = json.loads(_run(_projection_script(state, active=active, health=health)))
    assert model["active"]["health"] == health
    assert model["previous"]["reverse_precheck"]["can_reverse"] is True


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


@pytest.mark.parametrize("membership", (None, "", "ILLEGAL", "assigned"))
def test_pure_read_model_whitelists_membership_status(membership: str | None) -> None:
    state = {
        "schema_version": "stable-candidate-release-v3",
        "stable": _identity("a", "stable"),
        "previous_stable": _identity("c", "previous"),
        "candidate": None,
        "transaction": None,
    }
    active = {
        "version_id": WORKER_IDS["stable"],
        "git_sha": "a" * 40,
        "windows_revision": "a" * 40,
        "traffic_percent": 100,
        "previous_is_member": False,
        "previous_membership_status": membership,
    }
    model = json.loads(_run(_projection_script(state, active=active, health="HEALTHY")))
    assert model["previous"]["worker_traffic_membership_status"] == "UNKNOWN"
