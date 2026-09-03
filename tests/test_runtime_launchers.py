from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONTROL_FILES = (
    "xauusd_control_center.ps1",
    "control_center_common.ps1",
    "control_center_persistence_gateway.ps1",
    "control_center_provider_adapters.ps1",
    "control_center_runtime_supervision.ps1",
    "control_center_evidence_authority.ps1",
    "control_center_transaction_engine.ps1",
    "control_center_recovery_engine.ps1",
    "control_center_install.ps1",
    "control_center_presentation.ps1",
    "release-evidence-contract.json",
    "release_evidence_nodes.ps1",
    "recovery_hotfix.ps1",
    "release_runtime_read_model.ps1",
    "control_center.xaml",
    "xauusd_control_center_launcher.vbs",
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs",
)

CONTROL_CENTER_SOURCE_FILES = tuple(
    ROOT / "scripts" / name
    for name in RUNTIME_CONTROL_FILES
    if name == "xauusd_control_center.ps1" or name.startswith("control_center_")
)


def _control_center_source() -> str:
    """Return the composed Control Center contract, not one owner file."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in CONTROL_CENTER_SOURCE_FILES
    )


def _control_center_function_source(name: str) -> str:
    marker = f"function {name}"
    matches = []
    for path in CONTROL_CENTER_SOURCE_FILES:
        source = path.read_text(encoding="utf-8")
        if marker not in source:
            continue
        body = source.split(marker, 1)[1]
        matches.append(marker + body.split("\nfunction ", 1)[0])
    assert len(matches) == 1, f"expected one canonical definition for {name}"
    return matches[0]


def test_control_center_facade_composes_unique_canonical_owners() -> None:
    facade = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8",
    )
    owner_manifest = json.loads(
        (ROOT / "scripts" / "control-center-owners.json").read_text(
            encoding="utf-8",
        )
    )
    runtime_manifest = json.loads(
        (ROOT / "scripts" / "runtime-control-files.json").read_text(
            encoding="utf-8",
        )
    )
    owner_files = [owner["file"] for owner in owner_manifest["owners"]]
    assert len(facade.splitlines()) < 7600
    assert facade.count("\nfunction ") == 0
    assert len(owner_files) == 9
    assert len(set(owner_files)) == len(owner_files)
    assert set(owner_files).issubset(runtime_manifest["files"])
    for owner_file in owner_files:
        assert f'"{owner_file}"' in facade

    definitions: dict[str, str] = {}
    for owner_file in owner_files:
        source = (ROOT / "scripts" / owner_file).read_text(encoding="utf-8")
        for line in source.splitlines():
            if not line.startswith("function "):
                continue
            name = line.split()[1]
            assert name not in definitions, (
                f"{name} is defined by both {definitions.get(name)} and {owner_file}"
            )
            definitions[name] = owner_file
    assert 410 <= len(definitions) <= 430


def test_control_center_owner_boundaries_keep_authority_out_of_presentation() -> None:
    presentation = (
        ROOT / "scripts" / "control_center_presentation.ps1"
    ).read_text(encoding="utf-8")
    providers = (
        ROOT / "scripts" / "control_center_provider_adapters.ps1"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT / "scripts" / "control_center_persistence_gateway.ps1"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "scripts" / "control_center_runtime_supervision.ps1"
    ).read_text(encoding="utf-8")
    evidence_nodes = (
        ROOT / "scripts" / "release_evidence_nodes.ps1"
    ).read_text(encoding="utf-8")
    assert "Write-ReleaseControlState" not in presentation
    assert "Invoke-CloudflareDeployment" not in presentation
    assert "Start-ReleasePromotion" not in providers
    assert "Complete-ReleasePromotion" not in providers
    assert "Invoke-CloudflareDeployment" not in persistence
    assert "Complete-ReleasePromotion" not in runtime
    assert "function Write-ReleaseEvidenceUtf8Atomic" in persistence
    assert "function Write-ReleaseEvidenceUtf8Atomic" not in evidence_nodes
    for non_owner in (presentation, providers):
        assert "Set-Content" not in non_owner
        assert "[System.IO.File]::WriteAllText" not in non_owner
        assert "Move-Item" not in non_owner


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_control_center_composed_owner_bundle_loads_in_both_shells(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$owners=@('Write-ReleaseControlState','Get-CloudflareDeployment',"
        "'Get-ForecasterStatus','Publish-CandidateQualificationEvidence',"
        "'Start-ReleasePromotion','Complete-ReleaseRecovery',"
        "'Invoke-ControlPlaneInstall','Show-ControlCenter');"
        "$missing=@($owners|Where-Object{-not(Get-Command $_ -ErrorAction SilentlyContinue)});"
        'Write-Output "$($owners.Count),$($missing.Count)"',
        powershell=powershell,
    )
    assert result == "8,0"


def test_quote_bridge_uses_standalone_local_configuration() -> None:
    launcher = (
        ROOT
        / "ctrader"
        / "XauusdForwardQuoteBridge"
        / "run_live_quote_bridge.ps1"
    ).read_text(encoding="utf-8")

    assert "CTRADER_CLI_PATH" in launcher
    assert "CTRADER_SECRET_ROOT" in launcher
    assert ".local\\config" in launcher
    assert "$repositoryRoot" not in launcher
    assert "src\\ctrader\\windows_cli_path.txt" not in launcher


def test_control_center_treats_weekly_close_as_healthy() -> None:
    control_center = _control_center_source()

    assert "Test-ExpectedWeeklyMarketClosure" in control_center
    assert "Get-BrokerMarketSession" in control_center
    assert 'return "MARKET CLOSED"' in control_center
    assert '"MARKET CLOSED", "API OK"' in control_center
    assert '"SYNC ERROR", "SYNC STALE"' in control_center
    assert '"COLLECTOR STALE", "ANNOTATOR STALE"' in control_center
    assert '"SESSION STALE"' in control_center


def test_control_center_loads_collector_keys_without_exposing_them() -> None:
    control_center = _control_center_source()

    assert 'function Get-CollectorSecret' in control_center
    assert '.local\\secrets\\collector-keys.json' in control_center
    assert 'Get-CollectorSecret -Name "BLS_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "BEA_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "FRED_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "EIA_API_KEY"' in control_center
    assert 'ConvertFrom-Json' in control_center


def test_release_observability_secret_prefers_valid_local_file(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$root=Join-Path $repositoryRoot '.local\\secrets';"
        "New-Item -ItemType Directory -Path $root -Force|Out-Null;"
        "$path=Join-Path $root 'cloudflare-release.json';"
        "@{CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN='  local-release-token  '}|"
        "ConvertTo-Json|Set-Content -LiteralPath $path -Encoding UTF8;"
        "function Get-UserEnvironmentValue{return 'environment-release-token'};"
        "$secret=Get-ReleaseSecret -Name 'CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN';"
        'Write-Output "$($secret.available),$($secret.source),$($secret.value.Length),$($secret.diagnostic)"',
    )

    assert result == "True,LOCAL_SECRET_FILE,19,"
    assert "local-release-token" not in result
    assert "environment-release-token" not in result


def test_release_observability_secret_falls_back_to_user_environment(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-UserEnvironmentValue{return '  environment-release-token  '};"
        "$secret=Get-ReleaseSecret -Name 'CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN';"
        'Write-Output "$($secret.available),$($secret.source),$($secret.value.Length),$($secret.diagnostic)"',
    )

    assert result == "True,USER_ENVIRONMENT,25,"
    assert "environment-release-token" not in result


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        ("{bad-json", "LOCAL_SECRET_FILE_MALFORMED_JSON"),
        ('{"WRONG_KEY":"value"}', "LOCAL_SECRET_KEY_MISSING"),
        ('{"CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN":"   "}', "LOCAL_SECRET_VALUE_EMPTY"),
    ],
)
def test_release_observability_secret_file_fails_closed_with_bounded_diagnostic(
    tmp_path, content: str, diagnostic: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$root=Join-Path $repositoryRoot '.local\\secrets';"
        "New-Item -ItemType Directory -Path $root -Force|Out-Null;"
        f"[IO.File]::WriteAllText((Join-Path $root 'cloudflare-release.json'),'{content}');"
        "function Get-UserEnvironmentValue{return 'environment-release-token'};"
        "$secret=Get-ReleaseSecret -Name 'CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN';"
        'Write-Output "$($secret.available),$($secret.source),$($secret.value.Length),$($secret.diagnostic)"',
    )

    assert result == f"False,UNAVAILABLE,0,{diagnostic}"
    assert "environment-release-token" not in result


def test_release_observability_token_is_not_persisted(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$root=Join-Path $repositoryRoot '.local\\secrets';"
        "New-Item -ItemType Directory -Path $root -Force|Out-Null;"
        "$path=Join-Path $root 'cloudflare-release.json';"
        "@{CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN='nonpersistent-release-token'}|"
        "ConvertTo-Json|Set-Content -LiteralPath $path -Encoding UTF8;"
        "function Invoke-RestMethod{return [pscustomobject]@{success=$true;result=[pscustomobject]@{calculations=@()}}};"
        "$null=Invoke-WorkersObservabilityQuery "
        "-Filters @([pscustomobject]@{key='k';value='v'}) "
        "-Calculations @([pscustomobject]@{operator='count';alias='count'}) "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow);"
        "$release=New-ReleaseControlState -Stable (New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId 'stable' -WindowsRevision ('a'*40));"
        "Write-ReleaseControlState $release;Write-ReleaseHistory -Event 'SECRET_TEST' -Release $release;"
        "$persisted=(Get-Content -LiteralPath $releaseControlStatePath -Raw)+(Get-Content -LiteralPath $releaseHistoryPath -Raw);"
        'Write-Output "$($script:lastWorkersObservabilityCredentialSource),$([bool]($persisted -match \'nonpersistent-release-token\'))"',
    )

    assert result == "LOCAL_SECRET_FILE,False"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_future_release_history_is_versioned_bounded_and_preserves_old_lines(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "New-Item -ItemType Directory -Path (Split-Path $releaseHistoryPath -Parent) "
        "-Force|Out-Null;"
        "$old='{\"occurred_at\":\"legacy\",\"event\":\"OLD\","
        "\"release\":{\"validation_key\":\"legacy\"}}';"
        "$old|Set-Content -LiteralPath $releaseHistoryPath -Encoding UTF8;"
        "$release=New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId '11111111-1111-4111-8111-111111111111' "
        "-WindowsRevision ('a'*40) -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$release|Add-Member huge ('x'*200000);"
        "$release|Add-Member migration_acceptance ([pscustomobject]@{"
        "validation_key=$release.validation_key;receipt_digest=('b'*64)});"
        "Write-ReleaseHistory -Event 'BOUNDED_TEST' -Release $release "
        "-Detail @{transaction_id='tx-1';diagnostic=('z'*10000)};"
        "$lines=@(Get-Content -LiteralPath $releaseHistoryPath -Encoding UTF8);"
        "$event=$lines[-1]|ConvertFrom-ReleaseControlJson;"
        "$bytes=[Text.Encoding]::UTF8.GetByteCount($lines[-1]);"
        'Write-Output "$($lines.Count),$($event.schema_version),$bytes,'
        '$($event.release.validation_key),$($event.transaction_id),'
        '$([bool]($lines[0]-match \'OLD\')),$([bool]($lines[-1]-match \'xxxx\'))"',
        powershell=powershell,
    )
    fields = result.split(",")
    assert fields[:2] == ["2", "release-history-event-v2"]
    assert int(fields[2]) <= 65536
    assert fields[3] == (
        "11111111-1111-4111-8111-111111111111:" + "a" * 40
    )
    assert fields[4:] == ["tx-1", "True", "False"]


@pytest.mark.parametrize(
    ("status", "diagnostic"),
    [
        (400, "OBSERVABILITY_QUERY_FAILED"),
        (403, "OBSERVABILITY_CREDENTIAL_REJECTED"),
        (429, "OBSERVABILITY_RATE_LIMITED"),
        (503, "OBSERVABILITY_TRANSIENT_API_FAILURE"),
    ],
)
def test_release_observability_failure_class_is_bounded(
    tmp_path, status: int, diagnostic: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseSecret{return [pscustomobject]@{available=$true;"
        "value='classification-release-token';source='LOCAL_SECRET_FILE';diagnostic=$null}};"
        f"function Invoke-RestMethod{{$exception=[System.Exception]::new('safe');"
        f"$exception|Add-Member -NotePropertyName Response -NotePropertyValue "
        f"([pscustomobject]@{{StatusCode={status}}});throw $exception}};"
        "$null=Invoke-WorkersObservabilityQuery "
        "-Filters @([pscustomobject]@{key='k';value='v'}) "
        "-Calculations @([pscustomobject]@{operator='count';alias='count'}) "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow);"
        'Write-Output "$script:lastWorkersObservabilityDiagnostic,$script:lastWorkersObservabilityCredentialSource"',
    )

    assert result == f"{diagnostic},LOCAL_SECRET_FILE"
    assert "classification-release-token" not in result


def test_observability_retry_classifier_closes_legacy_exact_universe_pending(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$transient=Test-RetryableObservabilityDiagnostic "
        "-Diagnostic 'OBSERVABILITY_TRANSIENT_API_FAILURE';"
        "$pending=Test-RetryableObservabilityDiagnostic "
        "-Diagnostic 'OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING';"
        "$failed=Test-RetryableObservabilityDiagnostic "
        "-Diagnostic 'OBSERVABILITY_QUERY_FAILED';"
        'Write-Output "$transient,$pending,$failed"',
    )

    assert result == "True,False,False"


def test_release_observability_events_normalizes_real_cloudflare_envelope(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseSecret{return [pscustomobject]@{available=$true;"
        "value='events-release-token';source='LOCAL_SECRET_FILE';diagnostic=$null}};"
        "function Invoke-RestMethod{$event=[pscustomobject]@{'$metadata'=[pscustomobject]@{"
        "id='event-1';type='cf-worker-event'};'$workers'=[pscustomobject]@{cpuTimeMs=2;"
        "wallTimeMs=3;outcome='ok';scriptVersion=[pscustomobject]@{id='worker'};"
        "event=[pscustomobject]@{path='/api/status';request=[pscustomobject]@{method='GET';"
        "headers=[pscustomobject]@{'x-aurum-request-id'='request-1';"
        "'x-aurum-validation-run'='run';'x-aurum-validation-phase'='acceptance'}};"
        "response=[pscustomobject]@{status=200}}}};return [pscustomobject]@{success=$true;"
        "result=[pscustomobject]@{events=[pscustomobject]@{count=2;events=@($event);"
        "fields=@();series=@()}}}};"
        "$page=Invoke-WorkersObservabilityEventsQuery "
        "-Filters @([pscustomobject]@{key='k';value='v'}) "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow);"
        'Write-Output "$($page.total_count),$($page.page_count),'
        '$($page.records[0].event_id),$($page.records[0].cpu_ms),$($page.next_offset)"',
    )

    assert result == "2,1,event-1,2,event-1"


def test_release_observability_calculation_adapter_returns_internal_aggregate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseSecret{return [pscustomobject]@{available=$true;"
        "value='calculation-release-token';source='LOCAL_SECRET_FILE';diagnostic=$null}};"
        "function Invoke-RestMethod{return [pscustomobject]@{success=$true;result=[pscustomobject]@{"
        "run=[pscustomobject]@{};statistics=[pscustomobject]@{};calculations=@("
        "[pscustomobject]@{alias='invocations';aggregates=@([pscustomobject]@{value=310})})}}};"
        "$result=Invoke-WorkersObservabilityQuery "
        "-Filters @([pscustomobject]@{key='k';value='v'}) "
        "-Calculations @([pscustomobject]@{operator='count';alias='invocations'}) "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow);"
        "$value=Get-CalculationAggregate -QueryResult $result -Alias 'invocations';"
        'Write-Output "$(@($result.PSObject.Properties.Name)-join \',\'),$value"',
    )

    assert result == "aggregates,310"


def test_cpu_recovery_policy_is_bounded_and_never_requests_user_approval(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$p=Get-WorkerCpuEvidencePolicy;$source=[IO.File]::ReadAllText((Join-Path $scriptRepositoryRoot "
        "'scripts\\worker_cpu_evidence.ps1'));"
        'Write-Output "$($p.active_read_backoff_seconds.Count),'
        '$([int](($p.active_read_backoff_seconds|Measure-Object -Sum).Sum)),'
        '$($p.maximum_deficit_top_ups),$($p.maximum_headroom_top_ups),'
        '$($p.outlier_confirmation_acceptance),$($p.maximum_outlier_confirmations),'
        '$($p.maximum_background_reads),$($source -match \'Read-Host|PromptForChoice\')"',
    )

    assert result == "6,170,1,1,10,1,4,False"


def test_cpu_deficit_repair_policy_has_manifest_derived_global_bound(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$p=Get-WorkerCpuDeficitRepairPolicy;"
        'Write-Output "$($p.version),$($p.required_plateau_reads),'
        '$($p.maximum_provider_preflight_reads),$($p.maximum_family_count),'
        '$($p.requests_per_family),$($p.maximum_total_request_count)"',
    )

    assert result == "worker-cpu-deficit-repair-v1,3,3,4,4,16"


def test_provider_unavailable_persists_active_and_background_retry_budget(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';"
        "$candidate=[pscustomobject]@{worker_version_id='worker';git_sha=('a'*40)};"
        "$expected=@(1..12|ForEach-Object{[pscustomobject]@{request_id=('r-'+$_);"
        "family='status';scenario='default';method='GET';path='/api/status';"
        "phase='acceptance'}});$script:queries=0;function Start-Sleep{};"
        "function Invoke-WorkersObservabilityEventsQuery{$script:queries++;return $null};"
        "$from=[DateTimeOffset]::UtcNow.AddMinutes(-5);$to=[DateTimeOffset]::UtcNow;"
        "$null=Get-CandidateFrozenPlatformEvidence -Candidate $candidate -From $from -To $to "
        "-ExpectedRequests $expected -ValidationRun $run;"
        "$stored=Read-WorkerCpuRunArtifact -ValidationRun $run -Name 'provider-evidence.json';"
        "$active=[int]$stored.recovery.active_reads;"
        "1..4|ForEach-Object{$stored=Read-WorkerCpuRunArtifact -ValidationRun $run "
        "-Name 'provider-evidence.json';$stored.recovery.last_read_at="
        "[DateTimeOffset]::UtcNow.AddHours(-1).ToString('o');"
        "Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot $run) "
        "'provider-evidence.json') -Value $stored;"
        "$null=Get-CandidateFrozenPlatformEvidence -Candidate $candidate -From $from -To $to "
        "-ExpectedRequests $expected -ValidationRun $run};"
        "$final=Read-WorkerCpuRunArtifact -ValidationRun $run -Name 'provider-evidence.json';"
        'Write-Output "$script:queries,$active,$($final.recovery.background_reads),'
        '$script:lastWorkersObservabilityDiagnostic"',
    )

    assert result == "10,6,4,PROVIDER_EVIDENCE_INSUFFICIENT"


def test_deficit_repair_plateau_counts_only_successful_provider_reads(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$recovery=[pscustomobject]@{"
        "active_reads=6;background_reads=0;deficit_top_ups=0;headroom_top_ups=0};"
        "1..3|ForEach-Object{$recovery.background_reads=$_;$stored="
        "Write-WorkerCpuProviderEvidence -ValidationRun $run -Records @() "
        "-RecoveryState $recovery -ProviderReadSucceeded $false};"
        "$failed=Get-WorkerCpuProviderPlateauState -ValidationRun $run "
        "-CurrentDigest $stored.observed_universe_digest;1..3|ForEach-Object{"
        "$recovery.background_reads=$_;$stored=Write-WorkerCpuProviderEvidence "
        "-ValidationRun $run -Records @() -RecoveryState $recovery "
        "-ProviderReadSucceeded $true};$passed=Get-WorkerCpuProviderPlateauState "
        "-ValidationRun $run -CurrentDigest $stored.observed_universe_digest;"
        'Write-Output "$($failed.stable),$($failed.matching_reads),'
        '$($passed.stable),$($passed.matching_reads)"',
    )

    assert result == "False,0,True,3"


def test_raw_telemetry_metrics_keep_zero_cpu_samples(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$records=@("
        "[pscustomobject]@{cpu_ms=0;wall_ms=1;outcome='ok';status=200},"
        "[pscustomobject]@{cpu_ms=2;wall_ms=3;outcome='ok';status=200});"
        "$e=Get-ReleaseTelemetryMetrics -Records $records -RouteFamily 'test' "
        "-ExpectedInvocations 2;"
        'Write-Output "$($e.invocations),$($e.p95_cpu_ms),$($e.p99_cpu_ms),'
        '$($e.max_cpu_ms),$($e.responses_5xx),$($e.responses_1102),$($e.gate_state)"',
    )

    assert result == "2,2,2,2,0,0,PASSED"


def test_raw_telemetry_classifies_worker_1102_from_resource_outcomes(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$records=@("
        "[pscustomobject]@{cpu_ms=10;wall_ms=11;outcome='exceededCpu';status=500},"
        "[pscustomobject]@{cpu_ms=1;wall_ms=2;outcome='ok';status=200});"
        "$e=Get-ReleaseTelemetryMetrics -Records $records -RouteFamily 'test' "
        "-ExpectedInvocations 2;"
        'Write-Output "$($e.exceeded_cpu),$($e.exceeded_memory),'
        '$($e.responses_1102),$($e.responses_5xx),$($e.gate_state)"',
    )

    assert result == "1,0,1,1,FAILED"


def test_cpu_quota_uses_all_observed_samples_and_tolerates_only_reserve_omission(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|ForEach-Object{[pscustomobject]@{request_id=('r-'+$_);"
        "family='status';scenario='default';method='GET';path='/api/status';phase='acceptance';"
        "sample_kind=if($_ -le 10){'required'}else{'reserve'}}});"
        "$records=@(1..10|ForEach-Object{[pscustomobject]@{request_id=('r-'+$_);"
        "event_id=('e-'+$_);cpu_ms=4;wall_ms=5;status=200;outcome='ok'}});"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.groups[0].sent),$($d.groups[0].observed),'
        '$($d.groups[0].missing),$($d.missing_request_ids.Count),$($d.global.max_cpu_ms)"',
    )

    assert result == "QUALIFIED_WITH_PROVIDER_OMISSION,12,10,2,2,4"


def test_cpu_quota_rejects_concentrated_missing_family_and_absent_scenario(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@();$records=@();foreach($family in @('a','b')){foreach($scenario in @('one','two')){"
        "1..12|ForEach-Object{$id=($family+'-'+$scenario+'-'+$_);$expected += [pscustomobject]@{"
        "request_id=$id;family=$family;scenario=$scenario;method='GET';path='/';phase='acceptance'};"
        "if($family -eq 'a' -or $scenario -eq 'one'){$records += [pscustomobject]@{request_id=$id;"
        "event_id=('e-'+$id);cpu_ms=4;wall_ms=5;status=200;outcome='ok'}}}}};"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.deficient_groups.Count),'
        '$($d.deficient_groups[0].family),$($d.deficient_groups[0].scenario),'
        '$($d.deficient_groups[0].observed)"',
    )

    assert result == "PROVIDER_EVIDENCE_PENDING,1,b,two,0"


def test_provider_evidence_union_is_monotonic_across_delay_reorder_and_shorter_reads(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|ForEach-Object{[pscustomobject]@{request_id=('r-'+$_)}});"
        "function E($n){[pscustomobject]@{request_id=('r-'+$n);event_id=('e-'+$n);"
        "worker_version_id='worker';validation_run='11111111-1111-1111-1111-111111111111';"
        "validation_phase='acceptance';cpu_ms=1;wall_ms=2;status=200;outcome='ok'}};"
        "$a=Merge-WorkerCpuProviderEvidence -AcceptedRecords @() -NewRecords @(1..6|%{E $_}) "
        "-ExpectedRequests $expected -CandidateWorkerVersion worker "
        "-ValidationRun '11111111-1111-1111-1111-111111111111';"
        "$b=Merge-WorkerCpuProviderEvidence -AcceptedRecords $a -NewRecords @(12..7|%{E $_}) "
        "-ExpectedRequests $expected -CandidateWorkerVersion worker "
        "-ValidationRun '11111111-1111-1111-1111-111111111111';"
        "$c=Merge-WorkerCpuProviderEvidence -AcceptedRecords $b -NewRecords @((E 1)) "
        "-ExpectedRequests $expected -CandidateWorkerVersion worker "
        "-ValidationRun '11111111-1111-1111-1111-111111111111';"
        'Write-Output "$($a.Count),$($b.Count),$($c.Count),$(@($c.request_id|Sort-Object -Unique).Count)"',
    )

    assert result == "6,12,12,12"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_provider_evidence_union_canonicalizes_integral_metric_representation(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';"
        "$expected=@([pscustomobject]@{request_id='r-1'});"
        "$accepted=[pscustomobject]@{event_id='e-1';event_type='worker';"
        "request_id='r-1';worker_version_id='worker';validation_run=$run;"
        "validation_phase='acceptance';method='GET';path='/api/status';status=200;"
        "outcome='ok';cpu_ms=[int]1;wall_ms=[int]2};"
        "$fresh=$accepted.PSObject.Copy();$fresh.cpu_ms=[double]1;"
        "$fresh.wall_ms=[double]2;$merged=Merge-WorkerCpuProviderEvidence "
        "-AcceptedRecords @($accepted) -NewRecords @($fresh) -ExpectedRequests $expected "
        "-CandidateWorkerVersion worker -ValidationRun $run;"
        'Write-Output "$(@($merged).Count),$(@($merged)[0].event_id)"',
        powershell=powershell,
    )

    assert result == "1,e-1"


def test_provider_evidence_union_rejects_logically_changed_same_event(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';"
        "$expected=@([pscustomobject]@{request_id='r-1'});"
        "$accepted=[pscustomobject]@{event_id='e-1';event_type='worker';"
        "request_id='r-1';worker_version_id='worker';validation_run=$run;"
        "validation_phase='acceptance';method='GET';path='/api/status';status=200;"
        "outcome='ok';cpu_ms=[double]1;wall_ms=[double]2};"
        "$changed=$accepted.PSObject.Copy();$changed.cpu_ms=[double]3;"
        "try{$null=Merge-WorkerCpuProviderEvidence -AcceptedRecords @($accepted) "
        "-NewRecords @($changed) -ExpectedRequests $expected -CandidateWorkerVersion worker "
        "-ValidationRun $run;'NO_ERROR'}catch{$_.Exception.Message}",
    )

    assert result == "WORKER_CPU_PROVIDER_EVENT_CONFLICT"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("duplicate_request", "WORKER_CPU_PROVIDER_REQUEST_DUPLICATED"),
        ("wrong_version", "WORKER_CPU_PROVIDER_EVIDENCE_CONTAMINATED"),
        ("wrong_run", "WORKER_CPU_PROVIDER_EVIDENCE_CONTAMINATED"),
        ("unknown_request", "WORKER_CPU_PROVIDER_EVIDENCE_CONTAMINATED"),
    ],
)
def test_provider_evidence_union_fails_closed_on_identity_or_duplicate_conflict(
    tmp_path, case: str, reason: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$case='{case}';$expected=@([pscustomobject]@{{request_id='r-1'}},"
        "[pscustomobject]@{request_id='r-2'});"
        "$first=[pscustomobject]@{request_id='r-1';event_id='e-1';worker_version_id='worker';"
        "validation_run='11111111-1111-1111-1111-111111111111';validation_phase='acceptance';"
        "cpu_ms=1;wall_ms=2;status=200;outcome='ok'};"
        "$second=$first.PSObject.Copy();$second.event_id='e-2';"
        "if($case -ne 'duplicate_request'){$second.request_id='r-2'};"
        "if($case -eq 'wrong_version'){$second.worker_version_id='other'};"
        "if($case -eq 'wrong_run'){$second.validation_run='other'};"
        "if($case -eq 'unknown_request'){$second.request_id='r-3'};"
        "try{$null=Merge-WorkerCpuProviderEvidence -AcceptedRecords @($first) -NewRecords @($second) "
        "-ExpectedRequests $expected -CandidateWorkerVersion worker "
        "-ValidationRun '11111111-1111-1111-1111-111111111111';'NO_ERROR'}"
        "catch{$_.Exception.Message}",
    )

    assert result == reason


def test_targeted_top_up_adds_only_the_deficient_family_and_preserves_full_plan(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$routes=@([pscustomobject]@{family='a';scenario='one';method='GET';path='/a';"
        "warmup_samples=2},[pscustomobject]@{family='b';scenario='two';method='GET';path='/b';"
        "warmup_samples=2});$run='11111111-1111-1111-1111-111111111111';"
        "$plan=New-WorkerCpuRequestPlan -Routes $routes -ValidationRun $run "
        "-CandidateWorkerVersion worker -QualificationKey ('a'*64) "
        "-ValidationPlanDigest ('b'*64) -FixtureDigestSet @();"
        "$before=@($plan.requests).Count;$group=[pscustomobject]@{family='b';scenario='two';"
        "method='GET';path='/b'};$added=@(Add-WorkerCpuPlannedRequests -Plan $plan -Groups @($group) "
        "-SampleKind deficit_top_up -CountPerGroup 4);"
        '$a=@($added|? family -eq \'a\').Count;$b=@($added|? family -eq \'b\').Count;'
        'Write-Output "$before,$($plan.requests.Count),$($added.Count),$a,$b"',
    )

    assert result == "28,32,4,0,4"


@pytest.mark.parametrize(
    ("deficient_count", "provider_available", "expected"),
    [
        (1, True, "True,ELIGIBLE,4"),
        (2, True, "True,ELIGIBLE,8"),
        (4, True, "True,ELIGIBLE,16"),
        (5, True, "False,DEFICIT_REPAIR_GLOBAL_BUDGET_EXCEEDED,20"),
        (2, False, "False,DEFICIT_REPAIR_PROVIDER_UNAVAILABLE,8"),
    ],
)
def test_multi_family_deficit_repair_eligibility_is_globally_bounded(
    tmp_path, deficient_count: int, provider_available: bool, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$count={deficient_count};$available=${str(provider_available).lower()};"
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='original';family='qualified';"
        "scenario='default';method='GET';path='/';phase='acceptance'};"
        "$plan=[pscustomobject]@{validation_run=$run;candidate_worker_version='worker';"
        "qualification_key=$key;policy_version='worker-cpu-policy-v2';requests=@($request)};"
        "$provider=[pscustomobject]@{records=@();recovery=[pscustomobject]@{"
        "background_reads=4;deficit_top_ups=0}};"
        "$groups=@(1..$count|ForEach-Object{[pscustomobject]@{family=('f-'+$_);"
        "scenario='default';method='GET';path=('/f/'+$_);request_query='';fixture='';"
        "observed=9;required=10;missing=3}});"
        "$decision=[pscustomobject]@{state='PROVIDER_EVIDENCE_INSUFFICIENT';"
        "reason='OBSERVED_FAMILY_QUOTA_DEFICIT';deficient_groups=$groups};"
        "$responses=@([pscustomobject]@{request_id='original';passed=$true});"
        "$e=Get-WorkerCpuDeficitRepairEligibility -Plan $plan -ProviderEvidence $provider "
        "-Decision $decision -DirectResponses $responses -CandidateWorkerVersion worker "
        "-QualificationKey $key -ProviderAvailable $available -PlateauStable $true;"
        'Write-Output "$($e.eligible),$($e.reason),$($e.total_request_count)"',
    )

    assert result == expected


def test_deficit_repair_plan_is_frozen_before_send_and_idempotent_across_restart(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='original';family='qualified';"
        "scenario='default';method='GET';path='/';phase='acceptance'};"
        "$plan=[pscustomobject]@{validation_run=$run;candidate_worker_version='worker';"
        "qualification_key=$key;policy_version='worker-cpu-policy-v2';requests=@($request);"
        "request_universe_digest='old'};"
        "$groups=@([pscustomobject]@{family='a';scenario='one';method='GET';path='/a';"
        "request_query='';fixture='';observed=9;required=10;missing=3},"
        "[pscustomobject]@{family='b';scenario='two';method='POST';path='/b';"
        "request_query='';fixture='b.json';observed=9;required=10;missing=3});"
        "$repair=New-WorkerCpuDeficitRepairPlan -RequestPlan $plan -DeficientGroups $groups "
        "-CandidateWorkerVersion worker -QualificationKey $key -PriorProviderDigest ('b'*64) "
        "-PriorObservedTotal 18;"
        "$frozenBeforeApply=Test-Path (Join-Path (Get-WorkerCpuRunRoot $run) "
        "'deficit-repair-plan.json');$first=@(Apply-WorkerCpuDeficitRepairPlan -RequestPlan $plan "
        "-RepairPlan $repair);$countAfterFirst=@($plan.requests).Count;"
        "$second=@(Apply-WorkerCpuDeficitRepairPlan -RequestPlan $plan -RepairPlan $repair);"
        "$ids=@($repair.payload.requests.request_id|Sort-Object -Unique);"
        "$read=Read-WorkerCpuDeficitRepairPlan -ValidationRun $run;"
        'Write-Output "$frozenBeforeApply,$($first.Count),$countAfterFirst,'
        '$($plan.requests.Count),$($ids.Count),$($read.plan_digest -eq $repair.plan_digest),'
        '$($repair.payload.total_request_count)"',
    )

    assert result == "True,8,9,9,8,True,8"


def test_consumed_deficit_repair_cannot_start_a_second_round(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='original';family='a';scenario='one';"
        "method='GET';path='/';phase='acceptance'};"
        "$plan=[pscustomobject]@{validation_run=$run;candidate_worker_version='worker';"
        "qualification_key=$key;policy_version='worker-cpu-policy-v2';requests=@($request)};"
        "$provider=[pscustomobject]@{records=@();recovery=[pscustomobject]@{"
        "background_reads=4;deficit_top_ups=1}};"
        "$group=[pscustomobject]@{family='a';scenario='one';method='GET';path='/';"
        "observed=9;required=10;missing=3};$decision=[pscustomobject]@{"
        "state='PROVIDER_EVIDENCE_INSUFFICIENT';reason='OBSERVED_FAMILY_QUOTA_DEFICIT';"
        "deficient_groups=@($group)};$response=[pscustomobject]@{passed=$true};"
        "$e=Get-WorkerCpuDeficitRepairEligibility -Plan $plan -ProviderEvidence $provider "
        "-Decision $decision -DirectResponses @($response) -CandidateWorkerVersion worker "
        "-QualificationKey $key -ProviderAvailable $true -PlateauStable $true;"
        'Write-Output "$($e.eligible),$($e.reason)"',
    )

    assert result == "False,DEFICIT_REPAIR_ALREADY_CONSUMED"


def test_top_up_omission_remains_insufficient_and_observed_hard_failure_is_terminal(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@();$records=@();foreach($family in @('a','b')){1..16|%{"
        "$id=($family+'-'+$_);$expected += [pscustomobject]@{request_id=$id;family=$family;"
        "scenario='one';method='GET';path='/';phase='acceptance';sample_kind=if($_ -le 12)"
        "{'required'}else{'deficit_top_up'}};if(($family -eq 'a' -and $_ -le 9) -or "
        "($family -eq 'b' -and $_ -le 10)){$records += [pscustomobject]@{request_id=$id;"
        "event_id=('e-'+$id);cpu_ms=if($family -eq 'b' -and $_ -eq 10){10}else{4};"
        "wall_ms=5;status=if($family -eq 'b' -and $_ -eq 10){500}else{200};"
        "outcome=if($family -eq 'b' -and $_ -eq 10){'exceededCpu'}else{'ok'}}}}};"
        "$hard=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected "
        "-ProviderRecords $records -RecoveryBudgetExhausted $true;"
        "$records=@($records|Where-Object{$_.request_id -notlike 'b-*'});"
        "$omitted=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected "
        "-ProviderRecords $records -RecoveryBudgetExhausted $true;"
        'Write-Output "$($hard.state),$($hard.reason),$($omitted.state),'
        '$($omitted.deficient_groups.Count)"',
    )

    assert result == (
        "HARD_FAILURE,WORKER_CPU_OR_PLATFORM_HARD_FAILURE,"
        "PROVIDER_EVIDENCE_INSUFFICIENT,2"
    )


def test_cpu_decision_never_cherry_picks_reserve_or_top_up_samples(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);family='a';scenario='one';"
        "method='GET';path='/';phase='acceptance'}});"
        "$records=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);event_id=('e-'+$_);"
        "cpu_ms=if($_ -eq 12){9}else{4};wall_ms=5;status=200;outcome='ok'}});"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.groups[0].observed),$($d.groups[0].metrics.max_cpu_ms)"',
    )

    assert result == "HEADROOM_REVIEW,12,9"


@pytest.mark.parametrize(
    ("cpu", "status", "outcome", "expected"),
    [
        (4, 500, "ok", "WORKER_CPU_OR_PLATFORM_HARD_FAILURE"),
        (4, 200, "exceededCpu", "WORKER_CPU_OR_PLATFORM_HARD_FAILURE"),
        (4, 200, "exceededMemory", "WORKER_CPU_OR_PLATFORM_HARD_FAILURE"),
    ],
)
def test_cpu_tolerance_cannot_hide_observed_hard_failure(
    tmp_path, cpu: int, status: int, outcome: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);family='a';scenario='one';"
        "method='GET';path='/';phase='acceptance'}});"
        f"$records=@(1..12|%{{[pscustomobject]@{{request_id=('r-'+$_);event_id=('e-'+$_);"
        f"cpu_ms=if($_ -eq 1){{{cpu}}}else{{4}};wall_ms=5;status=if($_ -eq 1){{{status}}}else{{200}};"
        f"outcome=if($_ -eq 1){{'{outcome}'}}else{{'ok'}}}}}});"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        "Write-Output $d.reason",
    )

    assert result == expected


def test_successful_cpu_outlier_requires_one_same_shape_confirmation(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);family='market-history-read';"
        "scenario='24h-30m';method='GET';path='/api/market-history';request_query='limit=500';"
        "phase='acceptance';sample_kind='required'}});"
        "$records=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);event_id=('e-'+$_);"
        "cpu_ms=if($_ -eq 1){15}else{4};wall_ms=5;status=200;outcome='ok'}});"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.outlier_groups.Count),'
        '$($d.cpu_outliers[0].request_id),$($d.global.max_cpu_ms)"',
    )

    assert result == "CPU_OUTLIER_REVIEW_REQUIRED,1,r-1,15"


def test_clean_single_use_confirmation_qualifies_without_erasing_original_outlier(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@();$records=@();1..22|%{$id=('r-'+$_);$kind=if($_ -le 12)"
        "{'required'}else{'outlier_confirmation'};$expected += [pscustomobject]@{"
        "request_id=$id;family='market-history-read';scenario='24h-30m';method='GET';"
        "path='/api/market-history';request_query='limit=500';phase='acceptance';"
        "sample_kind=$kind};$records += [pscustomobject]@{request_id=$id;event_id=('e-'+$_);"
        "cpu_ms=if($_ -eq 1){15}else{4};wall_ms=5;status=200;outcome='ok'}};"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.global.invocations),$($d.global.max_cpu_ms),'
        '$($d.qualification_global.invocations),$($d.qualification_global.max_cpu_ms),'
        '$($d.isolated_cpu_outlier.request_id),$($d.outlier_confirmation.observed),'
        '$($d.outlier_confirmation.metrics.max_cpu_ms)"',
    )

    assert result == (
        "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER,22,15,21,4,r-1,10,4"
    )


@pytest.mark.parametrize(
    ("second_cpu", "expected_reason"),
    [
        (10, "REPRODUCIBLE_WORKER_CPU_PRESSURE"),
        (15, "REPRODUCIBLE_WORKER_CPU_PRESSURE"),
    ],
)
def test_reproduced_cpu_pressure_during_confirmation_is_terminal(
    tmp_path, second_cpu: int, expected_reason: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@();$records=@();1..22|%{$id=('r-'+$_);$kind=if($_ -le 12)"
        "{'required'}else{'outlier_confirmation'};$expected += [pscustomobject]@{"
        "request_id=$id;family='market-history-read';scenario='24h-30m';method='GET';"
        "path='/api/market-history';request_query='limit=500';phase='acceptance';"
        "sample_kind=$kind};$records += [pscustomobject]@{request_id=$id;event_id=('e-'+$_);"
        f"cpu_ms=if($_ -eq 1){{15}}elseif($_ -eq 13){{{second_cpu}}}else{{4}};"
        "wall_ms=5;status=200;outcome='ok'}};"
        "$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records;"
        'Write-Output "$($d.state),$($d.reason),$($d.cpu_outliers.Count)"',
    )

    assert result == f"HARD_FAILURE,{expected_reason},2"


def test_outlier_confirmation_plan_is_frozen_idempotent_and_exact_key_bound(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='r-1';family='market-history-read';"
        "scenario='24h-30m';method='GET';path='/api/market-history';request_query='limit=500';"
        "fixture='';phase='acceptance';sample_kind='required'};"
        "$plan=[pscustomobject]@{validation_run=$run;candidate_worker_version='worker';"
        "qualification_key=$key;policy_version='worker-cpu-policy-v2';requests=@($request);"
        "request_universe_digest='old'};$group=[pscustomobject]@{family='market-history-read';"
        "scenario='24h-30m';method='GET';path='/api/market-history';request_query='limit=500';"
        "fixture='';outliers=@([pscustomobject]@{request_id='r-1';event_id='e-1';cpu_ms=15;"
        "wall_ms=102;status=200;outcome='ok'})};"
        "$frozen=New-WorkerCpuOutlierConfirmationPlan -RequestPlan $plan -OutlierGroup $group "
        "-CandidateWorkerVersion worker -QualificationKey $key -PriorProviderDigest ('b'*64);"
        "$before=Test-Path (Join-Path (Get-WorkerCpuRunRoot $run) 'outlier-confirmation-plan.json');"
        "$first=@(Apply-WorkerCpuOutlierConfirmationPlan -RequestPlan $plan "
        "-ConfirmationPlan $frozen);$second=@(Apply-WorkerCpuOutlierConfirmationPlan "
        "-RequestPlan $plan -ConfirmationPlan $frozen);$mismatch='NO_ERROR';try{$null="
        "New-WorkerCpuOutlierConfirmationPlan -RequestPlan $plan -OutlierGroup $group "
        "-CandidateWorkerVersion worker -QualificationKey ('c'*64) "
        "-PriorProviderDigest ('b'*64)}catch{$mismatch=$_.Exception.Message};"
        '$sameShape=@($frozen.payload.requests|Where-Object{'
        '$_.path -eq "/api/market-history" -and $_.request_query -eq "limit=500"}).Count;'
        'Write-Output "$before,$($first.Count),$($second.Count),$($plan.requests.Count),'
        '$(@($frozen.payload.requests.request_id|Sort-Object -Unique).Count),$sameShape,$mismatch"',
    )

    assert result == (
        "True,10,10,11,10,10,WORKER_CPU_OUTLIER_CONFIRMATION_PLAN_IDENTITY_MISMATCH"
    )


def test_controller_sends_only_the_bounded_outlier_confirmation_requests(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='r-1';family='market-history-read';"
        "scenario='24h-30m';method='GET';path='/api/market-history';request_query='limit=500';"
        "fixture='';phase='acceptance';sample_kind='required'};"
        "$plan=[pscustomobject]@{validation_run=$run;candidate_worker_version='worker';"
        "qualification_key=$key;policy_version='worker-cpu-policy-v2';requests=@($request);"
        "request_universe_digest='old'};$group=[pscustomobject]@{family='market-history-read';"
        "scenario='24h-30m';method='GET';path='/api/market-history';request_query='limit=500';"
        "fixture='';outliers=@([pscustomobject]@{request_id='r-1';event_id='e-1';cpu_ms=15;"
        "wall_ms=102;status=200;outcome='ok'})};$decision=[pscustomobject]@{"
        "state='CPU_OUTLIER_REVIEW_REQUIRED';outlier_groups=@($group)};"
        "$provider=[pscustomobject]@{records=@();observed_universe_digest=('b'*64);"
        "recovery=[pscustomobject]@{active_reads=4;background_reads=4;deficit_top_ups=0;"
        "headroom_top_ups=0;outlier_confirmations=0}};$candidate=[pscustomobject]@{"
        "worker_version_id='worker';git_sha=('c'*40)};$route=[pscustomobject]@{"
        "family='market-history-read';scenario='24h-30m';method='GET';"
        "path='/api/market-history';request_query='limit=500';fixture=''};"
        "$routePlan=[pscustomobject]@{worker_reads=@($route);worker_writes=@()};"
        "$script:sent=0;function Invoke-CandidateRouteSample{param($Route,$RequestId);"
        "$script:sent++;[pscustomobject]@{request_id=$RequestId;requested_worker_version='worker';"
        "observed_worker_version='worker';observed_git_sha=('c'*40);status=200;passed=$true;"
        "reason='';route=$Route.path;resource=$Route.family;d1_operations='1';"
        "request_bytes='0';response_bytes='10';response_content_digest=('d'*64)}};"
        "$out=Invoke-CandidateCpuOutlierConfirmation -Candidate $candidate -RoutePlan $routePlan "
        "-RequestPlan $plan -Decision $decision -ProviderEvidence $provider "
        "-QualificationKey $key -FixtureRoot '';"
        "$second='NO_ERROR';try{$null=Invoke-CandidateCpuOutlierConfirmation "
        "-Candidate $candidate -RoutePlan $routePlan -RequestPlan $plan -Decision $decision "
        "-ProviderEvidence $provider -QualificationKey $key -FixtureRoot ''}"
        "catch{$second=$_.Exception.Message};"
        'Write-Output "$script:sent,$($out.requests.Count),$($plan.requests.Count),'
        '$($provider.recovery.outlier_confirmations),$second"',
    )

    assert result == "10,10,11,1,WORKER_CPU_OUTLIER_CONFIRMATION_NOT_ELIGIBLE"


def test_incomplete_confirmation_is_nonqualifying_and_cannot_start_another_round(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@();$records=@();1..22|%{$id=('r-'+$_);$kind=if($_ -le 12)"
        "{'required'}else{'outlier_confirmation'};$expected += [pscustomobject]@{"
        "request_id=$id;family='market-history-read';scenario='24h-30m';method='GET';"
        "path='/api/market-history';request_query='limit=500';phase='acceptance';"
        "sample_kind=$kind};if($_ -ne 22){$records += [pscustomobject]@{request_id=$id;"
        "event_id=('e-'+$_);cpu_ms=if($_ -eq 1){15}else{4};wall_ms=5;status=200;"
        "outcome='ok'}}};$d=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected "
        "-ProviderRecords $records -RecoveryBudgetExhausted $true;"
        'Write-Output "$($d.state),$($d.reason),$($d.confirmation_missing_request_ids.Count)"',
    )

    assert result == (
        "PROVIDER_EVIDENCE_INSUFFICIENT,"
        "CPU_OUTLIER_CONFIRMATION_EVIDENCE_INCOMPLETE,1"
    )


def test_isolated_outlier_receipt_retains_raw_event_and_exact_candidate_identity(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$key='a'*64;$decision=[pscustomobject]@{state='QUALIFIED_WITH_ISOLATED_CPU_OUTLIER';"
        "global=[pscustomobject]@{invocations=22;max_cpu_ms=15};"
        "qualification_global=[pscustomobject]@{invocations=21;max_cpu_ms=4};"
        "isolated_cpu_outlier=[pscustomobject]@{request_id='r-1';event_id='e-1';cpu_ms=15;"
        "status=200;outcome='ok'};outlier_confirmation=[pscustomobject]@{planned=10;"
        "observed=10;request_ids=@(1..10|%{'c-'+$_})}};"
        "$q=[pscustomobject]@{key=$key;fields=[pscustomobject]@{policy_digest=('b'*64)};"
        "candidate_worker_version='worker';candidate_git_sha=('c'*40);"
        "exact_candidate_binding=[pscustomobject]@{executable_bundle_etag=('d'*64)}};"
        "$written=Write-WorkerCpuQualificationReceipt -Qualification $q "
        "-ValidationRun '11111111-1111-1111-1111-111111111111' -Decision $decision;"
        "$read=Get-WorkerCpuQualificationReceipt -QualificationKey $key;"
        'Write-Output "$($read.outcome),$($read.source_worker_version),'
        '$($read.source_git_sha),$($read.cpu_evidence.global.max_cpu_ms),'
        '$($read.cpu_evidence.isolated_cpu_outlier.request_id),'
        '$($read.cpu_evidence.outlier_confirmation.observed)"',
    )

    assert result == (
        "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER,worker," + "c" * 40 + ",15,r-1,10"
    )


def test_aggregate_corroboration_cannot_override_raw_and_contradiction_fails_closed(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$expected=@(1..12|%{[pscustomobject]@{request_id=('r-'+$_);family='a';scenario='one';"
        "method='GET';path='/';phase='acceptance'}});"
        "$records=@(1..10|%{[pscustomobject]@{request_id=('r-'+$_);event_id=('e-'+$_);"
        "cpu_ms=4;wall_ms=5;status=200;outcome='ok'}});"
        "$matching=[pscustomobject]@{invocations=10};"
        "$ok=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records "
        "-AggregateEvidence $matching;$contradiction=[pscustomobject]@{invocations=9};"
        "$bad=Get-WorkerCpuQualificationDecision -ExpectedRequests $expected -ProviderRecords $records "
        '-AggregateEvidence $contradiction;Write-Output "$($ok.state),$($ok.aggregate.invocations),$($bad.reason)"',
    )

    assert result == (
        "QUALIFIED_WITH_PROVIDER_OMISSION,10,PROVIDER_CORROBORATION_CONTRADICTION"
    )


def test_control_plane_only_git_and_provenance_etag_change_reuse_cpu_behavior_key(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-WorkerVersionQualificationMetadata{param($Candidate)[pscustomobject]@{"
        "executable_bundle_etag=$script:etag;compatibility_date='2026-08-07';"
        "compatibility_flags=@('nodejs_compat');assets=[pscustomobject]@{serve_directly=$true};"
        "bindings=@([pscustomobject]@{name='DB';type='d1';resource='database'})}};"
        "function Get-WorkerCpuGitTreeDigest{param($Revision,$Paths) $script:tree};"
        "$script:etag='a'*64;$script:tree='f'*64;$plan=[pscustomobject]@{worker_reads=@([pscustomobject]@{"
        "family='status';scenario='default';method='GET';path='/api/status'});worker_writes=@()};"
        "$a=[pscustomobject]@{worker_version_id='one';git_sha='1'*40};"
        "$b=[pscustomobject]@{worker_version_id='two';git_sha='2'*40};"
        "$fixtures=@([pscustomobject]@{name='fixture';bytes=1;sha256='c'*64});"
        "$q1=Get-WorkerCpuQualificationIdentity -Candidate $a -RoutePlan $plan -FixtureDigestSet $fixtures;"
        "$script:etag='b'*64;$q2=Get-WorkerCpuQualificationIdentity -Candidate $b -RoutePlan $plan -FixtureDigestSet $fixtures;"
        "$script:tree='e'*64;$q3=Get-WorkerCpuQualificationIdentity -Candidate $b -RoutePlan $plan -FixtureDigestSet $fixtures;"
        'Write-Output "$($q1.key -eq $q2.key),$($q2.key -ne $q3.key),$($q1.key.Length)"',
    )

    assert result == "True,True,64"


def test_reused_cpu_receipt_binds_source_and_current_exact_worker_artifacts(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$key='a'*64;$qualification=[pscustomobject]@{key=$key;fields=[pscustomobject]@{version='v1'};"
        "candidate_worker_version='source-worker';candidate_git_sha=('1'*40);"
        "exact_candidate_binding=[pscustomobject]@{executable_bundle_etag=('b'*64)}};"
        "$decision=[pscustomobject]@{state='QUALIFIED';groups=@();"
        "global=[pscustomobject]@{invocations=10;max_cpu_ms=4;p95_cpu_ms=4;p99_cpu_ms=4;"
        "exceeded_cpu=0;exceeded_memory=0;responses_1102=0;responses_5xx=0}};"
        "$written=Write-WorkerCpuQualificationReceipt -Qualification $qualification "
        "-ValidationRun '11111111-1111-1111-1111-111111111111' -Decision $decision;"
        "$receipt=Get-WorkerCpuQualificationReceipt -QualificationKey $key;"
        "$candidate=[pscustomobject]@{worker_version_id='current-worker';git_sha=('2'*40)};"
        "$current=[pscustomobject]@{exact_candidate_binding=[pscustomobject]@{"
        "executable_bundle_etag=('c'*64)}};"
        "$e=New-ReusedWorkerCpuEvidence -Receipt $receipt -Candidate $candidate "
        "-Qualification $current;"
        'Write-Output "$($e.qualification_mode),$($e.source_worker_version),'
        '$($e.worker_version_id),$($e.source_executable_bundle_etag),'
        '$($e.current_executable_bundle_etag),$($receipt.receipt_digest -eq $written.receipt_digest)"',
    )

    assert result == (
        "CPU_QUALIFICATION_REUSED,source-worker,current-worker,"
        + "b" * 64 + "," + "c" * 64 + ",True"
    )


@pytest.mark.parametrize(
    ("case", "expected_diagnostic"),
    [
        ("missing_cpu", "OBSERVABILITY_SCHEMA_INVALID"),
        ("nonnumeric_count", "OBSERVABILITY_SCHEMA_INVALID"),
        ("malformed_cursor", "OBSERVABILITY_EVENT_CURSOR_INVALID"),
    ],
)
def test_observability_adapter_rejects_malformed_provider_pages(
    tmp_path, case: str, expected_diagnostic: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$case='{case}';"
        "function Get-ReleaseSecret{return [pscustomobject]@{available=$true;"
        "value='adapter-release-token';source='LOCAL_SECRET_FILE';diagnostic=$null}};"
        "function Invoke-RestMethod{$workers=[pscustomobject]@{wallTimeMs=2;outcome='ok';"
        "scriptVersion=[pscustomobject]@{id='worker'};event=[pscustomobject]@{path='/api/status';"
        "request=[pscustomobject]@{method='GET';headers=[pscustomobject]@{"
        "'x-aurum-request-id'='request';'x-aurum-validation-run'='run';"
        "'x-aurum-validation-phase'='acceptance'}};response=[pscustomobject]@{status=200}}};"
        "if($case -ne 'missing_cpu'){$workers|Add-Member cpuTimeMs 1};"
        "$event=[pscustomobject]@{'$metadata'=[pscustomobject]@{id='same';type='cf-worker-event'};"
        "'$workers'=$workers};$events=if($case -eq 'missing_cpu'){@($event)}"
        "else{@(1..2000|ForEach-Object{$event})};return [pscustomobject]@{success=$true;"
        "result=[pscustomobject]@{events=[pscustomobject]@{count=$(if($case -eq 'malformed_cursor'){2001}"
        "elseif($case -eq 'nonnumeric_count'){'bad'}else{$events.Count});events=$events;"
        "fields=@();series=@()}}}};"
        "$null=Invoke-WorkersObservabilityEventsQuery "
        "-Filters @([pscustomobject]@{key='k';value='v'}) "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-Offset $(if($case -eq 'malformed_cursor'){'same'}else{''});"
        "Write-Output $script:lastWorkersObservabilityDiagnostic",
    )

    assert result == expected_diagnostic


def test_frozen_raw_telemetry_paginates_without_moving_upper_bound(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}';"
        "function Start-Sleep{};function New-Event($number){"
        "$id=('event-{0:d4}' -f $number);$request=('request-{0:d4}' -f $number);"
        "[pscustomobject]@{event_id=$id;event_type='cf-worker-event';worker_version_id='worker';"
        "request_id=$request;validation_run='11111111-1111-1111-1111-111111111111';validation_phase='acceptance';method='GET';"
        "path='/api/status';status=200;outcome='ok';cpu_ms=0;wall_ms=1}};"
        "$script:queries=0;$script:bounds=@();function Invoke-WorkersObservabilityEventsQuery{"
        "param($To,$Offset);$script:queries++;$script:bounds+=$To.ToString('o');"
        "if($Offset){[pscustomobject]@{total_count=2001;page_count=1;records=@((New-Event 2001));next_offset=''}}"
        "else{[pscustomobject]@{total_count=2001;page_count=2000;records=@(1..2000|ForEach-Object{New-Event $_});"
        "next_offset='event-2000'}}};"
        "$expected=@(1..2001|ForEach-Object{[pscustomobject]@{"
        "request_id=('request-{0:d4}' -f $_);family='status-read';scenario='status';"
        "method='GET';path='/api/status';phase='acceptance'}});"
        "$e=Get-CandidateFrozenPlatformEvidence -Candidate $candidate "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-ExpectedRequests $expected -ValidationRun '11111111-1111-1111-1111-111111111111';"
        '$uniqueBounds=@($script:bounds|Select-Object -Unique);'
        'Write-Output "$script:queries,$($e.invocations),$($e.qualification_state),$($uniqueBounds.Count)"',
    )

    assert result == "2,2001,QUALIFIED,1"


def test_delayed_platform_telemetry_keeps_exact_candidate_retryable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$state.candidate.validation_state='NEW';"
        "$state.candidate.compatibility_state='APPROVED';Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('web/app/api/status/route.ts')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$true;"
        "requires_validation=$true;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true;state='READY'}};"
        "function Invoke-CandidateWorkerValidation{return [pscustomobject]@{passed=$true;"
        "validation_run='run-pending';expected_worker_invocations=330;"
        "observed_worker_invocations=$null;static_observability_state='PASSED';"
        "observability_credential_source='LOCAL_SECRET_FILE';"
            "observability_diagnostic='PROVIDER_EVIDENCE_PENDING';"
        "telemetry_window_from='2026-08-27T20:00:00Z';telemetry_window_to='2026-08-27T20:01:00Z';"
        "expected_requests=@([pscustomobject]@{request_id='request-1';family='status-read';"
        "scenario='status';method='GET';path='/api/status'});"
        "routes=@([pscustomobject]@{route='/api/status';boundary='WORKER_READ';passed=$true});"
        "cpu_evidence=$null}};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$saved=Get-ReleaseControlState;$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$($saved.candidate.validation_state),'
        '$($saved.candidate.validation.windows),$($saved.candidate.validation.cloudflare),'
        '$($saved.candidate.validation.reason),'
        '$(@($saved.candidate.validation.expected_requests).Count),'
        '$([bool]$saved.candidate.validation.telemetry_window_from),'
        '$($history.Contains(\'CANDIDATE_PLATFORM_PENDING\'))"',
    )
    assert result == (
        "PLATFORM_PENDING,PASSED,PENDING,"
        "PROVIDER_EVIDENCE_PENDING,1,True,True"
    )


def test_platform_retry_resumes_exact_telemetry_receipt_without_replaying_routes(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + _mock_free_plan_and_qualification_authority()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$run='11111111-1111-1111-1111-111111111111';"
        "$route=[pscustomobject]@{route='/api/status';path='/api/status';request_query='';"
        "family='status-read';scenario='status';method='GET';boundary='WORKER_READ';"
        "strategy='DIRECT_REQUEST';fixture='';passed=$true};"
        "$cpuPlan=[pscustomobject]@{worker_cpu_required=$true;requires_validation=$true;"
        "static_assets=@();worker_reads=@($route);worker_writes=@()};"
        "$request=[pscustomobject]@{request_id='request-1';family='status-read';scenario='status';"
        "method='GET';path='/api/status';request_query='';fixture='';phase='acceptance';"
        "sample_kind='required';planned_at='2026-08-27T20:00:00Z'};"
        "$plan=[pscustomobject]@{schema_version='worker-directed-ledger-v1';validation_run=$run;"
        "candidate_worker_version=$candidate.worker_version_id;qualification_key=('a'*64);"
        "policy_version='worker-cpu-policy-v2';validation_plan_digest=('b'*64);"
        "fixture_digest_set=@();requests=@($request);request_universe_digest=('c'*64)};"
        "Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot $run) 'plan.json') -Value $plan;"
        "$response=[pscustomobject]@{requested_worker_version=$candidate.worker_version_id;"
        "observed_worker_version=$candidate.worker_version_id;observed_git_sha=$candidate.git_sha;"
        "status=200;passed=$true;reason='';route='/api/status';resource='status';"
        "d1_operations='1';request_bytes='0';response_bytes='10';response_content_digest=('d'*64)};"
        "$null=Add-WorkerCpuDirectResponse -ValidationRun $run -Request $request -Response $response;"
        "$provider=[pscustomobject]@{schema_version='worker-provider-evidence-v1';validation_run=$run;"
        "records=@([pscustomobject]@{event_id='event-1';request_id='request-1';cpu_ms=1;status=200;outcome='ok'});"
        "recovery=[pscustomobject]@{active_reads=0;background_reads=0;deficit_top_ups=0;headroom_top_ups=0}};"
        "Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot $run) 'provider-evidence.json') -Value $provider;"
        "$state.candidate.compatibility_state='APPROVED';"
        "$state.candidate.validation_state='PLATFORM_PENDING';"
        "$state.candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PENDING';"
        "reason='PROVIDER_EVIDENCE_PENDING';observability_diagnostic='PROVIDER_EVIDENCE_PENDING';"
        "validation_run=$run;expected_worker_invocations=1;static_worker_invocations=0;"
        "static_observability_state='PASSED';telemetry_window_from='2026-08-27T20:00:00Z';"
        "telemetry_window_to='2026-08-27T20:01:00Z';"
        "expected_requests=@($request);routes=@($route);cpu_route_plan=$cpuPlan;"
        "worker_qualification=[pscustomobject]@{key=('a'*64)}};"
        "Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('web/app/api/status/route.ts')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return $cpuPlan};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true}};"
        "function Invoke-CandidateWorkerValidation{throw 'ROUTES_REPLAYED'};"
        "$script:qualificationWrites=0;function Get-WorkerCpuQualificationDecision{"
        "return [pscustomobject]@{state='QUALIFIED';groups=@();global=[pscustomobject]@{invocations=1}}};"
        "function Write-WorkerCpuQualificationReceipt{$script:qualificationWrites++;"
        "return [pscustomobject]@{receipt_digest=('e'*64)}};"
        "$script:evidenceCalls=0;function Get-CandidateFrozenPlatformEvidence{"
        "param($ExpectedRequests,$ValidationRun);$script:evidenceCalls++;"
        "return [pscustomobject]@{invocations=1;passed=$true;gate_state='PASSED';"
        "expected_invocations=1;responses_5xx=0;responses_1102=0;exceeded_cpu=0}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{passed=$true}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{state='NOT_REQUIRED'}};"
        "function Test-CandidateAuthBoundaryChanged{return $false};"
        "$passed=Invoke-AutomaticCandidateValidation -Candidate $candidate;"
        "$saved=Get-ReleaseControlState;"
        'Write-Output "$passed,$script:evidenceCalls,$($saved.candidate.validation_state),'
        '$($saved.candidate.validation.routes[0].route),$script:qualificationWrites,'
        '$($saved.candidate.validation.cpu_qualification_mode),'
        '$($saved.candidate.validation.cpu_evidence.qualification_receipt_digest)"',
    )

    assert result == (
        "True,1,PASSED,/api/status,1,CPU_QUALIFICATION_FRESH," + "e" * 64
    )


def test_platform_resume_repairs_two_families_without_replaying_complete_validation(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$candidate=[pscustomobject]@{worker_version_id='worker';git_sha=('b'*40);"
        "validation_key='validation'};$routes=@();$requests=@();foreach($family in @('a','b')){"
        "$route=[pscustomobject]@{family=$family;scenario='default';method='GET';"
        "path=('/'+$family);request_query='';fixture=''};$routes += $route;"
        "$request=[pscustomobject]@{request_id=('original-'+$family);family=$family;"
        "scenario='default';method='GET';path=('/'+$family);request_query='';fixture='';"
        "phase='acceptance';sample_kind='required'};$requests += $request};"
        "$plan=[pscustomobject]@{schema_version='worker-directed-ledger-v1';validation_run=$run;"
        "candidate_worker_version='worker';qualification_key=$key;"
        "policy_version='worker-cpu-policy-v2';requests=$requests;request_universe_digest='old'};"
        "Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot $run) 'plan.json') "
        "-Value $plan;foreach($request in $requests){$response=[pscustomobject]@{"
        "requested_worker_version='worker';observed_worker_version='worker';"
        "observed_git_sha=('b'*40);status=200;passed=$true;reason='';route=$request.path;"
        "resource=$request.family;d1_operations='';request_bytes='';response_bytes='';"
        "response_content_digest=('c'*64)};$null=Add-WorkerCpuDirectResponse "
        "-ValidationRun $run -Request $request -Response $response};"
        "$recovery=[pscustomobject]@{active_reads=6;background_reads=4;deficit_top_ups=0;"
        "headroom_top_ups=0};$stored=Write-WorkerCpuProviderEvidence -ValidationRun $run "
        "-Records @() -RecoveryState $recovery;"
        "$groups=@($routes|ForEach-Object{[pscustomobject]@{family=$_.family;"
        "scenario=$_.scenario;method=$_.method;path=$_.path;request_query='';fixture='';"
        "observed=9;required=10;missing=3}});$decision=[pscustomobject]@{"
        "state='PROVIDER_EVIDENCE_INSUFFICIENT';reason='OBSERVED_FAMILY_QUOTA_DEFICIT';"
        "deficient_groups=$groups};"
        "$validation=[pscustomobject]@{key='validation';validation_run=$run;"
        "expected_requests=$requests;cpu_route_plan=[pscustomobject]@{worker_reads=$routes;"
        "worker_writes=@()};telemetry_window_from='2026-08-30T00:00:00Z';"
        "telemetry_window_to='2026-08-30T00:01:00Z';routes=$routes;"
        "worker_qualification=[pscustomobject]@{key=$key};expected_worker_invocations=2;"
        "static_worker_invocations=0;static_observability_state='PASSED'};"
        "function Get-WorkerCpuQualificationDecision{return $decision};"
        "function Get-CandidateDeficitRepairProviderPreflight{return [pscustomobject]@{"
        "available=$true;plateau_stable=$true;decision=$decision;provider_evidence=$stored;"
        "digest_changed=$false}};function Invoke-CandidateWorkerValidation{throw 'FULL_REPLAY'};"
        "$script:sent=0;function Add-WorkerCpuRequestSend{$script:sent++};"
        "function Invoke-CandidateRouteSample{param($Route,$RequestId);"
        "[pscustomobject]@{request_id=$RequestId;requested_worker_version=$Route.expected_worker_version;"
        "observed_worker_version='worker';observed_git_sha=('b'*40);status=200;passed=[bool]("
        "$Route.expected_worker_version -eq 'worker' -and $Route.expected_git_sha -eq ('b'*40));"
        "reason='';route=$Route.path;resource=$Route.family;d1_operations='';"
        "request_bytes='';response_bytes='';response_content_digest=('d'*64)}};"
        "function Get-CandidateFrozenPlatformEvidence{return $null};"
        "$result=Resume-CandidateWorkerPlatformEvidence -Candidate $candidate "
        "-Validation $validation;$savedPlan=Read-WorkerCpuRunArtifact -ValidationRun $run "
        "-Name 'plan.json';$repair=Read-WorkerCpuDeficitRepairPlan -ValidationRun $run;"
        'Write-Output "$script:sent,$($result.expected_worker_invocations),'
        '$($result.directed_request_ledger.planned),$($result.directed_request_ledger.completed),'
        '$($repair.payload.deficient_families.Count),$($savedPlan.requests.Count)"',
    )

    assert result == "8,10,10,10,2,10"


def test_read_only_deficit_repair_reconciles_only_missing_controller_expectation(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$request=[pscustomobject]@{request_id='repair-1';family='market-history-read';"
        "scenario='';method='GET';path='/api/market-history';phase='acceptance';"
        "sample_kind='deficit_top_up'};$null=Add-WorkerCpuRequestSend -ValidationRun $run "
        "-Request $request -CandidateWorkerVersion 'worker' -QualificationKey $key;"
        "$response=[pscustomobject]@{requested_worker_version='';observed_worker_version='worker';"
        "observed_git_sha=('b'*40);status=200;passed=$false;reason='WORKER_IDENTITY_MISMATCH';"
        "route='/api/market-history';resource='market-history';mutated=$false;d1_operations='1';"
        "request_bytes='0';response_bytes='10';response_content_digest=('c'*64)};"
        "$null=Add-WorkerCpuDirectResponse -ValidationRun $run -Request $request -Response $response;"
        "$repaired=Repair-WorkerCpuDirectResponseIdentityExpectation -ValidationRun $run "
        "-Request $request -CandidateWorkerVersion 'worker' -CandidateGitSha ('b'*40) "
        "-QualificationKey $key;$receipt=@(Get-WorkerCpuDirectResponseReceipts $run)[0];"
        "$write=$request.PSObject.Copy();$write.request_id='write-1';$write.method='POST';"
        "$writeRejected=Repair-WorkerCpuDirectResponseIdentityExpectation -ValidationRun $run "
        "-Request $write -CandidateWorkerVersion 'worker' -CandidateGitSha ('b'*40) "
        "-QualificationKey $key;$wrong=$request.PSObject.Copy();$wrong.request_id='wrong-1';"
        "$null=Add-WorkerCpuRequestSend -ValidationRun $run -Request $wrong "
        "-CandidateWorkerVersion 'worker' -QualificationKey $key;$wrongResponse=$response.PSObject.Copy();"
        "$wrongResponse.observed_worker_version='other';$null=Add-WorkerCpuDirectResponse "
        "-ValidationRun $run -Request $wrong -Response $wrongResponse;"
        "$wrongRejected=Repair-WorkerCpuDirectResponseIdentityExpectation -ValidationRun $run "
        "-Request $wrong -CandidateWorkerVersion 'worker' -CandidateGitSha ('b'*40) "
        "-QualificationKey $key;Write-Output \"$repaired,$($receipt.passed),"
        "$($receipt.expected_worker_version),$writeRejected,$wrongRejected\"",
    )

    assert result == "True,True,worker,False,False"


def test_platform_resume_continues_existing_repair_after_audited_read_reconciliation(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$candidate=[pscustomobject]@{worker_version_id='worker';git_sha=('b'*40);"
        "validation_key='validation'};$routes=@();$requests=@();foreach($family in @('a','b')){"
        "$route=[pscustomobject]@{family=$family;scenario='default';method='GET';"
        "path=('/'+$family);request_query='';fixture='';strategy='DIRECT_REQUEST'};"
        "$routes += $route;$request=[pscustomobject]@{request_id=('original-'+$family);"
        "family=$family;scenario='default';method='GET';path=('/'+$family);request_query='';"
        "fixture='';phase='acceptance';sample_kind='required'};$requests += $request};"
        "$plan=[pscustomobject]@{schema_version='worker-directed-ledger-v1';validation_run=$run;"
        "candidate_worker_version='worker';qualification_key=$key;policy_version='policy';"
        "requests=$requests;request_universe_digest='old'};Write-WorkerCpuAtomicJson -Path "
        "(Join-Path (Get-WorkerCpuRunRoot $run) 'plan.json') -Value $plan;foreach($request in "
        "$requests){$response=[pscustomobject]@{requested_worker_version='worker';"
        "observed_worker_version='worker';observed_git_sha=('b'*40);status=200;passed=$true;"
        "reason='';route=$request.path;resource=$request.family;mutated=$false;d1_operations='';"
        "request_bytes='';response_bytes='';response_content_digest=('c'*64)};"
        "$null=Add-WorkerCpuDirectResponse -ValidationRun $run -Request $request -Response $response};"
        "$recovery=[pscustomobject]@{active_reads=6;background_reads=4;deficit_top_ups=0;"
        "headroom_top_ups=0};$stored=Write-WorkerCpuProviderEvidence -ValidationRun $run "
        "-Records @() -RecoveryState $recovery;$groups=@($routes|ForEach-Object{"
        "[pscustomobject]@{family=$_.family;scenario=$_.scenario;method=$_.method;path=$_.path;"
        "request_query='';fixture='';observed=9;required=10;missing=3}});"
        "$repair=New-WorkerCpuDeficitRepairPlan -RequestPlan $plan -DeficientGroups $groups "
        "-CandidateWorkerVersion 'worker' -QualificationKey $key -PriorProviderDigest ('d'*64) "
        "-PriorObservedTotal 18;$repairRequests=@(Apply-WorkerCpuDeficitRepairPlan -RequestPlan "
        "$plan -RepairPlan $repair);$first=$repairRequests[0];$null=Add-WorkerCpuRequestSend "
        "-ValidationRun $run -Request $first -CandidateWorkerVersion 'worker' -QualificationKey $key;"
        "$bad=[pscustomobject]@{requested_worker_version='';observed_worker_version='worker';"
        "observed_git_sha=('b'*40);status=200;passed=$false;reason='WORKER_IDENTITY_MISMATCH';"
        "route=$first.path;resource=$first.family;mutated=$false;d1_operations='';request_bytes='';"
        "response_bytes='';response_content_digest=('e'*64)};$null=Add-WorkerCpuDirectResponse "
        "-ValidationRun $run -Request $first -Response $bad;$validation=[pscustomobject]@{"
        "key='validation';validation_run=$run;expected_requests=$requests;cpu_route_plan="
        "[pscustomobject]@{worker_reads=$routes;worker_writes=@()};telemetry_window_from="
        "'2026-08-30T00:00:00Z';telemetry_window_to='2026-08-30T00:01:00Z';routes=$routes;"
        "worker_qualification=[pscustomobject]@{key=$key};expected_worker_invocations=2;"
        "static_worker_invocations=0;static_observability_state='PASSED'};"
        "$script:sent=0;function Add-WorkerCpuRequestSend{$script:sent++};"
        "function Invoke-CandidateRouteSample{param($Route,$RequestId);[pscustomobject]@{"
        "request_id=$RequestId;requested_worker_version=$Route.expected_worker_version;"
        "observed_worker_version='worker';observed_git_sha=('b'*40);status=200;passed=$true;"
        "reason='';route=$Route.path;resource=$Route.family;mutated=$false;d1_operations='';"
        "request_bytes='';response_bytes='';response_content_digest=('f'*64)}};"
        "function Get-CandidateFrozenPlatformEvidence{return $null};"
        "$result=Resume-CandidateWorkerPlatformEvidence -Candidate $candidate -Validation $validation;"
        "$receipts=@(Get-WorkerCpuDirectResponseReceipts $run);$events=@(Get-Content "
        "(Join-Path (Get-WorkerCpuRunRoot $run) 'directed-ledger.jsonl')|ForEach-Object{$_|ConvertFrom-Json});"
        "$resumedEvidence=Read-WorkerCpuRunArtifact $run 'provider-evidence.json';"
        "Write-Output \"$script:sent,$($receipts.Count),$(@($receipts|Where-Object{!$_.passed}).Count),"
        "$(@($events|Where-Object{$_.event -eq 'DIRECT_RESPONSE_IDENTITY_RECONCILED'}).Count),"
        "$(@((Read-WorkerCpuRunArtifact $run 'plan.json').requests).Count),"
        "$($resumedEvidence.recovery.active_reads),$($resumedEvidence.recovery.background_reads)\"",
    )

    assert result == "7,10,0,1,10,0,0"


def test_platform_resume_reopens_recovery_for_completed_persisted_repair(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$run='11111111-1111-1111-1111-111111111111';$key='a'*64;"
        "$candidate=[pscustomobject]@{worker_version_id='worker';git_sha=('b'*40);"
        "validation_key='validation'};$route=[pscustomobject]@{family='a';scenario='default';"
        "method='GET';path='/a';request_query='';fixture='';strategy='DIRECT_REQUEST'};"
        "$request=[pscustomobject]@{request_id='original';family='a';scenario='default';"
        "method='GET';path='/a';request_query='';fixture='';phase='acceptance';"
        "sample_kind='required'};$plan=[pscustomobject]@{schema_version='worker-directed-ledger-v1';"
        "validation_run=$run;candidate_worker_version='worker';qualification_key=$key;"
        "policy_version='policy';requests=@($request);request_universe_digest='old'};"
        "Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot $run) 'plan.json') "
        "-Value $plan;$response=[pscustomobject]@{requested_worker_version='worker';"
        "observed_worker_version='worker';observed_git_sha=('b'*40);status=200;passed=$true;"
        "reason='';route='/a';resource='a';mutated=$false;d1_operations='';request_bytes='';"
        "response_bytes='';response_content_digest=('c'*64)};$null=Add-WorkerCpuDirectResponse "
        "-ValidationRun $run -Request $request -Response $response;$recovery=[pscustomobject]@{"
        "active_reads=6;background_reads=4;deficit_top_ups=0;headroom_top_ups=0;"
        "last_read_at='2026-08-30T00:00:00Z'};$stored=Write-WorkerCpuProviderEvidence "
        "-ValidationRun $run -Records @() -RecoveryState $recovery;$group=[pscustomobject]@{"
        "family='a';scenario='default';method='GET';path='/a';request_query='';fixture='';"
        "observed=9;required=10;missing=1};$repair=New-WorkerCpuDeficitRepairPlan "
        "-RequestPlan $plan -DeficientGroups @($group) -CandidateWorkerVersion 'worker' "
        "-QualificationKey $key -PriorProviderDigest ('d'*64) -PriorObservedTotal 9;"
        "$repairRequests=@(Apply-WorkerCpuDeficitRepairPlan -RequestPlan $plan -RepairPlan $repair);"
        "foreach($topUpRequest in $repairRequests){$topUpResponse=$response.PSObject.Copy();"
        "$topUpResponse.response_content_digest=('e'*64);$null=Add-WorkerCpuDirectResponse "
        "-ValidationRun $run -Request $topUpRequest -Response $topUpResponse};"
        "$validation=[pscustomobject]@{key='validation';validation_run=$run;"
        "expected_requests=@($request);cpu_route_plan=[pscustomobject]@{worker_reads=@($route);"
        "worker_writes=@()};telemetry_window_from='2026-08-30T00:00:00Z';"
        "telemetry_window_to='2026-08-30T00:00:01Z';routes=@($route);"
        "worker_qualification=[pscustomobject]@{key=$key};expected_worker_invocations=1;"
        "static_worker_invocations=0;static_observability_state='PASSED'};"
        "$script:observedRecovery=$null;function Get-CandidateFrozenPlatformEvidence{"
        "$script:observedRecovery=Read-WorkerCpuRunArtifact $run 'provider-evidence.json';"
        "return $null};$result=Resume-CandidateWorkerPlatformEvidence -Candidate $candidate "
        "-Validation $validation;Write-Output \"$($script:observedRecovery.recovery.active_reads),"
        "$($script:observedRecovery.recovery.background_reads),"
        "$([DateTimeOffset]::Parse($result.telemetry_window_to) -gt "
        "[DateTimeOffset]::Parse($validation.telemetry_window_to))\"",
    )

    assert result == "0,0,True"


def test_deterministic_observability_contract_failure_is_terminal(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$state.candidate.validation_state='NEW';$state.candidate.compatibility_state='APPROVED';"
        "Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('web/app/api/status/route.ts')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$true;"
        "requires_validation=$true;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true}};"
        "function Invoke-CandidateWorkerValidation{return [pscustomobject]@{passed=$true;"
        "validation_run='run-schema';expected_worker_invocations=1;static_worker_invocations=0;"
        "static_observability_state='PASSED';observability_credential_source='LOCAL_SECRET_FILE';"
        "observability_diagnostic='OBSERVABILITY_SCHEMA_INVALID';routes=@();"
        "expected_requests=@();cpu_evidence=$null}};"
        "$null=Invoke-AutomaticCandidateValidation -Candidate $candidate;"
        "$saved=Get-ReleaseControlState;$history=Get-Content -Raw -Encoding UTF8 $releaseHistoryPath;"
        'Write-Output "$($saved.candidate.validation_state),$($saved.candidate.validation.reason),'
        '$($saved.candidate.validation.cloudflare),$($history.Contains(\'CANDIDATE_FAILED\'))"',
    )

    assert result == "FAILED,OBSERVABILITY_SCHEMA_INVALID,FAILED,True"


def test_repository_local_release_secret_path_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", ".local/secrets/cloudflare-release.json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0


def test_control_center_stages_releases_without_main_driven_activation() -> None:
    path = ROOT / "scripts" / "xauusd_control_center.ps1"
    control_center = _control_center_source()

    assert (
        '$reloadableServiceKeys = @('
        '"collector", "annotator", "api", "sync")'
    ) in control_center
    assert 'Match = "run_assistant_worker.py"' not in control_center
    assert 'CODE_REVISION_RELOAD_APPLIED' in control_center
    assert 'Write-RuntimeCodeState -Revision $Revision' in control_center
    assert 'Test-CodeReloadHealth -ReloadStarted $reloadStarted' in control_center
    assert 'Start-WatchdogReplacement' in control_center
    assert 'Invoke-RuntimeCandidateActivation' in control_center
    assert "$codeReloadTimeout = [TimeSpan]::FromMinutes(5)" in control_center
    assert "Add($codeReloadTimeout)" in control_center
    preflight = control_center.split(
        "function Invoke-ProductionShapePreflight", 1,
    )[1].split("function Update-RuntimeCheckout", 1)[0]
    assert '"--allow-pending-generation-decision"' in preflight
    assert "Get-DesiredMainRevision" not in control_center
    assert "Invoke-RuntimeCheckoutHandoff" not in control_center
    assert "Start-CandidateDiscovery" in control_center
    assert "Start-ReleasePromotion" in control_center
    assert "Invoke-ReverseStable" in control_center
    assert "Install-ProductionRuntime" in control_center
    assert 'RuntimeRoot must be separate from the development checkout' in control_center
    assert 'worktree add --detach --quiet' in control_center
    assert '-WindowStyle Hidden -PassThru' in control_center
    assert 'isolated-critical-status-diagnostics-v4' in control_center
    assert '$statusUrl = "http://127.0.0.1:$preflightPort/api/critical-status"' in preflight
    assert '"quote"' not in control_center.split("$reloadableServiceKeys =", 1)[1].splitlines()[0]

    reported = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(path), "-Action", "CodeRevision",
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert reported == expected


def test_local_assistant_worker_is_not_installed_or_supervised() -> None:
    control_center = _control_center_source()

    assert not (ROOT / "scripts" / "run_assistant_worker.py").exists()
    assert not (ROOT / "xauusd_forecaster" / "assistant_local_runtime.py").exists()
    assert 'Key = "assistant"' not in control_center
    assert "assistant" not in control_center.lower()


def _run_control_center_contract(
    tmp_path, body: str, *, powershell: str = "powershell.exe",
) -> str:
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir(exist_ok=True)
    repository.mkdir(exist_ok=True)
    manifest = repository / "web" / "worker-validation-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        (ROOT / "web" / "worker-validation-manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; {body}"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise AssertionError(
            "PowerShell control contract failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_recovery_short_observe_requires_exact_identity_receipts_and_owner(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$target=[pscustomobject]@{validation_key='worker:git';worker_version_id='worker';"
        "windows_revision=('b'*40)};$refMap=[ordered]@{};"
        "foreach($node in $releaseEvidencePrerequisiteNodes){$refMap[$node]=('a'*64)};"
        "$refMap.worker_cpu=('b'*64);$refs=[pscustomobject]$refMap;"
        "$tx=[pscustomobject]@{mode='RECOVERY_HOTFIX';recovery_action='APPLY_RECOVERY_HOTFIX';"
        "observe_contract=[pscustomobject]@{budget_class='SHORT_BOUNDED'};target=$target;"
        "previous=[pscustomobject]@{worker_version_id='stable'};evidence_receipt_refs=$refs};"
        "$release=[pscustomobject]@{transaction=$tx};"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@()}};"
        "function Get-DeploymentVersion{return [pscustomobject]@{version_id='worker'}};"
        "function Get-RuntimeCodeState{return [pscustomobject]@{applied_revision=('b'*40)}};"
        "function Test-SingleProductionOwner{return $true};"
        "function Test-CloudflareRollbackTarget{return $true};"
        "$script:expiry='2099-01-01T00:00:00Z';"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "$digest=if($Node -eq 'worker_cpu'){('b'*64)}else{('a'*64)};"
        "return [pscustomobject]@{receipt_digest=$digest;"
        "source_identity=[pscustomobject]@{qualification_state='PASSED';subject="
        "[pscustomobject]@{expires_at=$script:expiry}}}};"
        "$ok=Test-RecoveryShortObservationCycle $release ([pscustomobject]@{});"
        "$refs.worker_cpu=('d'*64);$bad=Test-RecoveryShortObservationCycle $release ([pscustomobject]@{});"
        "$refs.worker_cpu=('b'*64);$script:expiry='2000-01-01T00:00:00Z';"
        "$stale=Test-RecoveryShortObservationCycle $release ([pscustomobject]@{});"
        'Write-Output "$($ok.passed),$($ok.reason),$($bad.passed),$($bad.reason),'
        '$($stale.passed),$($stale.reason)"',
        powershell=powershell,
    )
    assert result == (
        "True,RECOVERY_BOUNDED_CYCLE_PASSED,False,"
        "RECOVERY_EVIDENCE_INVALID:worker_cpu,False,"
        "RECOVERY_EVIDENCE_LEASE_STALE:migration_live_lease"
    )


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_recovery_actions_have_authoritative_structured_commit_state(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$release=[pscustomobject]@{git_sha=('a'*40);worker_version_id='worker';"
        "windows_revision=('a'*40)};"
        "$hotfix=[pscustomobject]@{deployment_status='PROMOTING';transaction=[pscustomobject]@{type='PROMOTE';"
        "mode='RECOVERY_HOTFIX';recovery_action='APPLY_RECOVERY_HOTFIX';target=$release}};"
        "$restore=[pscustomobject]@{deployment_status='RECOVERING_LKG';transaction=[pscustomobject]@{type='RECOVERY';"
        "mode='RECOVERY_HOTFIX';recovery_action='RESTORE_LKG';target=$release}};"
        "$a=Test-ControlCenterReleaseOperationCommitted PromoteRecoveryHotfix $hotfix $release;"
        "$b=Test-ControlCenterReleaseOperationCommitted RestoreLastKnownGood $restore $release;"
        "$restore.transaction.mode='NORMAL';"
        "$c=Test-ControlCenterReleaseOperationCommitted RestoreLastKnownGood $restore $release;"
        'Write-Output "$a,$b,$c"',
        powershell=powershell,
    )
    assert result == "True,True,False"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_recovery_action_dispatch_reaches_each_authoritative_entrypoint(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:promote=0;$script:restore=0;$script:mode='';$script:reason='';"
        "function Start-ReleasePromotion{param($Mode,$RecoveryReason);$script:promote++;"
        "$script:mode=$Mode;$script:reason=$RecoveryReason;return $true};"
        "function Invoke-RestoreLastKnownGood{$script:restore++;return $true};"
        "function Get-ReleaseControlState{return [pscustomobject]@{ok=$true}};"
        "$a=Invoke-ControlCenterOperationAction PromoteRecoveryHotfix;"
        "$b=Invoke-ControlCenterOperationAction RestoreLastKnownGood;"
        'Write-Output "$script:promote,$script:restore,$script:mode,$script:reason,'
        '$($a.ok),$($b.ok)"',
        powershell=powershell,
    )
    assert result == (
        "1,1,RECOVERY_HOTFIX,EXPLICIT_OPERATOR_RECOVERY_HOTFIX,True,True"
    )


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_restore_lkg_completion_is_serialized_and_keeps_committed_identity(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$target=[pscustomobject]@{git_sha=('a'*40);worker_version_id='stable';"
        "windows_revision=('a'*40);validation_key='stable:key'};"
        "$script:state=[pscustomobject]@{stable=$target;previous_stable=$target;drift=$null;"
        "updated_at='';deployment_status='RECOVERY_OBSERVING';transaction=[pscustomobject]@{id='tx';"
        "type='RECOVERY';phase='OBSERVING';mode='RECOVERY_HOTFIX';"
        "recovery_action='RESTORE_LKG';target=$target;evidence_authority="
        "[pscustomobject]@{validation_key='stable:key';target_identity=$target;"
        "promote_receipt_digest=('f'*64)}}};"
        "$script:entered=0;$script:exited=0;$script:written=$null;"
        "function Enter-ReleaseTransactionLock{$script:entered++;"
        "$script:releaseTransactionLockHeld=$true;return $true};"
        "function Exit-ReleaseTransactionLock{$script:exited++;"
        "$script:releaseTransactionLockHeld=$false};"
        "function Get-ReleaseControlState{return $script:state};"
        "function Get-RuntimeUpdateState{return [pscustomobject]@{update_status='ACTIVE'}};"
        "function Test-RecoveryShortObservationCycle{return [pscustomobject]@{passed=$true}};"
        "function Get-ReleaseEvidenceCurrentReceipt{return [pscustomobject]@{"
        "receipt_digest=('f'*64);source_identity=[pscustomobject]@{subject="
        "[pscustomobject]@{transaction_id='tx'}}}};"
        "function New-ReleaseEvidenceAdapterArguments{param($Candidate,$BehaviorInputs,"
        "$SourceIdentity,$StartedAt,$CompletedAt,$WhyRan);return [pscustomobject]@{ok=$true}};"
        "function Publish-ObserveAttemptEvidence{param($Arguments);"
        "return [pscustomobject]@{receipt_digest=('a'*64)}};"
        "function Write-ReleaseControlState{param($State);$script:state=$State;$script:written=$State};"
        "function Write-ReleaseHistory{param($Event,$Release)};"
        "Complete-ReleaseRecovery;"
        'Write-Output "$script:entered,$script:exited,$($null -eq $script:state.transaction),'
        '$($script:state.stable.worker_version_id),$($script:state.deployment_status)"',
        powershell=powershell,
    )
    assert result == "1,1,True,stable,READY"


def _write_runtime_observation(tmp_path, **overrides) -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    state = {
        "update_status": "OBSERVING",
        "observing_revision": "b" * 40,
        "previous_revision": "a" * 40,
        "observation_started_at": started_at,
        "observation_ready_at": started_at,
        "observation_last_decision_time": "2026-08-13T03:00:00+00:00",
        "observation_success_cycles": 0,
        "observation_consecutive_failures": 0,
        "observation_validation_key": None,
        "observation_deferred_projection_obligations": [],
        "observation_deferred_projection_state": "NOT_REQUIRED",
        "observation_projection_boundary_at": started_at,
    }
    state.update(overrides)
    path = tmp_path / "runtime" / ".local" / "forward" / "runtime-update-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _write_control_bundle(root: Path, label: str, *, scripts_dir: bool = False) -> None:
    target = root / "scripts" if scripts_dir else root
    target.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_CONTROL_FILES:
        (target / name).write_text(f"{label}|{name}\n", encoding="utf-8")


def _bundle_result_expression(root: str) -> str:
    names = ",".join(f"'{name}'" for name in RUNTIME_CONTROL_FILES)
    return (
        f"$bundle = @({names}) | ForEach-Object {{ "
        f"(Get-Content -LiteralPath (Join-Path {root} $_) -Raw).Trim() }}; "
        "Write-Output ($bundle -join ',')"
    )


def _authorized_candidate(previous: str, candidate: str) -> str:
    stable_worker = "11111111-1111-4111-8111-111111111111"
    candidate_worker = "22222222-2222-4222-8222-222222222222"
    key = f"{candidate_worker}:{candidate}"
    return (
        f"$stable = New-ReleaseIdentity -GitSha '{previous}' "
        f"-WorkerVersionId '{stable_worker}' -WindowsRevision '{previous}' "
        "-ValidationState 'PASSED' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$stable.compatibility_state = 'PASSED'; "
        f"$candidateRelease = New-ReleaseIdentity -GitSha '{candidate}' "
        f"-WorkerVersionId '{candidate_worker}' -WindowsRevision '{candidate}' "
        "-ValidationState 'PASSED' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$candidateRelease.compatibility_state = 'PASSED'; "
        f"$candidateRelease.validation = [pscustomobject]@{{ key = '{key}' }}; "
        "$releaseState = New-ReleaseControlState -Stable $stable "
        "-Candidate $candidateRelease; "
        "$releaseState.candidate_materialization=[pscustomobject]@{"
        f"revision='{candidate}';state='MATERIALIZED';worker_version_id='{candidate_worker}'}}; "
        "Write-ReleaseControlState $releaseState; "
    )


def _mock_free_plan_and_qualification_authority() -> str:
    """Provide unrelated legacy validation tests with their accepted DAG inputs."""
    return (
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'free_plan'){return [pscustomobject]@{state='PASSED';"
        "source_identity=[pscustomobject]@{subject=[pscustomobject]@{candidate="
        "[pscustomobject]@{validation_key=$ValidationKey}}}}};return $null};"
        "function Publish-CandidateQualificationEvidence{return [pscustomobject]@{"
        "state='PASSED';receipts=[pscustomobject]@{artifact_provenance='accepted';"
        "free_plan='accepted'}}};"
    )


def _mock_active_promote_authority() -> str:
    """Bind direct switch/commit unit fixtures to one exact Promote receipt."""
    return (
        "$state=Get-ReleaseControlState;$promoteDigest=('f'*64);"
        "$targetIdentity=[pscustomobject]@{validation_key=$state.candidate.validation_key;"
        "worker_version_id=$state.candidate.worker_version_id;git_sha=$state.candidate.git_sha;"
        "windows_revision=$state.candidate.windows_revision;artifact_kind=$state.candidate.artifact_kind};"
        "$authority=[pscustomobject]@{schema_version='release-evidence-transaction-authority-v1';"
        "validation_key=$state.candidate.validation_key;promote_receipt_digest=$promoteDigest;"
        "dependency_receipts=[pscustomobject]@{};target_identity=$targetIdentity};"
        "$state.transaction=[pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "type='PROMOTE';phase='PRECHECK';target=$state.candidate;previous=$state.stable;"
        "deferred_projection_obligations=@();evidence_authority=$authority};"
        "Write-ReleaseControlState $state;"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'promote_attempt'){return [pscustomobject]@{state='PASSED';"
        "receipt_digest=$promoteDigest;"
        "source_identity=[pscustomobject]@{subject=[pscustomobject]@{transaction_id="
        "'11111111-1111-4111-8111-111111111111'}}}};return $null};"
        "function Publish-ObserveAttemptEvidence{return [pscustomobject]@{"
        "receipt_digest=('e'*64)}};"
    )


def _access_review_candidate(previous: str = "a" * 40, candidate: str = "b" * 40) -> str:
    return (
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.branch='main';$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.compatibility_state='COORDINATED_STORAGE_MIGRATION_PASSED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PASSED';"
        "reason='ACCESS_BOUNDARY_REVIEW_REQUIRED';"
        "data_parity=[pscustomobject]@{passed=$true;state='PASSED';marker='parity-kept'};"
        "auth_inspection=[pscustomobject]@{state='AUTH_BOUNDARY_NOT_TESTABLE';"
        "versioned_workers_dev='UNPROTECTED_TEST_SURFACE'};"
        "route_plan=[pscustomobject]@{contract_routes=@([pscustomobject]@{"
        "path='/admin/api/session';auth_required=$true})};"
        "routes=@([pscustomobject]@{family='status';state='PASSED'});"
        "cpu_evidence=[pscustomobject]@{passed=$true;gate_state='PASSED';"
        "p95_cpu_ms=4;p99_cpu_ms=5;max_cpu_ms=7};"
        "validation_run='kept-run';tested_at='2026-08-28T00:00:00Z'};"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='migration-kept'});"
        "Write-ReleaseControlState $state;"
        "function Get-CandidateCompatibilityApprovalGate{return [pscustomobject]@{"
        "state='PASSED';reason=$null}};"
    )


def _semantic_review_candidate(
    previous: str = "a" * 40, candidate: str = "b" * 40,
) -> str:
    return (
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.compatibility_state='COORDINATED_STORAGE_MIGRATION_PASSED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PASSED';"
        "reason='SEMANTIC_DATA_PARITY_REVIEW_REQUIRED';"
        "route_plan=[pscustomobject]@{contract_routes=@()};"
        "routes=@([pscustomobject]@{family='status';passed=$true});"
        "validation_run='11111111-1111-4111-8111-111111111111';"
        "worker_qualification=[pscustomobject]@{key=('d'*64);"
        "candidate_worker_version=$candidate.worker_version_id;"
        "candidate_git_sha=$candidate.git_sha};"
        "cpu_evidence=[pscustomobject]@{passed=$true;qualification_key=('d'*64);"
        "qualification_receipt_digest=('e'*64);p95_cpu_ms=4;p99_cpu_ms=5;max_cpu_ms=7};"
        "cpu_qualification_mode='CPU_QUALIFICATION_FRESH';"
        "directed_request_ledger=[pscustomobject]@{evidence_class='CONTROLLED_EXACT';"
        "request_universe_digest=('f'*64);planned=12;completed=12;passed=12};"
        "data_parity=[pscustomobject]@{passed=$false;state='REVIEW_REQUIRED'};"
        "tested_at='2026-08-28T00:00:00Z'};"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='migration-kept'});"
        "Write-ReleaseControlState $state;"
    )


def _write_coordinated_migration_files(tmp_path) -> None:
    target = tmp_path / "repository" / "web" / "drizzle"
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "0022_news_projection_generation.sql",
        "0023_operator_retry_sync_digest.sql",
        "0024_seed_bounded_audit_news_metrics.sql",
        "0025_seed_legacy_news_reverse_projection.sql",
        "0026_reconcile_legacy_news_current_identity.sql",
        "0027_materialize_news_projection_counts.sql",
        "0028_fence_legacy_news_current_identity.sql",
        "0029_news_projection_receipt_index.sql",
        "0030_news_evidence_cleanup_budget.sql",
    ):
        (target / name).write_text(
            (ROOT / "web" / "drizzle" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _coordinated_migration_contract_body(*, capability_overrides: str = "") -> str:
    database_id = "33333333-3333-4333-8333-333333333333"
    return (
        f"$script:testDatabaseId='{database_id}';"
        "$stable=[pscustomobject]@{git_sha=('a'*40);windows_revision=('a'*40);worker_version_id="
        "'11111111-1111-4111-8111-111111111111'};"
        "$candidate=[pscustomobject]@{git_sha=('b'*40);windows_revision=('b'*40);"
        "worker_version_id='22222222-2222-4222-8222-222222222222';"
        "validation_key=('22222222-2222-4222-8222-222222222222:'+('b'*40));"
        "browser_url='https://candidate.example'};"
        "function Invoke-RepositoryRead{param($Operation,$Arguments);"
        "switch($Operation){"
        "'READ_CANDIDATE_MIGRATION_TREE'{return [pscustomobject]@{passed=$true;output=@("
        "'web/drizzle/0022_news_projection_generation.sql',"
        "'web/drizzle/0023_operator_retry_sync_digest.sql',"
        "'web/drizzle/0024_seed_bounded_audit_news_metrics.sql',"
        "'web/drizzle/0025_seed_legacy_news_reverse_projection.sql',"
        "'web/drizzle/0026_reconcile_legacy_news_current_identity.sql',"
        "'web/drizzle/0027_materialize_news_projection_counts.sql',"
        "'web/drizzle/0028_fence_legacy_news_current_identity.sql',"
        "'web/drizzle/0029_news_projection_receipt_index.sql',"
        "'web/drizzle/0030_news_evidence_cleanup_budget.sql')}};"
        "'READ_CANDIDATE_MIGRATION_BLOB'{return [pscustomobject]@{passed=$true;output=@(('1'*40))}};"
        "'READ_CANDIDATE_MIGRATION'{if($Arguments[-1] -like '*:web/drizzle/0030_*'){"
        "return [pscustomobject]@{passed=$true;output=@('CREATE TABLE `news_evidence_cleanup_budget` ("
        "`id` integer PRIMARY KEY NOT NULL,`budget_day` text NOT NULL,"
        "`reserved_rows_written` integer NOT NULL,`updated_at` text NOT NULL,"
        "CHECK (`id` = 1),CHECK (`reserved_rows_written` >= 0));')}};"
        "return [pscustomobject]@{passed=$true;output=@('CREATE TABLE safe (id integer);')}};"
        "default{return [pscustomobject]@{passed=$false;output=@()}}}};"
        "function Get-CloudflareVersionDetails{param($VersionId);"
        "$sha=if($VersionId -eq $candidate.worker_version_id){$candidate.git_sha}else{$stable.git_sha};"
        "return [pscustomobject]@{id=$VersionId;annotations=[pscustomobject]@{"
        "'workers/message'=('release:'+$sha)};resources=[pscustomobject]@{bindings=@("
        "[pscustomobject]@{type='d1';name='DB';database_id=$script:testDatabaseId})}}};"
        "function Invoke-WranglerJson{param($Arguments);return [pscustomobject]@{"
        "uuid=$script:testDatabaseId;name='aurum-signal-room'}};"
        "$script:migrationMutationQueries=0;"
        "function Invoke-CoordinatedMigrationD1Query{param($Sql);"
        "if($Sql -notmatch '^\\s*(SELECT|WITH)\\b'){$script:migrationMutationQueries++};"
        "if($Sql -like 'SELECT name,*'){return @("
        "[pscustomobject]@{name='0022_news_projection_generation.sql';applied_at='now'},"
        "[pscustomobject]@{name='0023_operator_retry_sync_digest.sql';applied_at='now'},"
        "[pscustomobject]@{name='0024_seed_bounded_audit_news_metrics.sql';applied_at='now'},"
        "[pscustomobject]@{name='0025_seed_legacy_news_reverse_projection.sql';applied_at='now'},"
        "[pscustomobject]@{name='0026_reconcile_legacy_news_current_identity.sql';applied_at='now'},"
        "[pscustomobject]@{name='0027_materialize_news_projection_counts.sql';applied_at='now'},"
        "[pscustomobject]@{name='0028_fence_legacy_news_current_identity.sql';applied_at='now'},"
        "[pscustomobject]@{name='0029_news_projection_receipt_index.sql';applied_at='now'},"
        "[pscustomobject]@{name='0030_news_evidence_cleanup_budget.sql';applied_at='now'})};"
        "$row=[pscustomobject]@{projection_tables=7;projection_indexes=6;projection_triggers=6;"
        "projection_count_columns=6;projection_receipt_columns=10;retry_columns=4;"
        "evidence_cleanup_budget_tables=1;"
        "legacy_tables=4;legacy_decisions=20;projection_state='CURRENT';"
        "legacy_current_index_count=4117;legacy_current_detail_count=4117;"
        "legacy_missing_detail_count=0;legacy_review_violation_count=0;"
        "legacy_parsed_flag_mismatch_count=0;"
        "legacy_candidate_flag_mismatch_count=0;legacy_duplicate_cluster_count=0;"
        "legacy_extra_current_index_count=0;"
        "legacy_current_row_mismatch_count=0;"
        "summary_all_count=4117;summary_review_count=4117;"
        "summary_category_count=4117;summary_parsed_count=2000;current_parsed_count=2000;"
        "summary_candidate_count=125;current_candidate_count=125;invalid_candidate_expiry_count=0;"
        "active_generation_id=('c'*64);snapshot_id=('d'*64);source_digest=('e'*64);"
        "receipt_digest=('f'*64);index_count=4117;detail_count=4117;"
        "missing_detail_count=0;invariant_violation_count=0;generation_state='CURRENT';"
        "generation_contract_version='news-projection-generation-v4';"
        "generation_watermark='2026-08-26T05:00:00Z';"
        "generation_activated_at='2026-08-26T05:01:00Z';"
        "expected_receipt_digest=('f'*64);staged_index_count=4117;"
        "staged_detail_count=4117};"
        f"{capability_overrides}return $row}};"
        "function Get-CoordinatedMigrationEndpointEvidence{param($Candidate,$Stable);"
        "return [ordered]@{stable_status=200;candidate_status=200;"
        "stable_news_status='OK';stable_news_violation_count=0;"
        "news_generation_id=('c'*64);news_snapshot_id=('d'*64);"
        "news_source_digest=('e'*64);news_receipt_digest=('f'*64);"
        "news_index_count=4117;news_detail_count=4117}};"
        "$files=@('web/drizzle/0022_news_projection_generation.sql',"
        "'web/drizzle/0023_operator_retry_sync_digest.sql',"
        "'web/drizzle/0024_seed_bounded_audit_news_metrics.sql',"
        "'web/drizzle/0025_seed_legacy_news_reverse_projection.sql',"
        "'web/drizzle/0026_reconcile_legacy_news_current_identity.sql',"
        "'web/drizzle/0027_materialize_news_projection_counts.sql',"
        "'web/drizzle/0028_fence_legacy_news_current_identity.sql',"
        "'web/drizzle/0029_news_projection_receipt_index.sql',"
        "'web/drizzle/0030_news_evidence_cleanup_budget.sql');"
    )


def _expired_migration_acceptance_body() -> str:
    return (
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$checked=[DateTimeOffset]::UtcNow.AddHours(-3);"
        "$core=[ordered]@{schema_version='coordinated-storage-migration-receipt-v1';"
        "checked_at=$checked.ToString('o');expires_at=$checked.AddHours(2).ToString('o');"
        "evidence=$evidence};$root=[pscustomobject]$core;"
        "$root|Add-Member -NotePropertyName receipt_digest "
        "-NotePropertyValue (Get-CoordinatedMigrationReceiptDigest $core);"
        "Write-CoordinatedMigrationReceipt $root;"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest=$root.receipt_digest;"
        "checked_at=$root.checked_at;expires_at=$root.expires_at});"
        "$state=[pscustomobject]@{transaction=$null;stable=$stable;candidate=$candidate;"
        "updated_at=[DateTimeOffset]::UtcNow.ToString('o');"
        "candidate_materialization=[pscustomobject]@{state='MATERIALIZED'}};"
        "Write-ReleaseControlState $state;"
        "function Get-RuntimeCodeState{[pscustomobject]@{"
        "applied_revision=$stable.windows_revision}};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$stable.worker_version_id;percentage=100})}};"
    )


def test_coordinated_migration_query_preserves_bounded_sql_beyond_cmd_limit(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:wranglerArguments=$null;"
        "function Invoke-WranglerJson{param($Arguments);"
        "$script:wranglerArguments=$Arguments;"
        "return [pscustomobject]@{success=$true;results=@([pscustomobject]@{value=1})}};"
        "$sql=\"SELECT`r`n  1 AS value`n; -- \"+('x'*7000);"
        "$rows=@(Invoke-CoordinatedMigrationD1Query -Sql $sql);"
        "$command=[string]$script:wranglerArguments[5];"
        'Write-Output "$($rows[0].value)|$($script:wranglerArguments[4])|'
        '$($command.Length)|$($command -notmatch \"[`r`n]\")"',
    )
    assert result == "1|--command|7025|True"


def test_wrangler_json_bypasses_cmd_and_preserves_bounded_long_argument(
    tmp_path,
) -> None:
    wrangler = (
        tmp_path
        / "repository"
        / "web"
        / "node_modules"
        / "wrangler"
        / "bin"
        / "wrangler.js"
    )
    wrangler.parent.mkdir(parents=True, exist_ok=True)
    wrangler.write_text("// contract fixture\n", encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        "$script:nodeArguments=$null;"
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);"
        "$script:nodeArguments=@($Arguments[1..($Arguments.Count-1)]);"
        "return [pscustomobject]@{exit_code=0;stdout='{\"value\":1}';stderr='';"
        "stdout_lines=@('{\"value\":1}');stderr_lines=@()}};"
        "$long='x'*7000;$result=Invoke-WranglerJson -Arguments @('probe',$long);"
        'Write-Output "$($result.value)|$($script:nodeArguments[0])|'
        '$($script:nodeArguments[1].Length)|$($script:nodeArguments[2])"',
    )
    assert result == "1|probe|7000|--json"


def test_failed_preflight_never_switches_the_runtime_checkout(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$script:preflights = 0; $script:checkouts = 0; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { $script:preflights += 1; return $false }; "
        "function git { $script:checkouts += 1; $global:LASTEXITCODE = 0 }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        'Write-Output "$accepted,$script:preflights,$script:checkouts"',
    )

    assert result == "False,1,0"


def test_preflight_selects_an_available_loopback_port(tmp_path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        occupied_port = occupied.getsockname()[1]
        result = int(_run_control_center_contract(
            tmp_path,
            "Write-Output (Get-AvailableLoopbackPort)",
        ))

    assert 0 < result <= 65_535
    assert result != occupied_port


def test_candidate_preflight_migrates_an_isolated_consistent_copy(tmp_path) -> None:
    source = tmp_path / "legacy.sqlite3"
    target = tmp_path / "candidate" / "forward.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE retained_evidence (value TEXT NOT NULL)")
    connection.execute("INSERT INTO retained_evidence VALUES ('immutable')")
    connection.commit()
    connection.close()
    (source.parent / "dashboard-sync-status.json").write_text(
        json.dumps({"status": "OK", "last_success": "2026-08-13T19:00:00+00:00"}),
        encoding="utf-8",
    )
    incremental_states = {
        "dashboard-news-sync-state-cloudflare.json": {"cursor": "news-cursor"},
        "dashboard-learning-sync-state-cloudflare.json": {"hash": "learning"},
        "dashboard-learning-history-sync-state-cloudflare.json": {"cursor": 42},
        "dashboard-market-history-sync-state-cloudflare.json": {"cursor": "market"},
    }
    for name, payload in incremental_states.items():
        (source.parent / name).write_text(json.dumps(payload), encoding="utf-8")
    quotes = source.parent / "quotes"
    quotes.mkdir()
    (quotes / "market-session.json").write_text(
        json.dumps({"is_open": True}), encoding="utf-8",
    )

    result = _run_control_center_contract(
        tmp_path,
        f"New-CandidatePreflightDatabase -Python '{sys.executable}' "
        f"-StageRoot '{ROOT}' -SourceDatabase '{source}' "
        f"-TargetDatabase '{target}'; Copy-CandidatePreflightState "
        f"-SourceDatabase '{source}' -TargetDatabase '{target}'; "
        "Write-Output 'prepared'",
    )

    assert result == "prepared"
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    assert source_connection.execute(
        "SELECT name FROM sqlite_master WHERE name='news_event_identity_resolutions_v1'"
    ).fetchone() is None
    assert target_connection.execute(
        "SELECT value FROM retained_evidence"
    ).fetchone() == ("immutable",)
    assert target_connection.execute(
        "SELECT name FROM sqlite_master WHERE name='news_event_identity_resolutions_v1'"
    ).fetchone() == ("news_event_identity_resolutions_v1",)
    assert json.loads(
        (target.parent / "dashboard-sync-status.json").read_text(encoding="utf-8")
    )["status"] == "OK"
    for name, payload in incremental_states.items():
        assert json.loads(
            (target.parent / name).read_text(encoding="utf-8")
        ) == payload
    assert json.loads(
        (target.parent / "quotes" / "market-session.json").read_text(encoding="utf-8")
    )["is_open"] is True
    source_connection.close()
    target_connection.close()


def test_preflight_failure_always_stops_the_staged_api_process(tmp_path) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "$script:stops = 0; function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        "function Copy-CandidatePreflightDatabase {}; "
        "function Migrate-CandidatePreflightDatabase {}; "
        "function Copy-CandidatePreflightState {}; "
        "function Start-Process { $process = [pscustomobject]@{ HasExited = $false; Id = 424242 }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "$process | Add-Member ScriptMethod WaitForExit { param($milliseconds) "
        "$this.HasExited = $true; return $true }; "
        "return $process }; function Wait-CandidateCriticalStatus { return [pscustomobject]@{ "
        "ready = $false; error_code = 'CRITICAL_STATUS_HTTP_ERROR'; last_probe = "
        "[pscustomobject]@{ http_status = 500; response_body = 'failed'; "
        "transport_error = $null; elapsed_ms = 2 } } }; "
        "function Stop-Process { $script:stops += 1 }; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; stops = $script:stops; "
        "status = $state.update_status; code = $state.failure_code; "
        "phase = $state.failure_phase; diagnostics = $state.preflight_diagnostics } "
        "| ConvertTo-Json -Compress -Depth 8",
    ))

    assert result["accepted"] is False
    assert result["stops"] == 1
    assert result["status"] == "PREFLIGHT_FAILED"
    assert result["code"] == "CRITICAL_STATUS_HTTP_ERROR"
    assert result["phase"] == "WAIT_CRITICAL_STATUS"
    assert result["diagnostics"]["last_http_status"] == 500
    assert result["diagnostics"]["last_http_body"] == "failed"


@pytest.mark.parametrize(
    ("copy_body", "migration_body", "expected_phase", "expected_code", "detail"),
    [
        ("throw 'copy exploded'", "", "COPY_DATABASE", "COPY_DATABASE_FAILED", "copy exploded"),
        ("", "throw 'migration exploded'", "MIGRATE_DATABASE", "MIGRATE_DATABASE_FAILED", "migration exploded"),
    ],
)
def test_preflight_failure_identifies_database_phase(
    tmp_path, copy_body, migration_body, expected_phase, expected_code, detail
) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        f"function Copy-CandidatePreflightDatabase {{ {copy_body} }}; "
        f"function Migrate-CandidatePreflightDatabase {{ {migration_body} }}; "
        "function Copy-CandidatePreflightState {}; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; phase = $state.failure_phase; "
        "code = $state.failure_code; detail = $state.preflight_diagnostics.failure_detail } "
        "| ConvertTo-Json -Compress -Depth 8",
    ))

    assert result["accepted"] is False
    assert result["phase"] == expected_phase
    assert result["code"] == expected_code
    assert detail in result["detail"]


def test_candidate_api_exit_persists_bounded_secret_safe_diagnostics(tmp_path) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        "function Copy-CandidatePreflightDatabase {}; "
        "function Migrate-CandidatePreflightDatabase {}; "
        "function Copy-CandidatePreflightState {}; "
        "function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,"
        "$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError); "
        "Set-Content -LiteralPath $RedirectStandardOutput -Value ('x' * 5000); "
        "Set-Content -LiteralPath $RedirectStandardError "
        "-Value 'api_key=super-secret Bearer abc.def.ghi'; "
        "$process = [pscustomobject]@{ HasExited = $true; ExitCode = 23; Id = 42 }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "$process | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $true }; "
        "return $process }; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; code = $state.failure_code; "
        "phase = $state.failure_phase; exited = "
        "$state.preflight_diagnostics.candidate_process_exited; exit_code = "
        "$state.preflight_diagnostics.candidate_exit_code; stdout = "
        "$state.preflight_diagnostics.stdout_tail; stderr = "
        "$state.preflight_diagnostics.stderr_tail } | ConvertTo-Json -Compress",
    ))

    assert result["accepted"] is False
    assert result["code"] == "CANDIDATE_API_EXITED"
    assert result["phase"] == "WAIT_CRITICAL_STATUS"
    assert result["exited"] is True
    assert result["exit_code"] == 23
    assert len(result["stdout"]) <= 2060
    assert "super-secret" not in result["stderr"]
    assert "abc.def.ghi" not in result["stderr"]
    assert "[REDACTED]" in result["stderr"]


class _CandidateProbeHandler(BaseHTTPRequestHandler):
    mode = "success"

    def do_GET(self) -> None:  # noqa: N802
        if self.mode == "timeout":
            time.sleep(2)
        status = 500 if self.mode == "error" else 200
        body = (
            b'{"error":"api_key=super-secret"}'
            if self.mode == "error" else b'{"status":"OK"}'
        )
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args) -> None:
        pass


def _run_candidate_probe(tmp_path, mode: str) -> dict:
    tmp_path.mkdir()
    handler = type("CandidateProbeHandler", (_CandidateProbeHandler,), {"mode": mode})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_control_center_contract(
            tmp_path,
            f"Invoke-CandidateStatusProbe -Url "
            f"'http://127.0.0.1:{server.server_port}/api/critical-status' "
            "-TimeoutSeconds 1 | ConvertTo-Json -Compress",
        )
        return json.loads(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_candidate_critical_status_probe_distinguishes_success_http_and_timeout(
    tmp_path,
) -> None:
    success = _run_candidate_probe(tmp_path / "success", "success")
    error = _run_candidate_probe(tmp_path / "error", "error")
    timeout = _run_candidate_probe(tmp_path / "timeout", "timeout")

    assert success["ready"] is True
    assert success["http_status"] == 200
    assert success["error_code"] is None
    assert error["ready"] is False
    assert error["http_status"] == 500
    assert error["error_code"] == "CRITICAL_STATUS_HTTP_ERROR"
    assert "super-secret" not in json.dumps(error)
    assert "[REDACTED]" in json.dumps(error)
    assert timeout["ready"] is False
    assert timeout["http_status"] is None
    assert timeout["error_code"] == "CRITICAL_STATUS_TIMEOUT"


def test_candidate_readiness_accepts_a_successful_critical_probe(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$process = [pscustomobject]@{ HasExited = $false }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "function Start-Sleep {}; function Invoke-CandidateStatusProbe { "
        "return [pscustomobject]@{ ready = $true; error_code = $null; "
        "http_status = 200; response_body = $null; transport_error = $null; "
        "elapsed_ms = 1 } }; $result = Wait-CandidateCriticalStatus "
        "-Process $process -Url 'http://127.0.0.1:1/api/critical-status' "
        "-Deadline ([DateTimeOffset]::UtcNow.AddSeconds(1)); "
        'Write-Output "$($result.ready),$($result.last_probe.http_status)"',
    )

    assert result == "True,200"


def test_business_switch_never_invokes_control_bundle_copy(
    tmp_path,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$script:checkouts = @(); "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function Resolve-ServiceLaunchContracts { return $services }; "
        "function git { if ($args -contains 'checkout') { "
        "$script:checkouts += [string]$args[-1] }; $global:LASTEXITCODE = 0 }; "
        "function Copy-Item { throw 'copy failed' }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$accepted,$($script:checkouts -join \'|\'),$($state.update_status)"',
    )

    assert result == f"True,{candidate},STAGED"


def test_candidate_switch_preserves_reviewed_runtime_control_bundle(tmp_path) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    candidate = "b" * 40
    previous = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function Resolve-ServiceLaunchContracts { return $services }; "
        "function git { if ($args -contains 'checkout') { foreach ($name in "
        "$runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        f"-Value ('{candidate}|' + $name) }} }}; $global:LASTEXITCODE = 0 }}; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_runtime_control_bundle_records_exact_source_revision_and_hashes(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    source_manifest = json.loads(
        (ROOT / "scripts" / "runtime-control-files.json").read_text(encoding="utf-8")
    )
    source_files = tuple(source_manifest["files"])
    (runtime / "scripts").mkdir(parents=True)
    for name in source_files:
        shutil.copy2(ROOT / "scripts" / name, runtime / "scripts" / name)
    shutil.copy2(
        ROOT / "scripts" / "windows-service-launch-contract.json",
        runtime / "scripts" / "windows-service-launch-contract.json",
    )
    subprocess.run(["git", "init", "-q"], cwd=runtime, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=runtime, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=runtime,
        check=True,
    )
    subprocess.run(["git", "add", "scripts"], cwd=runtime, check=True)
    subprocess.run(["git", "commit", "-qm", "test bundle"], cwd=runtime, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=runtime,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    result = _run_control_center_contract(
        tmp_path,
        f"Sync-StableRuntimeControlFiles -SourceRoot $moduleRoot "
        f"-ControlRoot (Join-Path $repositoryRoot '.local\\runtime-control') "
        f"-SourceRevision '{revision}'; "
        "$manifest=Get-Content -LiteralPath (Join-Path $repositoryRoot "
        "'.local\\runtime-control\\runtime-control-bundle.json') -Raw | ConvertFrom-Json; "
        '$hashCount=@($manifest.files.PSObject.Properties).Count; '
        'Write-Output "$($manifest.source_revision),$($manifest.exact_revision),$hashCount"',
    )

    assert result == f"{revision},True,{len(source_files)}"


def _status_payload(**overrides) -> dict:
    payload = {
        "generated_at": "2026-08-21T10:20:00+00:00",
        "forward_epoch": "2026-08-01T00:00:00+00:00",
        "counts": {"decision_events": 100},
        "latest": {"decision_time": "2026-08-21T10:20:00+00:00"},
        "system": {"market_session": "OPEN", "quote_age_seconds": 25.0},
    }
    for key, value in overrides.items():
        owner, _, field = key.partition("__")
        if field:
            payload[owner][field] = value
        else:
            payload[owner] = value
    return payload


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ({"system__quote_age_seconds": 56_000}, "CANDIDATE_QUOTE_STALE"),
        ({"latest__decision_time": "2026-08-21T10:00:00+00:00"},
         "CANDIDATE_DECISION_BEHIND_STABLE"),
        ({"latest__decision_time": None}, "CANDIDATE_STATUS_SCHEMA_MISMATCH"),
        ({"system__quote_age_seconds": None}, "CANDIDATE_STATUS_SCHEMA_MISMATCH"),
        ({"generated_at": "not-a-time"}, "CANDIDATE_STATUS_SCHEMA_MISMATCH"),
        ({"counts__decision_events": 99}, "CANDIDATE_COUNT_REGRESSION"),
        ({"system__market_session": "DATA_UNAVAILABLE"}, "CANDIDATE_QUOTE_STALE"),
    ],
)
def test_candidate_status_parity_fails_closed(
    tmp_path, candidate: dict, expected: str,
) -> None:
    stable_json = json.dumps(_status_payload(), separators=(",", ":"))
    candidate_json = json.dumps(
        _status_payload(**candidate), separators=(",", ":"),
    )
    result = _run_control_center_contract(
        tmp_path,
        f"$stable='{stable_json}' | ConvertFrom-Json;"
        f"$candidate='{candidate_json}' | ConvertFrom-Json;"
        "$result=Test-CandidateStatusPayload -StablePayload $stable "
        "-CandidatePayload $candidate;"
        'Write-Output "$($result.passed),$($result.reason)"',
    )
    assert result == f"False,{expected}"


def test_candidate_status_parity_skips_open_quote_rule_when_closed(tmp_path) -> None:
    stable_json = json.dumps(
        _status_payload(
            system__market_session="CLOSED", system__quote_age_seconds=None,
        ), separators=(",", ":"),
    )
    candidate_json = json.dumps(
        _status_payload(
            system__market_session="CLOSED", system__quote_age_seconds=None,
        ), separators=(",", ":"),
    )
    result = _run_control_center_contract(
        tmp_path,
        f"$stable='{stable_json}' | ConvertFrom-Json;"
        f"$candidate='{candidate_json}' | ConvertFrom-Json;"
        "$result=Test-CandidateStatusPayload -StablePayload $stable "
        "-CandidatePayload $candidate;"
        'Write-Output "$($result.passed),$($result.reason)"',
    )
    assert result == "True,PASSED"


@pytest.mark.parametrize(("owner", "field"), [("latest", "decision_time"), ("system", "quote_age_seconds")])
def test_candidate_status_parity_rejects_missing_required_fields(
    tmp_path, owner: str, field: str,
) -> None:
    stable = _status_payload()
    candidate = _status_payload()
    candidate[owner].pop(field)
    stable_json = json.dumps(stable, separators=(",", ":"))
    candidate_json = json.dumps(candidate, separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        f"$stable='{stable_json}' | ConvertFrom-Json;"
        f"$candidate='{candidate_json}' | ConvertFrom-Json;"
        "$result=Test-CandidateStatusPayload -StablePayload $stable "
        "-CandidatePayload $candidate;"
        'Write-Output "$($result.passed),$($result.reason)"',
    )
    assert result == "False,CANDIDATE_STATUS_SCHEMA_MISMATCH"


def test_candidate_status_parity_accepts_valid_current_candidate(tmp_path) -> None:
    payload_json = json.dumps(_status_payload(), separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        f"$stable='{payload_json}' | ConvertFrom-Json;"
        f"$candidate='{payload_json}' | ConvertFrom-Json;"
        "$result=Test-CandidateStatusPayload -StablePayload $stable "
        "-CandidatePayload $candidate;"
        'Write-Output "$($result.passed),$($result.reason)"',
    )
    assert result == "True,PASSED"


def test_candidate_data_parity_checks_complete_bounded_route_set_and_identity(
    tmp_path,
) -> None:
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{worker_version_id='stable';git_sha='stable-git'};"
        "$candidate=[pscustomobject]@{worker_version_id='candidate';git_sha='candidate-git'};"
        "$script:paths=@();"
        "function Invoke-ExactVersionJson { param($VersionId,$Path);"
        "$script:paths += $Path;"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}' | ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at="
        "'2026-08-21T10:20:00+00:00'}}else{[pscustomobject]@{items=@(1)}};"
        "$observed=if($VersionId -eq 'candidate' -and $Path -eq '/api/news-evidence?mode=all&page=1&limit=20')"
        "{'wrong'}else{$VersionId};"
        "$git=if($VersionId -eq 'stable'){'stable-git'}else{'candidate-git'};"
        "return [pscustomobject]@{payload=$payload;"
        "observed_version_id=$observed;observed_git_sha=$git} };"
        "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        '$route=$result.routes | Where-Object {$_.route -like "/api/news-evidence*"};'
        'Write-Output "$($script:paths.Count),$($route.reason),$($result.passed)"',
    )
    assert result == "20,EXACT_VERSION_IDENTITY_MISMATCH,False"


def test_candidate_data_parity_rejects_unexpected_empty_dataset(tmp_path) -> None:
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{worker_version_id='stable';git_sha='stable-git'};"
        "$candidate=[pscustomobject]@{worker_version_id='candidate';git_sha='candidate-git'};"
        "function Invoke-ExactVersionJson { param($VersionId,$Path);"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}' | ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at="
        "'2026-08-21T10:20:00+00:00'}}elseif($Path -like '/api/news-index*')"
        "{$items=if($VersionId -eq 'stable'){@(1)}else{@()};"
        "[pscustomobject]@{items=$items}}"
        "else{[pscustomobject]@{items=@()}};"
        "$git=if($VersionId -eq 'stable'){'stable-git'}else{'candidate-git'};"
        "return [pscustomobject]@{payload=$payload;"
        "observed_version_id=$VersionId;observed_git_sha=$git} };"
        "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        '$route=$result.routes | Where-Object {$_.route -like "/api/news-index*"};'
        'Write-Output "$($route.reason),$($result.passed),$($route.error)"',
    )
    assert result == "CANDIDATE_DATASET_UNEXPECTEDLY_EMPTY,False,"


def _scoped_debt_parity_contract(
    *, stable_fails: bool, candidate_fails: bool,
    stable_fingerprint: str = "same-debt", candidate_fingerprint: str = "same-debt",
    changed: bool = False, candidate_identity: bool = True,
    candidate_hard_failure: bool = False,
) -> str:
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    contract_routes = (
        "@([pscustomobject]@{path='/api/audit';auth_required=$false})"
        if changed else "@()"
    )
    return (
        "$stable=[pscustomobject]@{worker_version_id='stable';git_sha='stable-git';"
        "artifact_kind='PRODUCTION_CANDIDATE'};"
        "$candidate=[pscustomobject]@{worker_version_id='candidate';git_sha='candidate-git'};"
        f"$plan=[pscustomobject]@{{contract_routes={contract_routes}}};"
        "function Get-ExactVersionJsonObservation{param($VersionId,$GitSha,$Path);"
        f"$fails=if($VersionId -eq 'stable'){{${str(stable_fails).lower()}}}"
        f"else{{${str(candidate_fails).lower()}}};"
        "if($Path -eq '/api/audit' -and $fails){"
        f"$fingerprint=if($VersionId -eq 'stable'){{'{stable_fingerprint}'}}"
        f"else{{'{candidate_fingerprint}'}};"
        f"$identity=if($VersionId -eq 'candidate'){{${str(candidate_identity).lower()}}}"
        "else{$true};"
        f"$hard=if($VersionId -eq 'candidate'){{${str(candidate_hard_failure).lower()}}}"
        "else{$false};"
        "return [pscustomobject]@{passed=$false;"
        "failure_class='HTTP_503';failure_fingerprint_available=$true;"
        "failure_fingerprint=$fingerprint;hard_safety_failure=$hard;"
        "failure_reason_code=if($hard){'DATA_INTEGRITY_VIOLATION'}else{'UPSTREAM_TIMEOUT'};"
        "diagnostic='bounded failure';identity_passed=$identity}};"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}'|ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at='2026-08-21T10:20:00Z'}}"
        "else{[pscustomobject]@{items=@(1)}};"
        "return [pscustomobject]@{passed=$true;identity_passed=$true;payload=$payload;"
        "observed_version_id=$VersionId;observed_git_sha=$GitSha}};"
        "$result=Test-CandidateDataParity $stable $candidate $plan;"
        "$audit=$result.routes|Where-Object {$_.route -eq '/api/audit'};"
        'Write-Output "$($result.passed),$($audit.acceptance_class),'
        '$($audit.state),$($audit.reason),$($result.stable_debt.Count)"'
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        (
            {"stable_fails": True, "candidate_fails": True},
            "True,C,EXISTING_STABLE_DEBT,UNCHANGED_EXISTING_STABLE_DEBT,1",
        ),
        (
            {"stable_fails": True, "candidate_fails": True,
             "candidate_fingerprint": "different-debt"},
            "False,C,FAILED,CANDIDATE_DEBT_EQUIVALENCE_UNPROVEN,0",
        ),
        (
            {"stable_fails": True, "candidate_fails": False},
            "True,C,STABLE_DEBT_IMPROVED,CANDIDATE_IMPROVES_STABLE_DEBT,1",
        ),
        (
            {"stable_fails": False, "candidate_fails": True},
            "False,C,FAILED,CANDIDATE_REGRESSION,0",
        ),
        (
            {"stable_fails": True, "candidate_fails": True, "changed": True},
            "False,B,FAILED,CHANGED_BOUNDARY_FAILURE,0",
        ),
        (
            {"stable_fails": True, "candidate_fails": True,
             "candidate_hard_failure": True},
            "False,C,FAILED,CANDIDATE_HARD_SAFETY_FAILURE,0",
        ),
        (
            {"stable_fails": True, "candidate_fails": True,
             "candidate_identity": False},
            "False,C,FAILED,EXACT_VERSION_IDENTITY_MISMATCH,0",
        ),
    ),
)
def test_scoped_debt_requires_proven_equivalence_without_weakening_safety(
    tmp_path, kwargs: dict[str, bool | str], expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path, _scoped_debt_parity_contract(**kwargs),
    )
    assert result == expected


def test_failure_fingerprint_is_bounded_machine_evidence(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$sameA=Get-ReleaseFailureFingerprint '/api/audit' 503 '/api/audit' "
        "'audit' 'd1_read' 'application/json; charset=utf-8' "
        "'{\"error_code\":\"UPSTREAM_TIMEOUT\"}';"
        "$sameB=Get-ReleaseFailureFingerprint '/api/audit' 503 '/api/audit' "
        "'audit' 'd1_read' 'application/json; charset=utf-8' "
        "'{\"error_code\":\"UPSTREAM_TIMEOUT\"}';"
        "$different=Get-ReleaseFailureFingerprint '/api/audit' 503 '/api/audit' "
        "'audit' 'exception' 'application/json' "
        "'{\"error\":\"request failed\",\"request_id\":\"volatile\"}';"
        "$untyped=Get-ReleaseFailureFingerprint '/api/audit' 503 '/api/audit' "
        "'audit' 'd1_read' 'application/json' "
        "'{\"error\":\"database unavailable\"}';"
        "$integrity=Get-ReleaseFailureFingerprint '/api/audit' 503 '/api/audit' "
        "'audit' 'd1_read' 'application/json' "
        "'{\"error_code\":\"DATA_INTEGRITY_VIOLATION\"}';"
        "$auth=Get-ReleaseFailureFingerprint '/api/audit' 403 '/api/audit' "
        "'audit' 'authorization' 'application/json' "
        "'{\"error_code\":\"ACCESS_DENIED\"}';"
        'Write-Output "$($sameA.available),$($sameA.digest -eq $sameB.digest),'
        '$($sameA.machine_reason),$($different.available),$($untyped.available),'
        '$($integrity.hard_safety_failure),$($auth.hard_safety_failure)"',
    )
    assert result == "True,True,UPSTREAM_TIMEOUT,False,False,True,True"


def test_failure_fingerprint_reads_bounded_http_response_evidence(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "Add-Type -AssemblyName System.Net.Http;"
        "$response=[System.Net.Http.HttpResponseMessage]::new(503);"
        "$response.Headers.Add('X-Aurum-Route','/api/audit');"
        "$response.Headers.Add('X-Aurum-Resource','audit');"
        "$response.Headers.Add('X-Aurum-Failure-Stage','d1_read');"
        "$response.Content=[System.Net.Http.StringContent]::new("
        "'{\"error_code\":\"UPSTREAM_TIMEOUT\"}',"
        "[System.Text.Encoding]::UTF8,'application/json');"
        "$body=Get-BoundedReleaseErrorBody $response;"
        "$contentType=Get-ReleaseResponseHeaderValue $response 'Content-Type';"
        "$fingerprint=Get-ReleaseFailureFingerprint '/api/audit' 503 "
        "(Get-ReleaseResponseHeaderValue $response 'X-Aurum-Route') "
        "(Get-ReleaseResponseHeaderValue $response 'X-Aurum-Resource') "
        "(Get-ReleaseResponseHeaderValue $response 'X-Aurum-Failure-Stage') "
        "$contentType $body;$response.Dispose();"
        'Write-Output "$($fingerprint.available),$($fingerprint.machine_reason)"',
    )
    assert result == "True,UPSTREAM_TIMEOUT"


def _legacy_parity_contract(
    *, candidate_header: str = "candidate", candidate_percentage: int = 0,
    split_generated_at: str = "2026-08-21T10:20:00+00:00",
) -> str:
    stable_sha = "a" * 40
    candidate_sha = "b" * 40
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    return (
        f"$stable=[pscustomobject]@{{worker_version_id='stable';git_sha='{stable_sha}';"
        f"windows_revision='{stable_sha}';artifact_kind='LEGACY_BOOTSTRAP_STABLE'}};"
        f"$candidate=[pscustomobject]@{{worker_version_id='candidate';git_sha='{candidate_sha}';"
        "artifact_kind='PRODUCTION_CANDIDATE'};"
        "function Get-CloudflareDeployment { [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='stable';percentage=100},"
        f"[pscustomobject]@{{version_id='candidate';percentage={candidate_percentage}}})}}}};"
        f"function Get-RuntimeCodeState {{ [pscustomobject]@{{applied_revision='{stable_sha}'}} }};"
        "$script:paths=@();"
        "function Invoke-ExactVersionJson { param($VersionId,$Path);"
        "$script:paths += \"$VersionId|$Path\";"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}' | ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at="
        "'2026-08-21T10:20:00+00:00'}}"
        f"elseif($Path -eq '/api/audit-briefs'){{[pscustomobject]@{{generated_at='{split_generated_at}';daily_news_briefs=@()}}}}"
        f"elseif($Path -eq '/api/audit-stories'){{[pscustomobject]@{{generated_at='{split_generated_at}';storylines=@()}}}}"
        f"elseif($Path -eq '/api/audit-decisions'){{[pscustomobject]@{{generated_at='{split_generated_at}';recent_decisions=@()}}}}"
        "else{[pscustomobject]@{items=@()}};"
        f"$observed=if($VersionId -eq 'stable'){{''}}else{{'{candidate_header}'}};"
        f"$git=if($VersionId -eq 'stable'){{''}}else{{'{candidate_sha}'}};"
        "[pscustomobject]@{payload=$payload;observed_version_id=$observed;"
        "observed_git_sha=$git} };"
    )


def test_legacy_bootstrap_parity_accepts_missing_stable_headers_and_split_routes(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _legacy_parity_contract()
        + "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        + 'Write-Output "$($result.passed),$($result.state),$($result.identity_mode),'
        + '$($result.deferred_obligations.Count),$($script:paths.Count)"',
    )
    assert result == (
        "True,PASSED_WITH_DEFERRED_OBLIGATIONS,"
        "LEGACY_BOOTSTRAP_STABLE_COMPAT,3,17"
    )


def test_legacy_bootstrap_parity_requires_current_stable100_evidence(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _legacy_parity_contract(candidate_percentage=10)
        + "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        + 'Write-Output "$($result.passed),$($result.reason),$($script:paths.Count)"',
    )
    assert result == "False,LEGACY_STABLE_DEPLOYMENT_EVIDENCE_UNPROVEN,0"


def test_legacy_bootstrap_parity_keeps_candidate_identity_exact(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _legacy_parity_contract(candidate_header="wrong")
        + "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        + '$route=$result.routes|Select-Object -First 1;'
        + 'Write-Output "$($result.passed),$($route.reason)"',
    )
    assert result == "False,EXACT_VERSION_IDENTITY_MISMATCH"


def test_legacy_bootstrap_split_audit_defers_candidate_only_producer(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _legacy_parity_contract(split_generated_at="2026-08-21T09:50:00+00:00")
        + "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        + '$route=$result.routes|Where-Object {$_.route -eq "/api/audit-briefs"};'
        + 'Write-Output "$($result.passed),$($result.state),$($route.state),'
        '$($result.deferred_obligations.Count)"',
    )
    assert result == (
        "True,PASSED_WITH_DEFERRED_OBLIGATIONS,"
        "DEFERRED_TO_POST_CUTOVER_OBSERVATION,3"
    )


def test_modern_stable_missing_identity_headers_never_uses_legacy_mode(tmp_path) -> None:
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{worker_version_id='stable';git_sha='stable-git';"
        "artifact_kind='PRODUCTION_CANDIDATE'};"
        "$candidate=[pscustomobject]@{worker_version_id='candidate';git_sha='candidate-git'};"
        "function Invoke-ExactVersionJson { param($VersionId,$Path);"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}'|ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at='2026-08-21T10:20:00+00:00'}}"
        "else{[pscustomobject]@{items=@()}};"
        "$version=if($VersionId -eq 'stable'){''}else{'candidate'};"
        "$git=if($VersionId -eq 'stable'){''}else{'candidate-git'};"
        "[pscustomobject]@{payload=$payload;observed_version_id=$version;observed_git_sha=$git}};"
        "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        '$route=$result.routes|Select-Object -First 1;'
        'Write-Output "$($result.identity_mode),$($result.passed),$($route.reason)"',
    )
    assert result == "EXACT_VERSION,False,EXACT_VERSION_IDENTITY_MISMATCH"


def test_modern_stable_uses_exact_version_mode_after_first_promotion(tmp_path) -> None:
    status_json = json.dumps(_status_payload(), separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{worker_version_id='stable';git_sha='stable-git';"
        "artifact_kind='PRODUCTION_CANDIDATE'};"
        "$candidate=[pscustomobject]@{worker_version_id='candidate';git_sha='candidate-git'};"
        "function Invoke-ExactVersionJson { param($VersionId,$Path);"
        f"$payload=if($Path -eq '/api/status'){{'{status_json}'|ConvertFrom-Json}}"
        "elseif($Path -eq '/api/audit'){[pscustomobject]@{generated_at='2026-08-21T10:20:00+00:00'}}"
        "else{[pscustomobject]@{items=@()}};"
        "$git=if($VersionId -eq 'stable'){'stable-git'}else{'candidate-git'};"
        "[pscustomobject]@{payload=$payload;observed_version_id=$VersionId;observed_git_sha=$git}};"
        "$result=Test-CandidateDataParity -Stable $stable -Candidate $candidate;"
        'Write-Output "$($result.identity_mode),$($result.passed)"',
    )
    assert result == "EXACT_VERSION,True"


def test_control_center_launcher_and_shortcut_use_verified_bundle_path(tmp_path) -> None:
    launcher = (ROOT / "scripts" / "xauusd_control_center_launcher.vbs").read_text(
        encoding="utf-8",
    )
    assert "-NoProfile -STA -WindowStyle Hidden" in launcher
    assert "-RuntimeRoot" in launcher and "-RepositoryRoot" in launcher
    assert "shell.Run command, 0, False" in launcher
    shortcut = tmp_path / "XAUUSD Forecaster Control Center.lnk"
    result = _run_control_center_contract(
        tmp_path,
        f"$path=Install-ControlShortcut -ShortcutPath '{shortcut}';"
        "$link=(New-Object -ComObject WScript.Shell).CreateShortcut($path);"
        'Write-Output "$($link.TargetPath)|$($link.Arguments)|$($link.WorkingDirectory)"',
    )
    target, arguments, working = result.split("|", 2)
    assert target.lower().endswith("\\system32\\wscript.exe")
    assert "xauusd_control_center_launcher.vbs" in arguments
    assert str(tmp_path / "runtime") in arguments
    assert str(tmp_path / "repository") in arguments
    assert working == str(tmp_path / "runtime")


def test_control_center_records_wpf_and_bounded_fallback_diagnostics() -> None:
    source = _control_center_source()
    assert 'event = "CONTROL_CENTER_UI_STARTED"' in source
    assert 'Write-ControlCenterUiStarted -Mode "WPF"' in source
    assert 'Write-ControlCenterUiStarted -Mode "WINFORMS_FALLBACK"' in source
    assert "Protect-PreflightDiagnosticText $FailureReason" in source


def test_candidate_auth_evidence_uses_formal_access_host_only() -> None:
    source = _control_center_source()
    body = source.split("function Get-CandidateAuthInspection", 1)[1].split(
        "function Invoke-AutomaticCandidateValidation", 1
    )[0]
    assert '$protectedDashboardUrl/admin/api/session' in body
    assert '$workerUrl/admin/api/session' not in body
    assert 'versioned_workers_dev = "UNPROTECTED_TEST_SURFACE"' in body
    assert '$protectedDashboardUrl = $workerUrl' in source
    assert 'aurum-signal-room.yiyousiow1234.chatgpt.site' not in source


def test_wpf_shell_is_bundled_with_winforms_fallback_and_release_controls() -> None:
    import xml.etree.ElementTree as ET

    root = ET.parse(ROOT / "scripts" / "control_center.xaml").getroot()
    serialized = ET.tostring(root, encoding="unicode")
    assert "LOCAL RUNTIME" in serialized
    assert "OVERALL" not in serialized
    for name in (
        "ServiceList", "StableIdentity", "CandidateIdentity", "PreviousIdentity",
        "PromoteButton", "ReverseButton", "StartButton", "StopButton",
        "CandidateChecks", "OpenStableButton", "OpenCandidateButton",
        "VerifyMigrationButton", "ApproveCompatibilityButton", "ApproveAccessButton",
        "CandidateTechnicalEvidence",
    ):
        assert name in serialized
    source = _control_center_source()
    assert "function Show-WpfControlCenter" in source
    assert "if (Show-WpfControlCenter)" in source
    assert "using WinForms fallback" in source
    assert 'Invoke-WpfOperation ([string]$button.CommandParameter)' in source
    assert 'Get-ControlCenterReleasePresentation -Release $release' in source


def test_control_center_route_status_is_not_inherited_from_another_gate() -> None:
    source = _control_center_source()
    assert '$apiRouteState = if ($directed.tested -gt 0)' in source
    assert '$contractCheck = if ($directed.tested -gt 0)' in source
    assert '"API routes: $contractCheck | $($directed.passed)/$($directed.tested)"' in source


def test_release_gui_actions_are_tracked_single_flight_in_both_shells() -> None:
    source = _control_center_source()
    wpf = source[source.index("function Show-WpfControlCenter"):source.index(
        "function Show-ControlCenter"
    )]
    fallback = source[source.index("function Show-ControlCenter"):]

    assert "if (Test-WpfOperationActive) { return }" in wpf
    assert "-WindowStyle Hidden -PassThru" in wpf
    assert "Set-WpfReleaseBusy -Busy $true -Operation $Operation" in wpf
    assert all(state in wpf for state in (
        "VERIFYING MIGRATION", "APPROVING", "PROMOTING", "REVERSING",
    ))
    assert all(name in wpf for name in (
        '"VerifyMigrationButton", "ApproveCompatibilityButton", "ApproveAccessButton", "PromoteButton", "ReverseButton"',
        "return [bool]$script:wpfOperation",
        "Refresh-WpfStatus",
        "$script:wpfOperation = $null",
    ))
    assert "Resolve-ControlCenterOperationPresentation" in wpf
    assert "Operation failed without diagnostic output." not in wpf
    assert "$script:wpfOperation.WaitForExit()" in wpf
    assert '"-OperationResultPath"' in wpf

    assert "if ($script:guiOperation) { return }" in fallback
    assert all(state in fallback for state in (
        "VERIFYING MIGRATION", "APPROVING", "PROMOTING", "REVERSING",
    ))
    assert fallback.index("Set-GuiBusy -Busy $true") < fallback.index(
        '$script:guiOperation = Start-Process -FilePath "powershell.exe"'
    )
    assert "$script:guiOperation.WaitForExit()" in fallback
    assert "Resolve-ControlCenterOperationPresentation" in fallback


def test_control_center_operation_callbacks_are_empty_safe_and_contained(tmp_path) -> None:
    empty = tmp_path / "empty.out"
    empty.write_text("", encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        f"$text=Get-ControlCenterOperationText -Path '{empty}';"
        "$script:failure='';$ok=Invoke-ControlCenterUiCallback "
        "-Callback { throw 'timer failed' } "
        "-OnFailure { param($message) $script:failure=$message };"
        "$before=Test-WpfFallbackAllowed -ContentRendered $false;"
        "$after=Test-WpfFallbackAllowed -ContentRendered $true;"
        'Write-Output "$($null -ne $text),$($text.Length),$ok,$script:failure,$before,$after"',
    )
    assert result == "True,0,False,timer failed,True,False"


def test_structured_child_success_ignores_ambient_native_exit_code(tmp_path) -> None:
    result_path = tmp_path / "operation-result.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseControlState{return $null};"
        "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);"
        "cmd.exe /c exit 23};"
        f"$code=Invoke-ControlCenterStructuredOperation -Operation 'Start' "
        f"-ResultPath '{result_path}';"
        f"$saved=Read-ControlCenterOperationResult -Path '{result_path}';"
        'Write-Output "$code,$LASTEXITCODE,$($saved.success),$($saved.committed),'
        '$($saved.reason),$($saved.control_revision)"',
    )
    assert result == f"0,23,True,True,COMPLETED,{'a' * 40}"
    assert not list(tmp_path.glob("operation-result.json.*.tmp"))


def test_structured_child_failure_is_nonzero_with_bounded_diagnostic(tmp_path) -> None:
    result_path = tmp_path / "operation-result.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseControlState{return $null};"
        "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);"
        "$global:LASTEXITCODE=0;throw 'deterministic child failure'};"
        f"$code=Invoke-ControlCenterStructuredOperation -Operation 'Start' "
        f"-ResultPath '{result_path}' 2>$null;"
        f"$saved=Read-ControlCenterOperationResult -Path '{result_path}';"
        'Write-Output "$code,$($saved.success),$($saved.committed),'
        '$($saved.reason),$($saved.diagnostic)"',
    )
    assert result == "1,False,False,OPERATION_FAILED,deterministic child failure"


def test_structured_result_overrides_conflicting_process_exit_status(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$success=[pscustomobject]@{operation='ApproveCompatibility';success=$true;"
        "committed=$true;reason='COMPLETED';diagnostic=''};"
        "$failure=[pscustomobject]@{operation='ApproveCompatibility';success=$false;"
        "committed=$false;reason='OPERATION_FAILED';diagnostic='gate rejected'};"
        "$reportedSuccess=Resolve-ControlCenterOperationPresentation "
        "-Operation 'ApproveCompatibility' -ProcessExitCode 17 -Result $success;"
        "$reportedFailure=Resolve-ControlCenterOperationPresentation "
        "-Operation 'ApproveCompatibility' -ProcessExitCode 0 -Result $failure;"
        'Write-Output "$($reportedSuccess.state),$($reportedSuccess.committed),'
        '$($reportedFailure.state),$($reportedFailure.diagnostic)"',
    )
    assert result == "SUCCESS,True,FAILURE,gate rejected"


def test_stale_structured_result_revision_is_rejected(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stale=[pscustomobject]@{operation='Start';success=$true;committed=$true;"
        "reason='COMPLETED';diagnostic='';control_revision=('b'*40)};"
        "$resolved=Resolve-ControlCenterOperationPresentation -Operation 'Start' "
        "-ProcessExitCode 0 -Result $stale -ExpectedControlRevision ('a'*40);"
        'Write-Output "$($resolved.state),$($resolved.reason)"',
    )
    assert result == "INDETERMINATE,OPERATION_RESULT_UNAVAILABLE"


def test_post_action_result_transport_failure_is_indeterminate_not_false_failure(
    tmp_path,
) -> None:
    missing_dir = tmp_path / "missing" / "result.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseControlState{return $null};"
        "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);return $null};"
        f"$code=Invoke-ControlCenterStructuredOperation -Operation 'Start' "
        f"-ResultPath '{missing_dir}' 2>$null;"
        "$resolved=Resolve-ControlCenterOperationPresentation -Operation 'Start' "
        "-ProcessExitCode $code;"
        'Write-Output "$code,$($resolved.state),$($resolved.reason)"',
    )
    assert result == "2,INDETERMINATE,OPERATION_RESULT_UNAVAILABLE"


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    (
        ("stdout diagnostic", "", "stdout diagnostic"),
        ("", "stderr diagnostic", "stderr diagnostic"),
        ("less useful stdout", "stderr diagnostic", "stderr diagnostic"),
        ("", "", ""),
    ),
)
def test_operation_diagnostic_uses_result_then_stderr_then_stdout(
    tmp_path, stdout: str, stderr: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"Write-Output (Get-ControlCenterOperationDiagnostic "
        f"-StandardOutput '{stdout}' -StandardError '{stderr}')",
    )
    assert result == expected


def test_authoritative_approval_commit_survives_result_transport_failure(tmp_path) -> None:
    key = f"22222222-2222-4222-8222-222222222222:{'b' * 40}"
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.compatibility_state='APPROVED';$candidate.validation_state='NEW';"
        f"$candidate|Add-Member -Force compatibility_approval ([pscustomobject]@{{"
        f"validation_key='{key}';resources_verified=$true}});"
        "Write-ReleaseControlState $state;"
        "Write-ReleaseHistory -Event 'CANDIDATE_COMPATIBILITY_APPROVED' "
        f"-Release $candidate -Detail @{{validation_key='{key}'}};"
        "$resolved=Resolve-ControlCenterOperationPresentation "
        "-Operation 'ApproveCompatibility' -ProcessExitCode 2 "
        f"-ReleaseState $state -ExpectedValidationKey '{key}';"
        'Write-Output "$($resolved.state),$($resolved.committed),$($resolved.reason)"',
    )
    assert result == "SUCCESS,True,AUTHORITATIVE_COMMIT_CONFIRMED"


@pytest.mark.parametrize(
    ("operation", "event"),
    (("PromoteCandidate", "STABLE_COMMITTED"), ("ReverseStable", "STABLE_REVERSED")),
)
def test_release_terminal_commit_requires_ready_state_and_exact_history(
    tmp_path, operation: str, event: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$release=New-ReleaseIdentity -GitSha ('a'*40) -WorkerVersionId "
        "'11111111-1111-4111-8111-111111111111' -WindowsRevision ('a'*40);"
        "$state=[pscustomobject]@{stable=$release;transaction=$null;deployment_status='READY'};"
        f"$before=Test-ControlCenterReleaseOperationCommitted -Operation '{operation}' "
        "-ReleaseState $state -ExpectedRelease $release;"
        f"Write-ReleaseHistory -Event '{event}' -Release $release;"
        f"$after=Test-ControlCenterReleaseOperationCommitted -Operation '{operation}' "
        "-ReleaseState $state -ExpectedRelease $release;"
        "$state.transaction=[pscustomobject]@{type='OTHER'};"
        f"$active=Test-ControlCenterReleaseOperationCommitted -Operation '{operation}' "
        "-ReleaseState $state -ExpectedRelease $release;"
        'Write-Output "$before,$after,$active"',
    )
    assert result == "False,True,False"


def test_successful_approval_commit_has_deterministic_child_success(tmp_path) -> None:
    result_path = tmp_path / "approve-result.json"
    key = f"22222222-2222-4222-8222-222222222222:{'b' * 40}"
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);"
        "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.compatibility_state='APPROVED';$candidate.validation_state='NEW';"
        f"$candidate|Add-Member -Force compatibility_approval ([pscustomobject]@{{"
        f"validation_key='{key}';resources_verified=$true}});"
        "Write-ReleaseControlState $state;"
        "Write-ReleaseHistory -Event 'CANDIDATE_COMPATIBILITY_APPROVED' "
        f"-Release $candidate -Detail @{{validation_key='{key}'}}}};"
        f"$code=Invoke-ControlCenterStructuredOperation "
        f"-Operation 'ApproveCompatibility' -ResultPath '{result_path}';"
        f"$saved=Read-ControlCenterOperationResult -Path '{result_path}';"
        'Write-Output "$code,$($saved.success),$($saved.committed),'
        '$($saved.release_validation_key)"',
    )
    assert result == f"0,True,True,{key}"


def test_precommit_approval_failure_has_deterministic_child_failure(tmp_path) -> None:
    result_path = tmp_path / "approve-result.json"
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);"
        "throw 'APPROVAL_RETRYABLE: GITHUB_TEMPORARILY_UNAVAILABLE'};"
        f"$code=Invoke-ControlCenterStructuredOperation "
        f"-Operation 'ApproveCompatibility' -ResultPath '{result_path}' 2>$null;"
        f"$saved=Read-ControlCenterOperationResult -Path '{result_path}';"
        'Write-Output "$code,$($saved.success),$($saved.committed),'
        '$($saved.diagnostic)"',
    )
    assert result == (
        "1,False,False,"
        "APPROVAL_RETRYABLE: GITHUB_TEMPORARILY_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "operation",
    (
        "PromoteCandidate", "ReverseStable", "Start", "Stop", "Restart",
        "ServiceStart", "ServiceStop", "DiscoverCandidate", "ReconcileRelease",
    ),
)
def test_sibling_gui_operations_share_explicit_structured_exit_contract(
    tmp_path, operation: str,
) -> None:
    result_path = tmp_path / f"{operation}.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ReleaseControlState{return $null};"
        "function Get-RuntimeControlBundleIdentity{return [pscustomobject]@{"
        "source_revision=('a'*40)}};"
        "function Invoke-ControlCenterOperationAction{param($Operation);return $null};"
        "function Test-ControlCenterReleaseOperationCommitted{return $true};"
        f"$code=Invoke-ControlCenterStructuredOperation -Operation '{operation}' "
        f"-ResultPath '{result_path}';"
        f"$saved=Read-ControlCenterOperationResult -Path '{result_path}';"
        'Write-Output "$code,$($saved.operation),$($saved.success)"',
    )
    assert result == f"0,{operation},True"


def test_gui_operation_lifecycle_prevents_orphan_and_duplicate_children() -> None:
    source = _control_center_source()
    wpf = source[source.index("function Show-WpfControlCenter"):source.index(
        "function Show-ControlCenter"
    )]
    fallback = source[source.index("function Show-ControlCenter"):]
    assert "if (Test-WpfOperationActive) { return }" in wpf
    assert "if ($script:guiOperation) { return }" in fallback
    assert "$eventArgs.Cancel = $true" in wpf
    assert "$eventArgs.Cancel = $true" in fallback
    assert "$script:wpfOperation = $null" in wpf
    assert "$script:guiOperation = $null" in fallback
    assert "$script:wpfOperationResultPath" in wpf
    assert "$script:guiOperationResultPath" in fallback
    assert "Refresh-WpfStatus" in wpf
    assert "Request-GuiStatus" in fallback
    assert "APPROVING | tracked background operation in progress" in source
    assert "PROMOTING | tracked background operation in progress" in source
    assert "REVERSING | tracked background operation in progress" in source
    assert " · " not in source
    assert "Â" not in source
    assert source.index("$script:wpfOperation = $null", source.index("$operationTimer.Add_Tick")) < source.index(
        'if ([string]$presentation.state -eq "FAILURE")', source.index("$operationTimer.Add_Tick")
    )
    assert "CONTROL_CENTER_OPERATION_COMPLETED" in source
    assert "exit ([int]$operationExitCode)" in source


def test_wpf_post_render_failures_cannot_enter_winforms_fallback() -> None:
    source = _control_center_source()
    wpf = source[source.index("function Show-WpfControlCenter"):source.index(
        "function Show-ControlCenter"
    )]
    assert wpf.index("$script:wpfUiStartedRecorded = $true") < wpf.index(
        'Write-ControlCenterUiStarted -Mode "WPF"'
    )
    assert "Test-WpfFallbackAllowed" in wpf
    assert "WPF runtime failure contained without fallback" in wpf
    assert "Get-ControlCenterOperationText" in wpf
    assert "Invoke-ControlCenterUiCallback" in wpf


def test_gui_children_are_bound_to_installed_script_and_parent_revision(tmp_path) -> None:
    installed = tmp_path / "runtime-control" / "xauusd_control_center.ps1"
    stale = tmp_path / "stale" / "xauusd_control_center.ps1"
    installed.parent.mkdir()
    stale.parent.mkdir()
    result = _run_control_center_contract(
        tmp_path,
        f"$pass=Test-ControlCenterChildIdentity -CurrentScriptPath '{installed}' "
        f"-InstalledScriptPath '{installed}' -CurrentRevision ('a'*40) "
        f"-ExpectedScriptPath '{installed}' -ExpectedRevision ('a'*40);"
        f"$stale=Test-ControlCenterChildIdentity -CurrentScriptPath '{stale}' "
        f"-InstalledScriptPath '{installed}' -CurrentRevision ('a'*40) "
        f"-ExpectedScriptPath '{installed}' -ExpectedRevision ('a'*40);"
        f"$revision=Test-ControlCenterChildIdentity -CurrentScriptPath '{installed}' "
        f"-InstalledScriptPath '{installed}' -CurrentRevision ('b'*40) "
        f"-ExpectedScriptPath '{installed}' -ExpectedRevision ('a'*40);"
        'Write-Output "$pass,$stale,$revision"',
    )
    assert result == "True,False,False"
    source = _control_center_source()
    assert source.count('"-ExpectedControlScriptPath"') >= 2
    assert source.count('"-ExpectedControlRevision"') >= 2
    assert "EXACT | HASH VERIFIED" in source
    assert "EXACT · HASH VERIFIED" not in source
    assert "EXACT Â· HASH VERIFIED" not in source


def test_wpf_runtime_loads_and_keeps_release_controls_reachable() -> None:
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Action", "WpfLayoutSmoke",
            "-RuntimeRoot", str(ROOT), "-RepositoryRoot", str(ROOT),
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    layouts = json.loads(result.stdout)
    assert {(row["viewport"], row["scale"]) for row in layouts} == {
        (viewport, scale)
        for viewport in ("1366x768", "1920x1080")
        for scale in (1, 1.25, 1.5)
    }
    assert all(row["critical_controls_reachable"] for row in layouts)
    constrained = [
        row for row in layouts
        if row["viewport"] == "1366x768" and row["scale"] == 1.5
    ]
    assert constrained[0]["vertical_scroll_available"] is True


def test_wpf_resource_is_utf8_and_footer_has_no_mojibake() -> None:
    xaml = (ROOT / "scripts" / "control_center.xaml").read_text(encoding="utf-8")
    source = _control_center_source()
    assert "Decision support only · never authorizes trading" in xaml
    assert "Decision support only Â· never authorizes trading" not in xaml
    assert "[IO.File]::ReadAllText" in source
    assert "[Text.UTF8Encoding]::new($false)" in source


def test_business_switch_ignores_control_copy_failure_hook(
    tmp_path,
) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$script:failedCandidateCopy = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function Resolve-ServiceLaunchContracts { return $services }; "
        "function git { if ($args -contains 'checkout') { $revision = [string]$args[-1]; "
        "foreach ($name in $runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        "-Value ($revision + '|' + $name) } }; $global:LASTEXITCODE = 0 }; "
        "function Copy-Item { param([string]$LiteralPath,[string]$Destination,[switch]$Force); "
        "$value = (Get-Content -LiteralPath $LiteralPath -Raw); "
        f"if (-not $script:failedCandidateCopy -and $value -like '{candidate}*' -and "
        "$LiteralPath -like '*xauusd_watchdog_guard.ps1') { "
        "$script:failedCandidateCopy = $true; throw 'candidate copy failed' }; "
        "Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath "
        "-Destination $Destination -Force:$Force }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_business_switch_never_moves_control_bundle_files(
    tmp_path,
) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$script:failedCandidateMove = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function Resolve-ServiceLaunchContracts { return $services }; "
        "function git { if ($args -contains 'checkout') { $revision = [string]$args[-1]; "
        "foreach ($name in $runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        "-Value ($revision + '|' + $name) } }; $global:LASTEXITCODE = 0 }; "
        "function Move-Item { param([string]$LiteralPath,[string]$Destination,[switch]$Force); "
        f"$value = (Get-Content -LiteralPath $LiteralPath -Raw); if (-not "
        f"$script:failedCandidateMove -and $value -like '{candidate}*' -and "
        "$LiteralPath -like '*xauusd_watchdog_guard.ps1') { "
        "$script:failedCandidateMove = $true; throw 'candidate move failed' }; "
        "Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath "
        "-Destination $Destination -Force:$Force }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_observation_rollback_preserves_independent_control_bundle(
    tmp_path,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    _write_control_bundle(tmp_path / "runtime", previous, scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", candidate
    )
    result = _run_control_center_contract(
        tmp_path,
        f"$script:rollbackState=[pscustomobject]@{{transaction=[pscustomobject]@{{"
        f"type='TEST';recovery_plan=[pscustomobject]@{{body=[pscustomobject]@{{"
        f"stable_revision='{previous}'}}}}}}}}; "
        "function Get-ReleaseControlState { return $script:rollbackState }; "
        "function Restore-RuntimeRecoveryPlan { return $true }; "
        "function Wait-RuntimeRecoveryPlanHealth { return $true }; "
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Restart-CodeReloadableServices {}; "
        "function Write-RuntimeCodeState {}; function Write-RuntimeUpdateFailure {}; "
        "function Write-WatchdogEvent {}; "
        f"$target=[pscustomobject]@{{validation_key='run:{candidate}';"
        "worker_version_id='22222222-2222-4222-8222-222222222222';"
        f"windows_revision='{candidate}'}};"
        f"$obligation=[pscustomobject]@{{route='/api/audit-stories';validation_key='run:{candidate}';"
        f"required_producer_revision='{candidate}'}};"
        "$projectionTransaction=[pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "target=$target;deferred_projection_obligations=@($obligation)};"
        "$null=Write-DeferredProjectionSyncRequest -Transaction $projectionTransaction "
        "-RequiredAfter ([DateTimeOffset]::UtcNow);"
        f"$restored = Invoke-RuntimeRollback -FailedRevision '{candidate}' "
        f"-PreviousRevision '{previous}' -Reason 'contract test'; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$restored|$((Test-Path -LiteralPath "
        "$deferredProjectionSyncCancelledPath))|$((Test-Path -LiteralPath "
        "$deferredProjectionSyncRequestPath))\"",
    ).splitlines()

    assert result == [
        ",".join(f"{candidate}|{name}" for name in RUNTIME_CONTROL_FILES),
        "True|True|False",
    ]


def test_candidate_observation_is_durable_before_revision_is_marked_applied(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:order = @(); "
        "function Restart-CodeReloadableServices { $script:order += 'reload'; "
        "return [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00') }; "
        "function Start-RuntimeObservation { $script:order += 'observe' }; "
        "function Write-RuntimeCodeState { $script:order += 'applied' }; "
        "function Write-WatchdogEvent {}; "
        "Invoke-RuntimeCandidateActivation -Revision ('b' * 40) "
        "-PreviousRevision ('a' * 40); "
        'Write-Output ($script:order -join ",")',
    )

    assert result == "reload,observe,applied"


def test_observation_reuses_the_reload_health_boundary(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:captured = $null; "
        "function Get-LatestRuntimeDecisionTime { return $null }; "
        "function Write-WatchdogEvent {}; "
        "function Write-RuntimeUpdateState { param([hashtable]$Values); "
        "$script:captured = $Values }; "
        "$boundary = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "Start-RuntimeObservation -Revision ('b' * 40) -PreviousRevision ('a' * 40) "
        "-HealthBoundary $boundary; "
        "Write-Output $script:captured.observation_health_boundary_at",
    )

    assert result == "2026-08-12T08:00:00.0000000+00:00"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_new_observation_clears_only_prior_attempt_terminal_state(
    tmp_path, powershell: str,
) -> None:
    _write_runtime_observation(
        tmp_path,
        observation_deferred_projection_evidence={"reason": "OLD_PENDING"},
        observation_deferred_projection_passed_at="2026-08-12T08:01:00+00:00",
        observation_deferred_code="OLD_DEFERRED",
        observation_deferred_at="2026-08-12T08:02:00+00:00",
        observation_original_failure_reason="OLD_TERMINAL_FAILURE",
        observation_original_failure_evidence={"reason": "OLD_FAILURE"},
        observation_original_failed_at="2026-08-12T08:03:00+00:00",
    )
    result = _run_control_center_contract(
        tmp_path,
        "function Get-LatestRuntimeDecisionTime { return 'new-decision' }; "
        "function Write-WatchdogEvent {}; "
        "$boundary=[DateTimeOffset]::Parse('2026-08-13T08:00:00+00:00'); "
        "Start-RuntimeObservation -Revision ('c' * 40) -PreviousRevision ('a' * 40) "
        "-HealthBoundary $boundary -DeferredProjectionObligations @([pscustomobject]@{"
        "route='/api/audit-briefs'}) -ValidationKey 'new-key' -ProjectionBoundary $boundary; "
        "$state=Get-RuntimeUpdateState; $fields=@("
        "$state.observation_deferred_projection_evidence,"
        "$state.observation_deferred_projection_passed_at,"
        "$state.observation_deferred_code,$state.observation_deferred_at,"
        "$state.observation_original_failure_reason,"
        "$state.observation_original_failure_evidence,"
        "$state.observation_original_failed_at)|Where-Object{$null -ne $_}; "
        "Write-Output \"$($state.update_status),$($state.observing_revision),"
        "$($state.observation_deferred_projection_state),$($state.observation_validation_key),"
        "$($state.observation_last_decision_time),$($fields.Count)\"",
        powershell=powershell,
    )

    assert result == f"OBSERVING,{'c' * 40},PENDING,new-key,new-decision,0"


def test_candidate_discovery_cannot_change_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$before = (Get-ReleaseControlState).stable.validation_key; "
        "$new = New-ReleaseIdentity -GitSha ('c' * 40) "
        "-WorkerVersionId '33333333-3333-4333-8333-333333333333' "
        "-WindowsRevision ('c' * 40); $state = Get-ReleaseControlState; "
        "$state.candidate = $new; Write-ReleaseControlState $state; "
        "$after = (Get-ReleaseControlState).stable.validation_key; "
        'Write-Output "$before,$after"',
    )

    stable_key = f"11111111-1111-4111-8111-111111111111:{previous}"
    assert result == f"{stable_key},{stable_key}"


def test_two_new_decision_cycles_activate_even_when_observed_together(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "function Get-RuntimeDecisionTimes { return @("
        "'2026-08-13T03:10:00+00:00','2026-08-13T03:05:00+00:00') }; "
        "$observed = Test-RuntimeObservation; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),$($state.observation_success_cycles)"',
    )

    assert result == "True,ACTIVE,2"


def test_observation_reads_two_cycles_from_bounded_status_contract(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "function Invoke-RestMethod { return [pscustomobject]@{recent_decisions=@("
        "[pscustomobject]@{decision_time='2026-08-13T03:10:00+00:00'},"
        "[pscustomobject]@{decision_time='2026-08-13T03:05:00+00:00'})} }; "
        "$observed = Test-RuntimeObservation; "
        "$state = Get-RuntimeUpdateState; "
        '$times = @(Get-RuntimeDecisionTimes); Write-Output '
        '"$observed,$($state.update_status),$($state.observation_success_cycles),$($times.Count)"',
    )

    assert result == "True,ACTIVE,2,2"


def test_observation_counts_only_strictly_new_five_minute_cycles(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "$script:times = @('invalid','2026-08-13T02:55:00+00:00',"
        "'2026-08-13T03:01:00+00:00','2026-08-13T03:05:00+00:00'); "
        "$script:index = 0; function Get-RuntimeDecisionTimes { "
        "$value = $script:times[$script:index]; $script:index += 1; return $value }; "
        "$null = Test-RuntimeObservation; $null = Test-RuntimeObservation; "
        "$null = Test-RuntimeObservation; $null = Test-RuntimeObservation; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$($state.update_status),$($state.observation_success_cycles),$($state.observation_last_decision_time)"',
    )

    assert result == "OBSERVING,1,2026-08-13T03:05:00+00:00"


def test_three_consecutive_observation_failures_trigger_one_rollback(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $false }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; "
        "Write-RuntimeUpdateState @{ update_status = 'ROLLED_BACK' }; return $true }; "
        "$first = Test-RuntimeObservation; $second = Test-RuntimeObservation; "
        "$third = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$first,$second,$third,$script:rollbacks,'
        '$($state.update_status),$($state.observation_original_failure_reason)"',
    )

    assert result == "True,True,False,1,ROLLED_BACK,reload health check failed"


def test_replacement_watchdog_preserves_original_observe_failure(tmp_path) -> None:
    original = "DEFERRED_PROJECTION_OBSERVATION_TIMEOUT"
    evidence = {
        "state": "PENDING",
        "reason": "CANDIDATE_PROJECTION_PARITY_PENDING",
    }
    _write_runtime_observation(
        tmp_path,
        observation_consecutive_failures=3,
        observation_original_failure_reason=original,
        observation_original_failure_evidence=evidence,
    )
    result = _run_control_center_contract(
        tmp_path,
        "$script:reason = $null; function Test-CodeReloadHealth { "
        "throw 'replacement must not rerun observation probes' }; "
        "function Invoke-RuntimeRollback { param($FailedRevision,$PreviousRevision,$Reason); "
        "$script:reason=$Reason; Write-RuntimeUpdateState @{ update_status='ROLLED_BACK' }; "
        "return $true }; $answer=Test-RuntimeObservation; $state=Get-RuntimeUpdateState; "
        'Write-Output "$answer,$script:reason,$($state.update_status),'
        '$($state.observation_original_failure_evidence.reason)"',
    )

    assert result == (
        "False,DEFERRED_PROJECTION_OBSERVATION_TIMEOUT,ROLLED_BACK,"
        "CANDIDATE_PROJECTION_PARITY_PENDING"
    )


def test_snapshot_refresh_defers_observation_without_consuming_failure_budget(
    tmp_path,
) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { "
        "return 'DEFERRED:STATUS_SNAPSHOT_REFRESH_IN_PROGRESS' }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),'
        '$($state.observation_consecutive_failures),'
        '$($state.observation_deferred_code),$script:rollbacks"',
    )

    assert result == (
        "True,OBSERVING,0,STATUS_SNAPSHOT_REFRESH_IN_PROGRESS,0"
    )


def test_observation_window_waits_for_the_worker_family_to_finish_starting(
    tmp_path,
) -> None:
    _write_runtime_observation(tmp_path, observation_ready_at=None)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { "
        "param($ReloadStarted, $AllowedWorkerStates); "
        "return $null -eq $AllowedWorkerStates -or $AllowedWorkerStates.Count -gt 1 }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),$($null -eq $state.observation_ready_at),$script:rollbacks"',
    )

    assert result == "True,OBSERVING,True,0"


def test_market_closure_pauses_observation_timeout_until_reopen(tmp_path) -> None:
    _write_runtime_observation(
        tmp_path,
        observation_started_at="2020-01-01T00:00:00+00:00",
        observation_ready_at="2020-01-01T00:00:00+00:00",
    )
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "function Get-RuntimeDecisionTimes { return @('2026-08-13T03:00:00+00:00') }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "function Invoke-RestMethod { return [pscustomobject]@{ system = "
        "[pscustomobject]@{ market_session = 'CLOSED' } } }; "
        "$closed = Test-RuntimeObservation; $paused = Get-RuntimeUpdateState; "
        "function Invoke-RestMethod { return [pscustomobject]@{ system = "
        "[pscustomobject]@{ market_session = 'OPEN' } } }; "
        "$reopened = Test-RuntimeObservation; "
        "$wasPaused = [DateTimeOffset]::Parse([string]$paused.observation_ready_at) "
        "-gt [DateTimeOffset]::Parse('2020-01-02T00:00:00+00:00'); "
        'Write-Output "$closed,$reopened,$wasPaused,$script:rollbacks"',
    )

    assert result == "True,True,True,0"


def test_observation_timeout_matches_the_thirty_minute_decision_window(
    tmp_path,
) -> None:
    old = "2020-01-01T00:00:00+00:00"
    quotes = tmp_path / "runtime" / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    results = []
    for minutes_to_close in (10, 45):
        _write_runtime_observation(
            tmp_path, observation_started_at=old, observation_ready_at=old,
        )
        now = datetime.now(timezone.utc)
        (quotes / "market-session.json").write_text(json.dumps({
            "observed_at": now.isoformat(),
            "is_open": True,
            "next_close_time": (
                now + timedelta(minutes=minutes_to_close)
            ).isoformat(),
        }), encoding="utf-8")
        results.append(_run_control_center_contract(
            tmp_path,
            "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
            "function Test-CurrentProductionShape { return $null }; "
            "function Get-RuntimeDecisionTimes { return @() }; "
            "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
            "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
            "$paused = [DateTimeOffset]::Parse([string]$state.observation_ready_at) "
            "-gt [DateTimeOffset]::Parse('2020-01-02T00:00:00+00:00'); "
            'Write-Output "$observed,$paused,$script:rollbacks"',
        ))

    assert results == ["True,True,0", "False,False,1"]


def test_watchdog_autostart_uses_one_windowless_registration_path(tmp_path) -> None:
    control_center = _control_center_source()
    launcher = ROOT / "scripts" / "xauusd_watchdog_launcher.vbs"
    launcher_text = launcher.read_text(encoding="utf-8")
    guard_launcher = ROOT / "scripts" / "xauusd_watchdog_guard_launcher.vbs"
    guard_launcher_text = guard_launcher.read_text(encoding="utf-8")

    assert "function Register-AutoStartTask" in control_center
    assert control_center.count("Register-ScheduledTask -TaskName $taskName") == 1
    assert control_center.count("Register-ScheduledTask -TaskName $guardTaskName") == 1
    assert 'New-TimeSpan -Minutes 2' in control_center
    assert "Ensure-WatchdogGuardTask" in control_center
    assert '"System32\\wscript.exe"' in control_center
    assert "shell.Run(command, 0, True)" in launcher_text
    assert "shell.Run(command, 0, True)" in guard_launcher_text
    assert "-WindowStyle Hidden" in guard_launcher_text
    assert "New-ScheduledTaskAction -Execute $wscript" in control_center
    assert "Loop While exitCode = 75" in launcher_text
    assert "-WindowStyle Hidden -ExecutionPolicy Bypass -File" not in control_center

    marker = tmp_path / "watchdog-marker.txt"
    probe = tmp_path / "watchdog-probe.ps1"
    probe.write_text(
        textwrap.dedent(
            f'''\
            param(
                [string]$Action,
                [string]$RuntimeRoot,
                [string]$RepositoryRoot
            )
            "$Action|$RuntimeRoot|$RepositoryRoot" | Set-Content -LiteralPath '{marker}'
            '''
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "cscript.exe", "//NoLogo", str(launcher), str(probe),
            str(tmp_path / "runtime"), str(tmp_path / "repository"),
        ],
        check=True,
    )

    assert marker.read_text(encoding="utf-8-sig").strip() == (
        f"Watchdog|{tmp_path / 'runtime'}|{tmp_path / 'repository'}"
    )


def test_hidden_watchdog_and_pwsh_load_identical_utf8_manifest_markers(tmp_path) -> None:
    repository = tmp_path / "repository \u4e2d\u6587 path"
    runtime = tmp_path / "runtime"
    (repository / "web").mkdir(parents=True)
    runtime.mkdir()
    manifest = ROOT / "web" / "worker-validation-manifest.json"
    (repository / "web" / manifest.name).write_bytes(manifest.read_bytes())
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "release@test.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repository, check=True)
    subprocess.run(["git", "add", "web/worker-validation-manifest.json"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repository, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    control = ROOT / "scripts" / "xauusd_control_center.ps1"
    launcher = ROOT / "scripts" / "xauusd_watchdog_launcher.vbs"
    probe = tmp_path / "manifest-probe.ps1"
    result5 = tmp_path / "manifest-ps5.json"
    result7 = tmp_path / "manifest-ps7.json"
    probe.write_text(textwrap.dedent(f'''\
        param([string]$Action,[string]$RuntimeRoot,[string]$RepositoryRoot)
        $invokedAction = $Action
        $null = . '{control}' -Action CodeRevision -RuntimeRoot $RuntimeRoot `
            -RepositoryRoot $RepositoryRoot
        $manifest = Get-WorkerValidationManifest -Revision '{revision}'
        $target = if ($PSVersionTable.PSVersion.Major -le 5) {{ '{result5}' }} else {{ '{result7}' }}
        $json = [pscustomobject]@{{
            action = $invokedAction
            markers = @($manifest.static_assets | ForEach-Object {{ [string]$_.marker }})
        }} | ConvertTo-Json -Depth 5 -Compress
        [IO.File]::WriteAllText($target, $json, [Text.UTF8Encoding]::new($false))
    '''), encoding="utf-8")

    subprocess.run([
        "cscript.exe", "//NoLogo", str(launcher), str(probe), str(runtime), str(repository),
    ], check=True)
    subprocess.run([
        "pwsh.exe", "-NoProfile", "-NonInteractive", "-File", str(probe),
        "-Action", "Watchdog", "-RuntimeRoot", str(runtime), "-RepositoryRoot", str(repository),
    ], check=True)
    ps5 = json.loads(result5.read_text(encoding="utf-8"))
    ps7 = json.loads(result7.read_text(encoding="utf-8"))
    expected = json.loads(manifest.read_text(encoding="utf-8"))

    assert ps5["action"] == ps7["action"] == "Watchdog"
    assert ps5["markers"] == ps7["markers"] == [
        row["marker"] or "" for row in expected["static_assets"]
    ]


def test_broker_closed_heartbeat_is_healthy_without_fresh_ticks(tmp_path) -> None:
    repo = tmp_path / "repo"
    quotes = repo / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (quotes / "market-session.json").write_text(json.dumps({
        "observed_at": now.isoformat(),
        "is_open": False,
        "time_till_open_seconds": 3600,
    }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$service = [pscustomobject]@{ Key = 'quote' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "Get-ServiceState -Service $service -Processes $processes"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "MARKET CLOSED"


def test_fresh_quotes_without_broker_session_trigger_bridge_recovery(tmp_path) -> None:
    repo = tmp_path / "repo"
    quotes = repo / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    (quotes / "xauusd-quotes.jsonl").write_text("{}\n", encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$service = [pscustomobject]@{ Key = 'quote' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "Get-ServiceState -Service $service -Processes $processes"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "SESSION STALE"


def test_watchdog_guard_restarts_only_after_heartbeat_is_stale(tmp_path) -> None:
    heartbeat = tmp_path / "control-watchdog-heartbeat.json"
    heartbeat.write_text(json.dumps({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "process_id": 0,
    }), encoding="utf-8")
    guard = ROOT / "scripts" / "xauusd_watchdog_guard.ps1"
    command = (
        f"$null = . '{guard}' -TaskName 'test-watchdog' "
        f"-HeartbeatPath '{heartbeat}' -MaxAgeSeconds 120; "
        "$script:starts = 0; "
        "function Stop-ScheduledTask {}; "
        "function Start-ScheduledTask { $script:starts += 1 }; "
        "$fresh = Invoke-WatchdogGuard; "
        f"@{{ observed_at = '2020-01-01T00:00:00+00:00'; process_id = 0 }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{heartbeat}'; "
        "$stale = Invoke-WatchdogGuard; "
        "Write-Output \"$fresh,$stale,$script:starts\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "False,True,1"


def test_failed_candidate_cannot_promote(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.validation_state = 'FAILED'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } "
        "catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_old_candidate_evidence_cannot_authorize_new_candidate(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.worker_version_id = '33333333-3333-4333-8333-333333333333'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } "
        "catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_completed_promotion_records_previous_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$state = Get-ReleaseControlState; $state.transaction.phase='OBSERVING'; "
        "Write-ReleaseControlState $state; "
        "function Test-CloudflareRollbackTarget { return $true }; "
        "Complete-ReleasePromotion; "
        "$final = Get-ReleaseControlState; "
        'Write-Output "$($final.stable.git_sha),$($final.previous_stable.git_sha),$($null -eq $final.transaction)"',
    )

    assert result == f"{candidate},{previous},True"


def test_candidate_arriving_during_promotion_is_queued(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    queued = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.transaction = [pscustomobject]@{ type='PROMOTE'; phase='CUTOVER' }; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='11111111-1111-4111-8111-111111111111'; "
        "Write-ReleaseControlState $state; "
        f"$new = New-ReleaseIdentity -GitSha '{queued}' "
        "-WorkerVersionId '33333333-3333-4333-8333-333333333333' "
        f"-WindowsRevision '{queued}'; "
        "function Get-CloudflareVersions { return @([pscustomobject]@{ "
        "id=$new.worker_version_id; metadata=[pscustomobject]@{ created_on='2026-08-20T12:00:00Z' }; "
        f"annotations=[pscustomobject]@{{ 'workers/message'='release:{queued} branch:main artifact_kind:PRODUCTION_CANDIDATE' }} }}) }}; "
        f"function Get-OriginMainRevision {{ '{queued}' }}; "
        "$null = Find-NewCandidateRelease; $final = Get-ReleaseControlState; "
        'Write-Output "$($final.candidate.git_sha),$($final.queued_candidate.git_sha)"',
    )

    assert result == f"{candidate},{queued}"


def test_preview_version_is_consumed_by_watermark_but_never_becomes_candidate(
    tmp_path,
) -> None:
    previous = "a" * 40
    preview = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, "b" * 40)
        + "$state=Get-ReleaseControlState; "
        "$state.candidate=$null; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='old'; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{ id='preview-version'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{preview} branch:feature artifact_kind:PREVIEW'}} }}) }}; "
        f"function Get-OriginMainRevision {{ '{preview}' }}; "
        "$found=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($null -eq $found),$($null -eq $final.candidate),$($final.candidate_discovery.watermark_version_id)"',
    )

    assert result == "True,True,preview-version"


def test_failed_candidate_is_not_rediscovered_after_restart(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, "c" * 40)
        + "$state=Get-ReleaseControlState; $state.candidate=$null; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='old'; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{id='candidate-version'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{candidate} branch:main artifact_kind:PRODUCTION_CANDIDATE'}} }}) }}; "
        f"function Get-OriginMainRevision {{ '{candidate}' }}; "
        "$first=Find-NewCandidateRelease; $state=Get-ReleaseControlState; "
        "$state.candidate.validation_state='FAILED'; Write-ReleaseControlState $state; "
        "$second=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($first.git_sha),$($null -eq $second),$($final.candidate.validation_state)"',
    )

    assert result == f"{candidate},True,FAILED"


def test_preview_artifact_cannot_promote_even_with_passed_evidence(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState; $state.candidate.artifact_kind='PREVIEW'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_preview_evidence_cannot_authorize_production_candidate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$sha=('b'*40); $preview=New-ReleaseIdentity -GitSha $sha "
        "-WorkerVersionId 'same-worker' -WindowsRevision $sha "
        "-ArtifactKind 'PREVIEW' -ValidationState 'PASSED'; "
        "$candidate=New-ReleaseIdentity -GitSha $sha -WorkerVersionId 'same-worker' "
        "-WindowsRevision $sha -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "Write-Output (Test-ReleaseIdentity $preview $candidate)",
    )

    assert result == "False"


def test_version_host_routes_distinguish_static_assets_from_worker_redirects(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "$dashboardUrl='https://aurum-signal-room.yiyousiow1234.chatgpt.site'; "
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' "
        "-WorkerVersionId '11111111-1111-1111-1111-111111111111' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$candidate|Add-Member browser_url 'https://11111111-aurum-signal-room.yiyousiow1234.workers.dev'; "
        "$plan=Get-CandidateRouteValidationPlan -ChangedFiles @('web/app/page.tsx'); "
        "function Invoke-CandidateStaticAssetRequest { param($RequestUri,$Headers); "
        "if($RequestUri.AbsolutePath -eq '/favicon.ico'){return [pscustomobject]@{"
        "status=301;location='/favicon.svg';content_type='';body_bytes=[byte[]]@();"
        "cf_cache_status='';etag='';age=''}};"
        "$redirects=@{'/assistant'='/admin/assistant';'/retry-jobs'='/admin/retry-jobs';"
        "'/status'='/admin/ai-usage'};if($redirects.ContainsKey($RequestUri.AbsolutePath)){"
        "return [pscustomobject]@{status=307;location=$redirects[$RequestUri.AbsolutePath];"
        "content_type='';body_bytes=[byte[]]@();cf_cache_status='';etag='';age='';"
        "worker_version=$candidate.worker_version_id;git_sha=$candidate.git_sha;"
        "route=$RequestUri.AbsolutePath}};"
        "$text=if($RequestUri.AbsolutePath -eq '/favicon.svg'){'<svg/>'}else{"
        "'<meta charset=\"utf-8\">Aurum Signal Room 系统健康状态 新闻与决策 "
        "OWNER OPERATIONS PRIVATE OPERATOR QUEUE AI 模型使用状态 ASSISTANT PAUSED "
        "管理员认证已完成'}; "
        "$type=if($RequestUri.AbsolutePath -eq '/favicon.svg'){'image/svg+xml'}else{'text/html'}; "
        "return [pscustomobject]@{status=200;location='';content_type=$type;"
        "body_bytes=[Text.Encoding]::UTF8.GetBytes($text);cf_cache_status='HIT';etag='x';age='1'} }; "
        "$e=Invoke-CandidateWorkerValidation -Candidate $candidate -RoutePlan $plan; "
        'Write-Output "$($plan.static_assets.Count),$($e.expected_worker_invocations),'
        '$($e.cpu_evidence),$($e.passed),$($e.routes[1].requested_host)"',
    )

    assert result == (
        "12,3,NOT_REQUIRED,True,"
        "11111111-aurum-signal-room.yiyousiow1234.workers.dev"
    )


def test_version_host_worker_redirects_use_direct_exact_identity_evidence(
    tmp_path,
) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' "
        "-WorkerVersionId '11111111-1111-1111-1111-111111111111' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$candidate|Add-Member browser_url 'https://11111111-aurum-signal-room.yiyousiow1234.workers.dev';"
        "$plan=[pscustomobject]@{static_assets=@([pscustomobject]@{path='/status';"
        "worker_expected=$true;content_type='text/html';body_encoding='utf-8';"
        "require_html_charset=$true;marker='OK';redirect_path='/admin/ai-usage'});"
        "worker_reads=@();worker_writes=@()};"
        "$script:wrong=$false;function Invoke-CandidateStaticAssetRequest{"
        "param($RequestUri);if($RequestUri.AbsolutePath -eq '/status'){"
        "return [pscustomobject]@{status=307;location='/admin/ai-usage';content_type='';"
        "body_bytes=[byte[]]@();cf_cache_status='';etag='';age='';"
        "worker_version=if($script:wrong){'wrong'}else{$candidate.worker_version_id};"
        "git_sha=$candidate.git_sha;route='/status'}};"
        "return [pscustomobject]@{status=200;location='';content_type='text/html; charset=utf-8';"
        "body_bytes=[Text.Encoding]::UTF8.GetBytes('OK');cf_cache_status='MISS';etag='x';age=''}};"
        "function Get-CandidateInvocationCount{throw 'static telemetry must not be queried'};"
        "$good=Invoke-CandidateWorkerValidation $candidate $plan;$script:wrong=$true;"
        "$bad=Invoke-CandidateWorkerValidation $candidate $plan;"
        'Write-Output "$($good.passed)|$($good.static_worker_invocations)|'
        '$($good.routes[0].observed_worker_version)|$($good.routes[0].observed_git_sha)|'
        '$($good.routes[0].observed_route)|$($bad.passed)|$($bad.routes[0].reason)"',
    )
    assert result == (
        "True|1|11111111-1111-1111-1111-111111111111|"
        f"{candidate}|/status|False|VERSION_HOST_WORKER_IDENTITY_MISMATCH"
    )


def test_compatibility_redirects_validate_their_final_page_marker() -> None:
    manifest = json.loads((ROOT / "web" / "worker-validation-manifest.json").read_text(
        encoding="utf-8",
    ))
    redirects = {
        row["path"]: (row["redirect_path"], row["marker"])
        for row in manifest["static_assets"] if row.get("redirect_path")
    }
    assert redirects["/status"] == ("/admin/ai-usage", "AI 模型使用状态")
    assert redirects["/assistant"] == ("/admin/assistant", "ASSISTANT PAUSED")
    assert redirects["/retry-jobs"] == (
        "/admin/retry-jobs", "PRIVATE OPERATOR QUEUE",
    )


def test_static_asset_validation_uses_raw_utf8_and_exact_contract(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' "
        "-WorkerVersionId '22222222-2222-2222-2222-222222222222' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$candidate|Add-Member browser_url 'https://22222222-aurum-signal-room.yiyousiow1234.workers.dev'; "
        "$route=[pscustomobject]@{path='/health';content_type='text/html';"
        "body_encoding='utf-8';require_html_charset=$true;marker='系统健康状态'}; "
        "$script:response=[pscustomobject]@{status=200;content_type='text/html';"
        "body_bytes=[Text.Encoding]::UTF8.GetBytes('<meta charset=\"utf-8\">系统健康状态');"
        "cf_cache_status='HIT';etag='asset';age='2'}; "
        "function Invoke-CandidateStaticAssetRequest { return $script:response }; "
        "$ok=Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route; "
        "$script:response.body_bytes=[byte[]](0xff,0xfe);"
        "$badUtf8=Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route; "
        "$script:response.body_bytes=[Text.Encoding]::UTF8.GetBytes('<meta charset=\"utf-8\">other');"
        "$missing=Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route; "
        "$script:response.body_bytes=[Text.Encoding]::UTF8.GetBytes('系统健康状态');"
        "$charset=Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route; "
        "$script:response.content_type='application/json';"
        "$wrongType=Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route; "
        'Write-Output "$($ok.passed),$($ok.marker_present),$($ok.body_sha256.Length),'
        '$($badUtf8.reason),$($missing.reason),$($charset.reason),'
        '$($wrongType.reason),$($ok.requested_host)"',
    )

    assert result == (
        "True,True,64,INVALID_UTF8_BODY,MARKER_MISSING,HTML_CHARSET_MISMATCH,"
        "CONTENT_TYPE_MISMATCH,"
        "22222222-aurum-signal-room.yiyousiow1234.workers.dev"
    )


def test_static_asset_validation_fails_closed_for_status_body_and_host(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' "
        "-WorkerVersionId '33333333-3333-3333-3333-333333333333' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$candidate|Add-Member browser_url 'https://33333333-aurum-signal-room.yiyousiow1234.workers.dev'; "
        "$route=[pscustomobject]@{path='/audit';content_type='text/html';"
        "body_encoding='utf-8';require_html_charset=$true;marker='新闻与决策'}; "
        "$script:response=[pscustomobject]@{status=401;content_type='text/html';"
        "body_bytes=[Text.Encoding]::UTF8.GetBytes('<meta charset=\"utf-8\">新闻与决策');"
        "cf_cache_status='';etag='';age=''}; "
        "function Invoke-CandidateStaticAssetRequest { return $script:response }; "
        "$reasons=@(); foreach($status in @(401,403,404,500)){"
        "$script:response.status=$status; $reasons+=(Invoke-CandidateStaticAssetSample "
        "-Candidate $candidate -Route $route).reason}; "
        "$script:response.status=200;$script:response.body_bytes=[byte[]]@();"
        "$reasons+=(Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route).reason; "
        "$candidate.browser_url='https://aurum-signal-room.yiyousiow1234.workers.dev';"
        "$reasons+=(Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route).reason; "
        "$candidate.browser_url='https://33333333-aurum-signal-room.yiyousiow1234.workers.dev';"
        "function Invoke-CandidateStaticAssetRequest { throw 'timeout' };"
        "$reasons+=(Invoke-CandidateStaticAssetSample -Candidate $candidate -Route $route).reason; "
        "Write-Output ($reasons -join ',')",
    )

    assert result == (
        "HTTP_STATUS_MISMATCH,HTTP_STATUS_MISMATCH,HTTP_STATUS_MISMATCH,"
        "HTTP_STATUS_MISMATCH,EMPTY_BODY,CANDIDATE_STATIC_HOST_MISMATCH,"
        "VALIDATION_REQUEST_FAILED"
    )


def test_candidate_version_url_is_derived_from_worker_not_formal_dashboard(tmp_path) -> None:
    candidate = "b" * 40
    worker = "44444444-4444-4444-4444-444444444444"
    result = _run_control_center_contract(
        tmp_path,
        "$dashboardUrl='https://aurum-signal-room.yiyousiow1234.chatgpt.site';"
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId '{worker}' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        f"$version=[pscustomobject]@{{id='{worker}';metadata=[pscustomobject]@{{"
        "has_preview=$true};annotations=[pscustomobject]@{"
        f"'workers/message'='release:{candidate} branch:main "
        "artifact_kind:PRODUCTION_CANDIDATE'}};"
        "Write-Output (Get-ReleaseVersionPreviewUrl -Version $version -Candidate $candidate)",
    )

    assert result == (
        "https://44444444-aurum-signal-room.yiyousiow1234.workers.dev"
    )


def test_dry_run_payload_requires_exact_fields_and_boolean_false(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$valid=[pscustomobject]@{status='DRY_RUN_OK';mutated=$false;"
        "route_family='status-ingest';count=0;message='';optional=$false};"
        "$missing=[pscustomobject]@{status='DRY_RUN_OK';route_family='status-ingest'};"
        "$wrongType=[pscustomobject]@{status='DRY_RUN_OK';mutated=0;"
        "route_family='status-ingest'};"
        "$wrongValue=[pscustomobject]@{status='OK';mutated=$false;"
        "route_family='status-ingest'};"
        '$values=@($valid,$missing,$wrongType,$wrongValue,$null)|ForEach-Object{'
        "Test-CandidateDryRunPayload -Payload $_ -ExpectedFamily 'status-ingest'};"
        "Write-Output ($values -join ',')",
    )

    assert result == "True,False,False,False,False"


def test_directed_summary_reports_counts_and_exact_static_predicate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$failure=[pscustomobject]@{method='GET';path='/health';status=200;"
        "passed=$false;reason='MARKER_MISSING';expected_marker='HEALTH_MARKER';"
        "marker_present=$false};"
        "$validation=[pscustomobject]@{cloudflare='FAILED';routes_tested=37;"
        "routes_passed=35;routes_failed=2;first_failure=$failure;routes=@($failure)};"
        "$summary=Get-DirectedWorkerValidationSummary -Validation $validation;"
        'Write-Output "$($summary.tested),$($summary.passed),$($summary.failed),'
        '$($summary.first_failure_line)"',
    )

    assert result == (
        "37,35,2,GET /health | HTTP 200 | MARKER_MISSING | "
        "EXPECTED marker=HEALTH_MARKER | ACTUAL marker_present=false"
    )


def test_manifest_selects_baseline_and_affected_route_sample_families(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$heavy=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/app/api/market-history/route.ts'); "
        "$shared=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/worker/api-router.ts'); "
        "$admin=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/app/admin/api/session/route.ts'); "
        "$docs=Get-CandidateRouteValidationPlan -ChangedFiles @('docs/README.md'); "
        "$heavyRoutes=@($heavy.worker_reads)+@($heavy.worker_writes); "
        "$sharedRoutes=@($shared.worker_reads)+@($shared.worker_writes); "
        "$sharedKeys=@($shared.static_assets|ForEach-Object{'STATIC|GET|'+$_.path})+"
        "@($sharedRoutes|ForEach-Object{$_.boundary+'|'+$_.method+'|'+$_.path+'|'+$_.scenario}); "
        "$adminRoutes=@($admin.worker_reads)+@($admin.worker_writes); "
        "$heavySamples=($heavyRoutes|Measure-Object acceptance_samples -Sum).Sum; "
        "$sharedSamples=($sharedRoutes|Measure-Object acceptance_samples -Sum).Sum; "
        'Write-Output "$($heavyRoutes.Count),$heavySamples,$($sharedRoutes.Count),'
        '$sharedSamples,$($adminRoutes.Count),$($docs.worker_cpu_required),'
        '$($shared.static_assets.Count+$sharedRoutes.Count),'
        '$(@($sharedKeys|Sort-Object -Unique).Count),$($shared.worker_reads.Count),'
        '$($shared.worker_writes.Count)"',
    )

    assert result == "7,70,31,310,0,False,43,43,12,19"


def test_static_manifest_rejects_missing_or_wrong_typed_contract_fields(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$path=Join-Path $repositoryRoot 'web\\worker-validation-manifest.json';"
        "$manifest=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json;"
        "$manifest.static_assets[0].content_type=$null;"
        "$manifest|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $path -Encoding UTF8;"
        "$missing=try{Get-WorkerValidationManifest|Out-Null;'PASS'}catch{$_.Exception.Message};"
        "$manifest.static_assets[0].content_type='text/html';"
        "$manifest.static_assets[0].require_html_charset='true';"
        "$manifest|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $path -Encoding UTF8;"
        "$wrongType=try{Get-WorkerValidationManifest|Out-Null;'PASS'}catch{$_.Exception.Message};"
        'Write-Output "$missing,$wrongType"',
    )

    assert result == (
        "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID,"
        "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_revision_manifest_and_native_diagnostics_are_strict_utf8_across_shells(
    tmp_path, powershell: str,
) -> None:
    repository = tmp_path / "repository"
    runtime = tmp_path / "runtime"
    (repository / "web").mkdir(parents=True)
    runtime.mkdir()
    manifest = ROOT / "web" / "worker-validation-manifest.json"
    (repository / "web" / manifest.name).write_bytes(manifest.read_bytes())
    (repository / "native-boundary.txt").write_text(
        "first\n\n\u4e2d\u6587 marker\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "release@test.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repository, check=True)
    subprocess.run(
        ["git", "add", "web/worker-validation-manifest.json", "native-boundary.txt"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repository, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null=. '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}';"
        "$prior=[Console]::OutputEncoding;[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(437);"
        f"$manifest=Get-WorkerValidationManifest -Revision '{revision}';"
        "$native=Invoke-Utf8NativeProcess -FilePath 'git.exe' -Arguments "
        f"@('-C','{repository}','show','{revision}:native-boundary.txt');"
        "$bad=Invoke-Utf8NativeProcess -FilePath 'git.exe' -Arguments "
        f"@('-C','{repository}','show','missing-release-object');"
        "$invalid=try{Invoke-Utf8NativeProcess -FilePath 'python.exe' -Arguments "
        "@('-c','import os; os.write(1, bytes([255]))')|Out-Null;'PASS'}"
        "catch{$_.Exception.Message};"
        "$payload=[pscustomobject]@{version=$PSVersionTable.PSVersion.ToString();"
        "markers=@($manifest.static_assets|ForEach-Object{$_.marker});"
        "native_lines=@($native.stdout_lines);"
        "bad_exit=$bad.exit_code;bad_stderr=[bool]$bad.stderr};"
        "$payload|Add-Member invalid_utf8 $invalid;"
        "$json=$payload|ConvertTo-Json -Depth 5 -Compress;"
        "[Console]::OutputEncoding=$prior;"
        "Write-Output ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(base64.b64decode(completed.stdout.strip()).decode("utf-8"))
    expected = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["markers"] == [row["marker"] for row in expected["static_assets"]]
    assert payload["native_lines"] == ["first", "", "\u4e2d\u6587 marker"]
    assert payload["bad_exit"] != 0
    assert payload["bad_stderr"] is True
    assert payload["invalid_utf8"] == "NATIVE_PROCESS_UTF8_INVALID"


def test_directed_route_sample_fails_closed_on_exact_identity_mismatch(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-WebRequest { return [pscustomobject]@{StatusCode=200;Content='{}';Headers=@{"
        "'X-Aurum-Worker-Version'='wrong-worker';'X-Aurum-Git-SHA'=('c'*40);"
        "'X-Aurum-Route'='/api/status';'X-Aurum-Resource'='status';"
        "'X-Aurum-D1-Operations'='1';'X-Aurum-Request-Bytes'='0';"
        "'X-Aurum-Response-Bytes'='2';'X-Aurum-Failure-Stage'='';"
        "'Server-Timing'='aurum;dur=1.00'}} };"
        f"$route=[pscustomobject]@{{method='GET';path='/api/status';request_query='';"
        f"strategy='DIRECT_REQUEST';family='status-read';expected_worker_version='worker';"
        f"expected_git_sha='{candidate}'}};"
        "$sample=Invoke-CandidateRouteSample -Route $route -VersionHeaders @{} "
        "-ValidationRun 'run-1' -FixtureRoot $repositoryRoot -IngestToken '';"
        'Write-Output "$($sample.passed),$($sample.reason),$($sample.method),$($sample.path),'
        '$($sample.status),$($sample.observed_worker_version),$($sample.resource)"',
    )

    assert result == "False,WORKER_IDENTITY_MISMATCH,GET,/api/status,200,wrong-worker,status"


def test_failed_directed_validation_persists_bounded_route_receipt(tmp_path) -> None:
    stable = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$stable=New-ReleaseIdentity -GitSha '{stable}' -WorkerVersionId 'stable-worker' "
        f"-WindowsRevision '{stable}';"
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'candidate-worker' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE' -Branch 'main';"
        "$candidate.compatibility_state='APPROVED';"
        "$state=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance { return $true };"
        "function Invoke-ProductionShapePreflight { return $true };"
        "function Test-RequiredGitHubChecks { return 'PASSED' };"
        "function Get-CandidateChangedFiles { return @('web/worker/api-router.ts') };"
        "function Get-CandidateCompatibilityRequirement { return [pscustomobject]@{state='COMPATIBLE';files=@()} };"
        "function Get-CandidateRouteValidationPlan { return [pscustomobject]@{worker_cpu_required=$true;requires_validation=$true;static_assets=@();worker_reads=@();worker_writes=@()} };"
        "function Set-CloudflareCandidatePointer {};"
        "function Wait-CandidatePlacementPropagation { return [pscustomobject]@{passed=$true;state='READY'} };"
        "function Invoke-CandidateWorkerValidation { return [pscustomobject]@{passed=$false;validation_run='run-2';"
        "expected_worker_invocations=10;observed_worker_invocations=$null;static_worker_invocations=0;"
        "static_observability_state='PASSED';cpu_evidence='NOT_RUN';routes=@([pscustomobject]@{"
        "route='/api/learning-history';path='/api/learning-history?limit=100';method='GET';passed=$false;"
        "reason='INVALID_RESOURCE';status=400;first_failure=[pscustomobject]@{method='GET';"
        "path='/api/learning-history?limit=100';expected_status=200;status=400;reason='INVALID_RESOURCE';"
        "requested_worker_version='candidate-worker';observed_worker_version='candidate-worker';"
        f"observed_git_sha='{candidate}';resource='learning-history';d1_operations='0';"
        "request_bytes='0';response_bytes='28';failure_stage='route';request_id='request-2';"
        "validation_run='run-2'}})} };"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate | Out-Null;"
        "$saved=Get-ReleaseControlState; $json=$saved|ConvertTo-Json -Depth 20 -Compress;"
        'Write-Output "$($saved.candidate.validation_state),$($saved.candidate.validation.reason),'
        '$($saved.candidate.validation.cloudflare),$($saved.candidate.validation.routes_failed),'
        '$($saved.candidate.validation.first_failure.method),$($saved.candidate.validation.first_failure.path),'
        '$($saved.candidate.validation.first_failure.status),$($saved.candidate.validation.first_failure.reason),'
        '$($saved.candidate.validation.data_parity.state),$($saved.candidate.validation.cpu_headroom.state),'
        "$([bool]($json -match 'token|secret'))\"",
    )

    assert result == (
        "FAILED,DIRECTED_WORKER_VALIDATION_FAILED,FAILED,1,GET,"
        "/api/learning-history?limit=100,400,INVALID_RESOURCE,NOT_RUN,NOT_RUN,False"
    )


def test_candidate_cpu_evidence_separates_exact_ledger_from_monotonic_provider_evidence() -> None:
    source = _control_center_source()
    evidence = (ROOT / "scripts" / "worker_cpu_evidence.ps1").read_text(
        encoding="utf-8",
    )

    assert "Get-CandidateFrozenPlatformEvidence -Candidate $Candidate" in source
    assert "CLOUDFLARE_WORKERS_OBSERVABILITY_MONOTONIC_EVENTS" in source
    assert "New-WorkerCpuRequestPlan" in source
    assert "Add-WorkerCpuDirectResponse" in source
    assert "Merge-WorkerCpuProviderEvidence" in source
    assert "CONTROLLED_EXACT" in evidence
    assert "CPU_QUALIFICATION_REUSED" in evidence


def test_version_at_or_before_watermark_cannot_replace_candidate(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    historical = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState; "
        "$state.candidate_discovery.initialized_at='2026-08-20T12:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T12:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='newer'; Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{id='older'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T11:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{historical} branch:main artifact_kind:PRODUCTION_CANDIDATE'}} }}) }}; "
        f"function Get-OriginMainRevision {{ '{current}' }}; "
        "$found=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($null -eq $found),$($final.candidate.git_sha)"',
    )

    assert result == f"True,{current}"


def test_v1_state_migrates_only_the_reviewed_legacy_candidate_provenance(
    tmp_path,
) -> None:
    accepted_worker = "dd823aa4-20f0-47e1-9255-1b785a4c17b0"
    accepted_sha = "14c055a35040fa963700c988f770c9bb52fa669e"
    result = _run_control_center_contract(
        tmp_path,
        "$state=[pscustomobject]@{schema_version='stable-candidate-release-v1'; "
        "stable=[pscustomobject]@{git_sha=('a'*40);worker_version_id='stable';windows_revision=('a'*40)}; "
        f"candidate=[pscustomobject]@{{git_sha='{accepted_sha}';worker_version_id='{accepted_worker}';windows_revision='{accepted_sha}'}}; "
        "previous_stable=$null;queued_candidate=$null}; Write-ReleaseControlState $state; "
        "$migrated=Get-ReleaseControlState; "
        'Write-Output "$($migrated.schema_version),$($migrated.stable.artifact_kind),'
        '$($migrated.stable.worker_git_sha),$($migrated.candidate.artifact_kind),'
        '$($migrated.candidate.validation_state)"',
    )

    assert result == (
        "stable-candidate-release-v3,LEGACY_BOOTSTRAP_STABLE,NOT_RECORDED,"
        "LEGACY_REFERENCE,REBASE_REQUIRED"
    )


def test_failed_runtime_rollback_does_not_rewrite_previous_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    older = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + f"$state=Get-ReleaseControlState; $state.previous_stable=New-ReleaseIdentity "
        f"-GitSha '{older}' -WorkerVersionId 'older-worker' -WindowsRevision '{older}' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$state.transaction=[pscustomobject]@{type='PROMOTE';previous=$state.stable;target=$state.candidate}; "
        "Write-ReleaseControlState $state; function git {$global:LASTEXITCODE=0}; "
        "function Sync-StableRuntimeControlFiles {}; function Restart-CodeReloadableServices {}; "
        "function Write-RuntimeCodeState {}; function Write-RuntimeUpdateFailure {}; "
        "function Write-WatchdogEvent {}; function Invoke-CloudflareDeployment {}; "
        f"$null=Invoke-RuntimeRollback -FailedRevision '{candidate}' -PreviousRevision '{previous}' -Reason 'test'; "
        "$final=Get-ReleaseControlState; Write-Output $final.previous_stable.git_sha",
    )

    assert result == older


@pytest.mark.parametrize(
    ("p95", "p99", "maximum", "expected"),
    [(4, 7, 9, "PASSED"), (7, 9, 10, "REVIEW_REQUIRED"), (4, 18, 18, "FAILED")],
)
def test_worker_cpu_gate_requires_free_tier_headroom(
    tmp_path, p95: int, p99: int, maximum: int, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$e=[pscustomobject]@{invocations=8; exceeded_cpu=0; responses_1102=0; "
        f"responses_5xx=0; p95_cpu_ms={p95}; p99_cpu_ms={p99}; max_cpu_ms={maximum}}}; "
        "Write-Output (Get-WorkerCpuGateState -Evidence $e -ExpectedInvocations 8)",
    )

    assert result == expected


@pytest.mark.parametrize(
    ("exceeded_cpu", "responses_5xx"), [(1, 0), (0, 1)],
)
def test_worker_cpu_gate_fails_platform_errors(
    tmp_path, exceeded_cpu: int, responses_5xx: int,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$e=[pscustomobject]@{{invocations=8; exceeded_cpu={exceeded_cpu}; "
        f"responses_1102={exceeded_cpu}; responses_5xx={responses_5xx}; "
        "p95_cpu_ms=4; p99_cpu_ms=7; max_cpu_ms=9}; "
        "Write-Output (Get-WorkerCpuGateState -Evidence $e -ExpectedInvocations 8)",
    )

    assert result == "FAILED"


@pytest.mark.parametrize(
    ("expected", "observed", "exceeded", "responses_1102", "responses_5xx", "maximum", "reason"),
    [
        (8, 7, 0, 0, 0, 9, "WORKER_INVOCATION_COUNT_MISMATCH"),
        (8, 8, 0, 0, 1, 9, "WORKER_5XX_OBSERVED"),
        (8, 8, 1, 1, 0, 9, "WORKER_PLATFORM_LIMIT_EXCEEDED"),
        (8, 8, 0, 0, 0, 11, "WORKER_CPU_HEADROOM_FAILED"),
    ],
)
def test_worker_platform_failure_reason_is_specific(
    tmp_path, expected: int, observed: int, exceeded: int, responses_1102: int,
    responses_5xx: int, maximum: int, reason: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$e=[pscustomobject]@{{expected_invocations={expected};invocations={observed};"
        f"exceeded_cpu={exceeded};responses_1102={responses_1102};"
        f"responses_5xx={responses_5xx};p99_cpu_ms=7;max_cpu_ms={maximum}}};"
        "Get-WorkerPlatformFailureReason -Evidence $e",
    )
    assert result == reason


def test_quota_policy_hard_failure_is_not_misclassified_as_missing_provider_rows(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$e=[pscustomobject]@{qualification_state='HARD_FAILURE';expected_invocations=12;"
        "invocations=10;exceeded_cpu=1;responses_1102=1;responses_5xx=0;"
        "p99_cpu_ms=4;max_cpu_ms=10};Write-Output (Get-WorkerPlatformFailureReason -Evidence $e)",
    )

    assert result == "WORKER_PLATFORM_LIMIT_EXCEEDED"


def test_failed_platform_gate_persists_complete_nonsecret_evidence(tmp_path) -> None:
    stable = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$stable=New-ReleaseIdentity -GitSha '{stable}' -WorkerVersionId 'stable-worker' "
        f"-WindowsRevision '{stable}';"
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'candidate-worker' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE' -Branch 'main';"
        "$candidate.compatibility_state='APPROVED';"
        "$state=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{return $true};"
        "function Test-RequiredGitHubChecks{return 'PASSED'};"
        "function Get-CandidateChangedFiles{return @('web/worker/api-router.ts')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='COMPATIBLE';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$true;"
        "requires_validation=$true;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true;state='READY'}};"
        "function Invoke-CandidateWorkerValidation{return [pscustomobject]@{passed=$true;"
        "validation_run='run-platform';expected_worker_invocations=8;observed_worker_invocations=8;"
        "static_observability_state='PASSED';observability_credential_source='LOCAL_SECRET_FILE';"
        "observability_diagnostic=$null;routes=@([pscustomobject]@{path='/api/status';passed=$true});"
        "cpu_evidence=[pscustomobject]@{expected_invocations=8;invocations=8;gate_state='FAILED';"
        "passed=$false;p95_cpu_ms=4;p99_cpu_ms=7;max_cpu_ms=9;exceeded_cpu=0;"
        "responses_1102=0;responses_5xx=1}}};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$saved=Get-ReleaseControlState;$validation=$saved.candidate.validation;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$($saved.candidate.validation_state),$($validation.reason),'
        '$($validation.routes.Count),$($validation.cpu_evidence.invocations),'
        '$($validation.cpu_evidence.responses_5xx),$($validation.observability_credential_source),'
        '$($validation.data_parity.state),$($validation.worker_failures.state),'
        '$([bool]($history -match \'WORKER_5XX_OBSERVED\')),'
        '$([bool](($validation|ConvertTo-Json -Depth 20) -match \'platform-secret-value\'))"',
    )

    assert result == (
        "FAILED,WORKER_5XX_OBSERVED,1,8,1,LOCAL_SECRET_FILE,"
        "NOT_RUN,FAILED,True,False"
    )


def test_worker_windows_mismatch_cannot_switch_runtime(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.windows_revision = ('c' * 40); "
        "Write-ReleaseControlState $state; "
        f"Write-Output (Update-RuntimeCheckout -Revision '{candidate}')",
    )

    assert result == "False"


def test_bootstrap_preserves_accepted_268_candidate_and_evidence(tmp_path) -> None:
    stable = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='76d314fc-e484-4f50-8ace-3689e0896709';percentage=100},"
        "[pscustomobject]@{version_id='dd823aa4-20f0-47e1-9255-1b785a4c17b0';percentage=0})} };"
        "function Get-CloudflareVersions { return @() };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{stable}'}} }};"
        "$state=Initialize-ReleaseControl;"
        'Write-Output "$($state.stable.worker_version_id),$($state.candidate.worker_version_id),$($state.candidate.git_sha),$($state.candidate.validation.cpu_evidence.exceeded_cpu)"',
    )

    assert result == (
        "76d314fc-e484-4f50-8ace-3689e0896709,"
        "dd823aa4-20f0-47e1-9255-1b785a4c17b0,"
        "14c055a35040fa963700c988f770c9bb52fa669e,0"
    )


def test_release_version_timestamp_normalizes_all_wrangler_shapes(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "[Globalization.CultureInfo]::CurrentCulture='en-GB';"
        "$scalar=[pscustomobject]@{metadata=[pscustomobject]@{created_on='2026-08-20T10:00:00+00:00'}};"
        "$array=[pscustomobject]@{metadata=[pscustomobject]@{created_on=@('bad','2026-08-20T11:00:00Z')}};"
        "$multiple=[pscustomobject]@{metadata=[pscustomobject]@{created_on=@(@('2026-08-20T09:00:00Z'),@('2026-08-20T12:00:00Z'))}};"
        "$dateTime=[pscustomobject]@{metadata=[pscustomobject]@{created_on=[datetime]'2026-08-25T12:41:06Z'}};"
        "$dateTimeOffset=[pscustomobject]@{metadata=[pscustomobject]@{created_on=[datetimeoffset]'2026-08-25T20:41:07+08:00'}};"
        "$malformed=[pscustomobject]@{metadata=[pscustomobject]@{created_on='not-a-date'}};"
        "$missing=[pscustomobject]@{metadata=[pscustomobject]@{}};"
        'Write-Output "$(Get-ReleaseVersionCreatedAt $scalar),'
        '$(Get-ReleaseVersionCreatedAt $array),$(Get-ReleaseVersionCreatedAt $multiple),'
        '$(Get-ReleaseVersionCreatedAt $dateTime),$(Get-ReleaseVersionCreatedAt $dateTimeOffset),'
        '$(Get-ReleaseVersionCreatedAt $malformed),$(Get-ReleaseVersionCreatedAt $missing)"',
    )
    assert result == (
        "2026-08-20T10:00:00.0000000+00:00,"
        "2026-08-20T11:00:00.0000000+00:00,"
        "2026-08-20T12:00:00.0000000+00:00,"
        "2026-08-25T12:41:06.0000000+00:00,"
        "2026-08-25T12:41:07.0000000+00:00,,"
    )


def test_release_version_timestamp_and_watermark_are_culture_invariant(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "[Globalization.CultureInfo]::CurrentCulture='en-MY';"
        "$version=[pscustomobject]@{id='version-new';metadata=[pscustomobject]@{"
        "created_on='08/26/2026 18:57:10'}};"
        "$discovery=[pscustomobject]@{watermark_created_at='08/26/2026 18:57:09';"
        "watermark_version_id='version-old'};"
        "$created=Get-ReleaseVersionCreatedAt $version;"
        "$after=Test-VersionAfterDiscoveryWatermark -Version $version -Discovery $discovery;"
        'Write-Output "$created,$after"',
    )
    assert result == "2026-08-26T18:57:10.0000000+00:00,True"


def test_release_timestamp_rejects_malformed_watermark_without_throwing(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$version=[pscustomobject]@{id='version-new';metadata=[pscustomobject]@{"
        "created_on='08/26/2026 18:57:10'}};"
        "$discovery=[pscustomobject]@{watermark_created_at='not-a-time';"
        "watermark_version_id='version-old'};"
        "$after=Test-VersionAfterDiscoveryWatermark -Version $version -Discovery $discovery;"
        'Write-Output "$after,$(Get-ReleaseVersionCreatedAt $version)"',
    )
    assert result == "False,2026-08-26T18:57:10.0000000+00:00"


def test_pwsh_json_dates_share_one_culture_invariant_control_boundary(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "[Globalization.CultureInfo]::CurrentCulture='zh-SG';"
        "$payload='{\"created_on\":\"2026-08-26T11:29:18.0000000+00:00\","
        "\"expires_at\":\"2026-08-26T12:29:18.0000000+00:00\","
        "\"watermark_created_at\":\"2026-08-26T11:29:18.0000000+00:00\"}'|ConvertFrom-Json;"
        "$created=ConvertTo-ReleaseTimestampUtc $payload.created_on;"
        "$expires=ConvertTo-ReleaseTimestampUtc $payload.expires_at;"
        "$version=[pscustomobject]@{id='new';metadata=[pscustomobject]@{"
        "created_on='2026-08-26T11:30:18.0000000+00:00'}};"
        "$after=Test-VersionAfterDiscoveryWatermark -Version $version -Discovery $payload;"
        'Write-Output "$($payload.created_on.GetType().Name),'
        '$($created.ToString(\'o\')),$($expires.ToString(\'o\')),$after"',
        powershell="pwsh.exe",
    )
    assert result == (
        "DateTime,2026-08-26T11:29:18.0000000+00:00,"
        "2026-08-26T12:29:18.0000000+00:00,True"
    )


def test_control_process_start_tokens_compare_as_exact_instants(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "[Globalization.CultureInfo]::CurrentCulture='zh-SG';"
        "$json='{\"token\":\"2026-08-26T11:22:48.0603020+08:00\"}'|ConvertFrom-Json;"
        "$same='2026-08-26T03:22:48.0603020+00:00';"
        "$different='2026-08-26T03:22:48.0603021+00:00';"
        "$checks=@("
        "(Test-ControlPlaneStartTokenEqual $json.token $same),"
        "(Test-ControlPlaneStartTokenEqual $same $different),"
        "(Test-ControlPlaneStartTokenEqual 'not-a-token' $same),"
        "(Test-ControlPlaneStartTokenEqual $null $same));"
        'Write-Output ($checks -join ",")',
        powershell="pwsh.exe",
    )
    assert result == "True,False,False,False"


def test_pwsh_installer_accepts_fresh_exact_watchdog_json_identity(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "[Globalization.CultureInfo]::CurrentCulture='zh-SG';"
        "$stamp=[DateTimeOffset]::UtcNow.ToString('o');"
        "$json='{\"observed_at\":\"'+$stamp+'\",\"process_id\":123,'"
        "+'\"process_start_token\":\"2026-08-26T11:22:48.0603020+08:00\",'"
        "+'\"control_bundle_revision\":\"'+('a'*40)+'\",'"
        "+'\"control_bundle_exact_revision\":true,'"
        "+'\"control_bundle_hash_verified\":true}';"
        "New-Item -ItemType Directory -Path (Split-Path $watchdogHeartbeatPath) "
        "-Force|Out-Null;Set-Content $watchdogHeartbeatPath $json -Encoding UTF8;"
        "$owner=[pscustomobject]@{process_id=123;"
        "process_start_token='2026-08-26T03:22:48.0603020+00:00'};"
        "$heartbeat=Assert-CurrentWatchdogHeartbeat -Owner $owner "
        "-ExpectedRevision ('a'*40);"
        'Write-Output "$($heartbeat.process_id),$($heartbeat.control_bundle_hash_verified)"',
        powershell="pwsh.exe",
    )
    assert result == "123,True"


def test_cloudflare_version_wrapper_enumerates_top_level_wrangler_array(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-WranglerJson { Write-Output -NoEnumerate @("
        "[pscustomobject]@{id='version-a';metadata=[pscustomobject]@{created_on='2026-08-20T10:00:00Z'}},"
        "[pscustomobject]@{id='version-b';metadata=[pscustomobject]@{created_on='2026-08-20T11:00:00Z'}}) };"
        "$versions=@(Get-CloudflareVersions | Sort-Object "
        "@{Expression={Get-ReleaseVersionCreatedAtValue -Version $_}},"
        "@{Expression={[string]$_.id}});"
        'Write-Output "$($versions.Count),$($versions[-1].id)"',
    )
    assert result == "2,version-b"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_rollback_target_uses_exact_view_when_recent_list_is_truncated(
    tmp_path, powershell: str,
) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    candidate = "22222222-2222-4222-8222-222222222222"
    recent_json = json.dumps([
        {
            "id": f"00000000-0000-4000-8000-{index:012d}",
            "number": index,
            "metadata": {
                "created_on": "2026-08-28T14:22:25Z",
                "source": "wrangler",
                "has_preview": True,
            },
        }
        for index in range(1, 11)
    ])
    version_json = json.dumps({
        "id": stable,
        "number": 979,
        "metadata": {
            "created_on": "2026-08-20T04:08:20Z",
            "source": "wrangler",
            "has_preview": True,
        },
        "annotations": {
            "workers/triggered_by": "version_upload",
            "workers/message": (
                f"release:{'a' * 40} branch:main "
                "artifact_kind:PRODUCTION_CANDIDATE"
            ),
        },
        "resources": {
            "script": {"etag": "exact-etag", "handlers": ["fetch"]},
            "bindings": [],
        },
    })
    deployment_json = json.dumps({
        "id": "deployment-id",
        "source": "wrangler",
        "strategy": "percentage",
        "versions": [
            {"version_id": stable, "percentage": 100},
            {"version_id": candidate, "percentage": 0},
        ],
    })
    result = _run_control_center_contract(
        tmp_path,
        "$script:listCalled=$false;"
        f"function Get-CloudflareVersions{{$script:listCalled=$true;"
        f"'{recent_json}'|ConvertFrom-Json}};"
        f"function Get-CloudflareVersionDetails{{param($VersionId);"
        f"'{version_json}'|ConvertFrom-Json}};"
        f"function Get-CloudflareDeployment{{'{deployment_json}'|ConvertFrom-Json}};"
        f"$target=[pscustomobject]@{{worker_version_id='{stable}';git_sha=('a'*40);"
        "worker_git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';branch='main'};"
        "$passed=Test-CloudflareRollbackTarget -Target $target;"
        'Write-Output "$passed,$script:listCalled"',
        powershell=powershell,
    )

    assert result == "True,False"


@pytest.mark.parametrize(
    "exact_lookup",
    (
        "throw 'VERSION_NOT_FOUND'",
        "throw 'PROVIDER_TRANSPORT_FAILED'",
        "[pscustomobject]@{id='22222222-2222-4222-8222-222222222222';"
        "metadata=[pscustomobject]@{source='wrangler'};resources=[pscustomobject]@{"
        "script=[pscustomobject]@{handlers=@('fetch')}}}",
        "[pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "metadata=[pscustomobject]@{source='wrangler'};resources=[pscustomobject]@{}}",
        "@([pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "metadata=[pscustomobject]@{source='wrangler'};resources=[pscustomobject]@{"
        "script=[pscustomobject]@{handlers=@('fetch')}}})",
    ),
    ids=(
        "missing",
        "transport-error",
        "candidate-stable-confusion",
        "malformed-resources",
        "malformed-array-envelope",
    ),
)
def test_rollback_target_exact_lookup_failures_are_fail_closed(
    tmp_path, exact_lookup: str,
) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        f"function Get-CloudflareVersionDetails{{param($VersionId);{exact_lookup}}};"
        "function Get-CloudflareDeployment{throw 'DEPLOYMENT_MUST_NOT_BE_READ'};"
        f"$target=[pscustomobject]@{{worker_version_id='{stable}'}};"
        "Write-Output (Test-CloudflareRollbackTarget -Target $target)",
    )

    assert result == "False"


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    (
        ("VERSION_NOT_FOUND", "UNAVAILABLE"),
        ("HTTP STATUS 404", "UNAVAILABLE"),
        ("HTTP STATUS 401", "UNKNOWN"),
        ("HTTP STATUS 403", "UNKNOWN"),
        ("credentials missing", "UNKNOWN"),
        ("version not found", "UNKNOWN"),
        ("Wrangler CLI is unavailable", "UNKNOWN"),
        ("provider transport timeout", "UNKNOWN"),
        ("HTTP STATUS 429", "UNKNOWN"),
        ("temporary provider unavailable", "UNKNOWN"),
    ),
)
def test_rollback_exact_lookup_provider_failure_taxonomy(
    tmp_path, diagnostic: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"function Get-CloudflareVersionDetails{{throw '{diagnostic}'}};"
        "$target=[pscustomobject]@{worker_version_id='11111111-1111-4111-8111-111111111111';"
        "git_sha=('a'*40);worker_git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';branch='main'};"
        "$identity=Resolve-ReleaseRuntimeIdentity $target;"
        "$result=Get-CloudflareRollbackArtifactObservation -IdentityResolution $identity -ForceFresh;"
        'Write-Output "$($result.status),$($result.reason)"',
    )
    assert result == f"{expected},WORKER_VERSION_PROVIDER_{expected}"


@pytest.mark.parametrize(
    ("native_behavior", "expected"),
    (
        ("return [pscustomobject]@{exit_code=1;stdout='';stderr='HTTP STATUS 404';stdout_lines=@();stderr_lines=@('HTTP STATUS 404')}", "UNAVAILABLE"),
        ("return [pscustomobject]@{exit_code=1;stdout='';stderr='Bearer secret HTTP STATUS 401';stdout_lines=@();stderr_lines=@()}", "UNKNOWN"),
        ("return [pscustomobject]@{exit_code=1;stdout='';stderr='HTTP STATUS 429 rate limit';stdout_lines=@();stderr_lines=@()}", "UNKNOWN"),
        ("return [pscustomobject]@{exit_code=1;stdout='';stderr='version not found';stdout_lines=@();stderr_lines=@()}", "UNKNOWN"),
        ("throw 'NATIVE_PROCESS_TIMEOUT'", "UNKNOWN"),
    ),
)
def test_real_wrangler_wrapper_preserves_bounded_provider_taxonomy(
    tmp_path, native_behavior: str, expected: str,
) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        "$cli=Join-Path $repositoryRoot 'web\\node_modules\\wrangler\\bin\\wrangler.js';"
        "New-Item -ItemType Directory -Path (Split-Path -Parent $cli) -Force|Out-Null;"
        "New-Item -ItemType File -Path $cli -Force|Out-Null;"
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);"
        f"{native_behavior}}};"
        f"$target=[pscustomobject]@{{worker_version_id='{version}';git_sha=('a'*40);"
        "worker_git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';branch='main'};"
        "$identity=Resolve-ReleaseRuntimeIdentity $target;"
        "$result=Get-CloudflareRollbackArtifactObservation $identity -ForceFresh;"
        'Write-Output "$($result.status),$($result.reason)"',
    )
    assert result == f"{expected},WORKER_VERSION_PROVIDER_{expected}"


@pytest.mark.parametrize(
    ("versions", "has_previous", "expected"),
    (
        ([{"version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "percentage": 100},
          {"version_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "percentage": 0}],
         True, "ASSIGNED"),
        ([{"version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "percentage": 100}],
         True, "NOT_ASSIGNED"),
        ([{"version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "percentage": 100},
          {"version_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "percentage": 0},
          {"version_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "percentage": 25}],
         True, "MISMATCH"),
        ([{"version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "percentage": 100}],
         False, "NOT_APPLICABLE"),
    ),
)
def test_traffic_membership_is_authoritative_enum(
    tmp_path, versions: list[dict], has_previous: bool, expected: str,
) -> None:
    deployment = json.dumps({"versions": versions})
    previous = ("[pscustomobject]@{worker_version_id="
                "'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'}") if has_previous else "$null"
    result = _run_control_center_contract(
        tmp_path,
        f"$deployment='{deployment}'|ConvertFrom-Json;$previous={previous};"
        "$result=Get-ReleaseTrafficObservation -Deployment $deployment -Previous $previous;"
        'Write-Output "$($result.previous_membership_status),$($result.previous_is_member)"',
    )
    assert result == f"{expected},{'True' if expected == 'ASSIGNED' else 'False'}"


@pytest.mark.parametrize(
    ("value", "valid", "parsed"),
    (
        ("$null", False, ""), ("''", False, ""), ("'NaN'", False, ""),
        ("'Infinity'", False, ""), ("-1", False, ""), ("101", False, ""),
        ("0", True, "0"), ("100", True, "100"),
    ),
)
def test_traffic_percentage_parser_is_finite_invariant_and_bounded(
    tmp_path, value: str, valid: bool, parsed: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$result=ConvertFrom-ReleaseTrafficPercentage {value};"
        'Write-Output "$($result.valid),$($result.value)"',
    )
    assert result == f"{'True' if valid else 'False'},{parsed}"


@pytest.mark.parametrize(
    "row",
    (
        "[pscustomobject]@{version_id='a'}",
        "[pscustomobject]@{version_id='a';percentage=$null}",
        "[pscustomobject]@{version_id='a';percentage=''}",
        "[pscustomobject]@{version_id='a';percentage='NaN'}",
        "[pscustomobject]@{version_id='a';percentage='Infinity'}",
        "[pscustomobject]@{version_id='a';percentage=-1}",
        "[pscustomobject]@{version_id='a';percentage=101}",
        "[pscustomobject]@{version_id='';percentage=100}",
    ),
)
def test_malformed_deployment_row_fails_membership_closed(tmp_path, row: str) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$deployment=[pscustomobject]@{{versions=@({row})}};"
        "$previous=[pscustomobject]@{worker_version_id='b'};"
        "$result=Get-ReleaseTrafficObservation $deployment $previous;"
        'Write-Output "$($result.status),$($result.previous_membership_status)"',
    )
    assert result == "MISMATCH,MISMATCH"


def test_positive_split_traffic_cannot_claim_singular_active_owner(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$deployment=[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='active';percentage=100},"
        "[pscustomobject]@{version_id='other';percentage=25})};"
        "$result=Get-ReleaseTrafficObservation $deployment $null;"
        'Write-Output "$($result.status),$($result.version_id)"',
    )
    assert result == "MISMATCH,"


def test_provider_unknown_membership_is_not_not_assigned(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$previous=[pscustomobject]@{worker_version_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'};"
        "$result=Get-ReleaseTrafficObservation -Deployment $null -Previous $previous -Status UNKNOWN;"
        'Write-Output "$($result.previous_membership_status),$($result.previous_is_member)"',
    )
    assert result == "UNKNOWN,False"


@pytest.mark.parametrize(
    "powershell",
    [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_runtime_composition_and_presenter_preserve_unknown_membership(
    tmp_path, powershell: str,
) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    previous = "22222222-2222-4222-8222-222222222222"
    identity = ("[pscustomobject]@{git_sha=('a'*40);worker_git_sha=('a'*40);"
                f"worker_version_id='{stable}';windows_revision=('a'*40);branch='main';"
                "artifact_kind='PRODUCTION_CANDIDATE';validation_key='key'}")
    previous_identity = ("[pscustomobject]@{git_sha=('b'*40);worker_git_sha=('b'*40);"
                         f"worker_version_id='{previous}';windows_revision=('b'*40);branch='main';"
                         "artifact_kind='PRODUCTION_CANDIDATE';validation_key='previous-key'}")
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CloudflareDeployment{throw 'provider unavailable'};"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('a'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='DEGRADED';"
        "reason='TEST';ownership_status='SINGLE_OWNER'}};"
        "function Get-CloudflareRollbackArtifactObservation{[pscustomobject]@{status='AVAILABLE';reason='OK'}};"
        "function Get-ReleaseWindowsArtifactObservation{[pscustomobject]@{status='AVAILABLE';reason='OK'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        f"$state=[pscustomobject]@{{schema_version='stable-candidate-release-v3';stable={identity};"
        f"previous_stable={previous_identity};candidate=$null;transaction=$null;"
        "deployment_status='READY';control_bundle_hash_verified=$true;"
        "control_bundle_exact_revision=$true;control_bundle_revision=('a'*40)};"
        "$model=Get-CurrentReleaseRuntimeReadModel -PersistedState $state;"
        "$state|Add-Member release_runtime $model;$view=Get-ControlCenterReleasePresentation $state;"
        'Write-Output "$($model.previous.worker_traffic_membership_status),'
        '$($view.previous_traffic_membership_status),$($view.can_reverse),'
        '$($model.previous.reverse_precheck.reason)"',
        powershell=powershell,
    )
    assert result == "UNKNOWN,UNKNOWN,False,ACTIVE_OBSERVATION_UNAVAILABLE"


@pytest.mark.parametrize(
    "powershell",
    [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_runtime_composition_allows_degraded_safe_reverse(
    tmp_path, powershell: str,
) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    previous = "22222222-2222-4222-8222-222222222222"
    result = _run_control_center_contract(
        tmp_path,
        f"function Get-CloudflareDeployment{{[pscustomobject]@{{versions=@("
        f"[pscustomobject]@{{version_id='{stable}';percentage=100}})}}}};"
            f"function Get-CloudflareVersionDetails{{[pscustomobject]@{{id='{stable}';"
            "metadata=[pscustomobject]@{source='wrangler'};"
            "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}};"
            "annotations=[pscustomobject]@{'workers/message'=('release:'+('a'*40)+' branch:main artifact_kind:PRODUCTION_CANDIDATE')}}};"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('a'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='DEGRADED';"
        "reason='LOCAL_API_OR_RUNTIME_HEALTH_FAILED';ownership_status='SINGLE_OWNER'}};"
        "function Get-CloudflareRollbackArtifactObservation{[pscustomobject]@{status='AVAILABLE';reason='OK'}};"
        "function Get-ReleaseWindowsArtifactObservation{[pscustomobject]@{status='AVAILABLE';reason='OK'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        f"$stable=[pscustomobject]@{{git_sha=('a'*40);worker_git_sha=('a'*40);worker_version_id='{stable}';"
        "windows_revision=('a'*40);branch='main';artifact_kind='PRODUCTION_CANDIDATE';validation_key='key'};"
        f"$previous=[pscustomobject]@{{git_sha=('b'*40);worker_git_sha=('b'*40);worker_version_id='{previous}';"
        "windows_revision=('b'*40);branch='main';artifact_kind='PRODUCTION_CANDIDATE';validation_key='previous-key'};"
        "$state=[pscustomobject]@{schema_version='stable-candidate-release-v3';stable=$stable;"
        "previous_stable=$previous;candidate=$null;transaction=$null;deployment_status='READY';"
        "control_bundle_hash_verified=$true;control_bundle_exact_revision=$true;control_bundle_revision=('a'*40)};"
        "$model=Get-CurrentReleaseRuntimeReadModel -PersistedState $state;"
        'Write-Output "$($model.active.health),$($model.active_matches_committed),'
        '$($model.previous.reverse_precheck.can_reverse),$($model.previous.reverse_precheck.reason)"',
        powershell=powershell,
    )
    assert result == "DEGRADED,True,True,READY"


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_provider_observation_cache_bounds_fast_gui_refreshes(
    tmp_path, powershell: str,
) -> None:
    active = "11111111-1111-4111-8111-111111111111"
    previous = "22222222-2222-4222-8222-222222222222"
    result = _run_control_center_contract(
        tmp_path,
        "$script:deployReads=0;$script:versionReads=0;"
        f"function Get-CloudflareDeployment{{$script:deployReads++;[pscustomobject]@{{versions=@("
        f"[pscustomobject]@{{version_id='{active}';percentage=100}})}}}};"
        "function Get-CloudflareVersionDetails{param($VersionId);$script:versionReads++;"
        "[pscustomobject]@{id=$VersionId;metadata=[pscustomobject]@{source='wrangler'};"
        "annotations=[pscustomobject]@{'workers/message'=('release:'+('a'*40)+' branch:main artifact_kind:PRODUCTION_CANDIDATE')};"
        "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}}}};"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('a'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='HEALTHY';"
        "business_health_status='HEALTHY';business_health_reason='OK';"
        "reason='OK';ownership_status='SINGLE_OWNER'}};"
        "function Get-ReleaseWindowsArtifactObservation{[pscustomobject]@{status='AVAILABLE';reason='OK'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        f"$stable=[pscustomobject]@{{git_sha=('a'*40);worker_git_sha=('a'*40);worker_version_id='{active}';"
        "windows_revision=('a'*40);branch='main';artifact_kind='PRODUCTION_CANDIDATE'};"
        f"$previous=[pscustomobject]@{{git_sha=('a'*40);worker_git_sha=('a'*40);worker_version_id='{previous}';"
        "windows_revision=('a'*40);branch='main';artifact_kind='PRODUCTION_CANDIDATE'};"
        "$state=[pscustomobject]@{schema_version='stable-candidate-release-v3';stable=$stable;"
        "previous_stable=$previous;candidate=$null;transaction=$null};"
        "1..6|%{$null=Get-CurrentReleaseRuntimeReadModel -PersistedState $state};"
        "$null=Get-CurrentReleaseRuntimeReadModel -PersistedState $state -ForceProviderRefresh;"
        'Write-Output "$script:deployReads,$script:versionReads"', powershell=powershell,
    )
    assert result == "2,4"


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_immutable_exact_version_fact_survives_independent_transport_failure(
    tmp_path, powershell: str,
) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        "$script:reads=0;"
        f"function Get-CloudflareVersionDetails{{param($VersionId);$script:reads++;"
        f"[pscustomobject]@{{id='{version}';metadata=[pscustomobject]@{{source='wrangler'}};"
        "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}}}};"
        f"$first=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        "function Get-CloudflareVersionDetails{param($VersionId);$script:reads++;throw 'transport'};"
        f"$failed=Get-ReleaseExactVersionProviderObservation -VersionId '{version}' -ForceFresh;"
        f"$reused=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        'Write-Output "$($first.status),$($failed.status),$($reused.status),$script:reads"',
        powershell=powershell,
    )
    assert result == "AVAILABLE,UNKNOWN,AVAILABLE,2"


@pytest.mark.parametrize("first_shape", ("MALFORMED", "WRONG_ID"))
def test_invalid_exact_envelope_retries_and_recovers_after_interval(
    tmp_path, first_shape: str,
) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    first = (
        f"[pscustomobject]@{{id='{version}'}}"
        if first_shape == "MALFORMED"
        else "[pscustomobject]@{id='22222222-2222-4222-8222-222222222222';"
             "metadata=[pscustomobject]@{source='wrangler'};resources=[pscustomobject]@{"
             "script=[pscustomobject]@{handlers=@('fetch')}}}"
    )
    result = _run_control_center_contract(
        tmp_path,
        "$script:reads=0;function Get-CloudflareVersionDetails{param($VersionId);"
        "$script:reads++;if($script:reads -eq 1){return " + first + "};"
        f"[pscustomobject]@{{id='{version}';metadata=[pscustomobject]@{{source='wrangler'}};"
        "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}}}};"
        f"$first=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        f"$script:releaseExactVersionObservationCache['{version}'].attempted_at="
        "[DateTimeOffset]::UtcNow.AddMinutes(-2).ToString('o');"
        f"$second=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        'Write-Output "$($first.status),$($second.status),$script:reads"',
    )
    expected_first = "UNKNOWN" if first_shape == "MALFORMED" else "MISMATCH"
    assert result == f"{expected_first},AVAILABLE,2"


def test_validated_exact_version_envelope_is_immutable_and_reused(tmp_path) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        "$script:reads=0;function Get-CloudflareVersionDetails{param($VersionId);$script:reads++;"
        f"[pscustomobject]@{{id='{version}';metadata=[pscustomobject]@{{source='wrangler'}};"
        "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}}}};"
        f"$first=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        f"$second=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        'Write-Output "$($first.status),$($second.status),$script:reads"',
    )
    assert result == "AVAILABLE,AVAILABLE,1"


def test_force_fresh_malformed_response_does_not_overwrite_validated_ui_fact(
    tmp_path,
) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        "$script:reads=0;function Get-CloudflareVersionDetails{param($VersionId);$script:reads++;"
        f"if($script:reads -eq 1){{return [pscustomobject]@{{id='{version}';"
        "metadata=[pscustomobject]@{source='wrangler'};resources=[pscustomobject]@{"
        "script=[pscustomobject]@{handlers=@('fetch')}}}};"
        f"return [pscustomobject]@{{id='{version}'}}}};"
        f"$valid=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        f"$fresh=Get-ReleaseExactVersionProviderObservation -VersionId '{version}' -ForceFresh;"
        f"$ui=Get-ReleaseExactVersionProviderObservation -VersionId '{version}';"
        'Write-Output "$($valid.status),$($fresh.status),$($ui.status),$script:reads"',
    )
    assert result == "AVAILABLE,UNKNOWN,AVAILABLE,2"


def test_exact_version_lookup_binds_account_worker_and_script_scope(tmp_path) -> None:
    version = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        "$cli=Join-Path $repositoryRoot 'web\\node_modules\\wrangler\\bin\\wrangler.js';"
        "New-Item -ItemType Directory -Path (Split-Path -Parent $cli) -Force|Out-Null;"
        "New-Item -ItemType File -Path $cli -Force|Out-Null;"
        "[Environment]::SetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID','prior','Process');"
        "$script:seenAccount='';$script:seenArguments=@();"
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);"
        "$script:seenAccount=[Environment]::GetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID','Process');"
        "$script:seenArguments=@($Arguments);"
        f"[pscustomobject]@{{exit_code=0;stdout='{{\"id\":\"{version}\"}}'}}}};"
        f"$result=Get-CloudflareVersionDetails -VersionId '{version}';"
        "$restored=[Environment]::GetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID','Process');"
        'Write-Output "$script:seenAccount|$restored|$($script:seenArguments -join \'|\')"',
    )
    assert result == (
        "48ce531f39e2310b4c858c8916a01d51|prior|"
        f"{tmp_path / 'repository' / 'web' / 'node_modules' / 'wrangler' / 'bin' / 'wrangler.js'}"
        f"|versions|view|{version}|--name|aurum-signal-room|--json"
    )


def test_mutable_deployment_observation_refreshes_after_ttl(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:reads=0;function Get-CloudflareDeployment{$script:reads++;"
        "[pscustomobject]@{versions=@()}};"
        "$null=Get-ReleaseDeploymentProviderObservation;"
        "$script:releaseDeploymentObservationCache.attempted_at="
        "([DateTimeOffset]::UtcNow-[TimeSpan]::FromMinutes(2)).ToString('o');"
        "$null=Get-ReleaseDeploymentProviderObservation;Write-Output $script:reads",
    )
    assert result == "2"


def test_winforms_splits_fast_local_and_slow_provider_refreshes() -> None:
    source = _control_center_source()
    winforms = source.split("function Show-ControlCenter {", 1)[1]
    assert '"-SkipProviderObservation"' in winforms
    assert "function Request-GuiReleaseStatus" in winforms
    assert "$releaseProviderObservationInterval.TotalSeconds" in winforms
    assert "if ($script:winFormsProviderObservation) { return }" in winforms
    assert "Start-ControlCenterProviderObservationProcess" in winforms
    assert "Complete-ControlCenterProviderObservationProcess" in winforms
    assert "Request-GuiStatus -ForceProviderRefresh" in winforms


def test_wpf_and_winforms_provider_reads_are_background_and_single_flight() -> None:
    source = _control_center_source()
    wpf = source.split("function Show-WpfControlCenter", 1)[1].split(
        "function Show-ControlCenter", 1,
    )[0]
    winforms = source.split("function Show-ControlCenter", 1)[1]
    assert "Start-ControlCenterProviderObservationProcess" in wpf
    assert "Complete-ControlCenterProviderObservationProcess" in wpf
    assert "if ($script:wpfProviderObservation) { return }" in wpf
    assert "Start-ControlCenterProviderObservationProcess" in winforms
    assert "if ($script:winFormsProviderObservation) { return }" in winforms
    assert "Get-ReleaseControlStatusSnapshot -SkipProviderObservation" in wpf
    assert '"-SkipProviderObservation"' in winforms


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_local_runtime_facts_use_explicit_previous_resolution_not_dynamic_scope(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$correct=[pscustomobject]@{status='COMPLETE';identity=[pscustomobject]@{"
        "windows_revision=('a'*40);git_sha=('a'*40)}};"
        "$previousResolution=[pscustomobject]@{status='COMPLETE';identity=[pscustomobject]@{"
        "windows_revision=('b'*40);git_sha=('b'*40)}};"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('c'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='HEALTHY'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        "function Get-ReleaseWindowsArtifactObservation{param($IdentityResolution);"
        "$script:observedResolution=$IdentityResolution;[pscustomobject]@{status='AVAILABLE'}};"
        "$null=Get-ReleaseLocalRuntimeFacts -PersistedState ([pscustomobject]@{}) "
        "-PreviousIdentityResolution $correct;"
        'Write-Output "$($script:observedResolution.identity.windows_revision)"',
        powershell=powershell,
    )
    assert result == "a" * 40


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_local_runtime_facts_have_no_dynamic_scope_fallback_and_malformed_is_closed(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('c'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='HEALTHY'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        "$malformed=[pscustomobject]@{status='MISMATCH';identity=$null};"
        "$facts=Get-ReleaseLocalRuntimeFacts -PersistedState ([pscustomobject]@{}) "
        "-PreviousIdentityResolution $malformed;"
        'Write-Output "$($facts.previous_windows_artifact.status),'
        '$($facts.previous_windows_artifact.reason)"',
        powershell=powershell,
    )
    assert result == "MISMATCH,PREVIOUS_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_local_runtime_facts_work_without_caller_previous_resolution_variable(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$explicit=[pscustomobject]@{status='COMPLETE';identity=[pscustomobject]@{"
        "windows_revision=('d'*40);git_sha=('d'*40)}};"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('c'*40)}};"
        "function Get-ReleaseActiveHealthObservation{[pscustomobject]@{status='HEALTHY'}};"
        "function Get-RuntimeControlBundleIdentity{[pscustomobject]@{exact_revision=$true}};"
        "function Get-ReleaseWindowsArtifactObservation{param($IdentityResolution);"
        "[pscustomobject]@{status='AVAILABLE';revision=$IdentityResolution.identity.windows_revision}};"
        "$facts=Get-ReleaseLocalRuntimeFacts -PersistedState ([pscustomobject]@{}) "
        "-PreviousIdentityResolution $explicit;"
        "Write-Output $facts.previous_windows_artifact.revision",
        powershell=powershell,
    )
    assert result == "d" * 40


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_native_process_deadline_terminates_hung_child(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$watch=[Diagnostics.Stopwatch]::StartNew();$reason='';"
        "try{$null=Invoke-Utf8NativeProcess -FilePath 'powershell.exe' "
        "-Arguments @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        "-TimeoutMilliseconds 200}catch{$reason=$_.Exception.Message};"
        "$watch.Stop();"
        'Write-Output "$reason,$([int]$watch.Elapsed.TotalSeconds)"',
        powershell=powershell,
    )
    reason, elapsed = result.split(",")
    assert reason == "NATIVE_PROCESS_TIMEOUT"
    # The exact 200 ms deadline and timeout reason are the contract. This outer
    # wall-clock guard detects a deadlock without treating shared-runner
    # scheduling latency as product behavior.
    assert int(elapsed) < 15


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_native_process_unresolved_termination_propagates_exact_ownership(
    tmp_path, powershell: str,
) -> None:
    receipt = tmp_path / "native-ownership.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Stop-NativeProcessTree{param($Process);"
        "[pscustomobject]@{state='TERMINATION_FAILED';root_exited=$false;"
        "descendants_exited=$false}};"
        "$reason='';try{$null=Invoke-Utf8NativeProcess -FilePath 'powershell.exe' "
        "-Arguments @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        f"-TimeoutMilliseconds 100 -OwnershipReceiptPath '{receipt}'"
        "}catch{$reason=$_.Exception.Message};"
        f"$owned=Get-Content '{receipt}' -Raw|ConvertFrom-Json;"
        "$alive=Test-NativeProcessIdentityAlive $owned.root;"
        "if($alive){Stop-Process -Id $owned.root.pid -Force};"
        f"Remove-Item '{receipt}' -Force;"
        'Write-Output "$reason,$alive,$($owned.root.pid),$($owned.root.start_token.Length -gt 0)"',
        powershell=powershell,
    )
    reason, alive, pid, token = result.split(",")
    assert reason == "NATIVE_PROCESS_TERMINATION_UNRESOLVED"
    assert alive == "True"
    assert int(pid) > 0
    assert token == "True"


def test_initial_native_ownership_receipt_failure_terminates_started_process(
    tmp_path,
) -> None:
    receipt = tmp_path / "initial-receipt.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Write-NativeProcessOwnershipReceipt{param($Path,$RootProcess);"
        "$script:startedPid=$RootProcess.Id;throw 'injected-write-failure'};"
        "$reason='';try{$null=Invoke-Utf8NativeProcess -FilePath 'powershell.exe' "
        "-Arguments @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        f"-TimeoutMilliseconds 5000 -OwnershipReceiptPath '{receipt}'"
        "}catch{$reason=$_.Exception.Message};"
        "$alive=[bool](Get-Process -Id $script:startedPid -ErrorAction SilentlyContinue);"
        f"$files=@(Get-ChildItem '{tmp_path}' -Filter 'initial-receipt.json*' "
        "-ErrorAction SilentlyContinue).Count;"
        'Write-Output "$reason,$alive,$files"',
    )
    assert result == "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED,False,0"


def test_initial_receipt_failure_with_unproved_termination_preserves_live_owner(
    tmp_path,
) -> None:
    receipt = tmp_path / "unresolved-receipt.json"
    result = _run_control_center_contract(
        tmp_path,
        "function Write-NativeProcessOwnershipReceipt{param($Path,$RootProcess);"
        "$script:startedProcess=$RootProcess;throw 'injected-write-failure'};"
        "function Stop-NativeProcessTree{param($Process);"
        "[pscustomobject]@{state='TERMINATION_FAILED'}};"
        "$reason='';try{$null=Invoke-Utf8NativeProcess -FilePath 'powershell.exe' "
        "-Arguments @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        f"-TimeoutMilliseconds 5000 -OwnershipReceiptPath '{receipt}'"
        "}catch{$reason=$_.Exception.Message};"
        "$same=[bool]($script:unresolvedNativeProcess.Id -eq $script:startedProcess.Id);"
        "$alive=[bool](Get-Process -Id $script:startedProcess.Id -ErrorAction SilentlyContinue);"
        "$script:startedProcess.Kill();$script:startedProcess.WaitForExit();"
        "$script:startedProcess.Dispose();"
        'Write-Output "$reason,$same,$alive"',
    )
    assert result == "NATIVE_PROCESS_TERMINATION_UNRESOLVED,True,True"


def test_native_receipt_requires_readable_root_identity(tmp_path) -> None:
    receipt = tmp_path / "identity-receipt.json"
    result = _run_control_center_contract(
        tmp_path,
        "$process=Start-Process powershell.exe -ArgumentList "
        "@('-NoProfile','-Command','Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        "function Get-NativeProcessIdentity{return $null};$reason='';"
        f"try{{$null=Write-NativeProcessOwnershipReceipt -Path '{receipt}' "
        "-RootProcess $process}catch{$reason=$_.Exception.Message};"
        "$process.Kill();$process.WaitForExit();$process.Dispose();"
        f'$exists=[bool](Test-Path \'{receipt}\');Write-Output "$reason,$exists"',
    )
    assert result == "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED,False"


@pytest.mark.parametrize(
    "receipt_body",
    (
        '{"schema_version":"wrong","root":{"pid":123,"start_token":"456"}}',
        '{"schema_version":"native-process-ownership-v1","root":{"pid":999,"start_token":"456"}}',
        '{"schema_version":"native-process-ownership-v1","root":{"pid":123,"start_token":"wrong"}}',
        "{malformed",
    ),
)
def test_native_receipt_readback_mismatch_fails_closed(
    tmp_path, receipt_body: str,
) -> None:
    receipt = tmp_path / "readback.json"
    receipt.write_text(receipt_body, encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        "$reason='';try{$null=Confirm-NativeProcessOwnershipReceipt "
        f"-Path '{receipt}' -ExpectedRoot ([pscustomobject]@{{pid=123;start_token='456'}})"
        "}catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"


def test_timeout_receipt_update_failure_still_terminates_tree(tmp_path) -> None:
    receipt = tmp_path / "timeout-update.json"
    result = _run_control_center_contract(
        tmp_path,
        "$script:realWrite=${function:Write-NativeProcessOwnershipReceipt};$script:writes=0;"
        "function Write-NativeProcessOwnershipReceipt{param($Path,$RootProcess,$DescendantIds=@());"
        "$script:writes++;if($script:writes -gt 1){throw 'injected-update-failure'};"
        "& $script:realWrite -Path $Path -RootProcess $RootProcess -DescendantIds $DescendantIds};"
        "$script:realStop=${function:Stop-NativeProcessTree};$script:stopCalled=$false;"
        "function Stop-NativeProcessTree{param($Process);$script:stopCalled=$true;"
        "& $script:realStop -Process $Process};$reason='';"
        "try{$null=Invoke-Utf8NativeProcess -FilePath 'powershell.exe' "
        "-Arguments @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        f"-TimeoutMilliseconds 100 -OwnershipReceiptPath '{receipt}'"
        "}catch{$reason=$_.Exception.Message};"
        f'$exists=[bool](Test-Path \'{receipt}\');'
        'Write-Output "$reason,$script:stopCalled,$exists"',
    )
    assert result == "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED,True,False"


def test_native_ownership_receipt_uses_atomic_verified_commit() -> None:
    source = _control_center_source()
    writer = source.split("function Write-NativeProcessOwnershipReceipt", 1)[1].split(
        "function Test-NativeProcessIdentityAlive", 1,
    )[0]
    assert "[System.IO.File]::WriteAllBytes($temporary, $bytes)" in writer
    assert "[System.IO.File]::Replace($temporary, $Path, $null, $true)" in writer
    assert "[System.IO.File]::Move($temporary, $Path)" in writer
    assert "Confirm-NativeProcessOwnershipReceipt" in writer
    assert "Set-Content" not in writer


def test_unresolved_native_work_keeps_provider_root_contained_until_exit() -> None:
    source = _control_center_source()
    action = source.split('"ReleaseProviderFactsJson" {', 1)[1].split(
        '"TerminateProviderObservation" {', 1,
    )[0]
    assert "NATIVE_PROCESS_TERMINATION_UNRESOLVED" in action
    assert "Wait-NativeProcessContainment -Process $script:unresolvedNativeProcess" in action


def test_provider_root_identity_failure_terminates_before_start_returns(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:providerPid=0;function Start-Process{param($FilePath,$ArgumentList,"
        "$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,"
        "$RedirectStandardError);$p=Microsoft.PowerShell.Management\\Start-Process "
        "-FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        "$script:providerPid=$p.Id;return $p};"
        "function Get-NativeProcessIdentity{return $null};"
        "function Stop-NativeProcessTree{param($Process);$Process.Kill();"
        "$Process.WaitForExit();[pscustomobject]@{state='TERMINATED'}};"
        "$reason='';try{$null=Start-ControlCenterProviderObservationProcess "
        "-ExpectedControlRevision ('a'*40)}catch{$reason=$_.Exception.Message};"
        "$alive=[bool](Get-Process -Id $script:providerPid -ErrorAction SilentlyContinue);"
        'Write-Output "$reason,$alive"',
    )
    assert result == "PROVIDER_OBSERVATION_PROCESS_IDENTITY_UNAVAILABLE,False"


def test_unresolved_provider_root_identity_preserves_single_flight_until_exit(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:starts=0;function Start-Process{param($FilePath,$ArgumentList,"
        "$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,"
        "$RedirectStandardError);$script:starts++;"
        "Microsoft.PowerShell.Management\\Start-Process -FilePath 'powershell.exe' "
        "-ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 60') "
        "-WindowStyle Hidden -PassThru};"
        "function Get-NativeProcessIdentity{return $null};"
        "function Stop-NativeProcessTree{[pscustomobject]@{state='TERMINATION_FAILED'}};"
        "$observation=Start-ControlCenterProviderObservationProcess "
        "-ExpectedControlRevision ('a'*40);"
        "$first=Complete-ControlCenterProviderObservationProcess $observation;"
        "if($first.release_slot -eq 'CLEAR'){$null=Start-ControlCenterProviderObservationProcess "
        "-ExpectedControlRevision ('a'*40)};"
        "$observation.process.Kill();$observation.process.WaitForExit();"
        "$last=Complete-ControlCenterProviderObservationProcess $observation;"
        'Write-Output "$($first.state),$($first.release_slot),$script:starts,'
        '$($last.state),$($last.release_slot)"',
    )
    assert result == "TERMINATION_UNRESOLVED,PRESERVE,1,UNKNOWN,CLEAR"


def test_provider_adapters_do_not_downgrade_unresolved_native_ownership(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CloudflareDeployment{throw 'NATIVE_PROCESS_TERMINATION_UNRESOLVED'};"
        "$deployment='';try{$null=Get-ReleaseDeploymentProviderObservation -ForceFresh}"
        "catch{$deployment=$_.Exception.Message};"
        "function Get-CloudflareVersionDetails{throw 'NATIVE_PROCESS_TERMINATION_UNRESOLVED'};"
        "$version='';try{$null=Get-ReleaseExactVersionProviderObservation "
        "-VersionId '11111111-1111-4111-8111-111111111111' -ForceFresh}"
        "catch{$version=$_.Exception.Message};"
        'Write-Output "$deployment,$version"',
    )
    assert result == (
        "NATIVE_PROCESS_TERMINATION_UNRESOLVED,"
        "NATIVE_PROCESS_TERMINATION_UNRESOLVED"
    )


def test_background_provider_timeout_is_bounded_and_cleans_unique_files(tmp_path) -> None:
    result_path = tmp_path / "provider.json"
    output_path = tmp_path / "provider.out"
    error_path = tmp_path / "provider.err"
    result = _run_control_center_contract(
        tmp_path,
        "$process=Start-Process -FilePath 'powershell.exe' -ArgumentList "
        "@('-NoProfile','-Command','Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        f"$resultPath='{result_path}';$outputPath='{output_path}';$errorPath='{error_path}';"
        "Set-Content $resultPath '{}';Set-Content $outputPath '';Set-Content $errorPath '';"
        "$observation=[pscustomobject]@{process=$process;result_path=$resultPath;"
        "output_path=$outputPath;error_path=$errorPath;"
        "attempted_at=[DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o');"
        "deadline_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')};"
        "$pidValue=$process.Id;$watch=[Diagnostics.Stopwatch]::StartNew();"
        "$first=Complete-ControlCenterProviderObservationProcess $observation;$watch.Stop();"
        "$limit=[DateTimeOffset]::UtcNow.AddSeconds(15);do{Start-Sleep -Milliseconds 50;"
        "$answer=Complete-ControlCenterProviderObservationProcess $observation}while("
        "$answer.release_slot -ne 'CLEAR' -and [DateTimeOffset]::UtcNow -lt $limit);"
        "$alive=[bool](Get-Process -Id $pidValue -ErrorAction SilentlyContinue);"
        "$files=[bool]((Test-Path $resultPath)-or(Test-Path $outputPath)-or(Test-Path $errorPath));"
        'Write-Output "$($first.state),$([int]$watch.Elapsed.TotalMilliseconds),'
        '$($answer.state),$alive,$files"',
    )
    state, callback_ms, final, alive, files = result.split(",")
    assert state == "TERMINATING"
    assert int(callback_ms) < 1000
    assert (final, alive, files) == ("TIMEOUT", "False", "False")


def _provider_parent_identity_ps1(prefix: str, *, artifact: str = "PRODUCTION_CANDIDATE") -> str:
    return (
        "[pscustomobject]@{"
        f"git_sha=('{prefix}'*40);worker_version_id='11111111-1111-4111-8111-111111111111';"
        f"windows_revision=('{prefix}'*40);artifact_kind='{artifact}';branch='main';"
        f"worker_git_sha=('{prefix}'*40);validation_key='validation-{prefix}';"
        "provenance_state='EXACT'}"
    )


def _provider_parent_release_ps1(*, transaction: str = "$null") -> str:
    stable = _provider_parent_identity_ps1("a")
    previous = _provider_parent_identity_ps1("b")
    return (
        "[pscustomobject]@{schema_version='stable-candidate-release-v3';"
        f"stable={stable};previous_stable={previous};transaction={transaction};"
        "deployment_status='READY';release_runtime=[pscustomobject]@{"
        "drift_status='MATCHED';active_matches_committed=$true;active=[pscustomobject]@{"
        "worker_version_id='11111111-1111-4111-8111-111111111111';"
        "worker_git_sha=('a'*40);worker_traffic_percent=100;windows_revision=('a'*40);"
        "observation_status='AVAILABLE';identity_status='COMPLETE';health='HEALTHY';"
        "business_health_status='HEALTHY';ownership_status='SINGLE_OWNER'};"
        "previous=[pscustomobject]@{worker_artifact=[pscustomobject]@{status='AVAILABLE'};"
        "windows_artifact=[pscustomobject]@{status='AVAILABLE'};"
        "worker_is_current_traffic_member=$false;worker_traffic_membership_status='NOT_ASSIGNED';"
        "reverse_precheck=[pscustomobject]@{"
        "can_reverse=$true;reason='READY'}}}}"
    )


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_provider_parent_timeout_replaces_prior_success_and_repeated_timeouts_stay_closed(
    tmp_path, powershell: str,
) -> None:
    release = _provider_parent_release_ps1()
    result = _run_control_center_contract(
        tmp_path,
        f"$local={release};$provider={release};"
        "$success=New-ControlCenterProviderObservationEnvelope -State AVAILABLE "
        "-Release $provider -AttemptedAt ([DateTimeOffset]::UtcNow.AddSeconds(-1)) "
        "-ObservedAt ([DateTimeOffset]::UtcNow);"
        "$merged=Merge-ControlCenterProviderObservation -LocalRelease $local "
        "-ProviderObservation $success;"
        "$before=Get-ControlCenterReleasePresentation $merged;"
        "$timeout=New-ControlCenterProviderObservationEnvelope -State TIMEOUT "
        "-PriorObservation $success -AttemptedAt ([DateTimeOffset]::UtcNow) "
        "-ObservedAt ([DateTimeOffset]::UtcNow);"
        f"$closed=Merge-ControlCenterProviderObservation -LocalRelease ({release}) "
        "-ProviderObservation $timeout;"
        "$after=Get-ControlCenterReleasePresentation $closed;"
        "$summary=Get-ControlCenterSummaryPresentation ([pscustomobject]@{"
        "captured_at=[DateTimeOffset]::UtcNow.ToString('o');services=@();release=$closed});"
        "$again=New-ControlCenterProviderObservationEnvelope -State TIMEOUT "
        "-PriorObservation $timeout -AttemptedAt ([DateTimeOffset]::UtcNow) "
        "-ObservedAt ([DateTimeOffset]::UtcNow);"
        f"$closedAgain=Merge-ControlCenterProviderObservation -LocalRelease ({release}) "
        "-ProviderObservation $again;"
        "$last=Get-ControlCenterReleasePresentation $closedAgain;"
        'Write-Output "$($before.stable_state),$($before.can_reverse),'
        '$($after.stable_state),$($after.can_reverse),$($after.reverse_reason),'
        '$($summary.overall),$($last.stable_state),$($last.can_reverse)"',
        powershell=powershell,
    )
    assert result == (
        "STABLE,True,UNKNOWN,False,PROVIDER_OBSERVATION_TIMEOUT,DEGRADED,UNKNOWN,False"
    )


def test_pending_provider_snapshot_has_sixty_second_max_stale_age(tmp_path) -> None:
    release = _provider_parent_release_ps1()
    result = _run_control_center_contract(
        tmp_path,
        f"$local={release};$provider={release};$now=[DateTimeOffset]::UtcNow;"
        "$success=New-ControlCenterProviderObservationEnvelope -State AVAILABLE "
        "-Release $provider -AttemptedAt $now.AddSeconds(-1) -ObservedAt $now "
        ";"
        "$pending=New-ControlCenterProviderObservationEnvelope -State PENDING "
        "-PriorObservation $success -AttemptedAt $now.AddSeconds(1);"
        f"$fresh=Merge-ControlCenterProviderObservation -LocalRelease ({release}) "
        "-ProviderObservation $pending -Now $now.AddSeconds(59);"
        f"$stale=Merge-ControlCenterProviderObservation -LocalRelease ({release}) "
        "-ProviderObservation $pending -Now $now.AddSeconds(61);"
        "$freshView=Get-ControlCenterReleasePresentation $fresh;"
        "$staleView=Get-ControlCenterReleasePresentation $stale;"
        'Write-Output "$($freshView.stable_state),$($freshView.can_reverse),'
        '$($staleView.stable_state),$($staleView.can_reverse),$($staleView.reverse_reason)"',
    )
    assert result == "STABLE,True,UNKNOWN,False,PROVIDER_OBSERVATION_STALE"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$facts.health_observation.ownership_status='INVALID'",
         "DEGRADED,INVALID,HEALTHY,False,PRODUCTION_OWNERSHIP_INVALID"),
        ("$facts.health_observation.business_health_status='DEGRADED';"
         "$facts.health_observation.status='DEGRADED';"
         "$facts.health_observation.reason='LOCAL_API_OR_RUNTIME_HEALTH_FAILED'",
         "DEGRADED,SINGLE_OWNER,DEGRADED,True,READY"),
        ("$facts.active_windows_observation.revision=('c'*40)",
         "DRIFT,SINGLE_OWNER,HEALTHY,False,ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE"),
        ("$facts.control_bundle_status='UNAVAILABLE'",
         "STABLE,SINGLE_OWNER,HEALTHY,False,CONTROL_BUNDLE_UNAVAILABLE"),
        ("$facts.release_lock_active=$true",
         "STABLE,SINGLE_OWNER,HEALTHY,False,RELEASE_LOCK_ACTIVE"),
    ),
)
def test_cached_provider_facts_never_overwrite_fresh_local_facts(
    tmp_path, mutation: str, expected: str,
) -> None:
    release = _provider_parent_release_ps1()
    result = _run_control_center_contract(
        tmp_path,
        f"$provider={release};$local={release};$now=[DateTimeOffset]::UtcNow;"
        "$facts=Get-ControlCenterLocalFactsFromRelease $local;"
        f"{mutation};"
        "$local.release_runtime|Add-Member local_facts $facts -Force;"
        "$success=New-ControlCenterProviderObservationEnvelope -State AVAILABLE "
        "-Release $provider -AttemptedAt $now.AddSeconds(-1) -ObservedAt $now;"
        "$pending=New-ControlCenterProviderObservationEnvelope -State PENDING "
        "-PriorObservation $success -AttemptedAt $now;"
        "$merged=Merge-ControlCenterProviderObservation -LocalRelease $local "
        "-ProviderObservation $pending -Now $now.AddSeconds(5);"
        "$view=Get-ControlCenterReleasePresentation $merged;"
        'Write-Output "$($view.stable_state),$($view.active_ownership_status),'
        '$($view.active_business_health),$($view.can_reverse),$($view.reverse_reason)"',
    )
    assert result == expected


def test_provider_timeout_keeps_fresh_local_blocker_visible(tmp_path) -> None:
    release = _provider_parent_release_ps1()
    result = _run_control_center_contract(
        tmp_path,
        f"$provider={release};$local={release};$now=[DateTimeOffset]::UtcNow;"
        "$facts=Get-ControlCenterLocalFactsFromRelease $local;"
        "$facts.health_observation.ownership_status='INVALID';"
        "$local.release_runtime|Add-Member local_facts $facts -Force;"
        "$success=New-ControlCenterProviderObservationEnvelope -State AVAILABLE "
        "-Release $provider -AttemptedAt $now.AddSeconds(-1) -ObservedAt $now;"
        "$timeout=New-ControlCenterProviderObservationEnvelope -State TIMEOUT "
        "-PriorObservation $success -AttemptedAt $now -ObservedAt $now;"
        "$merged=Merge-ControlCenterProviderObservation -LocalRelease $local "
        "-ProviderObservation $timeout;"
        "$view=Get-ControlCenterReleasePresentation $merged;"
        'Write-Output "$($view.stable_state),$($view.active_ownership_status),'
        '$($view.active_business_health),$($view.can_reverse),$($view.reverse_reason)"',
    )
    assert result == "DEGRADED,INVALID,HEALTHY,False,PROVIDER_OBSERVATION_TIMEOUT"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$local.stable.artifact_kind='LEGACY_BOOTSTRAP_STABLE'", "False"),
        ("$local.stable.provenance_state='WRONG'", "False"),
        ("$local.stable.worker_git_sha=('c'*40)", "False"),
        ("$local.stable.validation_key='changed-stable'", "False"),
        ("$local.schema_version='unknown-v9'", "False"),
        ("$local.previous_stable.artifact_kind='UNKNOWN'", "False"),
        ("$local.previous_stable.validation_key='changed-previous'", "False"),
        ("$local.transaction=[pscustomobject]@{phase='SWITCHING'}", "False"),
        ("", "True"),
    ),
)
def test_provider_parent_requires_complete_authority_fingerprint(
    tmp_path, mutation: str, expected: str,
) -> None:
    release = _provider_parent_release_ps1()
    result = _run_control_center_contract(
        tmp_path,
        f"$local={release};$provider={release};"
        "$snapshot=New-ControlCenterProviderObservationEnvelope -State AVAILABLE "
        "-Release $provider -AttemptedAt ([DateTimeOffset]::UtcNow) "
        "-ObservedAt ([DateTimeOffset]::UtcNow);"
        f"{mutation};"
        "if($null -eq $local){$local=$provider};"
        "$merged=Merge-ControlCenterProviderObservation -LocalRelease $local "
        "-ProviderObservation $snapshot;"
        "$accepted=[bool]($merged.release_runtime.active.observation_status -eq 'AVAILABLE' -and "
        "$merged.release_runtime.active_matches_committed);"
        "Write-Output $accepted",
    )
    assert result == expected


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_verified_process_tree_termination_kills_root_and_descendant(
    tmp_path, powershell: str,
) -> None:
    child_pid_path = tmp_path / "descendant.pid"
    child_command = (
        f"$child=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        f"Set-Content -LiteralPath '{child_pid_path}' -Value $child.Id;"
        "Start-Sleep -Seconds 60"
    )
    result = _run_control_center_contract(
        tmp_path,
        "$root=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        f"'{child_command.replace("'", "''")}') -WindowStyle Hidden -PassThru;"
        f"$limit=[DateTimeOffset]::UtcNow.AddSeconds(5);while(-not(Test-Path '{child_pid_path}')"
        "-and [DateTimeOffset]::UtcNow -lt $limit){Start-Sleep -Milliseconds 50};"
        f"$descendant=[int](Get-Content '{child_pid_path}');"
        "$answer=Stop-NativeProcessTree -Process $root;"
        "$rootAlive=[bool](Get-Process -Id $root.Id -ErrorAction SilentlyContinue);"
        "$childAlive=[bool](Get-Process -Id $descendant -ErrorAction SilentlyContinue);"
        'Write-Output "$($answer.state),$rootAlive,$childAlive"',
        powershell=powershell,
    )
    assert result == "TERMINATED,False,False"


@pytest.mark.parametrize("termination_state", ("COMMAND_NONZERO", "COMMAND_TIMEOUT"))
def test_tree_termination_command_failure_uses_verified_fallback(
    tmp_path, termination_state: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-NativeTreeTerminationCommand{param($ProcessId,$TimeoutMilliseconds);"
        f"[pscustomobject]@{{state='{termination_state}';exit_code=1}}}};"
        "$root=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        "$answer=Stop-NativeProcessTree -Process $root;"
        "$alive=[bool](Get-Process -Id $root.Id -ErrorAction SilentlyContinue);"
        'Write-Output "$($answer.state),$alive"',
    )
    assert result == "TERMINATED,False"


def test_unresolved_termination_preserves_single_flight_and_temp_files(tmp_path) -> None:
    result_path = tmp_path / "provider.json"
    output_path = tmp_path / "provider.out"
    error_path = tmp_path / "provider.err"
    termination_result_path = tmp_path / "termination.json"
    result = _run_control_center_contract(
        tmp_path,
        "$process=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        "$termination=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command','exit 0') "
        "-WindowStyle Hidden -PassThru;$termination.WaitForExit();"
        f"$resultPath='{result_path}';$outputPath='{output_path}';$errorPath='{error_path}';"
        f"$terminationResultPath='{termination_result_path}';"
        "Set-Content $resultPath '{}';Set-Content $outputPath '';Set-Content $errorPath '';"
        "Set-Content $terminationResultPath '{\"state\":\"TERMINATION_FAILED\"}';"
        "$observation=[pscustomobject]@{process=$process;result_path=$resultPath;"
        "output_path=$outputPath;error_path=$errorPath;"
        "process_identity=(Get-NativeProcessIdentity $process);native_receipt_path=$null;"
        "termination_process=$termination;termination_result_path=$terminationResultPath;"
        "attempted_at=[DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o');"
        "deadline_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')};"
        "$answer=Complete-ControlCenterProviderObservationProcess $observation;"
        "$files=[bool]((Test-Path $resultPath)-and(Test-Path $outputPath)-and(Test-Path $errorPath));"
        "$alive=[bool](Get-Process -Id $process.Id -ErrorAction SilentlyContinue);"
        "$process.Kill();$process.WaitForExit();"
        "$cleared=Complete-ControlCenterProviderObservationProcess $observation;"
        "$remaining=[bool]((Test-Path $resultPath)-or(Test-Path $outputPath)-or"
        "(Test-Path $errorPath)-or(Test-Path $terminationResultPath));"
        'Write-Output "$($answer.state),$($answer.release_slot),$alive,$files,'
        '$($cleared.state),$($cleared.release_slot),$remaining"',
    )
    assert result == "TERMINATION_UNRESOLVED,PRESERVE,True,True,TIMEOUT,CLEAR,False"


def test_provider_root_exit_does_not_clear_live_nested_native_owner(tmp_path) -> None:
    child_pid_path = tmp_path / "nested.pid"
    receipt_path = tmp_path / "nested-receipt.json"
    result_path = tmp_path / "provider.json"
    output_path = tmp_path / "provider.out"
    error_path = tmp_path / "provider.err"
    child_command = (
        f"$child=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        f"Set-Content -LiteralPath '{child_pid_path}' -Value $child.Id;"
        "Start-Sleep -Seconds 1"
    )
    result = _run_control_center_contract(
        tmp_path,
        "$root=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        f"'{child_command.replace("'", "''")}') -WindowStyle Hidden -PassThru;"
        f"$limit=[DateTimeOffset]::UtcNow.AddSeconds(5);while(-not(Test-Path '{child_pid_path}')"
        "-and [DateTimeOffset]::UtcNow -lt $limit){Start-Sleep -Milliseconds 25};"
        f"$childPid=[int](Get-Content '{child_pid_path}');"
        f"$null=Write-NativeProcessOwnershipReceipt -Path '{receipt_path}' -RootProcess $root "
        "-DescendantIds @($childPid);$root.WaitForExit();"
        f"Set-Content '{result_path}' '{{}}';Set-Content '{output_path}' '';"
        f"Set-Content '{error_path}' '';"
        "$observation=[pscustomobject]@{process=$root;process_identity=(Get-NativeProcessIdentity $root);"
        f"result_path='{result_path}';output_path='{output_path}';error_path='{error_path}';"
        f"native_receipt_path='{receipt_path}';termination_process=$null;termination_result_path=$null;"
        "expected_control_revision=$null;attempted_at=[DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o');"
        "deadline_at=[DateTimeOffset]::UtcNow.AddMinutes(1).ToString('o')};"
        "$first=Complete-ControlCenterProviderObservationProcess $observation;"
        "$preserved=[bool]((Test-Path $observation.native_receipt_path)-and"
        "(Get-Process -Id $childPid -ErrorAction SilentlyContinue));"
        "$limit=[DateTimeOffset]::UtcNow.AddSeconds(15);do{Start-Sleep -Milliseconds 50;"
        "$last=Complete-ControlCenterProviderObservationProcess $observation}while("
        "$last.release_slot -ne 'CLEAR' -and [DateTimeOffset]::UtcNow -lt $limit);"
        "$childAlive=[bool](Get-Process -Id $childPid -ErrorAction SilentlyContinue);"
        'Write-Output "$($first.state),$($first.release_slot),$preserved,'
        '$($last.state),$($last.release_slot),$childAlive"',
    )
    assert result == "TERMINATING,PRESERVE,True,UNKNOWN,CLEAR,False"


def test_ui_poll_remains_nonblocking_while_termination_worker_blocks(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$root=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru;"
        "$worker=Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command',"
        "'Start-Sleep -Seconds 5') -WindowStyle Hidden -PassThru;"
        "$observation=[pscustomobject]@{process=$root;process_identity=(Get-NativeProcessIdentity $root);"
        "result_path=$null;output_path=$null;error_path=$null;native_receipt_path=$null;"
        "termination_process=$worker;termination_result_path=$null;expected_control_revision=$null;"
        "attempted_at=[DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o');"
        "deadline_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')};"
        "$workerId=$worker.Id;$localRefreshCount=0;$watch=[Diagnostics.Stopwatch]::StartNew();"
        "1..5|ForEach-Object{$localRefreshCount++;"
        "$answer=Complete-ControlCenterProviderObservationProcess $observation};$watch.Stop();"
        "$sameWorker=[bool]($observation.termination_process.Id -eq $workerId);"
        "$root.Kill();$worker.Kill();$root.WaitForExit();$worker.WaitForExit();"
        "$root.Dispose();$worker.Dispose();"
        'Write-Output "$($answer.state),$($answer.release_slot),$localRefreshCount,'
        '$sameWorker,$([int]$watch.Elapsed.TotalMilliseconds)"',
    )
    state, slot, count, same_worker, elapsed_ms = result.split(",")
    assert (state, slot, count, same_worker) == ("TERMINATING", "PRESERVE", "5", "True")
    assert int(elapsed_ms) < 1000


def test_completed_provider_read_model_with_unknown_active_observation_is_not_available(
    tmp_path,
) -> None:
    release = _provider_parent_release_ps1().replace(
        "observation_status='AVAILABLE'", "observation_status='UNKNOWN'",
    )
    result = _run_control_center_contract(
        tmp_path,
        f"$release={release};$snapshot=New-ControlCenterProviderObservationEnvelope "
        "-State AVAILABLE -Release $release "
        "-AttemptedAt ([DateTimeOffset]::UtcNow.AddSeconds(-1)) "
        "-ObservedAt ([DateTimeOffset]::UtcNow);"
        "$merged=Merge-ControlCenterProviderObservation -LocalRelease $release "
        "-ProviderObservation $snapshot;"
        "$view=Get-ControlCenterReleasePresentation $merged;"
        'Write-Output "$($snapshot.state),$($view.stable_state),'
        '$($view.can_reverse),$($view.reverse_reason)"',
    )
    assert result == "UNKNOWN,UNKNOWN,False,PROVIDER_OBSERVATION_UNKNOWN"


def test_wpf_and_winforms_share_provider_snapshot_and_verified_close_contract() -> None:
    source = _control_center_source()
    wpf = source.split("function Show-WpfControlCenter", 1)[1].split(
        "function Show-ControlCenter", 1,
    )[0]
    winforms = source.split("function Show-ControlCenter", 1)[1]

    assert "wpfLastProviderRelease" not in source
    assert "lastGuiRelease" not in source
    for gui in (wpf, winforms):
        assert "New-ControlCenterProviderObservationEnvelope" in gui
        assert "Merge-ControlCenterProviderObservation" in gui
        assert 'release_slot -eq "CLEAR"' in gui
    assert "if ($script:wpfProviderObservation) { return }" in wpf
    assert "if ($script:winFormsProviderObservation) { return }" in winforms
    wpf_close = wpf.split("Add_Closing", 1)[1].split("Add_Closed", 1)[0]
    winforms_close = winforms.split("Add_FormClosing", 1)[1].split("ShowDialog", 1)[0]
    for close_handler in (wpf_close, winforms_close):
        assert "Complete-ControlCenterProviderObservationProcess" in close_handler
        assert "Stop-NativeProcessTree" not in close_handler
        assert "release_slot" in close_handler


def test_provider_parent_contract_tracks_full_authority_and_completion_times() -> None:
    source = _control_center_source()
    fingerprint = source.split(
        "function Get-ControlCenterProviderAuthorityFingerprint", 1,
    )[1].split("function New-ControlCenterProviderObservationEnvelope", 1)[0]
    envelope = source.split(
        "function New-ControlCenterProviderObservationEnvelope", 1,
    )[1].split("function Start-ControlCenterProviderObservationProcess", 1)[0]

    for field in (
        "schema_version", "git_sha", "worker_version_id", "windows_revision",
        "artifact_kind", "branch", "worker_git_sha", "validation_key", "provenance_state",
        "transaction_active",
    ):
        assert field in fingerprint
    for field in (
        "state", "provider_facts", "attempted_at", "observed_at", "last_success_at",
        "authority_fingerprint",
    ):
        assert field in envelope
    assert '$State -ne "PENDING"' in envelope


def test_provider_envelope_owns_only_provider_facts_and_shared_composer() -> None:
    source = _control_center_source()
    envelope_body = source.split(
        "function New-ControlCenterProviderObservationEnvelope", 1,
    )[1].split("function Start-ControlCenterProviderObservationProcess", 1)[0]
    returned = envelope_body.split("return [pscustomobject]@{", 1)[1]
    start_body = source.split(
        "function Start-ControlCenterProviderObservationProcess", 1,
    )[1].split("function Invoke-ControlCenterProviderTermination", 1)[0]
    merge_body = source.split(
        "function Merge-ControlCenterProviderObservation", 1,
    )[1].split("function Import-WpfControlCenterWindow", 1)[0]

    assert "provider_facts = $lastFacts" in returned
    assert "release =" not in returned
    assert '"ReleaseProviderFactsJson"' in start_body
    assert '"ReleaseStatusJson"' not in start_body
    assert "Join-ReleaseRuntimeFacts" in merge_body
    assert "providerRelease.release_runtime" not in merge_body


def test_gui_timeout_completion_is_polled_without_ui_thread_tree_termination() -> None:
    source = _control_center_source()
    wpf = source.split("function Show-WpfControlCenter", 1)[1].split(
        "function Show-ControlCenter", 1,
    )[0]
    winforms = source.split("function Show-ControlCenter", 1)[1]
    completion = source.split(
        "function Complete-ControlCenterProviderObservationProcess", 1,
    )[1].split("function Set-ControlCenterProviderUnknownReadModel", 1)[0]

    assert "Start-ControlCenterProviderTerminationProcess" in completion
    for gui in (wpf, winforms):
        assert "Complete-ControlCenterProviderObservationProcess" in gui
        assert "Stop-NativeProcessTree" not in gui
        assert "release_slot" in gui


def test_reverse_action_bypasses_provider_cache_after_lock() -> None:
    source = _control_center_source()
    body = source.split("function Invoke-ReverseStable", 1)[1].split(
        "function Reconcile-ReleaseControlState", 1,
    )[0]
    assert body.index("Enter-ReleaseTransactionLock") < body.index("-ForceProviderRefresh")
    assert "$target = $runtimeReadModel.previous_committed" in body
    assert "$target = $state.previous_stable" not in body


def test_fresh_action_observation_never_falls_back_to_inflight_gui_cache(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:releaseProviderRefreshInFlight=$true;"
        "$script:releaseDeploymentObservationCache=[pscustomobject]@{status='AVAILABLE';"
        "value=[pscustomobject]@{versions=@()};observed_at=[DateTimeOffset]::UtcNow.ToString('o');"
        "attempted_at=[DateTimeOffset]::UtcNow.ToString('o')};"
        "$state=[pscustomobject]@{schema_version='stable-candidate-release-v3';"
        "stable=$null;previous_stable=$null;candidate=$null;transaction=$null};"
        "$model=Get-CurrentReleaseRuntimeReadModel -PersistedState $state -ForceProviderRefresh;"
        'Write-Output "$($model.active.observation_status),$($model.previous.reverse_precheck.reason)"',
    )
    assert result == "UNKNOWN,COMMITTED_IDENTITY_INVALID"


def test_all_operator_and_json_consumers_preserve_membership_enum() -> None:
    source = _control_center_source()
    assert source.count("previous_traffic_membership_status") >= 3
    assert '$releaseView.previous_traffic_membership_status.Replace' in source
    status_json = source.split('"StatusJson" {', 1)[1].split('"ReleaseStatusJson" {', 1)[0]
    release_json = source.split('"ReleaseStatusJson" {', 1)[1].split('"CodeRevision" {', 1)[0]
    assert "Get-ReleaseControlStatusSnapshot" in status_json
    assert "Get-ReleaseControlStatusSnapshot" in release_json
    wpf = source.split("function Show-WpfControlCenter", 1)[1].split(
        "function Show-ControlCenter", 1,
    )[0]
    winforms = source.split("function Show-ControlCenter", 1)[1]
    assert "Get-ControlCenterReleasePresentation" in wpf
    assert "Get-ControlCenterReleasePresentation" in winforms


@pytest.mark.parametrize(
    ("business", "ownership", "overall", "expected"),
    (
        ("HEALTHY", "SINGLE_OWNER", "HEALTHY", "STABLE"),
        ("DEGRADED", "SINGLE_OWNER", "DEGRADED", "DEGRADED"),
        ("HEALTHY", "INVALID", "DEGRADED", "DEGRADED"),
        ("HEALTHY", "UNKNOWN", "UNKNOWN", "UNKNOWN"),
    ),
)
def test_shared_presenter_requires_health_and_single_ownership_for_stable(
    tmp_path, business: str, ownership: str, overall: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{git_sha=('a'*40)};"
        "$runtime=[pscustomobject]@{drift_status='MATCHED';active_matches_committed=$true;"
        f"active=[pscustomobject]@{{observation_status='AVAILABLE';identity_status='COMPLETE';"
        f"health='{overall}';business_health_status='{business}';ownership_status='{ownership}'}};"
        "previous=[pscustomobject]@{reverse_precheck=[pscustomobject]@{can_reverse=$true;reason='READY'}}};"
        "$release=[pscustomobject]@{stable=$stable;candidate=$null;transaction=$null;"
        "deployment_status='READY';release_runtime=$runtime};"
        "$view=Get-ControlCenterReleasePresentation -Release $release;"
        'Write-Output "$($view.stable_state),$($view.active_business_health),$($view.active_ownership_status)"',
    )
    assert result == f"{expected},{business},{ownership}"


@pytest.mark.parametrize(
    ("owner_scenario", "business_ok", "owner_ok", "business", "ownership", "overall", "reverse"),
    (
        ("single", True, True, "HEALTHY", "SINGLE_OWNER", "HEALTHY", "READY"),
        ("duplicate", True, False, "HEALTHY", "INVALID", "DEGRADED", "PRODUCTION_OWNERSHIP_INVALID"),
        ("missing", True, False, "HEALTHY", "INVALID", "DEGRADED", "PRODUCTION_OWNERSHIP_INVALID"),
        ("single", False, True, "DEGRADED", "SINGLE_OWNER", "DEGRADED", "READY"),
    ),
)
def test_real_active_health_composition_reads_business_and_owner_once(
    tmp_path, owner_scenario: str, business_ok: bool, owner_ok: bool, business: str,
    ownership: str, overall: str, reverse: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:businessReads=0;$script:ownerReads=0;"
        f"function Test-CurrentBusinessRuntimeHealth{{$script:businessReads++;${str(business_ok).lower()}}};"
        f"function Test-SingleProductionOwner{{$script:ownerReads++;${str(owner_ok).lower()}}};"
        "$health=Get-ReleaseActiveHealthObservation;"
        "$artifact=[pscustomobject]@{status='AVAILABLE';reason='OK'};"
        "$pre=New-ReleaseReversePrecheck -PreviousIdentity ([pscustomobject]@{id='p'}) "
        "-CommittedIdentityStatus COMPLETE -PreviousIdentityStatus COMPLETE "
        "-WorkerArtifact $artifact -WindowsArtifact $artifact -ControlBundleStatus AVAILABLE "
        "-OwnershipStatus $health.ownership_status -ActiveObservationStatus AVAILABLE "
        "-ActiveIdentityStatus COMPLETE -ActiveMatchesCommitted $true;"
        'Write-Output "$($health.business_health_status),$($health.ownership_status),'
        '$($health.status),$($pre.reason),$script:businessReads,$script:ownerReads"',
    )
    assert result == f"{business},{ownership},{overall},{reverse},1,1"


def test_business_health_helper_has_no_ownership_authority() -> None:
    source = _control_center_source()
    business = source.split("function Test-CurrentBusinessRuntimeHealth", 1)[1].split(
        "function Test-CurrentStableRuntimeHealth", 1,
    )[0]
    combined = source.split("function Test-CurrentStableRuntimeHealth", 1)[1].split(
        "function Invoke-PromotionFreshnessCoordinator", 1,
    )[0]
    assert "Test-SingleProductionOwner" not in business
    assert "Test-CurrentBusinessRuntimeHealth" in combined
    assert "Test-SingleProductionOwner" in combined


@pytest.mark.parametrize(
    ("stable_state", "overall"),
    (("STABLE", "HEALTHY"), ("DEGRADED", "DEGRADED"),
     ("UNKNOWN", "DEGRADED"), ("DRIFT", "FAILED")),
)
def test_global_summary_never_hides_release_stable_state(
    tmp_path, stable_state: str, overall: str,
) -> None:
    runtime = {
        "STABLE": ("MATCHED", "HEALTHY", "HEALTHY", "SINGLE_OWNER", True),
        "DEGRADED": ("MATCHED", "DEGRADED", "DEGRADED", "SINGLE_OWNER", True),
        "UNKNOWN": ("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", False),
        "DRIFT": ("DRIFT", "HEALTHY", "HEALTHY", "SINGLE_OWNER", False),
    }[stable_state]
    drift, health, business, ownership, matches = runtime
    result = _run_control_center_contract(
        tmp_path,
        "$stable=[pscustomobject]@{git_sha=('a'*40)};"
        f"$runtime=[pscustomobject]@{{drift_status='{drift}';active_matches_committed=${str(matches).lower()};"
        f"active=[pscustomobject]@{{observation_status='AVAILABLE';identity_status='COMPLETE';"
        f"health='{health}';business_health_status='{business}';ownership_status='{ownership}'}};"
        "previous=[pscustomobject]@{reverse_precheck=[pscustomobject]@{can_reverse=$false;reason='BLOCKED'}}};"
        "$release=[pscustomobject]@{stable=$stable;candidate=$null;transaction=$null;"
        "deployment_status='READY';release_runtime=$runtime};"
        "$snapshot=[pscustomobject]@{captured_at=[DateTimeOffset]::UtcNow.ToString('o');"
        "services=@([pscustomobject]@{State='RUNNING'});release=$release};"
        "$summary=Get-ControlCenterSummaryPresentation $snapshot;"
        "$view=Get-ControlCenterReleasePresentation $release;"
        'Write-Output "$($view.stable_state),$($summary.overall)"',
    )
    assert result == f"{stable_state},{overall}"


def test_wpf_and_winforms_consume_the_shared_global_summary() -> None:
    source = _control_center_source()
    wpf = source.split("function Show-WpfControlCenter", 1)[1].split(
        "function Show-ControlCenter", 1,
    )[0]
    winforms = source.split("function Show-ControlCenter", 1)[1]
    assert "Get-ControlCenterSummaryPresentation" in wpf
    assert "Get-ControlCenterSummaryPresentation" in winforms
    assert '$bad = @($status' not in wpf


def test_rollback_artifact_availability_does_not_require_deployment_membership(tmp_path) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    candidate = "22222222-2222-4222-8222-222222222222"
    version_json = json.dumps({
        "id": stable,
        "number": 979,
        "metadata": {"source": "wrangler"},
        "annotations": {"workers/message": (
            f"release:{'a' * 40} branch:main artifact_kind:PRODUCTION_CANDIDATE"
        )},
        "resources": {"script": {"handlers": ["fetch"]}},
    })
    deployment_json = json.dumps({
        "versions": [{"version_id": candidate, "percentage": 100}],
    })
    result = _run_control_center_contract(
        tmp_path,
        f"function Get-CloudflareVersionDetails{{param($VersionId);"
        f"'{version_json}'|ConvertFrom-Json}};"
        f"function Get-CloudflareDeployment{{'{deployment_json}'|ConvertFrom-Json}};"
        f"$target=[pscustomobject]@{{worker_version_id='{stable}';git_sha=('a'*40);"
        "worker_git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';branch='main'};"
        "Write-Output (Test-CloudflareRollbackTarget -Target $target)",
    )

    assert result == "True"


def test_rollback_artifact_lookup_does_not_consume_deployment_transport(tmp_path) -> None:
    stable = "11111111-1111-4111-8111-111111111111"
    result = _run_control_center_contract(
        tmp_path,
        f"function Get-CloudflareVersionDetails{{param($VersionId);[pscustomobject]@{{"
        f"id='{stable}';number=979;metadata=[pscustomobject]@{{source='wrangler'}};"
        "annotations=[pscustomobject]@{'workers/message'=('release:'+('a'*40)+' branch:main artifact_kind:PRODUCTION_CANDIDATE')};"
        "resources=[pscustomobject]@{script=[pscustomobject]@{handlers=@('fetch')}}}};"
        "function Get-CloudflareDeployment{throw 'PROVIDER_TRANSPORT_FAILED'};"
        f"$target=[pscustomobject]@{{worker_version_id='{stable}';git_sha=('a'*40);"
        "worker_git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';branch='main'};"
        "Write-Output (Test-CloudflareRollbackTarget -Target $target)",
    )

    assert result == "True"


@pytest.mark.parametrize(
    "powershell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_windows_legacy_adapter_consumes_exact_resolved_pair(
    tmp_path, powershell: str,
) -> None:
    revision = "783d25314b090dd7fbbf124777c3b8de517d2b85"
    worker = "76d314fc-e484-4f50-8ace-3689e0896709"
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);"
        "if('show' -in $Arguments){[pscustomobject]@{exit_code=1;stdout=''}}"
        "else{[pscustomobject]@{exit_code=0;stdout=''}}};"
        f"$exact=[pscustomobject]@{{git_sha='{revision}';worker_git_sha='NOT_RECORDED';"
        f"worker_version_id='{worker}';windows_revision='{revision}';"
        "artifact_kind='LEGACY_BOOTSTRAP_STABLE';branch='main';"
        "provenance_state='LEGACY_EXACT_WORKER_WINDOWS_PAIR'};"
        "$wrong=$exact|Select-Object *;$wrong.provenance_state='ARBITRARY';"
        "$exactResult=Get-ReleaseWindowsArtifactObservation -IdentityResolution "
        "(Resolve-ReleaseRuntimeIdentity $exact);"
        "$wrongResult=Get-ReleaseWindowsArtifactObservation -IdentityResolution "
        "(Resolve-ReleaseRuntimeIdentity $wrong);"
        'Write-Output "$($exactResult.status),$($wrongResult.status)"',
        powershell=powershell,
    )
    assert result == "AVAILABLE,MISMATCH"


def test_bootstrap_watermark_uses_newest_valid_timestamp_then_version_id(tmp_path) -> None:
    stable = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CloudflareDeployment { [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='stable-worker';percentage=100})} };"
        "function Get-CloudflareVersions { @("
        "[pscustomobject]@{id='version-a';metadata=[pscustomobject]@{created_on=@('bad','2026-08-20T12:00:00Z')}},"
        "[pscustomobject]@{id='version-b';metadata=[pscustomobject]@{created_on=@(@('2026-08-20T12:00:00Z'))}},"
        "[pscustomobject]@{id='version-z';metadata=[pscustomobject]@{created_on='bad'}}) } ;"
        f"function Get-RuntimeCodeState {{ [pscustomobject]@{{applied_revision='{stable}'}} }};"
        "$state=Initialize-ReleaseControl;"
        'Write-Output "$($state.candidate_discovery.watermark_version_id),$($state.candidate_discovery.watermark_created_at)"',
    )
    assert result == "version-b,2026-08-20T12:00:00.0000000+00:00"


def test_candidate_discovery_accepts_object_array_timestamp_without_crashing(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "c" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate=$null;"
        "$state.candidate_discovery.initialized_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_created_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_version_id='old';Write-ReleaseControlState $state;"
        f"function Get-OriginMainRevision {{ '{candidate}' }};"
        "function Get-CloudflareVersions { @([pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "metadata=[pscustomobject]@{created_on=@('bad','2026-08-20T12:00:00Z');has_preview=$true};"
        f"annotations=[pscustomobject]@{{'workers/message'='release:{candidate} branch:main artifact_kind:PRODUCTION_CANDIDATE'}}}}) }};"
        "$found=Find-NewCandidateRelease;Write-Output $found.git_sha",
    )
    assert result == candidate


def test_candidate_discovery_ignores_late_older_main_build(tmp_path) -> None:
    current = "d" * 40
    older = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "c" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate=$null;"
        "$state.candidate_discovery.initialized_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_created_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_version_id='old';Write-ReleaseControlState $state;"
        f"function Get-OriginMainRevision {{ '{current}' }};"
        "function Get-CloudflareVersions { @([pscustomobject]@{id='late-old';"
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:00:00Z';has_preview=$true};"
        f"annotations=[pscustomobject]@{{'workers/message'='release:{older} branch:main "
        "artifact_kind:PRODUCTION_CANDIDATE'}}) };"
        "$found=Find-NewCandidateRelease;$saved=Get-ReleaseControlState;"
        'Write-Output "$($null -eq $found),$($saved.candidate_materialization.state),'
        '$($saved.candidate_materialization.revision),$($saved.candidate_discovery.watermark_version_id)"',
    )
    assert result == f"True,PENDING,{current},late-old"


def test_pending_exact_main_materializes_on_later_discovery_poll(tmp_path) -> None:
    current = "d" * 40
    older = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "c" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate=$null;"
        "$state.candidate_discovery.initialized_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_created_at='2026-08-20T10:00:00Z';"
        "$state.candidate_discovery.watermark_version_id='old';Write-ReleaseControlState $state;"
        f"function Get-OriginMainRevision {{ '{current}' }};"
        "$script:buildReady=$false;function Get-CloudflareVersions {"
        "$versions=@([pscustomobject]@{id='late-old';metadata=[pscustomobject]@{"
        "created_on='2026-08-20T12:00:00Z';has_preview=$true};"
        f"annotations=[pscustomobject]@{{'workers/message'='release:{older} branch:main "
        "artifact_kind:PRODUCTION_CANDIDATE'}});"
        "if($script:buildReady){$versions+= [pscustomobject]@{id='exact-current';"
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:05:00Z';has_preview=$true};"
        f"annotations=[pscustomobject]@{{'workers/message'='release:{current} branch:main "
        "artifact_kind:PRODUCTION_CANDIDATE'}}};return $versions};"
        "$first=Find-NewCandidateRelease;$pending=Get-ReleaseControlState;"
        "$script:buildReady=$true;$second=Find-NewCandidateRelease;"
        "$saved=Get-ReleaseControlState;"
        'Write-Output "$($null -eq $first),$($pending.candidate_materialization.state),'
        '$($second.git_sha),$($saved.candidate_materialization.state),'
        '$($saved.candidate_materialization.worker_version_id)"',
    )
    assert result == f"True,PENDING,{current},MATERIALIZED,exact-current"


def test_candidate_placement_propagation_timeout_is_retryable(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, candidate)
        + "$candidatePlacementPropagationTimeout=[TimeSpan]::Zero;"
        "function Invoke-ExactVersionJson { throw '404 before placement propagation' };"
        "$result=Wait-CandidatePlacementPropagation -Candidate $candidateRelease;"
        'Write-Output "$($result.passed),$($result.state),$($result.reason)"',
    )
    assert result == (
        "False,RETRYABLE,CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
    )


def test_abandoned_release_lock_is_recovered_without_touching_state(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    lock = runtime / ".local" / "forward" / "release-control.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(json.dumps({
        "owner_pid": 2147483647,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        "$entered=Enter-ReleaseTransactionLock; $history=Get-Content -LiteralPath $releaseHistoryPath -Raw; "
        'Write-Output "$entered,$($history.Contains(\'ABANDONED_LOCK_RECOVERED\'))"; '
        "Exit-ReleaseTransactionLock",
    )

    assert result == "True,True"


def test_live_release_lock_is_never_stolen(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null; "
        "[pscustomobject]@{owner_pid=$PID;acquired_at=[DateTimeOffset]::UtcNow.ToString('o')} "
        "| ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json'); "
        "Write-Output (Enter-ReleaseTransactionLock)",
    )

    assert result == "False"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_passed_candidate_promotes_only_after_observation_commit(
    tmp_path, powershell: str,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    reload_boundary = "2026-08-31T18:29:12.0306041+00:00"
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;"
        "$state.candidate.validation|Add-Member -Force -NotePropertyName data_parity -NotePropertyValue ([pscustomobject]@{"
        "deferred_obligations=@([pscustomobject]@{route='/api/audit-stories';"
        "state='DEFERRED_TO_POST_CUTOVER_OBSERVATION';"
        "validation_key=$state.candidate.validation_key;"
        "required_producer_revision=$state.candidate.windows_revision})});"
        "Write-ReleaseControlState $state;"
        + "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "function Assert-ActiveControlBundle { return [pscustomobject]@{exact_revision=$true} }; "
        "function Test-ProductionCandidateProvenance { return $true }; "
        "function Test-CloudflareRollbackTarget { return $true }; "
        "function Test-CloudflareReleasePlacement { return $true }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }}; "
        "function Test-SingleProductionOwner { return $true }; "
        "function Invoke-PromotionFreshnessCoordinator { return [pscustomobject]@{state='PASSED'} }; "
        "$script:dependencyDigests=[pscustomobject]@{};"
        "$releaseEvidencePromotionDependencyNodes|ForEach-Object{"
        "$script:dependencyDigests|Add-Member -Force -NotePropertyName $_ -NotePropertyValue ('d'*64)};"
        "function Assert-ReleaseEvidenceQualification{"
        "$s=Get-ReleaseControlState;"
        "$subject=[pscustomobject]@{deferred_obligations=$s.candidate.validation.data_parity.deferred_obligations};"
        "$source=[pscustomobject]@{subject=$subject};"
        "$semantic=[pscustomobject]@{source_identity=$source};"
        "return [pscustomobject]@{state='PASSED';receipt_digests=$script:dependencyDigests;"
        "receipts=[pscustomobject]@{semantic_contract=$semantic}}};"
        "function Publish-PromoteAttemptEvidence{return [pscustomobject]@{receipt_digest=('p'*64)}};"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'promote_attempt'){$s=Get-ReleaseControlState;return [pscustomobject]@{"
        "receipt_digest=('p'*64);source_identity=[pscustomobject]@{subject=[pscustomobject]@{"
        "transaction_id=$s.transaction.id}}}}};"
        "function Publish-ObserveAttemptEvidence{return [pscustomobject]@{receipt_digest=('o'*64)}};"
        f"function New-RuntimeRecoveryPlan {{ return [pscustomobject]@{{body="
        f"[pscustomobject]@{{stable_revision='{previous}'}};digest=('0' * 64)}} }}; "
        "function Update-RuntimeCheckout { return $true }; "
        "$script:cutover=@(); "
        f"$script:reloadBoundary=[DateTimeOffset]::Parse('{reload_boundary}'); "
        "function Restart-CodeReloadableServices { $script:cutover += 'windows-with-sync-paused'; return $script:reloadBoundary }; "
        "function Write-DeferredProjectionSyncRequest { param($Transaction,$RequiredAfter);"
        "$script:cutover += 'projection-request';"
        "$script:requestBoundary=$RequiredAfter;return [pscustomobject]@{state='PERSISTED'} }; "
        "function Complete-DeferredServiceReload { $script:cutover += 'sync-resumed' }; "
        "function Start-RuntimeObservation { param($Revision,$PreviousRevision,$HealthBoundary,"
        "$DeferredProjectionObligations,$ValidationKey,$ProjectionBoundary);"
        "$script:projectionBoundary=$ProjectionBoundary;"
        "Write-RuntimeUpdateState @{update_status='ACTIVE';"
        "observation_validation_key=$ValidationKey;"
        "observation_deferred_projection_state='PASSED'} }; "
        "function Write-RuntimeCodeState {}; "
        "function Write-WatchdogEvent {}; "
        "function Invoke-CloudflareDeployment { $script:cutover += 'worker' }; "
        "$started=Start-ReleasePromotion; $during=Get-ReleaseControlState; "
        "Complete-ReleasePromotion; $after=Get-ReleaseControlState; "
        'Write-Output "$started,$($during.stable.git_sha),$($during.transaction.phase),$($after.stable.git_sha),'
        '$($script:cutover -join \';\'),$($script:projectionBoundary.ToUniversalTime().ToString(\'o\')),'
        '$($script:requestBoundary.ToUniversalTime().ToString(\'o\'))"',
        powershell=powershell,
    )

    assert result == (
        f"True,{previous},OBSERVING,{candidate},"
        f"windows-with-sync-paused;worker;projection-request;sync-resumed,"
        f"{reload_boundary},{reload_boundary}"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_promotion_freshness_coordinator_renews_only_live_leases(
    tmp_path, powershell: str,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation|Add-Member -Force auth_inspection ([pscustomobject]@{"
        "state='ACCESS_QUALIFICATION_REUSED'});"
        "$candidate|Add-Member -Force access_qualification ([pscustomobject]@{"
        "state='ACCESS_QUALIFICATION_REUSED';receipt_digest=('d'*64)});"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest=('a'*64)});"
        "$candidate|Add-Member -Force migration_qualification ([pscustomobject]@{"
        "state='MIGRATION_QUALIFICATION_RENEWED';receipt_digest=('b'*64)});"
        "Write-ReleaseControlState $state;$script:migrationCalls=0;$script:accessCalls=0;"
        "function Get-CandidateChangedFiles{return @('web/drizzle/0030_news_evidence_cleanup_budget.sql')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=@('web/drizzle/0030_news_evidence_cleanup_budget.sql')}};"
        "function Ensure-CoordinatedMigrationQualification{param($Candidate,$Stable,$MigrationFiles,$MinimumRemaining);"
        "$script:migrationCalls++;$Candidate.migration_qualification.receipt_digest=('c'*64);"
        "return [pscustomobject]@{state='MIGRATION_QUALIFICATION_RENEWED';"
        "root_receipt_digest=('a'*64);receipt=[pscustomobject]@{receipt_digest=('c'*64)}}};"
        "function Ensure-AccessQualificationMachineReceipt{param($Candidate,$MinimumRemaining);"
        "$script:accessCalls++;$Candidate.access_qualification.receipt_digest=('e'*64);"
        "return [pscustomobject]@{state='ACCESS_QUALIFICATION_RENEWED';receipt_digest=('e'*64)}};"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'human_access_root'){return [pscustomobject]@{receipt_digest=('d'*64);"
        "source_identity=[pscustomobject]@{qualification_state='PASSED';"
        "subject=[pscustomobject]@{root_receipt_digest=('d'*64)}}}}};"
        "function Test-CloudflareReleasePlacement{return $true};"
        "function Test-CloudflareRollbackTarget{return $true};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{previous}'}}}};"
        "function Test-CurrentStableRuntimeHealth{return $true};"
        "function Publish-PromotionFreshnessEvidence{param($State);"
        "$null=Ensure-AccessQualificationMachineReceipt -Candidate $State.candidate "
        "-MinimumRemaining ([TimeSpan]::FromMinutes(30));"
        "return [pscustomobject]@{state='PASSED'}};"
        "function Invoke-AutomaticCandidateValidation{throw 'broad validation must not run'};"
        "$summary=Invoke-PromotionFreshnessCoordinator $state;$saved=Get-ReleaseControlState;"
        "$m=$summary.steps|Where-Object name -eq 'migration_live_lease';"
        "$a=$summary.steps|Where-Object name -eq 'access_provider_lease';"
        'Write-Output "$($summary.state),$($summary.minimum_remaining_seconds),'
        '$($m.execution_mode),$($a.execution_mode),$script:migrationCalls,$script:accessCalls,'
        '$($null -eq $saved.transaction),$($saved.candidate.validation_state)"',
        powershell=powershell,
    )
    assert result == "PASSED,1800,RENEWED,FRESH,1,1,True,PASSED"


@pytest.mark.parametrize(
    ("failed_check", "expected"),
    (
        ("placement", "Cloudflare Stable/Candidate placement drifted."),
        ("rollback", "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"),
        ("health", "CURRENT_STABLE_RUNTIME_UNHEALTHY"),
    ),
)
def test_promotion_freshness_failure_never_starts_transaction(
    tmp_path, failed_check: str, expected: str,
) -> None:
    previous = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, "b" * 40)
        + f"$script:failed='{failed_check}';"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='PASSED';files=@()}};"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'human_access_root'){return [pscustomobject]@{receipt_digest='NOT_REQUIRED';"
        "source_identity=[pscustomobject]@{qualification_state='NOT_REQUIRED'}}}};"
        "function Test-CloudflareReleasePlacement{return $script:failed -ne 'placement'};"
        "function Test-CloudflareRollbackTarget{return $script:failed -ne 'rollback'};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{previous}'}}}};"
        "function Test-CurrentStableRuntimeHealth{return $script:failed -ne 'health'};"
        "$state=Get-ReleaseControlState;$reason='';"
        "try{Invoke-PromotionFreshnessCoordinator $state|Out-Null}catch{$reason=$_.Exception.Message};"
        "$saved=Get-ReleaseControlState;"
        '$failedStep=$saved.candidate.promotion_freshness.steps|Select-Object -Last 1;'
        'Write-Output "$reason,$($null -eq $saved.transaction),$($saved.deployment_status),'
        '$($saved.candidate.promotion_freshness.state),$($failedStep.name),$($failedStep.state)"',
    )
    expected_step = {
        "placement": "candidate_placement",
        "rollback": "rollback_precheck",
        "health": "current_owner_health",
    }[failed_check]
    assert result == f"{expected},True,READY,FAILED,{expected_step},FAILED"


def test_deferred_projection_obligation_blocks_stable_commit(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;"
        "$obligation=[pscustomobject]@{route='/api/audit-stories';"
        "state='DEFERRED_TO_POST_CUTOVER_OBSERVATION';"
        "validation_key=$state.candidate.validation_key;"
        "required_producer_revision=$state.candidate.windows_revision};"
        "$targetIdentity=[pscustomobject]@{validation_key=$state.candidate.validation_key;"
        "worker_version_id=$state.candidate.worker_version_id;git_sha=$state.candidate.git_sha;"
        "windows_revision=$state.candidate.windows_revision};"
        "$state.transaction=[pscustomobject]@{id='tx';type='PROMOTE';phase='OBSERVING';"
        "target=$state.candidate;previous=$state.stable;"
        "deferred_projection_obligations=@($obligation);evidence_authority=[pscustomobject]@{"
        "validation_key=$state.candidate.validation_key;target_identity=$targetIdentity;"
        "promote_receipt_digest=('p'*64)}};Write-ReleaseControlState $state;"
        "Write-RuntimeUpdateState @{update_status='ACTIVE';"
        "observation_validation_key=$state.candidate.validation_key;"
        "observation_deferred_projection_state='PENDING'};"
        "function Enter-ReleaseTransactionLock{return $true};"
        "function Exit-ReleaseTransactionLock{};function Test-CloudflareRollbackTarget{return $true};"
        "function Get-ReleaseEvidenceCurrentReceipt{return [pscustomobject]@{receipt_digest=('p'*64);"
        "source_identity=[pscustomobject]@{subject=[pscustomobject]@{transaction_id='tx'}}}};"
        "function Publish-ObserveAttemptEvidence{return [pscustomobject]@{receipt_digest=('o'*64)}};"
        "Complete-ReleasePromotion;$pending=Get-ReleaseControlState;"
        "$runtime=Get-RuntimeUpdateState;"
        "$runtime.observation_deferred_projection_state='PASSED';"
        "$runtime|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $runtimeUpdateStatePath;"
        "Complete-ReleasePromotion;$passed=Get-ReleaseControlState;"
        'Write-Output "$($pending.transaction.phase),$($pending.stable.git_sha),'
        '$($null -eq $passed.transaction),$($passed.stable.git_sha)"',
    )
    assert result == f"OBSERVING,{previous},True,{candidate}"


def test_deferred_projection_probe_uses_installed_control_bundle_and_runtime_authority(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$target=New-ReleaseIdentity -GitSha ('b'*40) "
        "-WorkerVersionId '22222222-2222-4222-8222-222222222222' "
        "-WindowsRevision ('b'*40) -Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$obligation=[pscustomobject]@{route='/api/audit-stories'};"
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments,$WorkingDirectory,$Environment)"
        "$script:probePath=$Arguments[0];$script:probeWorkingDirectory=$WorkingDirectory;"
        "$attemptIndex=[Array]::IndexOf($Arguments,'--observe-attempt');"
        "$script:observeAttempts += @($Arguments[$attemptIndex+1]);"
        "[pscustomobject]@{exit_code=0;stdout_lines=@('{\"state\":\"PASSED\","
        "\"reason\":\"PASSED\",\"routes\":[]}');stderr_lines=@()}};"
        "$answer=Test-DeferredProjectionObligations -Obligations @($obligation) "
        "-Target $target -RequiredAfter ([DateTimeOffset]::UtcNow) "
        "-ValidationKey $target.validation_key;"
        "$second=Test-DeferredProjectionObligations -Obligations @($obligation) "
        "-Target $target -RequiredAfter ([DateTimeOffset]::UtcNow) "
        "-ValidationKey $target.validation_key;"
        "$bundleRoot=[IO.Path]::GetDirectoryName($script:probePath);"
        "$manifest=Get-Content -LiteralPath (Join-Path $bundleRoot "
        "'runtime-control-files.json') -Raw -Encoding UTF8|ConvertFrom-Json;"
        "$declared='check_deferred_projection_parity.py' -in @($manifest.files);"
        "$bundled=$bundleRoot -ne (Join-Path $moduleRoot 'scripts');"
        '$runtimeBound=$script:probeWorkingDirectory -eq $moduleRoot;'
        "$attemptsValid=@($script:observeAttempts|Where-Object{$_ -match '^[0-9a-f]{32}$'}).Count -eq 2;"
        "$attemptsDistinct=$script:observeAttempts[0] -ne $script:observeAttempts[1];"
        'Write-Output "$($answer.state),$($second.state),$declared,$bundled,$runtimeBound,$attemptsValid,$attemptsDistinct"',
    )
    assert result == "PASSED,PASSED,True,True,True,True,True"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_deferred_projection_request_binds_cutover_and_rollback_cancels_it(
    tmp_path, powershell: str,
) -> None:
    revision = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$target=[pscustomobject]@{{validation_key='run:{revision}';"
        "worker_version_id='22222222-2222-4222-8222-222222222222';"
        f"windows_revision='{revision}'}};"
        "$obligation=[pscustomobject]@{route='/api/audit-stories';"
        f"validation_key='run:{revision}';required_producer_revision='{revision}'}};"
        "$transaction=[pscustomobject]@{id='11111111-1111-4111-8111-111111111111';"
        "target=$target;deferred_projection_obligations=@($obligation)};"
        "$boundary=[DateTimeOffset]'2026-09-01T01:02:03+00:00';"
        "$request=Write-DeferredProjectionSyncRequest -Transaction $transaction "
        "-RequiredAfter $boundary;"
        "$persisted=Get-Content -LiteralPath $deferredProjectionSyncRequestPath "
        "-Raw -Encoding UTF8|ConvertFrom-Json;"
        f"Cancel-DeferredProjectionSyncRequest -FailedRevision '{revision}';"
        "$cancelled=Get-Content -LiteralPath $deferredProjectionSyncCancelledPath "
        "-Raw -Encoding UTF8|ConvertFrom-Json;"
        "$observedBoundary=ConvertTo-ReleaseTimestampUtc -Value $persisted.required_after;"
        'Write-Output "$($persisted.schema_version),$($persisted.target),'
        '$($observedBoundary.ToString(\'o\')),$($persisted.routes.Count),'
        '$($cancelled.state),$((Test-Path -LiteralPath '
        '$deferredProjectionSyncRequestPath))"', powershell=powershell,
    )
    assert result == (
        "deferred-projection-sync-v1,cloudflare,"
        "2026-09-01T01:02:03.0000000+00:00,1,CANCELLED_BY_ROLLBACK,False"
    )
    cancelled_path = (
        tmp_path / "runtime" / ".local" / "forward"
        / "deferred-projection-sync-cancelled.json"
    )
    raw = cancelled_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw)["request"]["producer_revision"] == revision


def test_crashed_cutover_is_reconciled_to_recovery_required(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState; "
        "$state.transaction=[pscustomobject]@{type='PROMOTE';phase='CUTOVER';target=$state.candidate;previous=$state.stable}; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='22222222-2222-4222-8222-222222222222';percentage=100})} }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{candidate}'}} }}; "
        "$final=Reconcile-ReleaseControlState; "
        'Write-Output "$($final.deployment_status),$($final.drift.code),$($final.drift.phase)"',
    )

    assert result == "RECOVERY_REQUIRED,INCOMPLETE_RELEASE_TRANSACTION,CUTOVER"


def test_crash_after_observation_pass_commits_exact_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _mock_active_promote_authority()
        + "$state=Get-ReleaseControlState;$state.transaction.phase='OBSERVING';"
        "Write-ReleaseControlState $state;"
        f"Write-RuntimeUpdateState @{{update_status='ACTIVE';activated_revision='{candidate}'}};"
        "function Test-CloudflareRollbackTarget { return $true };"
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='22222222-2222-4222-8222-222222222222';percentage=100})} };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{candidate}'}} }};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($null -eq $final.transaction)"',
    )

    assert result == f"READY,{candidate},True"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_crashed_lkg_recovery_resumes_bounded_observation(
    tmp_path, powershell: str,
) -> None:
    stable = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(stable, candidate)
        + "$state=Get-ReleaseControlState;$target=$state.stable;"
        "$state.transaction=[pscustomobject]@{id='recovery';type='RECOVERY';phase='CUTOVER';"
        "mode='RECOVERY_HOTFIX';recovery_action='RESTORE_LKG';target=$target;previous=$target};"
        "Write-ReleaseControlState $state;$script:observed='';"
        "function Start-RuntimeObservation{param($Revision,$PreviousRevision,$Mode,$ValidationKey);"
        "$script:observed=\"$Revision,$PreviousRevision,$Mode,$ValidationKey\"};"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})}};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{stable}'}}}};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.transaction.phase),$($final.deployment_status)|$script:observed"',
        powershell=powershell,
    )
    assert result == (
        f"OBSERVING,RECOVERY_OBSERVING|{stable},{stable},RESTORE_LKG,"
        "11111111-1111-4111-8111-111111111111:" + stable
    )


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    ("worker_at_lkg", "windows_at_lkg", "expected_worker_repairs", "expected_windows_repairs"),
    ((False, False, 1, 1), (True, False, 0, 1), (False, True, 1, 0)),
)
def test_crashed_lkg_partial_switch_repairs_only_missing_side_before_observe(
    tmp_path, powershell: str, worker_at_lkg: bool, windows_at_lkg: bool,
    expected_worker_repairs: int, expected_windows_repairs: int,
) -> None:
    stable = "a" * 40
    candidate = "b" * 40
    initial_worker = (
        "11111111-1111-4111-8111-111111111111" if worker_at_lkg
        else "22222222-2222-4222-8222-222222222222"
    )
    initial_windows = stable if windows_at_lkg else candidate
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(stable, candidate)
        + "$state=Get-ReleaseControlState;$target=$state.stable;"
        "$state.transaction=[pscustomobject]@{id='recovery';type='RECOVERY';phase='CUTOVER';"
        "mode='RECOVERY_HOTFIX';recovery_action='RESTORE_LKG';target=$target;previous=$target};"
        f"Write-ReleaseControlState $state;$script:worker='{initial_worker}';"
        f"$script:windows='{initial_windows}';$script:workerRepairs=0;$script:windowsRepairs=0;"
        "$script:observed=0;"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$script:worker;percentage=100})}};"
        "function Get-RuntimeCodeState{return [pscustomobject]@{applied_revision=$script:windows}};"
        "function Test-CloudflareRollbackTarget{return $true};"
        "function Invoke-CloudflareDeployment{param($StableVersionId,$Message);"
        "$script:worker=$StableVersionId;$script:workerRepairs++};"
        "function Invoke-ReleaseWindowsRestore{param($Revision);"
        "$script:windows=$Revision;$script:windowsRepairs++};"
        "function Test-SingleProductionOwner{return $true};"
        "function Start-RuntimeObservation{$script:observed++};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$script:workerRepairs,$script:windowsRepairs,$script:observed,'
        '$($final.transaction.phase),$($final.deployment_status)"',
        powershell=powershell,
    )
    assert result == (
        f"{expected_worker_repairs},{expected_windows_repairs},1,"
        "OBSERVING,RECOVERY_OBSERVING"
    )


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_crashed_lkg_observe_completion_reenters_authoritative_completion(
    tmp_path, powershell: str,
) -> None:
    stable = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(stable, candidate)
        + "$state=Get-ReleaseControlState;$target=$state.stable;"
        "$state.transaction=[pscustomobject]@{id='recovery';type='RECOVERY';phase='OBSERVING';"
        "mode='RECOVERY_HOTFIX';recovery_action='RESTORE_LKG';target=$target;previous=$target};"
        "Write-ReleaseControlState $state;$script:completed=0;"
        f"function Get-RuntimeUpdateState{{return [pscustomobject]@{{update_status='ACTIVE';activated_revision='{stable}'}}}};"
        "function Complete-ReleaseRecovery{$script:completed++};"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})}};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{stable}'}}}};"
        "$null=Reconcile-ReleaseControlState;Write-Output $script:completed",
        powershell=powershell,
    )
    assert result == "1"


def test_crashed_reverse_enters_observation_before_commit(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$target=$state.stable;$current=$state.candidate;"
        "$state.stable=$current;$state.previous_stable=$target;"
        "$state.transaction=[pscustomobject]@{type='REVERSE';phase='REVERSING';target=$target;previous=$current};"
        "Write-ReleaseControlState $state;"
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})} };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($final.previous_stable.git_sha)"',
    )

    assert result == f"REVERSE_OBSERVING,{current},{previous}"


def test_reverse_restores_both_identities_without_d1_mutation(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$state.previous_stable=$state.stable;"
        "$state.stable=$state.candidate;$state.candidate=$null;Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock { return $true };function Exit-ReleaseTransactionLock {};"
        "function Assert-ActiveControlBundle { return [pscustomobject]@{exact_revision=$true} };"
        "function Get-CurrentReleaseRuntimeReadModel { $live=Get-ReleaseControlState;"
        "return [pscustomobject]@{committed_stable=$live.stable;"
        "previous_committed=$live.previous_stable;previous="
        "[pscustomobject]@{reverse_precheck=[pscustomobject]@{can_reverse=$true;reason='READY'}}} };"
        "function Test-SingleProductionOwner { return $true };"
        f"function New-RuntimeRecoveryPlan {{ return [pscustomobject]@{{body="
        f"[pscustomobject]@{{stable_revision='{current}'}};digest=('0' * 64)}} }};"
        "function Stop-ForecasterService {};"
        "function Start-RuntimeObservation {};"
        "$script:worker='';$script:windows='';"
        "function Invoke-CloudflareDeployment { param($StableVersionId);$script:worker=$StableVersionId };"
        "function Invoke-ReleaseWindowsRestore { param($Revision);$script:windows=$Revision };"
        "$ok=Invoke-ReverseStable;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.stable.git_sha),$script:worker,$script:windows"',
    )
    source = _control_center_source()
    reverse_body = source.split("function Invoke-ReverseStable", 1)[1].split(
        "function Reconcile-ReleaseControlState", 1,
    )[0]

    assert result == f"True,{current},11111111-1111-4111-8111-111111111111,{previous}"
    assert "D1" not in reverse_body
    assert "database" not in reverse_body.lower()


@pytest.mark.parametrize("persisted_eligible", ("$true", "$false"))
@pytest.mark.parametrize(
    "live_reason",
    ("ACTIVE_OBSERVATION_UNAVAILABLE", "ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE"),
)
def test_reverse_action_rechecks_live_authority_before_transaction(
    tmp_path, persisted_eligible: str, live_reason: str,
) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$state.previous_stable=$state.stable;"
        "$state.stable=$state.candidate;$state.candidate=$null;"
        f"$state.previous_stable_rollback_eligible={persisted_eligible};"
        "$before=($state|ConvertTo-Json -Depth 12 -Compress);Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{$script:releaseTransactionLockHeld=$true;return $true};"
        "function Exit-ReleaseTransactionLock{$script:releaseTransactionLockHeld=$false};"
        "function Assert-ActiveControlBundle{return [pscustomobject]@{exact_revision=$true}};"
        "function Get-CurrentReleaseRuntimeReadModel{return [pscustomobject]@{previous="
        "[pscustomobject]@{reverse_precheck=[pscustomobject]@{can_reverse=$false;"
        f"reason='{live_reason}'}}}}}}}};"
        "$reason='';try{$null=Invoke-ReverseStable}catch{$reason=$_.Exception.Message};"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$reason,$($null -eq $final.transaction),$($final.deployment_status),'
        '$($final.previous_stable_rollback_eligible)"',
    )

    assert result == (
        f"REVERSE_PRECHECK_BLOCKED:{live_reason},True,READY,"
        + ("True" if persisted_eligible == "$true" else "False")
    )


def test_reverse_fresh_provider_timeout_never_creates_transaction(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$state.previous_stable=$state.stable;"
        "$state.stable=$state.candidate;$state.candidate=$null;Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{$script:releaseTransactionLockHeld=$true;return $true};"
        "function Exit-ReleaseTransactionLock{$script:releaseTransactionLockHeld=$false};"
        "function Assert-ActiveControlBundle{[pscustomobject]@{exact_revision=$true}};"
        "function Get-CloudflareDeployment{throw 'NATIVE_PROCESS_TIMEOUT'};"
        "function Get-CurrentReleaseRuntimeReadModel{"
        "$provider=Get-ReleaseDeploymentProviderObservation -ForceFresh;"
        "return [pscustomobject]@{previous=[pscustomobject]@{reverse_precheck="
        "[pscustomobject]@{can_reverse=$false;reason='ACTIVE_OBSERVATION_UNAVAILABLE'}}}};"
        "$reason='';try{$null=Invoke-ReverseStable}catch{$reason=$_.Exception.Message};"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$reason,$($null -eq $final.transaction)"',
    )
    assert result == "REVERSE_PRECHECK_BLOCKED:ACTIVE_OBSERVATION_UNAVAILABLE,True"


def test_release_drift_is_detected_without_changing_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='99999999-9999-4999-8999-999999999999';percentage=100})} }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }}; "
        "$final=Reconcile-ReleaseControlState; "
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha)"',
    )

    assert result == f"DEPLOYMENT_DRIFT,{previous}"


def test_release_requires_exactly_one_owner_for_every_side_effect_service(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ForecasterProcesses { param($Service); "
        "if ($Service.Key -eq 'sync') { return @([pscustomobject]@{ProcessId=1},[pscustomobject]@{ProcessId=2}) }; "
        "return [pscustomobject]@{ProcessId=1} }; "
        "$duplicate=Test-SingleProductionOwner; "
        "function Get-ForecasterProcesses { param($Service); return [pscustomobject]@{ProcessId=1} }; "
        "$single=Test-SingleProductionOwner; Write-Output \"$duplicate,$single\"",
    )

    assert result == "False,True"


def test_storage_migration_requires_coordinated_compatibility_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "Write-Output (Test-AutomaticStorageCompatibility -ChangedFiles "
        "@('web/worker/index.ts','web/drizzle/0022_new.sql'))",
    )

    assert result == "False"


def test_platform_binding_change_requires_coordinated_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "Write-Output (Test-AutomaticStorageCompatibility -ChangedFiles "
        "@('web/wrangler.jsonc'))",
    )
    assert result == "False"


def test_compatibility_classifier_separates_storage_from_platform_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$storage=Get-CandidateCompatibilityRequirement @('web/drizzle/0022.sql');"
        "$platform=Get-CandidateCompatibilityRequirement @('web/wrangler.jsonc');"
        "$automatic=Get-CandidateCompatibilityRequirement @('web/app/api/status/route.ts');"
        'Write-Output "$($storage.state),$($platform.state),$($automatic.state)"',
    )
    assert result == (
        "COORDINATED_STORAGE_MIGRATION_REQUIRED,"
        "PLATFORM_CONFIG_REVIEW_REQUIRED,AUTOMATIC"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_coordinated_migration_receipt_is_exact_fresh_and_live(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$receipt=New-CoordinatedMigrationReceipt $evidence;"
        "Write-CoordinatedMigrationReceipt $receipt;"
        "$verified=Assert-CoordinatedMigrationReceipt $candidate $stable $files;"
        'Write-Output "$($verified.schema_version),$($verified.evidence.database_id),'
        '$($verified.evidence.reverse_safe)"',
        powershell=powershell,
    )
    assert result == (
        "coordinated-storage-migration-receipt-v1,"
        "33333333-3333-4333-8333-333333333333,True"
    )


def test_release_control_json_preserves_timestamp_strings_across_runtimes(
    tmp_path,
) -> None:
    payload = json.dumps({
        "utc": "2026-08-27T16:54:04+00:00",
        "fractional": "2026-08-27T16:54:04.029540+00:00",
        "offset": "2026-08-28T00:54:04.029540+08:00",
    }, separators=(",", ":"))
    outputs = [
        _run_control_center_contract(
            tmp_path,
            f"$parsed='{payload}'|ConvertFrom-ReleaseControlJson;"
            'Write-Output "$($parsed.utc.GetType().FullName)|$($parsed.utc)|'
            '$($parsed.fractional.GetType().FullName)|$($parsed.fractional)|'
            '$($parsed.offset.GetType().FullName)|$($parsed.offset)"',
            powershell=powershell,
        )
        for powershell in ("powershell.exe", "pwsh.exe")
    ]

    assert outputs == [outputs[0], outputs[0]]
    assert outputs[0] == (
        "System.String|2026-08-27T16:54:04+00:00|"
        "System.String|2026-08-27T16:54:04.029540+00:00|"
        "System.String|2026-08-28T00:54:04.029540+08:00"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_d1_release_evidence_ingestion_preserves_timestamp_string(
    tmp_path, powershell: str,
) -> None:
    wrangler = (
        tmp_path / "repository" / "web" / "node_modules" / "wrangler" / "bin"
        / "wrangler.js"
    )
    wrangler.parent.mkdir(parents=True, exist_ok=True)
    wrangler.write_text("// contract fixture\n", encoding="utf-8")
    timestamp = "2026-08-27T16:54:04.029540+00:00"
    response = json.dumps([{
        "success": True,
        "results": [{"generation_watermark": timestamp}],
    }], separators=(",", ":"))
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-Utf8NativeProcess{"
        f"return [pscustomobject]@{{exit_code=0;stdout='{response}';stderr='';"
        f"stdout_lines=@('{response}');stderr_lines=@()}}}};"
        "$row=@(Invoke-CoordinatedMigrationD1Query -Sql 'SELECT 1')[0];"
        'Write-Output "$($row.generation_watermark.GetType().FullName)|'
        '$($row.generation_watermark)"',
        powershell=powershell,
    )
    assert result == f"System.String|{timestamp}"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_migration_receipt_later_recheck_preserves_exact_timestamp(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$script:liveJson=$evidence|ConvertTo-Json -Compress -Depth 12;"
        "function Get-CoordinatedMigrationLiveEvidence{"
        "return $script:liveJson|ConvertFrom-ReleaseControlJson};"
        "$parsed=$script:liveJson|ConvertFrom-ReleaseControlJson;"
        "$receipt=New-CoordinatedMigrationReceipt $parsed;"
        "Write-CoordinatedMigrationReceipt $receipt;"
        "$verified=Assert-CoordinatedMigrationReceipt $candidate $stable $files;"
        'Write-Output "$($verified.evidence.news_watermark.GetType().FullName)|'
        '$($verified.evidence.news_watermark)|$($verified.receipt_digest.Length)"',
        powershell=powershell,
    )

    assert result == "System.String|2026-08-26T05:00:00Z|64"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_migration_receipt_rejects_actual_timestamp_change_on_later_recheck(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$receipt=New-CoordinatedMigrationReceipt $evidence;"
        "Write-CoordinatedMigrationReceipt $receipt;"
        "$script:next=$evidence|ConvertTo-Json -Compress -Depth 12|"
        "ConvertFrom-ReleaseControlJson;"
        "$script:next.news_watermark='2026-08-26T05:00:01Z';"
        "function Get-CoordinatedMigrationLiveEvidence{return $script:next};"
        "$reason='PASSED';try{Assert-CoordinatedMigrationReceipt "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
        powershell=powershell,
    )

    assert result == "MIGRATION_RECEIPT_GENERATION_MUTATED:news_watermark"


@pytest.mark.parametrize(
    ("activation", "expected"),
    (
        ("2026-08-26T05:02:00Z", "PASSED"),
        ("2026-08-26T04:59:00Z", "MIGRATION_RECEIPT_GENERATION_REGRESSION"),
    ),
)
def test_migration_receipt_tolerates_only_forward_valid_current_generation(
    tmp_path, activation: str, expected: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$receipt=New-CoordinatedMigrationReceipt $evidence;"
        "Write-CoordinatedMigrationReceipt $receipt;"
        "$script:next=$evidence|ConvertTo-Json -Depth 12|ConvertFrom-Json;"
        "$script:next.news_generation_id=('9'*64);"
        "$script:next.news_snapshot_id=('8'*64);"
        "$script:next.news_source_digest=('7'*64);"
        "$script:next.news_receipt_digest=('6'*64);"
        f"$script:next.news_activated_at='{activation}';"
        "function Get-CoordinatedMigrationLiveEvidence{return $script:next};"
        "$reason='PASSED';try{Assert-CoordinatedMigrationReceipt "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == expected


def test_migration_contract_reads_the_exact_candidate_not_stable_checkout(
    tmp_path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract@example.invalid"],
        cwd=repository,
        check=True,
    )
    (repository / "stable.txt").write_text("stable\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "stable"], cwd=repository, check=True)
    stable = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _write_coordinated_migration_files(tmp_path)
    subprocess.run(["git", "add", "web/drizzle"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate migrations"], cwd=repository, check=True)
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "-q", stable], cwd=repository, check=True)

    result = _run_control_center_contract(
        tmp_path,
        "$changed=@('web/drizzle/0022_news_projection_generation.sql',"
        "'web/drizzle/0023_operator_retry_sync_digest.sql',"
        "'web/drizzle/0024_seed_bounded_audit_news_metrics.sql',"
        "'web/drizzle/0025_seed_legacy_news_reverse_projection.sql',"
        "'web/drizzle/0026_reconcile_legacy_news_current_identity.sql',"
        "'web/drizzle/0027_materialize_news_projection_counts.sql',"
        "'web/drizzle/0028_fence_legacy_news_current_identity.sql',"
        "'web/drizzle/0029_news_projection_receipt_index.sql',"
        "'web/drizzle/0030_news_evidence_cleanup_budget.sql');"
        f"$files=Get-CoordinatedMigrationFiles $changed '{candidate}';"
        f"Assert-CoordinatedMigrationCapabilityContract $files '{candidate}';"
        'Write-Output "$($files.Count),$(git -C $repositoryRoot rev-parse HEAD)"',
    )
    assert result == f"9,{stable}"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$candidate.git_sha=('9'*40)", "MIGRATION_RECEIPT_CANDIDATE_MISMATCH"),
        (
            "$candidate.worker_version_id='99999999-9999-4999-8999-999999999999'",
            "MIGRATION_RECEIPT_CANDIDATE_MISMATCH",
        ),
        (
            "$saved=Get-Content $coordinatedMigrationReceiptPath -Raw|"
            "ConvertFrom-ReleaseControlJson;"
            "$saved.expires_at=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o');"
            "$core=[ordered]@{schema_version=$saved.schema_version;checked_at=$saved.checked_at;"
            "expires_at=$saved.expires_at;evidence=$saved.evidence};"
            "$saved.receipt_digest=Get-CoordinatedMigrationReceiptDigest $core;"
            "$saved|ConvertTo-Json -Depth 12|Set-Content $coordinatedMigrationReceiptPath",
            "MIGRATION_RECEIPT_STALE",
        ),
        (
            "$saved=Get-Content $coordinatedMigrationReceiptPath -Raw|"
            "ConvertFrom-ReleaseControlJson;"
            "$saved.evidence.database_name='tampered';"
            "$saved|ConvertTo-Json -Depth 12|Set-Content $coordinatedMigrationReceiptPath",
            "MIGRATION_RECEIPT_TAMPERED",
        ),
    ),
)
@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_coordinated_migration_receipt_rejects_reuse_staleness_and_tampering(
    tmp_path, mutation: str, expected: str, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$receipt=New-CoordinatedMigrationReceipt $evidence;"
        "Write-CoordinatedMigrationReceipt $receipt;"
        f"{mutation};$reason='';try{{Assert-CoordinatedMigrationReceipt "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
        powershell=powershell,
    )
    assert result == expected


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_expired_migration_acceptance_renews_from_exact_live_evidence(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$rootPath=Get-CoordinatedMigrationRootReceiptPath $root.receipt_digest;"
        "$before=[IO.File]::ReadAllBytes($rootPath);$script:migrationMutationQueries=0;"
        "$qualification=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$after=[IO.File]::ReadAllBytes($rootPath);"
        "$same=[Convert]::ToBase64String($before) -ceq [Convert]::ToBase64String($after);"
        "$renewals=@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File);"
        'Write-Output "$($qualification.state),$($candidate.migration_qualification.state),'
        '$($qualification.root_receipt_digest -eq $root.receipt_digest),$same,'
        '$script:migrationMutationQueries,$($renewals.Count)"',
        powershell=powershell,
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWED,MIGRATION_QUALIFICATION_RENEWED,True,True,0,1"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_fresh_migration_acceptance_does_not_create_renewal(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$root=New-CoordinatedMigrationReceipt $evidence;Write-CoordinatedMigrationReceipt $root;"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest=$root.receipt_digest});"
        "$state=[pscustomobject]@{transaction=$null;stable=$stable;candidate=$candidate};"
        "Write-ReleaseControlState $state;function Get-CloudflareDeployment{throw 'unused'};"
        "$script:migrationMutationQueries=0;"
        "$qualification=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$count=if(Test-Path $coordinatedMigrationRenewalReceiptRoot){"
        "@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File).Count}else{0};"
        'Write-Output "$($qualification.state),$count,$script:migrationMutationQueries"',
        powershell=powershell,
    )
    assert result == "MIGRATION_ACCEPTED,0,0"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_near_expiry_migration_acceptance_renews_before_promotion(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "$evidence=Get-CoordinatedMigrationLiveEvidence $candidate $stable $files;"
        "$root=New-CoordinatedMigrationReceipt $evidence;Write-CoordinatedMigrationReceipt $root;"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest=$root.receipt_digest});"
        "$state=[pscustomobject]@{transaction=$null;stable=$stable;candidate=$candidate};"
        "Write-ReleaseControlState $state;$script:migrationMutationQueries=0;"
        "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=$stable.windows_revision}};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$stable.worker_version_id;percentage=100})}};"
        "$qualification=Ensure-CoordinatedMigrationQualification $candidate $stable $files "
        "-MinimumRemaining ([TimeSpan]::FromHours(3));"
        "$count=@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($qualification.state),$count,$script:migrationMutationQueries"',
        powershell=powershell,
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWED,1,0"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            "$script:testDatabaseId='44444444-4444-4444-8444-444444444444';",
            "MIGRATION_RECEIPT_LIVE_EVIDENCE_MISMATCH:database_id",
        ),
        (
            "$candidate.windows_revision=('9'*40);",
            "MIGRATION_QUALIFICATION_CANDIDATE_IDENTITY_INVALID",
        ),
        (
            "$stable.windows_revision=('9'*40);",
            "MIGRATION_QUALIFICATION_STABLE_IDENTITY_INVALID",
        ),
        (
            "$script:changed=$evidence|ConvertTo-Json -Depth 16|ConvertFrom-ReleaseControlJson;"
            "$script:changed.news_generation_id=('9'*64);"
            "function Get-CoordinatedMigrationLiveEvidence{return $script:changed};",
            "MIGRATION_RECEIPT_GENERATION_CHANGED",
        ),
    ),
)
def test_migration_renewal_rejects_changed_live_authority(
    tmp_path, mutation: str, expected: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + mutation
        + "$reason='';try{Ensure-CoordinatedMigrationQualification "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == expected


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            "$state=Get-ReleaseControlState;$state.transaction=[pscustomobject]@{type='PROMOTE'};"
            "Write-ReleaseControlState $state;",
            "MIGRATION_QUALIFICATION_RENEWAL_TRANSACTION_ACTIVE",
        ),
        (
            "New-Item -ItemType Directory -Path $runtimeStateMigrationLockPath|Out-Null;",
            "MIGRATION_QUALIFICATION_RENEWAL_LOCK_ACTIVE",
        ),
        (
            "function Get-RuntimeCodeState{[pscustomobject]@{applied_revision=('9'*40)}};",
            "MIGRATION_QUALIFICATION_RENEWAL_WINDOWS_IDENTITY_UNSAFE",
        ),
        (
            "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
            "[pscustomobject]@{version_id=$stable.worker_version_id;percentage=50},"
            "[pscustomobject]@{version_id=$candidate.worker_version_id;percentage=50})}};",
            "MIGRATION_QUALIFICATION_RENEWAL_PRODUCTION_OWNERSHIP_UNSAFE",
        ),
    ),
)
def test_migration_renewal_requires_quiescent_single_owner_boundary(
    tmp_path, mutation: str, expected: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + mutation
        + "$reason='';try{Ensure-CoordinatedMigrationQualification "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == expected


@pytest.mark.parametrize(
    ("stable_id", "stable_message", "expected"),
    (
        ("$stable.worker_version_id", "$null", "PASSED"),
        ("$stable.worker_version_id", "('release:'+('9'*40))", "MIGRATION_STABLE_VERSION_IDENTITY_MISMATCH"),
        ("'99999999-9999-4999-8999-999999999999'", "$null", "MIGRATION_STABLE_VERSION_IDENTITY_MISMATCH"),
    ),
)
def test_migration_live_evidence_uses_exact_stable_version_with_legacy_provenance(
    tmp_path, stable_id: str, stable_message: str, expected: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + "function Get-CloudflareVersionDetails{param($VersionId);"
        "$isCandidate=$VersionId -eq $candidate.worker_version_id;"
        f"$id=if($isCandidate){{$candidate.worker_version_id}}else{{{stable_id}}};"
        "$message=if($isCandidate){'release:'+$candidate.git_sha}else{"
        f"{stable_message}}};$annotations=[pscustomobject]@{{'workers/message'=$message}};"
        "[pscustomobject]@{id=$id;annotations=$annotations;resources=[pscustomobject]@{"
        "bindings=@([pscustomobject]@{type='d1';name='DB';"
        "database_id=$script:testDatabaseId})}}};"
        "$reason='PASSED';try{Get-CoordinatedMigrationLiveEvidence "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == expected


def test_migration_renewal_rejects_broken_root_receipt_digest(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$path=Get-CoordinatedMigrationRootReceiptPath $root.receipt_digest;"
        "$saved=Get-Content $path -Raw|ConvertFrom-ReleaseControlJson;"
        "$saved.evidence.database_name='tampered';"
        "$saved|ConvertTo-Json -Depth 12|Set-Content $path;"
        "$reason='';try{Ensure-CoordinatedMigrationQualification "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "MIGRATION_RECEIPT_TAMPERED"


def test_migration_renewal_chain_tampering_fails_closed(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$qualification=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$digest=$qualification.receipt.receipt_digest;"
        "$path=Get-CoordinatedMigrationRenewalReceiptPath $digest;"
        "$saved=Get-Content $path -Raw|ConvertFrom-ReleaseControlJson;"
        "$saved.previous_migration_renewal_digest=('9'*64);"
        "$saved|ConvertTo-Json -Depth 16|Set-Content $path;"
        "$reason='';try{Ensure-CoordinatedMigrationQualification "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"


def test_expired_migration_renewal_links_the_previous_immutable_lease(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$rootReceipt=Assert-CoordinatedMigrationRootReceipt $candidate $stable "
        "$files $root.receipt_digest -AllowStale;"
        "$live=Assert-CoordinatedMigrationLiveEvidenceMatchesRoot $rootReceipt "
        "$candidate $stable $files -RequireExactGeneration;"
        "$fresh=New-CoordinatedMigrationRenewalReceipt $candidate $stable $rootReceipt $live;"
        "$oldCore=Get-CoordinatedMigrationRenewalCore $fresh;"
        "$oldChecked=[DateTimeOffset]::UtcNow.AddHours(-3);"
        "$oldCore['checked_at']=$oldChecked.ToString('o');"
        "$oldCore['expires_at']=$oldChecked.AddHours(2).ToString('o');"
        "$old=[pscustomobject]$oldCore;$old|Add-Member -NotePropertyName receipt_digest "
        "-NotePropertyValue (Get-CoordinatedMigrationRenewalReceiptDigest $oldCore);"
        "Write-CoordinatedMigrationRenewalReceipt $old;"
        "$candidate|Add-Member -Force migration_qualification ([pscustomobject]@{"
        "state='MIGRATION_QUALIFICATION_RENEWED';validation_key=$candidate.validation_key;"
        "root_receipt_digest=$root.receipt_digest;receipt_digest=$old.receipt_digest});"
        "$next=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$count=@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($next.state),'
        '$($next.receipt.previous_migration_renewal_digest -eq $old.receipt_digest),$count"',
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWED,True,2"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_unpersisted_fresh_migration_renewal_is_recovered_from_immutable_store(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$rootReceipt=Assert-CoordinatedMigrationRootReceipt $candidate $stable "
        "$files $root.receipt_digest -AllowStale;"
        "$live=Assert-CoordinatedMigrationLiveEvidenceMatchesRoot $rootReceipt "
        "$candidate $stable $files -RequireExactGeneration;"
        "$orphan=New-CoordinatedMigrationRenewalReceipt $candidate $stable $rootReceipt $live;"
        "Write-CoordinatedMigrationRenewalReceipt $orphan;"
        "$script:migrationMutationQueries=0;"
        "$qualification=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$count=@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($qualification.state),'
        '$($qualification.receipt.receipt_digest -eq $orphan.receipt_digest),'
        '$($candidate.migration_qualification.receipt_digest -eq $orphan.receipt_digest),'
        '$count,$script:migrationMutationQueries"',
        powershell=powershell,
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWED,True,True,1,0"


def test_unpersisted_stale_migration_renewal_is_linked_by_next_lease(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$rootReceipt=Assert-CoordinatedMigrationRootReceipt $candidate $stable "
        "$files $root.receipt_digest -AllowStale;"
        "$live=Assert-CoordinatedMigrationLiveEvidenceMatchesRoot $rootReceipt "
        "$candidate $stable $files -RequireExactGeneration;"
        "$fresh=New-CoordinatedMigrationRenewalReceipt $candidate $stable $rootReceipt $live;"
        "$oldCore=Get-CoordinatedMigrationRenewalCore $fresh;"
        "$oldChecked=[DateTimeOffset]::UtcNow.AddHours(-3);"
        "$oldCore['checked_at']=$oldChecked.ToString('o');"
        "$oldCore['expires_at']=$oldChecked.AddHours(2).ToString('o');"
        "$orphan=[pscustomobject]$oldCore;$orphan|Add-Member -NotePropertyName receipt_digest "
        "-NotePropertyValue (Get-CoordinatedMigrationRenewalReceiptDigest $oldCore);"
        "Write-CoordinatedMigrationRenewalReceipt $orphan;"
        "$next=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$count=@(Get-ChildItem $coordinatedMigrationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($next.state),'
        '$($next.receipt.previous_migration_renewal_digest -eq $orphan.receipt_digest),$count"',
    )
    assert result == "MIGRATION_QUALIFICATION_RENEWED,True,2"


def test_migration_renewal_cannot_move_to_another_runtime_root(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$null=Ensure-CoordinatedMigrationQualification $candidate $stable $files;"
        "$moduleRoot=Join-Path $moduleRoot 'moved-runtime';"
        "$reason='';try{Ensure-CoordinatedMigrationQualification "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "MIGRATION_RECEIPT_RUNTIME_ROOT_MISMATCH"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_supersession_recovery_entrypoint_renews_stale_migration_qualification(
    tmp_path, powershell: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _expired_migration_acceptance_body()
        + "$candidate|Add-Member -Force artifact_kind 'PRODUCTION_CANDIDATE';"
        "$candidate|Add-Member -Force compatibility_state 'PASSED';"
        "$candidate|Add-Member -Force validation_state 'PASSED';"
        "$candidate|Add-Member -Force validation ([pscustomobject]@{"
        "key=$candidate.validation_key;repository='PASSED';windows='PASSED';"
        "cloudflare='PASSED';data_parity=[pscustomobject]@{state='PASSED'};"
        "worker_qualification=[pscustomobject]@{key=('d'*64);"
        "candidate_worker_version=$candidate.worker_version_id;candidate_git_sha=$candidate.git_sha};"
        "cpu_evidence=[pscustomobject]@{qualification_key=('d'*64);"
        "qualification_receipt_digest=('e'*64)};"
        "auth_inspection=[pscustomobject]@{state='HUMAN_ACCESS_BOUNDARY_ACCEPTED'}});"
        "$head=New-ReleaseIdentity -GitSha ('c'*40) -WorkerVersionId "
        "'cccccccc-cccc-4ccc-8ccc-cccccccccccc' -WindowsRevision ('c'*40) "
        "-Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$head.compatibility_state='REVIEW_REQUIRED';$head.validation_state='REVIEW_REQUIRED';"
        "$head.validation=[pscustomobject]@{key=$head.validation_key;"
        "reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'};"
        "$state=Get-ReleaseControlState;$state.candidate=$head;Write-ReleaseControlState $state;"
        "Write-ReleaseHistory -Event 'CANDIDATE_SUPERSEDED' -Release $candidate "
        "-Detail @{replacement_key=$head.validation_key};"
        "function Get-ProductionCandidateProvenanceResult{[pscustomobject]@{"
        "state='PASSED';mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';"
        "current_main_git_sha=('f'*40)}};"
        "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Get-CandidateChangedFiles{return $files};"
        "function Get-CandidateCompatibilityRequirement{[pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=$files}};"
        "function Get-WorkerCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64);source_worker_version=$candidate.worker_version_id;"
        "source_git_sha=$candidate.git_sha}};"
        "function Assert-AccessBoundaryAcceptanceReceipt{[pscustomobject]@{"
        "receipt_digest=('1'*64)}};function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{[pscustomobject]@{passed=$true}};"
        "$rootPath=Get-CoordinatedMigrationRootReceiptPath $root.receipt_digest;"
        "$before=[Convert]::ToBase64String([IO.File]::ReadAllBytes($rootPath));"
        "$restored=Restore-ControlPlaneOnlySupersededCandidate $state ('f'*40);"
        "$after=[Convert]::ToBase64String([IO.File]::ReadAllBytes($rootPath));"
        "$final=Get-ReleaseControlState;$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$($restored.validation_key -eq $candidate.validation_key),'
        '$($final.candidate.migration_qualification.state),$($before -ceq $after),'
        '$($history.Contains(\'MIGRATION_QUALIFICATION_RENEWED\'))"',
        powershell=powershell,
    )
    assert result == "True,MIGRATION_QUALIFICATION_RENEWED,True,True"


def test_migration_capability_reuses_json_projection_from_one_bounded_scan() -> None:
    control_center = _control_center_source()
    capability_sql = control_center.split('$capabilitySql = @"', 1)[1].split(
        '"@', 1,
    )[0]

    assert "WITH current_projection AS MATERIALIZED (" in capability_sql
    assert "json_each(r.items_json)" in capability_sql
    assert capability_sql.count("FROM current_projection") >= 4
    assert "EXCEPT SELECT detail_key FROM current_projection" in capability_sql
    assert "JOIN current_projection" not in capability_sql


@pytest.mark.parametrize(
    ("setup", "expected"),
    (
        (
            "function Invoke-WranglerJson{param($Arguments);return [pscustomobject]@{"
            "uuid='44444444-4444-4444-8444-444444444444';name='wrong'}}",
            "MIGRATION_DATABASE_IDENTITY_MISMATCH",
        ),
        (
            "function Invoke-CoordinatedMigrationD1Query{param($Sql);"
            "if($Sql -like 'SELECT name,*'){return [pscustomobject]@{"
            "name='0022_news_projection_generation.sql';applied_at='now'}}}",
            "MIGRATION_LEDGER_PENDING:0023_operator_retry_sync_digest.sql,"
            "0024_seed_bounded_audit_news_metrics.sql,"
            "0025_seed_legacy_news_reverse_projection.sql,"
            "0026_reconcile_legacy_news_current_identity.sql,"
            "0027_materialize_news_projection_counts.sql,"
            "0028_fence_legacy_news_current_identity.sql,"
            "0029_news_projection_receipt_index.sql,"
            "0030_news_evidence_cleanup_budget.sql",
        ),
        (
            "function Invoke-CoordinatedMigrationD1Query{param($Sql);"
                "if($Sql -like 'SELECT name,*'){return @("
                "[pscustomobject]@{name='0022_news_projection_generation.sql'},"
                "[pscustomobject]@{name='0023_operator_retry_sync_digest.sql'},"
                "[pscustomobject]@{name='0024_seed_bounded_audit_news_metrics.sql'},"
                "[pscustomobject]@{name='0025_seed_legacy_news_reverse_projection.sql'},"
                "[pscustomobject]@{name='0026_reconcile_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0027_materialize_news_projection_counts.sql'},"
                "[pscustomobject]@{name='0028_fence_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0029_news_projection_receipt_index.sql'},"
                "[pscustomobject]@{name='0030_news_evidence_cleanup_budget.sql'})};"
            "return [pscustomobject]@{projection_tables=4;projection_indexes=4;"
            "retry_columns=4}}",
            "MIGRATION_SCHEMA_CAPABILITY_MISSING",
        ),
        (
            "$script:legacyFailure=$true;function Invoke-CoordinatedMigrationD1Query{"
                "param($Sql);if($Sql -like 'SELECT name,*'){return @("
                "[pscustomobject]@{name='0022_news_projection_generation.sql'},"
                "[pscustomobject]@{name='0023_operator_retry_sync_digest.sql'},"
                "[pscustomobject]@{name='0024_seed_bounded_audit_news_metrics.sql'},"
                "[pscustomobject]@{name='0025_seed_legacy_news_reverse_projection.sql'},"
                "[pscustomobject]@{name='0026_reconcile_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0027_materialize_news_projection_counts.sql'},"
                "[pscustomobject]@{name='0028_fence_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0029_news_projection_receipt_index.sql'},"
                "[pscustomobject]@{name='0030_news_evidence_cleanup_budget.sql'})};"
            "return [pscustomobject]@{projection_tables=7;projection_indexes=6;projection_triggers=6;"
            "projection_count_columns=6;projection_receipt_columns=10;"
            "retry_columns=4;evidence_cleanup_budget_tables=1;"
            "legacy_tables=3;legacy_decisions=0}}",
            "MIGRATION_LEGACY_COMPATIBILITY_FAILED",
        ),
        (
            "$script:legacyNewsFailure=$true;function Invoke-CoordinatedMigrationD1Query{"
                "param($Sql);if($Sql -like 'SELECT name,*'){return @("
                "[pscustomobject]@{name='0022_news_projection_generation.sql'},"
                "[pscustomobject]@{name='0023_operator_retry_sync_digest.sql'},"
                "[pscustomobject]@{name='0024_seed_bounded_audit_news_metrics.sql'},"
                "[pscustomobject]@{name='0025_seed_legacy_news_reverse_projection.sql'},"
                "[pscustomobject]@{name='0026_reconcile_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0027_materialize_news_projection_counts.sql'},"
                "[pscustomobject]@{name='0028_fence_legacy_news_current_identity.sql'},"
                "[pscustomobject]@{name='0029_news_projection_receipt_index.sql'},"
                "[pscustomobject]@{name='0030_news_evidence_cleanup_budget.sql'})};"
            "$row=[pscustomobject]@{projection_tables=7;projection_indexes=6;projection_triggers=6;"
            "projection_count_columns=6;projection_receipt_columns=10;"
            "retry_columns=4;evidence_cleanup_budget_tables=1;"
            "legacy_tables=4;legacy_decisions=20;"
            "projection_state='CURRENT';active_generation_id=('c'*64);"
            "snapshot_id=('d'*64);source_digest=('e'*64);receipt_digest=('f'*64);"
            "index_count=4117;detail_count=4117;missing_detail_count=0;"
            "invariant_violation_count=0;generation_state='CURRENT';"
            "expected_receipt_digest=('f'*64);staged_index_count=4117;"
            "staged_detail_count=4117;legacy_current_index_count=4117;"
            "legacy_current_detail_count=4027;legacy_missing_detail_count=90;"
            "legacy_review_violation_count=0;legacy_parsed_flag_mismatch_count=0;"
            "legacy_candidate_flag_mismatch_count=0;legacy_duplicate_cluster_count=0;"
            "legacy_extra_current_index_count=0;legacy_current_row_mismatch_count=0};"
            "return $row}",
            "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED",
        ),
        (
            "function Get-CloudflareVersionDetails{param($VersionId);"
            "$id=if($VersionId -eq $stable.worker_version_id){"
            "'44444444-4444-4444-8444-444444444444'}else{$script:testDatabaseId};"
            "$sha=if($VersionId -eq $stable.worker_version_id){$stable.git_sha}"
            "else{$candidate.git_sha};return [pscustomobject]@{id=$VersionId;"
            "annotations=[pscustomobject]@{'workers/message'=('release:'+$sha)};"
            "resources=[pscustomobject]@{bindings=@("
            "[pscustomobject]@{type='d1';name='DB';database_id=$id})}}}",
            "MIGRATION_REVERSE_DATABASE_IDENTITY_MISMATCH",
        ),
        (
            "function Get-CoordinatedMigrationEndpointEvidence{"
            "param($Candidate,$Stable);throw 'MIGRATION_LEGACY_NEWS_READ_FAILED'}",
            "MIGRATION_LEGACY_NEWS_READ_FAILED",
        ),
    ),
)
def test_coordinated_migration_live_gate_fails_closed(
    tmp_path, setup: str, expected: str,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body()
        + f"{setup};$reason='';try{{Get-CoordinatedMigrationLiveEvidence "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == expected


def test_coordinated_migration_rejects_equal_legacy_counts_with_extra_identity(
    tmp_path,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body(
            capability_overrides="$row.legacy_extra_current_index_count=1;",
        )
        + "$reason='';try{Get-CoordinatedMigrationLiveEvidence "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED"


def test_coordinated_migration_rejects_equal_identity_with_mutated_legacy_row(
    tmp_path,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _coordinated_migration_contract_body(
            capability_overrides="$row.legacy_current_row_mismatch_count=1;",
        )
        + "$reason='';try{Get-CoordinatedMigrationLiveEvidence "
        "$candidate $stable $files|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED"


def test_successful_coordinated_migration_acceptance_is_audited_and_exact(
    tmp_path,
) -> None:
    _write_coordinated_migration_files(tmp_path)
    previous, candidate = "a" * 40, "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + _coordinated_migration_contract_body()
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='REVIEW_REQUIRED';"
        "$state.candidate.branch='main';"
        "$state.candidate.compatibility_state='REVIEW_REQUIRED';"
        "$state.candidate|Add-Member -Force browser_url 'https://candidate.example';"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "reason='COORDINATED_STORAGE_MIGRATION_REQUIRED';review_files=$files};"
        "Write-ReleaseControlState $state;"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-RequiredGitHubChecksResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-CandidateChangedFiles{return $files};"
        "$accepted=Verify-CandidateCoordinatedMigration;$final=Get-ReleaseControlState;"
        "$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$($accepted.validation_state),'
        '$($final.candidate.migration_acceptance.validation_key -eq '
        '$final.candidate.validation_key),'
        '$($null -eq $final.PSObject.Properties[\'migration_sync_hold\']),'
        '$($history.Contains(\'COORDINATED_STORAGE_MIGRATION_PASSED\'))"',
    )
    assert result == "NEW,True,True,True"


def test_coordinated_migration_verify_never_stops_stable_sync(tmp_path) -> None:
    _write_coordinated_migration_files(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + _coordinated_migration_contract_body()
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='REVIEW_REQUIRED';"
        "$state.candidate.branch='main';"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'};Write-ReleaseControlState $state;"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-RequiredGitHubChecksResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-CandidateChangedFiles{return $files};"
        "$script:stops=0;function Stop-ForecasterService{$script:stops++};"
        "$null=Verify-CandidateCoordinatedMigration;$final=Get-ReleaseControlState;"
        'Write-Output "$script:stops,$($null -eq '
        "$final.PSObject.Properties['migration_sync_hold'])\"",
    )
    assert result == "0,True"


@pytest.mark.parametrize(
    ("transaction", "expected"),
    [
        ("$null", "False"),
        ("[pscustomobject]@{type='PROMOTE';phase='PRECHECK'}", "True"),
        ("[pscustomobject]@{type='PROMOTE';phase='CUTOVER'}", "True"),
        ("[pscustomobject]@{type='PROMOTE';phase='OBSERVING'}", "False"),
        ("[pscustomobject]@{type='REVERSE';phase='REVERSING'}", "True"),
    ],
)
def test_watchdog_suppresses_stopped_sync_only_during_switch(
    tmp_path, transaction: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$state=[pscustomobject]@{{transaction={transaction}}};"
        "Write-Output (Test-WatchdogRecoverySuppressed 'sync' 'STOPPED' $state)",
    )
    assert result == expected


def test_prepare_and_verify_never_suppress_sync_recovery(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$prepare=[pscustomobject]@{transaction=$null;lifecycle_phase='PREPARE'};"
        "$verify=[pscustomobject]@{transaction=$null;lifecycle_phase='VERIFY'};"
        "$a=Test-WatchdogRecoverySuppressed 'sync' 'STOPPED' $prepare;"
        "$b=Test-WatchdogRecoverySuppressed 'sync' 'STOPPED' $verify;"
        'Write-Output "$a,$b"',
    )
    assert result == "False,False"


def test_platform_compatibility_approval_is_exact_audited_and_narrow(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';$candidate.compatibility_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "reason='PLATFORM_CONFIG_REVIEW_REQUIRED';resources_verified=$true;"
        "review_files=@('web/wrangler.jsonc')};Write-ReleaseControlState $state;"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-RequiredGitHubChecksResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-CandidateChangedFiles{return @('web/wrangler.jsonc')};"
        "function Test-CandidatePlatformResources{return $true};"
        "$approved=Approve-CandidateCompatibility;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$($approved.validation_state),'
        '$($approved.compatibility_approval.validation_key -eq $approved.validation_key),'
        '$($history.Contains(\'CANDIDATE_COMPATIBILITY_APPROVED\'))"',
    )
    assert result == "NEW,True,True"


def test_compatibility_approval_cannot_be_double_written(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.compatibility_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "reason='PLATFORM_CONFIG_REVIEW_REQUIRED';resources_verified=$true;"
        "windows='PASSED';review_files=@('web/wrangler.jsonc')};"
        "Write-ReleaseControlState $state;"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-RequiredGitHubChecksResult{return [pscustomobject]@{state='PASSED'}};"
        "function Get-CandidateChangedFiles{return @('web/wrangler.jsonc')};"
        "function Test-CandidatePlatformResources{return $true};"
        "$null=Approve-CandidateCompatibility;$second='';"
        "try{$null=Approve-CandidateCompatibility}catch{$second=$_.Exception.Message};"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        "$count=([regex]::Matches($history,'CANDIDATE_COMPATIBILITY_APPROVED')).Count;"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$count,$($second.Contains(\'Only an exact verified\')),'
        '$($final.candidate.compatibility_state),'
        '$($final.candidate.compatibility_approval.validation_key -eq '
        '$final.candidate.validation_key)"',
    )
    assert result == "1,True,APPROVED,True"


@pytest.mark.parametrize(
    ("gate", "diagnostic", "reason"),
    (
        ("FETCH", "fatal: operation timed out", "REPOSITORY_TRANSPORT_UNAVAILABLE"),
        ("GITHUB", "HTTP 503 Service Unavailable", "GITHUB_TEMPORARILY_UNAVAILABLE"),
        ("GITHUB", "HTTP 429 rate limit exceeded", "GITHUB_TEMPORARILY_UNAVAILABLE"),
    ),
)
def test_compatibility_approval_transient_failure_preserves_review_evidence(
    tmp_path, gate: str, diagnostic: str, reason: str,
) -> None:
    setup = (
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.branch='main';"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.compatibility_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "reason='PLATFORM_CONFIG_REVIEW_REQUIRED';resources_verified=$true;"
        "windows='PASSED';review_files=@('web/wrangler.jsonc')};"
        "Write-ReleaseControlState $state;$before=Get-Content $releaseControlStatePath -Raw;"
    )
    if gate == "FETCH":
        mocks = (
            "function Invoke-RepositoryRead{return [pscustomobject]@{passed=$false;"
            "failure_class='TRANSIENT_EXTERNAL';exit_code=128;"
            f"diagnostic='{diagnostic}'}}}};"
        )
    else:
        mocks = (
            "function Get-ProductionCandidateProvenanceResult{"
            "return [pscustomobject]@{state='PASSED'}};"
            "function Invoke-Utf8NativeProcess{return [pscustomobject]@{exit_code=1;stdout='';"
            f"stderr='{diagnostic}';stdout_lines=@();stderr_lines=@('{diagnostic}')}}}};"
        )
    result = _run_control_center_contract(
        tmp_path,
        setup + mocks
        + "$message='';try{Approve-CandidateCompatibility|Out-Null}catch{$message=$_.Exception.Message};"
        "$after=Get-Content $releaseControlStatePath -Raw;$final=Get-ReleaseControlState;"
        'Write-Output "$($message.Contains(\'APPROVAL_RETRYABLE\')),'
        '$($message.Contains(\'' + reason + '\')),$($before -eq $after),'
        '$($null -eq $final.candidate.compatibility_approval),'
        '$($final.candidate.validation_state),$($final.candidate.compatibility_state),'
        '$($final.candidate.validation.windows),'
        '$($final.candidate.validation.key -eq $final.candidate.validation_key)"',
    )
    assert result == "True,True,True,True,REVIEW_REQUIRED,REVIEW_REQUIRED,PASSED,True"


def test_compatibility_approval_deterministic_provenance_failure_is_rejected(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.compatibility_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "reason='PLATFORM_CONFIG_REVIEW_REQUIRED';resources_verified=$true;windows='PASSED'};"
        "Write-ReleaseControlState $state;$before=Get-Content $releaseControlStatePath -Raw;"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{"
        "state='FAILED';reason='PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED'}};"
        "$message='';try{Approve-CandidateCompatibility|Out-Null}catch{$message=$_.Exception.Message};"
        "$after=Get-Content $releaseControlStatePath -Raw;"
        'Write-Output "$($message.Contains(\'APPROVAL_REJECTED\')),'
        '$($message.Contains(\'PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED\')),'
        '$($before -eq $after)"',
    )
    assert result == "True,True,True"


def test_storage_or_resource_failure_cannot_use_compatibility_approval(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='REVIEW_REQUIRED';"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "reason='COORDINATED_STORAGE_MIGRATION_REQUIRED';resources_verified=$true};"
        "Write-ReleaseControlState $state;"
        "try{Approve-CandidateCompatibility|Out-Null;'APPROVED'}catch{'BLOCKED'}",
    )
    assert result == "BLOCKED"


def test_required_github_gate_set_is_exact_and_missing_gate_stays_pending(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$runs=@($requiredGitHubChecks | ForEach-Object { [pscustomobject]@{"
        "id=1;name=$_;head_sha=('a'*40);started_at='2026-08-23T01:00:00Z';"
        "status='completed';conclusion='success'} });"
        "$script:payload=[pscustomobject]@{check_runs=$runs}|ConvertTo-Json -Depth 5;"
        "function Invoke-Utf8NativeProcess{return [pscustomobject]@{exit_code=0;stdout=$script:payload;"
        "stderr='';stdout_lines=@($script:payload);stderr_lines=@()}};"
        "$all=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$script:payload=[pscustomobject]@{check_runs=@($runs | Where-Object name -ne 'Web build and tests')}|ConvertTo-Json -Depth 5;"
        "$missing=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$runs[0].conclusion='failure';$script:payload=[pscustomobject]@{check_runs=$runs}|ConvertTo-Json -Depth 5;"
        "$failed=Test-RequiredGitHubChecks -Revision ('a'*40);"
        'Write-Output "$all,$missing,$failed"',
    )
    assert result == "PASSED,PENDING,CHECKS_BLOCKED"


def test_required_github_gate_uses_latest_exact_sha_attempt(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$base=@($requiredGitHubChecks | ForEach-Object { [pscustomobject]@{"
        "id=1;name=$_;head_sha=('a'*40);started_at='2026-08-23T01:00:00Z';"
        "status='completed';conclusion='success'} });"
        "$old=[pscustomobject]@{id=2;name=$requiredGitHubChecks[0];head_sha=('a'*40);"
        "started_at='2026-08-23T02:00:00Z';status='completed';conclusion='cancelled'};"
        "$new=[pscustomobject]@{id=3;name=$requiredGitHubChecks[0];head_sha=('a'*40);"
        "started_at='2026-08-23T03:00:00Z';status='completed';conclusion='success'};"
        "$script:payload=[pscustomobject]@{check_runs=@($base)+@($old,$new)}|ConvertTo-Json -Depth 5;"
        "function Invoke-Utf8NativeProcess{return [pscustomobject]@{exit_code=0;stdout=$script:payload;"
        "stderr='';stdout_lines=@($script:payload);stderr_lines=@()}};"
        "$recovered=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$new.status='in_progress';$new.conclusion=$null;"
        "$script:payload=[pscustomobject]@{check_runs=@($base)+@($old,$new)}|ConvertTo-Json -Depth 5;"
        "$pending=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$new.head_sha=('b'*40);"
        "$script:payload=[pscustomobject]@{check_runs=@($base)+@($old,$new)}|ConvertTo-Json -Depth 5;"
        "$wrongSha=Test-RequiredGitHubChecks -Revision ('a'*40);"
        'Write-Output "$recovered,$pending,$wrongSha"',
    )
    assert result == "PASSED,PENDING,CHECKS_BLOCKED"


@pytest.mark.parametrize(
    ("operation", "diagnostic", "expected"),
    (
        ("FETCH_ORIGIN", "fatal: operation timed out", True),
        ("FETCH_ORIGIN", "Failed to connect to github.com port 443", True),
        ("FETCH_ORIGIN", "Temporary failure in name resolution", True),
        ("FETCH_ORIGIN", "Connection reset by peer", True),
        ("FETCH_ORIGIN", "TLS handshake connection terminated", True),
        ("GITHUB_CHECKS_API", "HTTP 503 Service Unavailable", True),
        ("GITHUB_CHECKS_API", "HTTP 429 rate limit exceeded", True),
        ("GITHUB_CHECKS_API", "HTTP 403 API rate limit exceeded", True),
        ("GITHUB_CHECKS_API", "HTTP 403 forbidden", False),
        ("GITHUB_CHECKS_API", "HTTP 403 authentication failed rate limit", False),
        ("FETCH_ORIGIN", "Authentication failed for repository", False),
        ("FETCH_ORIGIN", "Permission denied", False),
        ("FETCH_ORIGIN", "fatal: couldn't find remote ref invalid", False),
        ("FETCH_ORIGIN", "Repository not found", False),
        ("LOCAL_DIFF", "Failed to connect to github.com port 443", False),
    ),
)
def test_repository_transport_failure_classifier_is_bounded(
    tmp_path, operation: str, diagnostic: str, expected: bool,
) -> None:
    escaped = diagnostic.replace("'", "''")
    result = _run_control_center_contract(
        tmp_path,
        f"Test-TransientExternalRepositoryFailure -Operation '{operation}' "
        f"-ExitCode 1 -Diagnostic '{escaped}'",
    )
    assert result == str(expected)


def test_github_cli_auth_and_invalid_payload_are_not_retryable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Invoke-Utf8NativeProcess{return [pscustomobject]@{exit_code=1;stdout='';"
        "stderr='HTTP 401 authentication failed';stdout_lines=@();"
        "stderr_lines=@('HTTP 401 authentication failed')}};"
        "$auth=Get-RequiredGitHubChecksResult -Revision ('a'*40);"
        "function Invoke-Utf8NativeProcess{return [pscustomobject]@{exit_code=0;stdout='not-json';"
        "stderr='';stdout_lines=@('not-json');stderr_lines=@()}};"
        "$invalid=Get-RequiredGitHubChecksResult -Revision ('a'*40);"
        'Write-Output "$($auth.state),$($auth.reason),$($invalid.state),$($invalid.reason)"',
    )
    assert result == (
        "FAILED,GITHUB_CHECKS_ACCESS_FAILED,"
        "FAILED,GITHUB_CHECKS_RESPONSE_INVALID"
    )


def test_repository_diagnostics_are_bounded_and_secret_safe(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$value=Protect-PreflightDiagnosticText "
        "'fatal: https://oauth-secret@github.com/repository timeout' -Limit 200;"
        "Write-Output $value",
    )
    assert result == "fatal: https://[REDACTED]@github.com/repository timeout"


def test_fetch_timeout_keeps_exact_candidate_retryable_and_non_promotable(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='NEW';"
        "$state.candidate.compatibility_state='PENDING';Write-ReleaseControlState $state;"
        "$candidate=$state.candidate;"
        "function Test-ProductionCandidateProvenance{"
        "$script:lastRepositoryValidationResult=[pscustomobject]@{"
        "state='REPOSITORY_PENDING';reason='REPOSITORY_TRANSPORT_UNAVAILABLE';"
        "operation='FETCH_ORIGIN';exit_code=128;diagnostic='fatal: operation timed out'};"
        "return $false};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$saved=Get-ReleaseControlState;$view=Get-ControlCenterReleasePresentation $saved;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$($saved.candidate.validation_state),'
        '$($saved.candidate.validation.reason),$($saved.candidate.validation.windows),'
        '$($saved.candidate.validation.repository_retryable),$($view.can_promote),'
        '$($saved.candidate.validation_key -eq $candidate.validation_key),'
        '$($history.Contains(\'CANDIDATE_REPOSITORY_PENDING\'))"',
    )
    assert result == (
        "CHECKS_PENDING,REPOSITORY_TRANSPORT_UNAVAILABLE,NOT_RUN,"
        "True,False,True,True"
    )


def test_same_key_recovers_after_github_transport_without_repeating_preflight(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + _mock_free_plan_and_qualification_authority()
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='NEW';"
        "$state.candidate.compatibility_state='PENDING';"
        "$state.candidate|Add-Member compatibility_approval ([pscustomobject]@{"
        "validation_key=$state.candidate.validation_key;resources_verified=$true});"
        "Write-ReleaseControlState $state;$candidate=$state.candidate;"
        "$script:githubAvailable=$false;$script:preflights=0;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{$script:preflights++;return $true};"
        "function Test-RequiredGitHubChecks{if($script:githubAvailable){'PASSED'}else{"
        "$script:lastGitHubChecksResult=[pscustomobject]@{state='REPOSITORY_PENDING';"
        "reason='GITHUB_TEMPORARILY_UNAVAILABLE';exit_code=1;"
        "diagnostic='HTTP 503 Service Unavailable'};'REPOSITORY_PENDING'}};"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$false;"
        "requires_validation=$false;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true;state='READY'}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{state='PASSED'}};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$pending=Get-ReleaseControlState;$script:githubAvailable=$true;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "function Reconcile-ReleaseControlState{};function Find-NewCandidateRelease{return $null};"
        "Invoke-CandidateDiscovery|Out-Null;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$($pending.candidate.validation_state),'
        '$($pending.candidate.validation.windows),$($final.candidate.validation_state),'
        '$script:preflights,$($final.candidate.validation_key -eq $candidate.validation_key),'
        '$($final.candidate.compatibility_approval.validation_key -eq $candidate.validation_key),'
        '$($history.Contains(\'CANDIDATE_REPOSITORY_PENDING\')),'
        '$($history.Contains(\'CANDIDATE_CHECKS_RECOVERED\'))"',
    )
    assert result == "CHECKS_PENDING,PASSED,PASSED,1,True,True,True,True"


@pytest.mark.parametrize(
    ("same_validation_key", "expected_preflights"),
    ((True, 0), (False, 1)),
)
def test_approved_candidate_reuses_only_same_key_windows_preflight(
    tmp_path, same_validation_key: bool, expected_preflights: int,
) -> None:
    prior_key = (
        "$candidate.validation_key"
        if same_validation_key
        else "('stale:' + ('c'*40))"
    )
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + _mock_free_plan_and_qualification_authority()
        + _mock_free_plan_and_qualification_authority()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$state.candidate.validation_state='NEW';"
        "$state.candidate.compatibility_state='APPROVED';"
        f"$state.candidate.validation=[pscustomobject]@{{key={prior_key};"
        "repository='PASSED';windows='PASSED';cloudflare='PENDING'};"
        "$state.candidate|Add-Member -Force compatibility_approval ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;resources_verified=$true});"
        "Write-ReleaseControlState $state;$script:preflights=0;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{$script:preflights++;return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$false;"
        "requires_validation=$false;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true;state='READY'}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{state='PASSED'}};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$saved=Get-ReleaseControlState;"
        'Write-Output "$($saved.candidate.validation_state),$script:preflights"',
    )
    assert result == f"PASSED,{expected_preflights}"


def test_deterministic_provenance_failure_remains_terminal(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='NEW';"
        "Write-ReleaseControlState $state;$candidate=$state.candidate;"
        "function Test-ProductionCandidateProvenance{"
        "$script:lastRepositoryValidationResult=[pscustomobject]@{state='FAILED';"
        "reason='PRODUCTION_CANDIDATE_MAIN_REACHABILITY_REQUIRED'};return $false};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$saved=Get-ReleaseControlState;"
        'Write-Output "$($saved.candidate.validation_state),$($saved.candidate.validation.error)"',
    )
    assert result == "FAILED,PRODUCTION_CANDIDATE_MAIN_REACHABILITY_REQUIRED"


def test_checks_blocked_candidate_recovers_on_same_sha_without_duplicate_identity(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + _mock_free_plan_and_qualification_authority()
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='NEW';"
        "$state.candidate.compatibility_state='PENDING';Write-ReleaseControlState $state;"
        "$candidate=$state.candidate;$script:checksPass=$false;$script:preflights=0;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{$script:preflights++;return $true};"
        "function Test-RequiredGitHubChecks{if($script:checksPass){'PASSED'}else{'CHECKS_BLOCKED'}};"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$false;"
        "requires_validation=$false;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true;state='READY'}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{state='PASSED'}};"
        "Invoke-AutomaticCandidateValidation -Candidate $candidate|Out-Null;"
        "$blocked=Get-ReleaseControlState;$script:checksPass=$true;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "function Reconcile-ReleaseControlState{};function Find-NewCandidateRelease{return $null};"
        "Invoke-CandidateDiscovery|Out-Null;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        "$blockedEvents=([regex]::Matches($history,'CANDIDATE_CHECKS_BLOCKED')).Count;"
        "$recoveredEvents=([regex]::Matches($history,'CANDIDATE_CHECKS_RECOVERED')).Count;"
        'Write-Output "$($blocked.candidate.validation_state),$($blocked.candidate.validation.repository),'
        '$($final.candidate.validation_state),$script:preflights,$blockedEvents,$recoveredEvents,'
        '$($final.candidate.git_sha -eq $candidate.git_sha)"',
    )
    assert result == "CHECKS_BLOCKED,FAILED,PASSED,1,1,1,True"


def test_checks_blocked_presentation_is_retryable_but_not_promotable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='CHECKS_BLOCKED';"
        "$state.control_bundle_hash_verified=$true;$state.control_bundle_exact_revision=$true;"
        "$state.control_bundle_revision=('c'*40);"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "repository='FAILED';repository_retryable=$true;windows='PASSED';"
        "reason='REQUIRED_GITHUB_CHECKS_BLOCKED'};Write-ReleaseControlState $state;"
        "$view=Get-ControlCenterReleasePresentation -Release $state;"
        'Write-Output "$($view.candidate_state),$($view.can_promote),$($view.candidate_detail)"',
    )
    assert result == (
        "CHECKS_BLOCKED,False,Required GitHub checks failed and may be rerun "
        "for this exact SHA."
    )


def test_repository_pending_presentation_is_distinct_and_non_promotable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='CHECKS_PENDING';"
        "$state.control_bundle_hash_verified=$true;$state.control_bundle_exact_revision=$true;"
        "$state.control_bundle_revision=('c'*40);"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "repository='PENDING';repository_retryable=$true;windows='PASSED';"
        "reason='GITHUB_TEMPORARILY_UNAVAILABLE'};Write-ReleaseControlState $state;"
        "$view=Get-ControlCenterReleasePresentation -Release $state;"
        'Write-Output "$($view.candidate_state),$($view.can_promote),$($view.candidate_detail)"',
    )
    assert result == (
        "CHECKS_PENDING,False,GitHub temporarily unavailable. "
        "Retrying automatically."
    )


def test_production_candidate_requires_exact_current_main(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$sha=('b'*40);$candidate=New-ReleaseIdentity -GitSha $sha "
        "-WorkerVersionId 'worker' -WindowsRevision $sha -Branch 'feature' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$script:exact=$true;function Invoke-Utf8NativeProcess{param($Arguments);"
        "$stdout=if($Arguments -contains 'rev-parse'){if($script:exact){'b'*40}else{'c'*40}}else{''};"
        "return [pscustomobject]@{exit_code=0;stdout=$stdout;stderr='';"
        "stdout_lines=if($stdout){@($stdout)}else{@()};stderr_lines=@()}};"
        "$feature=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='PREVIEW';$preview=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='UNKNOWN';$unknown=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='PRODUCTION_CANDIDATE';"
        "$candidate.branch='main';$main=Test-ProductionCandidateProvenance $candidate;"
        "$script:exact=$false;$older=Test-ProductionCandidateProvenance $candidate;"
        'Write-Output "$feature,$preview,$unknown,$main,$older"',
    )
    assert result == "False,False,False,True,False"


@pytest.mark.parametrize(
    ("changed", "expected_state", "expected_mode"),
    (
        (
            "@('scripts/xauusd_control_center.ps1','scripts/check_deferred_projection_parity.py','scripts/access-qualification-contract.json','tests/test_runtime_launchers.py')",
            "PASSED",
            "CONTROL_PLANE_ONLY_MAIN_ADVANCE",
        ),
        ("@('web/app/api/status/route.ts')", "FAILED", ""),
        ("@('scripts/run_dashboard_sync.py')", "FAILED", ""),
    ),
)
def test_control_plane_only_main_movement_preserves_immutable_candidate_artifact(
    tmp_path, changed: str, expected_state: str, expected_mode: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$candidate=New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId '22222222-2222-4222-8222-222222222222' "
        "-WindowsRevision ('a'*40) -Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "function Invoke-RepositoryRead{[pscustomobject]@{passed=$true;failure_class='';exit_code=0}};"
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments)"
        "$op=[string]$Arguments[2];$stdout='';$lines=@();$exit=0;"
        "if($op -eq 'rev-parse'){$stdout=('b'*40);$lines=@($stdout)}"
        f"elseif($op -eq 'diff'){{$lines={changed};$stdout=$lines -join \"`n\"}}"
        "elseif($op -eq 'cat-file'){$stdout='ok';$lines=@('ok')}"
        "[pscustomobject]@{exit_code=$exit;stdout=$stdout;stderr='';"
        "stdout_lines=$lines;stderr_lines=@()}};"
        "$answer=Get-ProductionCandidateProvenanceResult $candidate;"
        'Write-Output "$($answer.state),$($answer.mode)"',
    )
    assert result == f"{expected_state},{expected_mode}"


def test_candidate_discovery_does_not_supersede_control_plane_only_candidate(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("0" * 40, "a" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.branch='main';"
        "$state.candidate.validation_state='PLATFORM_PENDING';"
        "$state.candidate.validation=[pscustomobject]@{key=$state.candidate.validation_key;"
        "repository='PASSED';windows='PASSED';reason='PROVIDER_EVIDENCE_PENDING'};"
        "Write-ReleaseControlState $state;"
        "function Get-OriginMainRevision{return ('b'*40)};"
        "function Restore-ControlPlaneOnlySupersededCandidate{return $null};"
        "function Get-ProductionCandidateProvenanceResult{return [pscustomobject]@{"
        "state='PASSED';mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';"
        "current_main_git_sha=('b'*40)}};"
        "$script:listed=$false;function Get-CloudflareVersions{$script:listed=$true;"
        "[pscustomobject]@{id='new-version';metadata=[pscustomobject]@{"
        "created_on=[DateTimeOffset]::UtcNow.ToString('o')}}};"
        "$found=Find-NewCandidateRelease;$final=Get-ReleaseControlState;"
        'Write-Output "$($found.git_sha),$($found.validation_state),$script:listed,'
        '$($final.candidate_materialization.state),'
        '$($final.candidate_materialization.worker_version_id)"',
    )
    assert result == (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,PLATFORM_PENDING,True,"
        "PRESERVED,22222222-2222-4222-8222-222222222222"
    )


def test_unvalidated_control_plane_replacement_restores_exact_superseded_candidate(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("0" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$replacement=$state.candidate;"
        "$replacement.branch='main';$replacement.compatibility_state='PENDING';"
        "$replacement.validation_state='CHECKS_BLOCKED';"
        "$replacement.validation=[pscustomobject]@{key=$replacement.validation_key;"
        "reason='REQUIRED_GITHUB_CHECKS_BLOCKED'};Write-ReleaseControlState $state;"
        "$prior=New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' "
        "-WindowsRevision ('a'*40) -Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$prior.compatibility_state='PASSED';$prior.validation_state='PASSED';"
        "$prior.validation=[pscustomobject]@{key=$prior.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PASSED';"
        "data_parity=[pscustomobject]@{state='PASSED'};validation_run='run-kept';"
        "worker_qualification=[pscustomobject]@{key=('d'*64);"
        "candidate_worker_version=$prior.worker_version_id;candidate_git_sha=$prior.git_sha};"
        "cpu_evidence=[pscustomobject]@{qualification_key=('d'*64);"
        "qualification_receipt_digest=('e'*64)};"
        "auth_inspection=[pscustomobject]@{state='HUMAN_ACCESS_BOUNDARY_ACCEPTED'}};"
        "$prior|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$prior.validation_key;receipt_digest=('f'*64)});"
        "Write-ReleaseHistory -Event 'CANDIDATE_SUPERSEDED' -Release $prior "
        "-Detail @{replacement_key=$replacement.validation_key};"
        "function Get-ProductionCandidateProvenanceResult{param($Candidate,$VerifiedOriginMainRevision)"
        "return [pscustomobject]@{"
        "state='PASSED';mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';"
        "current_main_git_sha=('c'*40)}};"
        "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Get-CandidateChangedFiles{return @('web/drizzle/required.sql')};"
        "function Get-CandidateCompatibilityRequirement{[pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=@('web/drizzle/required.sql')}};"
        "function Ensure-CoordinatedMigrationQualification{[pscustomobject]@{"
        "root_receipt_digest=('f'*64)}};"
        "function Get-WorkerCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64);source_worker_version=$prior.worker_version_id;"
        "source_git_sha=$prior.git_sha}};"
        "function Assert-AccessBoundaryAcceptanceReceipt{[pscustomobject]@{"
        "receipt_digest=('1'*64)}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{[pscustomobject]@{passed=$true}};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$state.stable.worker_version_id;percentage=100})}};"
        "$restored=Restore-ControlPlaneOnlySupersededCandidate $state ('c'*40);"
        "$final=Get-ReleaseControlState;$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$($restored.worker_version_id),$($restored.validation.validation_run),'
        '$($final.candidate_materialization.state),'
        '$($final.candidate.migration_acceptance.receipt_digest),'
        '$($history.Contains(\'CANDIDATE_RECOVERED_THROUGH_SUPERSESSION_CHAIN\'))"',
    )
    assert result == (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa,run-kept,PRESERVED,"
        + "f" * 64 + ",True"
    )


def _supersession_chain_contract(scenario: str) -> str:
    setup = {
        "two_hop": "Add-Edge $mid $head;Add-Edge $qualified $mid;",
        "four_hop": (
            "Add-Edge $mid $head;Add-Edge $mid2 $mid;"
            "Add-Edge $mid3 $mid2;Add-Edge $qualified $mid3;"
        ),
        "first_qualified": (
            "Add-Edge $qualified $head;Add-Edge $qualified2 $qualified;"
        ),
        "wrong_identity": (
            "$mid.windows_revision=('f'*40);Add-Edge $mid $head;"
            "Add-Edge $qualified $mid;"
        ),
        "wrong_ancestry": (
            "$script:ancestryInvalid=$mid.validation_key;Add-Edge $mid $head;"
            "Add-Edge $qualified $mid;"
        ),
        "missing_edge": "Add-Edge $mid $head;",
        "edge_outside_tail": (
            "Add-Edge $qualified $mid;"
            "for($i=0;$i -lt 127;$i++){Write-ReleaseHistory "
            "-Event 'UNRELATED_DIAGNOSTIC' -Release $null -Detail @{index=$i}};"
            "Add-Edge $mid $head;"
        ),
        "old_schema_missing_edge": (
            "$old=[pscustomobject]@{schema_version='release-history-event-v1';"
            "event='CANDIDATE_SUPERSEDED';release=$mid;"
            "detail=[pscustomobject]@{replacement_key=$head.validation_key}};"
            "Add-ControlCenterUtf8Line -Path $releaseHistoryPath "
            "-Line ($old|ConvertTo-Json -Compress -Depth 12) "
            "-MaximumBytes $releaseHistoryMaximumEventBytes;"
        ),
        "duplicate_edge": "Add-Edge $mid $head;Add-Edge $mid2 $head;",
        "cycle": "Add-Edge $mid $head;Add-Edge $head $mid;",
        "self_loop": "Add-Edge $head $head;",
        "unrelated": "Add-Edge $qualified $mid;",
        "receipt_mismatch": (
            "$script:cpuReceiptInvalid=$true;"
            "Add-Edge $qualified $head;"
        ),
        "reused_receipt": (
            "$script:reusedCpu=$true;"
            "$qualified.validation.cpu_evidence|Add-Member -Force qualification_mode "
            "'CPU_QUALIFICATION_REUSED';"
            "$qualified.validation.cpu_evidence|Add-Member -Force source_worker_version "
            "'11111111-1111-4111-8111-111111111111';"
            "$qualified.validation.cpu_evidence|Add-Member -Force source_git_sha ('1'*40);"
            "$qualified.validation.cpu_evidence|Add-Member -Force worker_version_id "
            "$qualified.worker_version_id;"
            "$qualified.validation.cpu_evidence|Add-Member -Force candidate_git_sha "
            "$qualified.git_sha;"
            "Add-Edge $qualified $head;"
        ),
        "reused_receipt_wrong_current": (
            "$script:reusedCpu=$true;"
            "$qualified.validation.cpu_evidence|Add-Member -Force qualification_mode "
            "'CPU_QUALIFICATION_REUSED';"
            "$qualified.validation.cpu_evidence|Add-Member -Force source_worker_version "
            "'11111111-1111-4111-8111-111111111111';"
            "$qualified.validation.cpu_evidence|Add-Member -Force source_git_sha ('1'*40);"
            "$qualified.validation.cpu_evidence|Add-Member -Force worker_version_id "
            "$head.worker_version_id;"
            "$qualified.validation.cpu_evidence|Add-Member -Force candidate_git_sha "
            "$qualified.git_sha;"
            "Add-Edge $qualified $head;"
        ),
        "qualification_key_mismatch": (
            "$qualified.validation.cpu_evidence.qualification_key=('a'*64);"
            "Add-Edge $qualified $head;"
        ),
        "accepted_intermediate": (
            "Add-Edge $mid $head;Write-ReleaseHistory -Event 'CANDIDATE_PASSED' "
            "-Release $mid;Add-Edge $qualified $mid;"
        ),
        "partially_validated_head": (
            "$head.compatibility_state='COORDINATED_STORAGE_MIGRATION_PASSED';"
            "$head.validation_state='PLATFORM_PENDING';"
            "$head.validation.reason='WORKER_CPU_DIRECTED_LEDGER_IN_PROGRESS';"
            "$head|Add-Member -Force migration_acceptance ([pscustomobject]@{"
            "validation_key=$head.validation_key;receipt_digest=('7'*64)});"
            "Write-ReleaseHistory -Event 'COORDINATED_STORAGE_MIGRATION_PASSED' "
            "-Release $head;Add-Edge $qualified $head;"
        ),
        "observation_failed": (
            "$observed=New-Intermediate ('6'*40) "
            "'66666666-6666-4666-8666-666666666666';"
            "$observed.compatibility_state='PASSED';"
            "$observed.validation_state='FAILED';"
            "$observed.validation=[pscustomobject]@{key=$observed.validation_key;"
            "error='OBSERVATION_FAILED';"
            "reason='DEFERRED_PROJECTION_OBSERVATION_TIMEOUT';"
            "prior_validation=[pscustomobject]@{state='PASSED'}};"
            "$observed|Add-Member -Force migration_acceptance ([pscustomobject]@{"
            "validation_key=$observed.validation_key;receipt_digest=('e'*64)});"
            "function Restore-ControlPlaneObservationFailedCandidate{"
            "param($State,$MainRevision)return $State.candidate};"
            "Add-Edge $observed $head;"
        ),
        "worker_reused": (
            "$sameWorker=New-Intermediate ('c'*40) $mid.worker_version_id;"
            "Add-Edge $mid $head;Add-Edge $sameWorker $mid;"
        ),
        "max_depth": (
            "$successor=$head;for($i=1;$i -le 17;$i++){"
            "$sha=('{0:x}' -f $i).PadLeft(40,[char]'0');"
            "$worker=('{0:x8}-0000-4000-8000-{0:x12}' -f $i);"
            "$node=New-Intermediate $sha $worker;Add-Edge $node $successor;"
            "$successor=$node};"
        ),
        "byte_bound": (
            "Add-Edge $mid $head;Add-Edge $qualified $mid;"
            "$script:candidateSupersessionHistoryByteLimit=64;"
        ),
    }[scenario]
    return (
        _authorized_candidate("0" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$head=$state.candidate;"
        "$head.branch='main';$head.compatibility_state='REVIEW_REQUIRED';"
        "$head.validation_state='REVIEW_REQUIRED';$head.validation=[pscustomobject]@{"
        "key=$head.validation_key;repository='PASSED';windows='PASSED';"
        "cloudflare='PENDING';reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'};"
        "Write-ReleaseControlState $state;"
        "function New-Intermediate($sha,$worker){$item=New-ReleaseIdentity -GitSha $sha "
        "-WorkerVersionId $worker -WindowsRevision $sha -Branch 'main' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';$item.compatibility_state='PENDING';"
        "$item.validation_state='TESTING';$item.validation=[pscustomobject]@{"
        "key=$item.validation_key;repository='PASSED';windows='PASSED';"
        "cloudflare='PENDING';reason='REQUIRED_GITHUB_CHECKS_PENDING'};return $item};"
        "function Set-Qualified($item){$item.compatibility_state='PASSED';"
        "$item.validation_state='PASSED';$item.validation=[pscustomobject]@{"
        "key=$item.validation_key;repository='PASSED';windows='PASSED';cloudflare='PASSED';"
        "data_parity=[pscustomobject]@{state='PASSED'};worker_qualification=[pscustomobject]@{"
        "key=('d'*64);candidate_worker_version=$item.worker_version_id;"
        "candidate_git_sha=$item.git_sha};cpu_evidence=[pscustomobject]@{"
        "qualification_key=('d'*64);qualification_receipt_digest=('e'*64)};"
        "auth_inspection=[pscustomobject]@{state='HUMAN_ACCESS_BOUNDARY_ACCEPTED'}};"
        "$item|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$item.validation_key;receipt_digest=('e'*64)});return $item};"
        "$mid=New-Intermediate ('a'*40) 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';"
        "$mid2=New-Intermediate ('c'*40) 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';"
        "$mid3=New-Intermediate ('d'*40) 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';"
        "$qualified=Set-Qualified (New-Intermediate ('e'*40) "
        "'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');"
        "$qualified2=Set-Qualified (New-Intermediate ('f'*40) "
        "'ffffffff-ffff-4fff-8fff-ffffffffffff');"
        "function Add-Edge($prior,$successor){Write-ReleaseHistory "
        "-Event 'CANDIDATE_SUPERSEDED' -Release $prior "
        "-Detail @{replacement_key=$successor.validation_key}};"
        f"{setup}"
        "function Get-ProductionCandidateProvenanceResult{param($Candidate,$VerifiedOriginMainRevision)"
        "[pscustomobject]@{"
        "state='PASSED';mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';"
        "current_main_git_sha=('9'*40)}};"
        "function Test-CandidateSupersessionAncestry{param($Predecessor,$Successor,$MainRevision)"
        "return [bool]($Predecessor.validation_key -ne $script:ancestryInvalid)};"
        "function Test-PreservedCandidateEvidenceAvailable{param($Candidate)"
        "return [bool]($Candidate.validation_state -eq 'PASSED' -and "
        "$Candidate.validation_key -ne $script:invalidEvidenceKey)};"
        "function Get-CandidateChangedFiles{return @('web/drizzle/required.sql')};"
        "function Get-CandidateCompatibilityRequirement{[pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=@('web/drizzle/required.sql')}};"
        "function Ensure-CoordinatedMigrationQualification{param($Candidate,$Stable,$MigrationFiles)"
        "[pscustomobject]@{root_receipt_digest=('e'*64)}};"
        "function Get-WorkerCpuQualificationReceipt{param($QualificationKey)"
        "[pscustomobject]@{receipt_digest=$(if($script:cpuReceiptInvalid){('0'*64)}else{('e'*64)});"
        "source_worker_version=$(if($script:reusedCpu){"
        "'11111111-1111-4111-8111-111111111111'}else{$qualified.worker_version_id});"
        "source_git_sha=$(if($script:reusedCpu){('1'*40)}else{$qualified.git_sha})}};"
        "function Assert-AccessBoundaryAcceptanceReceipt{param($Candidate,$Stable)"
        "[pscustomobject]@{receipt_digest=('1'*64)}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{[pscustomobject]@{passed=$true}};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$state.stable.worker_version_id;percentage=100})}};"
        "try{$restored=Restore-ControlPlaneOnlySupersededCandidate $state ('9'*40);"
        "if($restored){Write-Output ('FOUND:'+ $restored.validation_key)}"
        "else{Write-Output 'NONE'}}catch{Write-Output ('ERROR:'+ $_.Exception.Message)}"
    )


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("two_hop", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
        ("four_hop", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
        ("first_qualified", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
        ("wrong_identity", "ERROR:CANDIDATE_SUPERSESSION_EDGE_IDENTITY_INVALID"),
        ("wrong_ancestry", "ERROR:CANDIDATE_SUPERSESSION_ANCESTRY_INVALID"),
        ("missing_edge", "NONE"),
        ("edge_outside_tail", "NONE"),
        ("old_schema_missing_edge", "NONE"),
        ("duplicate_edge", "ERROR:CANDIDATE_SUPERSESSION_EDGE_AMBIGUOUS"),
        ("cycle", "ERROR:CANDIDATE_SUPERSESSION_CYCLE"),
        ("self_loop", "ERROR:CANDIDATE_SUPERSESSION_SELF_LOOP"),
        ("unrelated", "NONE"),
        ("receipt_mismatch", "ERROR:CANDIDATE_SUPERSESSION_QUALIFICATION_REUSE_INVALID:CANDIDATE_SUPERSESSION_CPU_RECEIPT_INVALID"),
        ("reused_receipt", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
        ("reused_receipt_wrong_current", "ERROR:CANDIDATE_SUPERSESSION_QUALIFICATION_REUSE_INVALID:CANDIDATE_SUPERSESSION_CPU_REUSE_LINEAGE_INVALID"),
        ("qualification_key_mismatch", "ERROR:CANDIDATE_SUPERSESSION_INTERMEDIATE_UNSAFE"),
        ("accepted_intermediate", "ERROR:CANDIDATE_SUPERSESSION_INTERMEDIATE_UNSAFE"),
        ("partially_validated_head", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
        ("observation_failed", "FOUND:66666666-6666-4666-8666-666666666666:" + "6" * 40),
        ("worker_reused", "ERROR:CANDIDATE_SUPERSESSION_WORKER_REUSED"),
        ("max_depth", "ERROR:CANDIDATE_SUPERSESSION_MAX_DEPTH_EXCEEDED"),
        ("byte_bound", "ERROR:CANDIDATE_SUPERSESSION_HISTORY_BYTE_BOUND_EXCEEDED"),
    ],
)
def test_supersession_chain_recovery_is_bounded_and_fail_closed(
    tmp_path, scenario: str, expected: str,
) -> None:
    assert _run_control_center_contract(
        tmp_path, _supersession_chain_contract(scenario),
    ) == expected


@pytest.mark.parametrize("scenario,expected", (
    ("two_hop", "FOUND:eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee:" + "e" * 40),
    ("missing_edge", "NONE"),
))
@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_supersession_chain_recovery_has_powershell_runtime_parity(
    tmp_path, powershell: str, scenario: str, expected: str,
) -> None:
    if not shutil.which(powershell):
        pytest.skip(f"{powershell} is not installed")
    assert _run_control_center_contract(
        tmp_path, _supersession_chain_contract(scenario), powershell=powershell,
    ) == expected


def test_supersession_head_without_edge_is_not_applicable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("0" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$head=$state.candidate;"
        "$head.compatibility_state='PENDING';$head.validation_state='CHECKS_PENDING';"
        "$head.validation=[pscustomobject]@{key=$head.validation_key};"
        "Write-ReleaseHistory -Event 'UNRELATED_DIAGNOSTIC' -Release $null;"
        "$plan=Get-CandidateSupersessionRecoveryPlan $head ('b'*40);"
        'Write-Output "$($plan.state),$($plan.reason)"',
    )
    assert result == "NOT_APPLICABLE,CANDIDATE_SUPERSESSION_CHAIN_NOT_FOUND"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_unavailable_supersession_reuse_falls_back_once_without_copying_evidence(
    tmp_path, powershell: str,
) -> None:
    if not shutil.which(powershell):
        pytest.skip(f"{powershell} is not installed")
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("0" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$head=$state.candidate;"
        "$head.branch='main';$head.compatibility_state='PENDING';"
        "$head.validation_state='CHECKS_PENDING';"
        "$head.validation=[pscustomobject]@{key=$head.validation_key};"
        "$state.candidate_discovery.initialized_at='2026-09-03T00:00:00Z';"
        "$state.candidate_discovery.watermark_created_at='2026-09-03T00:00:00Z';"
        "$state.candidate_discovery.watermark_version_id='older';"
        "Write-ReleaseControlState $state;"
        "$prior=New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' "
        "-WindowsRevision ('a'*40) -Branch 'main' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$prior.compatibility_state='PENDING';$prior.validation_state='TESTING';"
        "$prior.validation=[pscustomobject]@{key=$prior.validation_key;"
        "cpu_evidence=[pscustomobject]@{receipt_digest=('e'*64)}};"
        "Write-ReleaseHistory -Event 'CANDIDATE_SUPERSEDED' -Release $prior "
        "-Detail @{replacement_key=$head.validation_key};"
        "function Get-OriginMainRevision{return ('b'*40)};"
        "function Get-ProductionCandidateProvenanceResult{"
        "[pscustomobject]@{state='PASSED';mode='EXACT_MAIN';"
        "current_main_git_sha=('b'*40)}};"
        "function Test-CandidateSupersessionAncestry{return $true};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$state.stable.worker_version_id;percentage=100})}};"
        "function Get-CloudflareVersions{@([pscustomobject]@{id=$head.worker_version_id;"
        "metadata=[pscustomobject]@{created_on='2026-09-03T01:00:00Z'};"
        "annotations=[pscustomobject]@{'workers/message'="
        "('release:'+('b'*40)+' branch:main artifact_kind:PRODUCTION_CANDIDATE')}})};"
        "$script:pointerCalls=0;function Set-CloudflareCandidatePointer{$script:pointerCalls++};"
        "$script:validationCalls=0;function Invoke-AutomaticCandidateValidation{"
        "param($Candidate)$script:validationCalls++;$s=Get-ReleaseControlState;"
        "$s.candidate.validation_state='REVIEW_REQUIRED';"
        "$s.candidate.validation=[pscustomobject]@{key=$s.candidate.validation_key;"
        "reason='HUMAN_REVIEW_REQUIRED'};Write-ReleaseControlState $s;return $true};"
        "function Enter-ReleaseTransactionLock{return $true};"
        "function Exit-ReleaseTransactionLock{};function Reconcile-ReleaseControlState{};"
        "$null=Invoke-CandidateDiscovery;$null=Invoke-CandidateDiscovery;"
        "$final=Get-ReleaseControlState;"
        "$events=@(Get-Content -LiteralPath $releaseHistoryPath|ForEach-Object{"
        "$_|ConvertFrom-ReleaseControlJson});"
        "$diagnostics=@($events|Where-Object{"
        "$_.event -eq 'CANDIDATE_SUPERSESSION_REUSE_UNAVAILABLE' -and "
        "$_.detail.chain_head -eq $head.validation_key -and "
        "$_.detail.reason -eq 'CANDIDATE_SUPERSESSION_REUSE_PREDECESSOR_UNAVAILABLE' -and "
        "$_.detail.current_main -eq ('b'*40)});"
        "$hasCpu=[bool]$final.candidate.validation.PSObject.Properties['cpu_evidence'];"
        'Write-Output "$script:validationCalls,$script:pointerCalls,'
        '$($final.candidate.worker_version_id),$($final.candidate.git_sha),'
        '$($final.candidate.validation_state),$($diagnostics.Count),$hasCpu,'
        '$($null -eq $final.transaction)"',
        powershell=powershell,
    )
    assert result == (
        "1,0,22222222-2222-4222-8222-222222222222,"
        + "b" * 40 + ",REVIEW_REQUIRED,1,False,True"
    )


def test_observe_probe_failure_restores_exact_qualification_and_preserves_attempt(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;$target=$candidate;"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='migration-kept'});"
        "$prior=[pscustomobject]@{key=$candidate.validation_key;repository='PASSED';"
        "windows='PASSED';cloudflare='PASSED';validation_run='run-kept';"
        "worker_qualification=[pscustomobject]@{key=('d'*64);"
        "candidate_worker_version=$candidate.worker_version_id;candidate_git_sha=$candidate.git_sha};"
        "cpu_evidence=[pscustomobject]@{qualification_key=('d'*64);"
        "qualification_receipt_digest=('e'*64);"
        "qualification_mode='CPU_QUALIFICATION_REUSED';"
        "source_worker_version='11111111-1111-4111-8111-111111111111';"
        "source_git_sha=('1'*40);worker_version_id=$candidate.worker_version_id;"
        "candidate_git_sha=$candidate.git_sha};data_parity=[pscustomobject]@{"
        "state='PASSED_WITH_DEFERRED_OBLIGATIONS';marker='parity-kept'};"
        "auth_inspection=[pscustomobject]@{state='ACCESS_QUALIFICATION_REUSED'}};"
        "$candidate.validation_state='FAILED';$candidate.validation=[pscustomobject]@{"
        "key=$candidate.validation_key;error='OBSERVATION_FAILED';"
        "reason='DEFERRED_PROJECTION_OBSERVATION_TIMEOUT';prior_validation=$prior;"
        "deferred_projection_evidence=[pscustomobject]@{state='PENDING';attempts=9};"
        "tested_at='2026-08-31T01:02:03+00:00'};"
        "$mid=New-ReleaseIdentity -GitSha ('c'*40) -WorkerVersionId "
        "'cccccccc-cccc-4ccc-8ccc-cccccccccccc' -WindowsRevision ('c'*40) "
        "-Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$mid.validation_state='TESTING';$mid.validation=[pscustomobject]@{"
        "key=$mid.validation_key;repository='PASSED';windows='PASSED';cloudflare='PENDING'};"
        "$head=New-ReleaseIdentity -GitSha ('d'*40) -WorkerVersionId "
        "'dddddddd-dddd-4ddd-8ddd-dddddddddddd' -WindowsRevision ('d'*40) "
        "-Branch 'main' -ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$head.compatibility_state='REVIEW_REQUIRED';$head.validation_state='REVIEW_REQUIRED';"
        "$head.validation=[pscustomobject]@{key=$head.validation_key;repository='PASSED';"
        "windows='PASSED';cloudflare='PENDING';reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'};"
        "$state.candidate=$head;Write-ReleaseControlState $state;"
        "Write-ReleaseHistory -Event 'CANDIDATE_SUPERSEDED' -Release $mid "
        "-Detail @{replacement_key=$head.validation_key};"
        "Write-ReleaseHistory -Event 'CANDIDATE_SUPERSEDED' -Release $target "
        "-Detail @{replacement_key=$mid.validation_key};"
        "function Get-ProductionCandidateProvenanceResult{param($Candidate,$VerifiedOriginMainRevision)"
        "[pscustomobject]@{"
        "state='PASSED';mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';"
        "current_main_git_sha=('f'*40)}};"
        "function Test-CandidateSupersessionAncestry{return $true};"
        "function Get-CandidateChangedFiles{return @('web/drizzle/required.sql')};"
        "function Get-CandidateCompatibilityRequirement{[pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=@('web/drizzle/required.sql')}};"
        "function Ensure-CoordinatedMigrationQualification{[pscustomobject]@{"
        "root_receipt_digest='migration-kept'}};"
        "function Read-WorkerCpuRunArtifact{[pscustomobject]@{validation_run='run-kept'}};"
        "function Get-CloudflareVersionDetails{param($VersionId)[pscustomobject]@{id=$VersionId}};"
        "function Get-ReleaseGitShaFromVersion{return ('b'*40)};"
        "function Get-ReleaseArtifactKindFromVersion{return 'PRODUCTION_CANDIDATE'};"
        "function Get-WorkerCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64);"
        "source_worker_version='11111111-1111-4111-8111-111111111111';"
        "source_git_sha=('1'*40)}};"
        "function Assert-AccessQualificationReuseReceipt{return [pscustomobject]@{state='PASSED'}};"
        "$script:pointerCalls=0;function Set-CloudflareCandidatePointer{$script:pointerCalls++};"
        "function Wait-CandidatePlacementPropagation{[pscustomobject]@{passed=$true}};"
        "function Get-CloudflareDeployment{[pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id=$state.stable.worker_version_id;percentage=100})}};"
        "$restored=Restore-ControlPlaneOnlySupersededCandidate $state ('f'*40);"
        "$final=Get-ReleaseControlState;$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$($restored.validation_state),$($restored.validation.data_parity.marker),'
        '$($final.candidate.last_release_attempt.reason),'
        '$($final.candidate.last_release_attempt.deferred_projection_evidence.attempts),'
        '$script:pointerCalls,$($final.candidate_materialization.state),'
        '$($history.Contains(\'CANDIDATE_RELEASE_ATTEMPT_FAILURE_PRESERVED\')),'
        '$($history.Contains(\'CANDIDATE_RELEASE_ATTEMPT_RECOVERED\'))"',
    )
    assert result == (
        "PASSED,parity-kept,DEFERRED_PROJECTION_OBSERVATION_TIMEOUT,9,1,"
        "PRESERVED,True,True"
    )


@pytest.mark.parametrize(
    "unsafe_setup",
    (
        "$candidate.validation.reason='SEMANTIC_DATA_PARITY_FAILED';",
        "$candidate.validation.error='CANDIDATE_FAILED';",
        "$candidate.validation.prior_validation.key='wrong-key';",
        "$candidate.validation.prior_validation.cloudflare='FAILED';",
        "$candidate.migration_acceptance.validation_key='wrong-key';",
        "$script:provenanceMoved=$true;",
        "$script:providerMissing=$true;",
        "$script:cpuReceiptInvalid=$true;",
        "$script:accessInvalid=$true;",
        "$script:placementMissing=$true;",
    ),
)
def test_observe_probe_qualification_recovery_fails_closed_when_authority_moved(
    tmp_path, unsafe_setup: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key});"
        "$candidate.validation_state='FAILED';$candidate.validation=[pscustomobject]@{"
        "key=$candidate.validation_key;error='OBSERVATION_FAILED';"
        "reason='DEFERRED_PROJECTION_OBSERVATION_TIMEOUT';tested_at='failed-at';"
        "prior_validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PASSED';"
        "worker_qualification=[pscustomobject]@{key=('d'*64);"
        "candidate_worker_version=$candidate.worker_version_id;candidate_git_sha=$candidate.git_sha};"
        "cpu_evidence=[pscustomobject]@{qualification_key=('d'*64);"
        "qualification_receipt_digest=('e'*64)};"
        "data_parity=[pscustomobject]@{state='PASSED_WITH_DEFERRED_OBLIGATIONS'};"
        "auth_inspection=[pscustomobject]@{state='ACCESS_QUALIFICATION_REUSED'}}};"
        f"{unsafe_setup}Write-ReleaseControlState $state;"
        "function Get-ProductionCandidateProvenanceResult{[pscustomobject]@{"
        "state=$(if($script:provenanceMoved){'FAILED'}else{'PASSED'});"
        "mode='CONTROL_PLANE_ONLY_MAIN_ADVANCE';current_main_git_sha=('c'*40)}};"
        "function Get-CloudflareVersionDetails{param($VersionId)"
        "if($script:providerMissing){throw 'missing'};[pscustomobject]@{id=$VersionId}};"
        "function Get-ReleaseGitShaFromVersion{return ('b'*40)};"
        "function Get-ReleaseArtifactKindFromVersion{return 'PRODUCTION_CANDIDATE'};"
        "function Get-WorkerCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=$(if($script:cpuReceiptInvalid){'wrong'}else{('e'*64)});"
        "source_worker_version=$candidate.worker_version_id;"
        "source_git_sha=$candidate.git_sha}};"
        "function Assert-AccessQualificationReuseReceipt{"
        "if($script:accessInvalid){throw 'invalid'};[pscustomobject]@{state='PASSED'}};"
        "$script:pointerCalls=0;function Set-CloudflareCandidatePointer{$script:pointerCalls++};"
        "function Wait-CandidatePlacementPropagation{[pscustomobject]@{"
        "passed=(-not $script:placementMissing)}};"
        "$restored=Restore-ControlPlaneObservationFailedCandidate $state ('c'*40);"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$($null -eq $restored),$($final.candidate.validation_state),'
        '$script:pointerCalls,$($null -eq $final.candidate.last_release_attempt)"',
    )
    expected_pointer_calls = "1" if unsafe_setup == "$script:placementMissing=$true;" else "0"
    assert result == f"True,FAILED,{expected_pointer_calls},True"


def test_legacy_reference_evidence_is_readable_but_never_promotable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;"
        "$state.candidate.artifact_kind='LEGACY_REFERENCE';"
        "$state.candidate.validation_state='REBASE_REQUIRED';"
        "$state.candidate.validation=[pscustomobject]@{reason='REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED';"
        "cpu_evidence=[pscustomobject]@{samples=104;p95_cpu_ms=4;max_cpu_ms=5}};"
        "Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "$presentation=Get-ControlCenterReleasePresentation (Get-ReleaseControlState);"
        "try{Start-ReleasePromotion|Out-Null;$promoted=$true}catch{$promoted=$false};"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$($presentation.can_promote),$promoted,'
        '$($final.candidate.validation.cpu_evidence.samples),'
        '$($final.candidate.validation.reason)"',
    )
    assert result == (
        "False,False,104,REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED"
    )


def test_normal_release_control_never_applies_or_provisions_storage() -> None:
    control = _control_center_source()
    runbook = (ROOT / "docs" / "runbooks" / "CLOUDFLARE_DEPLOYMENT.md").read_text(
        encoding="utf-8"
    )
    assert "d1 migrations apply" not in control
    assert "--experimental-provision" not in control
    normal_commands = runbook.split("## Bootstrap", 1)[0]
    assert "d1 migrations apply" not in normal_commands


def test_review_required_is_terminal_for_exact_candidate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='REVIEW_REQUIRED';"
        "Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "function Reconcile-ReleaseControlState{};function Find-NewCandidateRelease{return $null};"
        "function Invoke-AutomaticCandidateValidation{throw 'must not retry'};"
        "$ok=Invoke-CandidateDiscovery;Write-Output $ok",
    )
    assert result == "True"


def test_explicit_cpu_review_retry_is_exact_audited_and_preserves_prior_gates(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='REVIEW_REQUIRED';"
        "reason='WORKER_CPU_HEADROOM_REVIEW_REQUIRED';tested_at='prior';"
        "validation_run='11111111-1111-1111-1111-111111111111';"
        "expected_requests=@([pscustomobject]@{request_id='request-1'});"
        "cpu_route_plan=[pscustomobject]@{worker_reads=@();worker_writes=@()};"
        "worker_qualification=[pscustomobject]@{key=('a'*64)}};"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='receipt'});"
        "Write-ReleaseControlState $state;$script:resumeState='';"
        "function Invoke-AutomaticCandidateValidation{param($Candidate)"
        "$current=Get-ReleaseControlState;$script:resumeState=($current.candidate.validation_state+'|'+"
        "$current.candidate.validation.reason);return $true};"
        "$ok=Retry-CandidateValidation;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$ok,$script:resumeState,$($final.candidate.validation_state),'
        '$($final.candidate.migration_acceptance.receipt_digest),'
        '$($history.Contains(\'WORKER_CPU_HEADROOM_REVIEW_REQUIRED\')),'
        '$($history.Contains(\'CANDIDATE_CPU_TARGETED_RETRY_REQUESTED\')),'
        '$($history.Contains(\'"full_matrix_replay":false\'))"',
    )
    assert result == (
        "True,PLATFORM_PENDING|PROVIDER_EVIDENCE_PENDING,PLATFORM_PENDING,"
        "receipt,True,True,True"
    )


def test_cpu_policy_key_movement_forces_fresh_cpu_evidence_and_preserves_migration(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$run='11111111-1111-1111-1111-111111111111';"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='REVIEW_REQUIRED';"
        "reason='WORKER_CPU_HEADROOM_REVIEW_REQUIRED';tested_at='prior';"
        "validation_run=$run;expected_requests=@([pscustomobject]@{request_id='request-1'});"
        "cpu_route_plan=[pscustomobject]@{worker_reads=@();worker_writes=@()};"
        "worker_qualification=[pscustomobject]@{key=('a'*64)}};"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='receipt'});"
        "$oldPlan=[pscustomobject]@{validation_run=$run;policy_version='worker-cpu-policy-v1';"
        "qualification_key=('a'*64);requests=@()};Write-WorkerCpuAtomicJson -Path "
        "(Join-Path (Get-WorkerCpuRunRoot $run) 'plan.json') -Value $oldPlan;"
        "Write-ReleaseControlState $state;$script:entry='';"
        "function Invoke-AutomaticCandidateValidation{param($Candidate)"
        "$current=Get-ReleaseControlState;$script:entry=($current.candidate.validation_state+'|' +"
        "$current.candidate.validation.reason);return $true};"
        "$ok=Retry-CandidateValidation;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$ok,$script:entry,$($final.candidate.migration_acceptance.receipt_digest),'
        '$($history.Contains(\'CANDIDATE_CPU_POLICY_MOVED\')),'
        '$($history.Contains(\'"qualification_reused":false\')),'
        '$($history.Contains(\'"fresh_cpu_matrix_required":true\'))"',
    )

    assert result == "True,NEW|CPU_QUALIFICATION_POLICY_MOVED,receipt,True,True,True"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_semantic_retry_preserves_exact_directed_cpu_and_windows_evidence(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _semantic_review_candidate()
        + "$script:semanticCalls=0;$script:cpuReceiptCalls=0;"
        "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Assert-CandidateCpuQualificationReceipt{param($Candidate,$Validation)"
        "$script:cpuReceiptCalls++;[pscustomobject]@{receipt_digest=('e'*64)}};"
        "function Test-CandidateDataParity{param($Stable,$Candidate,$RoutePlan)"
        "$script:semanticCalls++;[pscustomobject]@{passed=$true;state='PASSED';marker='fresh'}};"
        "function Get-CandidateAuthInspection{[pscustomobject]@{"
        "state='UNAUTHENTICATED_BOUNDARY_CONFIRMED'}};"
        "function Test-CandidateAuthBoundaryChanged{return $false};"
        "function Invoke-AutomaticCandidateValidation{throw 'broad retry forbidden'};"
        "$ok=Retry-CandidateValidation;$final=Get-ReleaseControlState;"
        "$history=Get-Content $releaseHistoryPath -Raw;"
        'Write-Output "$ok,$($final.candidate.validation_state),'
        '$($final.candidate.validation.reason),$script:semanticCalls,$script:cpuReceiptCalls,'
        '$($final.candidate.validation.validation_run),'
        '$($final.candidate.validation.directed_request_ledger.completed),'
        '$($final.candidate.validation.cpu_evidence.max_cpu_ms),'
        '$($final.candidate.migration_acceptance.receipt_digest),'
        '$($history.Contains(\'"directed_replayed":false\')),'
        '$($history.Contains(\'"cpu_replayed":false\'))"',
        powershell=powershell,
    )
    assert result == (
        "True,PASSED,SEMANTIC_DATA_PARITY_PASSED,1,1,"
        "11111111-1111-4111-8111-111111111111,12,7,migration-kept,True,True"
    )


def test_semantic_retry_remains_review_required_without_replaying_accepted_work(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _semantic_review_candidate()
        + "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Assert-CandidateCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64)}};"
        "function Test-CandidateDataParity{[pscustomobject]@{passed=$false;"
        "state='REVIEW_REQUIRED';reason='CANDIDATE_QUOTE_STALE'}};"
        "function Get-CandidateAuthInspection{[pscustomobject]@{state='NOT_REQUIRED'}};"
        "$ok=Retry-CandidateSemanticValidation;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.candidate.validation_state),'
        '$($final.candidate.validation.reason),'
        '$($final.candidate.validation.data_parity.reason),'
        '$($final.candidate.validation.directed_request_ledger.completed)"',
    )
    assert result == (
        "False,REVIEW_REQUIRED,SEMANTIC_DATA_PARITY_REVIEW_REQUIRED,"
        "CANDIDATE_QUOTE_STALE,12"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("$candidate.validation.key='wrong';", "SEMANTIC_RETRY_EXACT_REVIEW_EVIDENCE_REQUIRED"),
        ("$candidate.validation.cpu_evidence.passed=$false;", "SEMANTIC_RETRY_CPU_QUALIFICATION_INVALID"),
        ("$candidate.validation.directed_request_ledger=$null;", "SEMANTIC_RETRY_DIRECTED_LEDGER_INVALID"),
        ("$candidate.validation.directed_request_ledger.completed=11;", "SEMANTIC_RETRY_DIRECTED_LEDGER_INVALID"),
    ],
)
def test_semantic_retry_fails_closed_before_live_probe(
    tmp_path, mutation: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _semantic_review_candidate()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        + mutation
        + "Write-ReleaseControlState $state;$script:semanticCalls=0;"
        "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Assert-CandidateCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64)}};"
        "function Test-CandidateDataParity{$script:semanticCalls++;throw 'must not run'};"
        "$reason='';try{Retry-CandidateSemanticValidation|Out-Null}"
        "catch{$reason=$_.Exception.Message};"
        'Write-Output "$reason,$script:semanticCalls"',
    )
    assert result == f"{expected},0"


def test_semantic_retry_preserves_access_boundary_review_after_parity_passes(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _semantic_review_candidate()
        + "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Assert-CandidateCpuQualificationReceipt{[pscustomobject]@{"
        "receipt_digest=('e'*64)}};"
        "function Test-CandidateDataParity{[pscustomobject]@{passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{[pscustomobject]@{"
        "state='AUTH_BOUNDARY_NOT_TESTABLE'}};"
        "function Test-CandidateAuthBoundaryChanged{return $true};"
        "$ok=Retry-CandidateSemanticValidation;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.candidate.validation_state),'
        '$($final.candidate.validation.reason),'
        '$($final.candidate.validation.auth_inspection.state)"',
    )
    assert result == (
        "False,REVIEW_REQUIRED,ACCESS_BOUNDARY_REVIEW_REQUIRED,"
        "AUTH_BOUNDARY_NOT_TESTABLE"
    )


def test_semantic_retry_does_not_invent_cpu_requirement_for_non_worker_change(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _semantic_review_candidate()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation.worker_qualification=$null;"
        "$candidate.validation.cpu_evidence='NOT_REQUIRED';"
        "$candidate.validation.directed_request_ledger=$null;"
        "Write-ReleaseControlState $state;"
        "function Test-PreservedCandidateEvidenceAvailable{return $true};"
        "function Assert-CandidateCpuQualificationReceipt{throw 'CPU must not be required'};"
        "function Test-CandidateDataParity{[pscustomobject]@{passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{[pscustomobject]@{state='NOT_REQUIRED'}};"
        "function Test-CandidateAuthBoundaryChanged{return $false};"
        "$ok=Retry-CandidateSemanticValidation;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.candidate.validation.cpu_evidence),'
        '$($final.candidate.validation_state)"',
    )
    assert result == "True,NOT_REQUIRED,PASSED"


def test_explicit_review_retry_rejects_non_retryable_reason(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.validation_state='REVIEW_REQUIRED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';"
        "reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'};"
        "Write-ReleaseControlState $state;$diagnostic='';"
        "try{$null=Retry-CandidateValidation}catch{$diagnostic=$_.Exception.Message};"
        "Write-Output $diagnostic",
    )
    assert result == "Only an exact retryable Candidate review can restart validation."


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_access_approval_is_exact_idempotent_and_preserves_passed_evidence(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + "$first=Approve-CandidateAccessBoundary 'ALL_REQUIRED_ACCESS_CHECKS_PASSED';"
        "$firstDigest=$first.access_acceptance.receipt_digest;"
        "$second=Approve-CandidateAccessBoundary 'ALL_REQUIRED_ACCESS_CHECKS_PASSED';"
        "$final=Get-ReleaseControlState;$receipt=Assert-AccessBoundaryAcceptanceReceipt "
        "$final.candidate $final.stable;$history=Get-Content $releaseHistoryPath -Raw;"
        "$count=([regex]::Matches($history,'CANDIDATE_ACCESS_BOUNDARY_ACCEPTED')).Count;"
        'Write-Output "$($final.candidate.validation_state),'
        '$($final.candidate.compatibility_state),'
        '$($receipt.validation_key -eq $final.candidate.validation_key),'
        '$($receipt.checklist.owner_login_succeeds),'
        '$($receipt.checklist.owner_resource_accessible),'
        '$($receipt.checklist.unauthorized_access_denied),'
        '$($receipt.checklist.logout_succeeds),'
        '$($receipt.checklist.access_denied_after_logout),'
        '$($receipt.checklist.reauthentication_succeeds),'
        '$($final.candidate.validation.validation_run),'
        '$($final.candidate.validation.data_parity.marker),'
        '$($final.candidate.migration_acceptance.receipt_digest),'
        '$($firstDigest -eq $second.access_acceptance.receipt_digest),$count"',
        powershell=powershell,
    )
    assert result == (
        "PASSED,PASSED,True,True,True,True,True,True,True,kept-run,"
        "parity-kept,migration-kept,True,1"
    )


def test_unobservable_protected_host_enters_access_review_without_losing_gates(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$candidate.branch='main';$candidate.validation_state='NEW';"
        "$candidate.compatibility_state='PENDING';Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{"
        "state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{"
        "worker_cpu_required=$false;requires_validation=$false;static_assets=@();"
        "worker_reads=@();worker_writes=@();contract_routes=@([pscustomobject]@{"
        "path='/admin/api/session';auth_required=$true})}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{"
        "passed=$true;state='READY'}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{"
        "passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{"
        "state='AUTH_BOUNDARY_NOT_TESTABLE';"
        "versioned_workers_dev='UNPROTECTED_TEST_SURFACE'}};"
        "$ok=Invoke-AutomaticCandidateValidation $candidate;"
        "$final=Get-ReleaseControlState;$view=Get-ControlCenterReleasePresentation $final;"
        'Write-Output "$ok,$($final.candidate.validation_state),'
        '$($final.candidate.validation.reason),'
        '$($final.candidate.validation.repository),'
        '$($final.candidate.validation.windows),'
        '$($final.candidate.validation.cloudflare),$($view.can_approve_access),'
        '$($view.can_promote)"',
    )
    assert result == (
        "False,REVIEW_REQUIRED,ACCESS_BOUNDARY_REVIEW_REQUIRED,"
        "PASSED,PASSED,PASSED,True,False"
    )
@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$candidate.git_sha=('9'*40)", "ACCESS_RECEIPT_CANDIDATE_MISMATCH"),
        (
            "$candidate.worker_version_id='99999999-9999-4999-8999-999999999999'",
            "ACCESS_RECEIPT_CANDIDATE_MISMATCH",
        ),
        ("$candidate.validation_key='wrong-key'", "ACCESS_RECEIPT_MISSING"),
        (
            "$stable.worker_version_id='99999999-9999-4999-8999-999999999999'",
            "ACCESS_RECEIPT_STABLE_MISMATCH",
        ),
        ("$protectedDashboardUrl='https://other-protected.example'", "ACCESS_PROTECTED_HOST_INVALID"),
        (
            "$saved=Get-Content $accessBoundaryReceiptPath -Raw|ConvertFrom-ReleaseControlJson;"
            "$saved.accepted_at=[DateTimeOffset]::UtcNow.AddHours(-3).ToString('o');"
            "$saved.expires_at=([DateTimeOffset]$saved.accepted_at).AddHours(2).ToString('o');"
            "$core=[ordered]@{schema_version=$saved.schema_version;accepted_at=$saved.accepted_at;"
            "expires_at=$saved.expires_at;accepted_by=$saved.accepted_by;"
            "validation_key=$saved.validation_key;candidate=$saved.candidate;stable=$saved.stable;"
            "access_boundary=$saved.access_boundary;checklist=$saved.checklist};"
            "$saved.receipt_digest=Get-AccessBoundaryReceiptDigest $core;"
            "$saved|ConvertTo-Json -Depth 12|Set-Content $accessBoundaryReceiptPath",
            "ACCESS_RECEIPT_STALE",
        ),
        (
            "$saved=Get-Content $accessBoundaryReceiptPath -Raw|ConvertFrom-ReleaseControlJson;"
            "$saved.accepted_by='tampered';"
            "$saved|ConvertTo-Json -Depth 12|Set-Content $accessBoundaryReceiptPath",
            "ACCESS_RECEIPT_TAMPERED",
        ),
        (
            "$saved=Get-Content $accessBoundaryReceiptPath -Raw|ConvertFrom-ReleaseControlJson;"
            "$saved.checklist.PSObject.Properties.Remove('reauthentication_succeeds');"
            "$core=[ordered]@{schema_version=$saved.schema_version;accepted_at=$saved.accepted_at;"
            "expires_at=$saved.expires_at;accepted_by=$saved.accepted_by;"
            "validation_key=$saved.validation_key;candidate=$saved.candidate;stable=$saved.stable;"
            "access_boundary=$saved.access_boundary;checklist=$saved.checklist};"
            "$saved.receipt_digest=Get-AccessBoundaryReceiptDigest $core;"
            "$saved|ConvertTo-Json -Depth 12|Set-Content $accessBoundaryReceiptPath",
            "ACCESS_RECEIPT_CHECKLIST_INCOMPLETE:reauthentication_succeeds",
        ),
    ),
)
@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_access_receipt_rejects_wrong_identity_staleness_and_tampering(
    tmp_path, mutation: str, expected: str, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;$stable=$state.stable;"
        "$checklist=[ordered]@{owner_login_succeeds=$true;"
        "owner_resource_accessible=$true;unauthorized_access_denied=$true;"
        "logout_succeeds=$true;access_denied_after_logout=$true;"
        "reauthentication_succeeds=$true};"
        "$receipt=New-AccessBoundaryAcceptanceReceipt $candidate $stable $checklist;"
        "Write-AccessBoundaryAcceptanceReceipt $receipt;"
        "$accessBoundaryReceiptPath=Get-AccessBoundaryReceiptPath $receipt.validation_key;"
        f"{mutation};$reason='';try{{Assert-AccessBoundaryAcceptanceReceipt "
        "$candidate $stable -SkipCandidateStateBinding|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
        powershell=powershell,
    )
    assert result == expected


def test_access_approval_requires_explicit_complete_human_confirmation(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + "$reason='';try{Approve-CandidateAccessBoundary ''|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == "ACCESS_CHECKLIST_EXPLICIT_CONFIRMATION_REQUIRED"


def _access_provider_inspection_contract() -> str:
    return (
        "$now=[DateTimeOffset]::UtcNow;"
        "$provider=[pscustomobject]@{"
        "inspection_method='CLOUDFLARE_AUTHENTICATED_DASHBOARD_READ_ONLY';"
        "observed_at=$now.ToString('o');audit_window_start=$now.AddDays(-7).ToString('o');"
        "audit_window_end=$now.ToString('o');application_change_count=0;policy_change_count=0;"
        "policy_last_updated_at=$now.AddDays(-10).ToString('o');"
        "application_id='2f91233e-cabe-4f48-806c-83699de5e713';"
        "application_audience='4750fd9ae50ac47ae51d1d3605ca899e5603c691a7fe0c24457f3e335ed43ad1';"
        "application_name='XAUUSD Admin Owner';application_type='self_hosted';"
        "application_session_duration='24h';"
        "destinations=@('/admin*','/assistant','/retry-jobs','/status');"
        "policy_id='d8ce9484-aca9-4c39-a211-37d2fa8ba9cf';"
        "policy_name='Allow Assistant owner';policy_action='allow';policy_order=1;"
        "policy_rule_count=1;policy_session_duration='24h';"
        "owner_rule_sha256='8f9feab1a920b0921878048f9c54743d2f33e681a50e6ef05bdcbb50fc126759';"
        "identity_providers=@('google');mfa_required=$false;browser_isolation=$false;"
        "purpose_justification=$false;temporary_authentication=$false};"
    )


def _historical_access_authority_contract() -> str:
    return (
        "$state=Get-ReleaseControlState;$old=New-ReleaseIdentity -GitSha ('a'*40) "
        "-WorkerVersionId 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' "
        "-WindowsRevision ('a'*40) -ValidationState 'PASSED' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$checks=[ordered]@{owner_login_succeeds=$true;owner_resource_accessible=$true;"
        "unauthorized_access_denied=$true;logout_succeeds=$true;"
        "access_denied_after_logout=$true;reauthentication_succeeds=$true};"
        "$prior=New-AccessBoundaryAcceptanceReceipt $old $state.stable $checks;"
        "Write-AccessBoundaryAcceptanceReceipt $prior;"
        "Write-ReleaseHistory -Event 'CANDIDATE_ACCESS_BOUNDARY_ACCEPTED' -Release $old "
        "-Detail @{validation_key=$old.validation_key;receipt_digest=$prior.receipt_digest};"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_access_qualification_reuse_is_machine_evidence_and_preserves_other_gates(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + _historical_access_authority_contract()
        + _access_provider_inspection_contract()
        + "$inspection=Register-AccessProviderInspection $provider;"
        "function Get-AccessQualificationIdentity{param($GitSha,$ProviderInspection)"
        "[pscustomobject]@{access_qualification_key=('1'*64);core=[pscustomobject]@{"
        "protected_boundary=[pscustomobject]@{origin='https://aurum-signal-room.yiyousiow1234.workers.dev'};"
        "repository_artifacts=[ordered]@{auth='same'}}}};"
        "$reused=Invoke-CandidateAccessQualificationReuse;$final=Get-ReleaseControlState;"
        "$verified=Assert-AccessQualificationReuseReceipt $final.candidate;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        "$humanCount=([regex]::Matches($history,'CANDIDATE_ACCESS_BOUNDARY_ACCEPTED')).Count;"
        "$reuseCount=([regex]::Matches($history,'CANDIDATE_ACCESS_QUALIFICATION_REUSED')).Count;"
        'Write-Output "$($final.candidate.validation_state),'
        '$($final.candidate.validation.auth_inspection.state),'
        '$($verified.prior_access_key -eq $verified.current_access_key),'
        '$($verified.changed_access_artifacts.Count),'
        '$($final.candidate.validation.validation_run),'
        '$($final.candidate.validation.data_parity.marker),'
        '$($final.candidate.migration_acceptance.receipt_digest),$humanCount,$reuseCount"',
        powershell=powershell,
    )
    assert result == (
        "PASSED,ACCESS_QUALIFICATION_REUSED,True,0,kept-run,parity-kept,"
        "migration-kept,1,1"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$provider.policy_action='deny'", "ACCESS_PROVIDER_CONFIGURATION_CHANGED"),
        ("$provider.application_change_count=1", "ACCESS_PROVIDER_INSPECTION_INVALID"),
        ("$provider.inspection_method='DEPENDENCY_PROBE'", "ACCESS_PROVIDER_INSPECTION_INVALID"),
    ),
)
def test_access_provider_inspection_fails_closed(
    tmp_path, mutation: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_provider_inspection_contract()
        + f"{mutation};$reason='';try{{Register-AccessProviderInspection $provider|Out-Null}}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == expected


def test_access_qualification_key_change_requires_human_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + _historical_access_authority_contract()
        + _access_provider_inspection_contract()
        + "$null=Register-AccessProviderInspection $provider;"
        "function Get-AccessQualificationIdentity{param($GitSha,$ProviderInspection)"
        "$key=if($GitSha -eq ('a'*40)){('1'*64)}else{('2'*64)};"
        "[pscustomobject]@{access_qualification_key=$key;core=[pscustomobject]@{"
        "protected_boundary=[pscustomobject]@{origin='https://aurum-signal-room.yiyousiow1234.workers.dev'};"
        "repository_artifacts=[ordered]@{auth=$GitSha}}}};"
        "$reason='';try{Invoke-CandidateAccessQualificationReuse|Out-Null}"
        "catch{$reason=$_.Exception.Message};$final=Get-ReleaseControlState;"
        'Write-Output "$reason,$($final.candidate.validation_state),$($final.candidate.validation.reason)"',
    )
    assert result == (
        "ACCESS_QUALIFICATION_KEY_CHANGED,REVIEW_REQUIRED,ACCESS_BOUNDARY_REVIEW_REQUIRED"
    )


def _stale_access_reuse_ready_for_renewal(ttl_setup: str = "") -> str:
    return (
        _access_review_candidate()
        + _historical_access_authority_contract()
        + _access_provider_inspection_contract()
        + ttl_setup
        + "$inspection=Register-AccessProviderInspection $provider;"
        "function Get-AccessQualificationIdentity{param($GitSha,$ProviderInspection)"
        "[pscustomobject]@{access_qualification_key=('1'*64);core=[pscustomobject]@{"
        "protected_boundary=[pscustomobject]@{origin='https://aurum-signal-room.yiyousiow1234.workers.dev'};"
        "repository_artifacts=[ordered]@{auth='same'}}}};"
        "$null=Invoke-CandidateAccessQualificationReuse;$state=Get-ReleaseControlState;"
        "$candidate=$state.candidate;$reusePath=Get-AccessQualificationReuseReceiptPath $candidate.validation_key;"
        "$reuse=Get-Content $reusePath -Raw -Encoding UTF8|ConvertFrom-ReleaseControlJson;"
        "$staleAt=[DateTimeOffset]::UtcNow.AddHours(-3);"
        "$reuse.verified_at=$staleAt.ToString('o');"
        "$reuse.expires_at=$staleAt.Add($accessMachineReceiptMaxAge).ToString('o');"
        "$reuseCore=[ordered]@{schema_version=$reuse.schema_version;state=$reuse.state;"
        "verified_at=$reuse.verified_at;expires_at=$reuse.expires_at;validation_key=$reuse.validation_key;"
        "candidate_git_sha=$reuse.candidate_git_sha;candidate_worker_version_id=$reuse.candidate_worker_version_id;"
        "access_key=$reuse.access_key;prior_access_receipt_digest=$reuse.prior_access_receipt_digest;"
        "prior_access_key=$reuse.prior_access_key;current_access_key=$reuse.current_access_key;"
        "protected_origin=$reuse.protected_origin;provider_fingerprint=$reuse.provider_fingerprint;"
        "provider_inspection_receipt_digest=$reuse.provider_inspection_receipt_digest;"
        "changed_access_artifacts=@($reuse.changed_access_artifacts)};"
        "$reuse.receipt_digest=Get-AccessQualificationReuseReceiptDigest $reuseCore;"
        "$reuse|ConvertTo-Json -Depth 16|Set-Content $reusePath -Encoding UTF8;"
        "$candidate.access_qualification.receipt_digest=$reuse.receipt_digest;"
        "$candidate.validation.auth_inspection.receipt_digest=$reuse.receipt_digest;"
        "$state.candidate=$candidate;Write-ReleaseControlState $state;"
        "$oldReuseBytes=Get-Content $reusePath -Raw -Encoding UTF8;"
    )


def _cloudflare_access_read_stubs(audit_event: str = "$null") -> str:
    return (
        "function Get-ReleaseSecret{return [pscustomobject]@{available=$true;value='read-token';"
        "source='LOCAL_SECRET_FILE';diagnostic=$null}};"
        f"$script:auditEvent={audit_event};"
        "$script:policyDecision='allow';"
        "$script:policyUpdatedAt=$null;"
        "function Invoke-RestMethod{param($Method,$Uri,$Headers,$TimeoutSec);"
        "if($Uri -match '/logs/audit'){return [pscustomobject]@{success=$true;"
        "result=@($script:auditEvent|Where-Object{$null-ne$_});result_info=[pscustomobject]@{cursor=''}}};"
        "if($Uri -match '/identity_providers$'){return [pscustomobject]@{success=$true;"
        "result=@([pscustomobject]@{id='google-id';type='google'})}};"
        "if($Uri -match '/policies/'){return [pscustomobject]@{success=$true;result=[pscustomobject]@{"
        "id='d8ce9484-aca9-4c39-a211-37d2fa8ba9cf';name='Allow Assistant owner';"
        "decision=$script:policyDecision;precedence=1;session_duration='24h';"
        "updated_at=if($script:policyUpdatedAt){$script:policyUpdatedAt}else{$provider.policy_last_updated_at};"
        "include=@([pscustomobject]@{email=[pscustomobject]@{"
        "email='owner@example.test'}});require=@();isolation_required=$false;"
        "purpose_justification_required=$false;approval_required=$false}}};"
        "return [pscustomobject]@{success=$true;result=[pscustomobject]@{"
        "id='2f91233e-cabe-4f48-806c-83699de5e713';"
        "aud='4750fd9ae50ac47ae51d1d3605ca899e5603c691a7fe0c24457f3e335ed43ad1';"
        "name='XAUUSD Admin Owner';type='self_hosted';session_duration='24h';"
        "allowed_idps=@('google-id');destinations=@("
        "[pscustomobject]@{uri='https://aurum-signal-room.yiyousiow1234.workers.dev/admin*'},"
        "[pscustomobject]@{uri='https://aurum-signal-room.yiyousiow1234.workers.dev/assistant'},"
        "[pscustomobject]@{uri='https://aurum-signal-room.yiyousiow1234.workers.dev/retry-jobs'},"
        "[pscustomobject]@{uri='https://aurum-signal-room.yiyousiow1234.workers.dev/status'})}};};"
    )


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_stale_machine_access_evidence_renews_without_new_human_acceptance(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$receipt=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$new=Assert-AccessQualificationMachineReceipt $candidate;"
        "$reuseUnchanged=(Get-Content $reusePath -Raw -Encoding UTF8)-ceq$oldReuseBytes;"
        "$renewalFiles=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        "$humanEvents=([regex]::Matches((Get-Content $releaseHistoryPath -Raw),"
        "'CANDIDATE_ACCESS_BOUNDARY_ACCEPTED')).Count;"
        'Write-Output "$($new.state),$($new.root_human_receipt_digest -eq '
        '$reuse.prior_access_receipt_digest),$reuseUnchanged,$renewalFiles,$humanEvents"',
        powershell=powershell,
    )
    assert result == "ACCESS_QUALIFICATION_RENEWED,True,True,1,1"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_near_expiry_access_machine_lease_renews_before_promotion(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$script:accessNow=[DateTimeOffset]::UtcNow;"
        "function Get-AccessEvidenceUtcNow{return $script:accessNow};"
        "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$script:accessNow=(ConvertTo-ReleaseTimestampUtc $first.issued_at).AddMinutes(105);"
        "$second=Ensure-AccessQualificationMachineReceipt $candidate "
        "-MinimumRemaining ([TimeSpan]::FromMinutes(30));"
        "$count=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($second.state),$($second.previous_machine_receipt_digest -eq '
        '$first.receipt_digest),$count"',
        powershell=powershell,
    )
    assert result == "ACCESS_QUALIFICATION_RENEWED,True,2"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_new_candidate_renews_from_complete_historical_machine_chain(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$firstPath=Get-AccessQualificationRenewalReceiptPath $first.receipt_digest;"
        "$firstBytes=Get-Content $firstPath -Raw -Encoding UTF8;"
        "$state=Get-ReleaseControlState;$old=$state.candidate;"
        "$new=New-ReleaseIdentity -GitSha ('c'*40) "
        "-WorkerVersionId 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' "
        "-WindowsRevision ('c'*40) -ValidationState 'REVIEW_REQUIRED' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$new.branch='main';$new.compatibility_state='COORDINATED_STORAGE_MIGRATION_PASSED';"
        "$new.validation=$old.validation.PSObject.Copy();$new.validation.key=$new.validation_key;"
        "$new.validation.reason='ACCESS_BOUNDARY_REVIEW_REQUIRED';"
        "$new.validation.auth_inspection=[pscustomobject]@{"
        "state='AUTH_BOUNDARY_NOT_TESTABLE';versioned_workers_dev='UNPROTECTED_TEST_SURFACE'};"
        "$state.candidate=$new;Write-ReleaseControlState $state;"
        "$script:accessNow=[DateTimeOffset]::UtcNow.AddMinutes(5);"
        "function Get-AccessEvidenceUtcNow{return $script:accessNow};"
        "$renewed=Invoke-CandidateAccessQualificationReuse;"
        "$verified=Assert-AccessQualificationMachineReceipt $renewed;"
        "$priorUnchanged=(Get-Content $firstPath -Raw -Encoding UTF8)-ceq$firstBytes;"
        "$files=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        '$provider=Get-AccessProviderInspectionReceiptByDigest '
        '$verified.provider_inspection_receipt_digest;'
        'Write-Output "$($verified.state),$($renewed.validation_state),'
        '$($verified.previous_machine_receipt_digest -eq $first.receipt_digest),'
        '$($verified.root_human_receipt_digest -eq $first.root_human_receipt_digest),'
        '$priorUnchanged,$files,$($provider.audit_window_start -eq $first.inspection_window_end)"',
        powershell=powershell,
    )
    assert result == "ACCESS_QUALIFICATION_RENEWED,PASSED,True,True,True,2,True"


def test_new_candidate_renewal_does_not_fall_back_past_corrupt_machine_tip(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$firstPath=Get-AccessQualificationRenewalReceiptPath $first.receipt_digest;"
        "$first.receipt_digest=('f'*64);"
        "$first|ConvertTo-Json -Depth 16|Set-Content $firstPath -Encoding UTF8;"
        "$reason='';try{Get-LatestHistoricalAccessMachineAuthority|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result in {
        "ACCESS_QUALIFICATION_RENEWAL_TAMPERED",
        "ACCESS_QUALIFICATION_RENEWAL_MISSING",
    }


def test_access_renewal_is_idempotent_for_same_provider_inspection(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$count1=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        "$second=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$count2=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        'Write-Output "$($first.receipt_digest -eq $second.receipt_digest),$count1,$count2"',
    )
    assert result == "True,1,1"


def test_legacy_dashboard_minute_timestamp_renews_from_exact_api_value(tmp_path) -> None:
    legacy_minute = (
        "$p=ConvertTo-ReleaseTimestampUtc $provider.policy_last_updated_at;"
        "$provider.policy_last_updated_at=([DateTimeOffset]::new($p.Year,$p.Month,$p.Day,"
        "$p.Hour,$p.Minute,0,[TimeSpan]::Zero)).ToString('o');"
    )
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal(legacy_minute)
        + _cloudflare_access_read_stubs()
        + "$script:policyUpdatedAt=(ConvertTo-ReleaseTimestampUtc "
        "$provider.policy_last_updated_at).AddSeconds(4).ToString('o');"
        "$receipt=Ensure-AccessQualificationMachineReceipt $candidate;"
        'Write-Output $receipt.state',
    )
    assert result == "ACCESS_QUALIFICATION_RENEWED"


def test_policy_timestamp_precision_compatibility_remains_fail_closed(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$legacy=[pscustomobject]@{schema_version='access-provider-inspection-v1';"
        "inspection_method='CLOUDFLARE_AUTHENTICATED_DASHBOARD_READ_ONLY'};"
        "$api=[pscustomobject]@{schema_version='access-provider-inspection-v2';"
        "inspection_method='CLOUDFLARE_ACCESS_API_READ_ONLY'};"
        "$previous=[DateTimeOffset]::Parse('2026-08-19T17:09:00Z');"
        "$sameMinute=$previous.AddSeconds(4);$nextMinute=$previous.AddMinutes(1);"
        "$legacyOk=Test-AccessProviderPolicyTimestampCompatible $sameMinute $previous $legacy;"
        "$legacyNext=Test-AccessProviderPolicyTimestampCompatible $nextMinute $previous $legacy;"
        "$apiExact=Test-AccessProviderPolicyTimestampCompatible $sameMinute $previous $api;"
        'Write-Output "$legacyOk,$legacyNext,$apiExact"',
    )
    assert result == "True,False,False"


def test_access_renewal_chain_can_repeat_without_replacing_prior_receipts(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$script:accessNow=[DateTimeOffset]::UtcNow;"
        "function Get-AccessEvidenceUtcNow{return $script:accessNow};"
        "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$script:accessNow=$script:accessNow.AddHours(3);"
        "$second=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$verified=Assert-AccessQualificationMachineReceipt $candidate;"
        "$files=@(Get-ChildItem $accessQualificationRenewalReceiptRoot -File).Count;"
        'Write-Output "$files,$($second.previous_machine_receipt_digest -eq '
        '$first.receipt_digest),$($verified.root_human_receipt_digest -eq '
        '$reuse.prior_access_receipt_digest)"',
    )
    assert result == "2,True,True"


def test_access_renewal_rejects_access_sensitive_identity_change(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "function Get-AccessQualificationIdentity{[pscustomobject]@{"
        "access_qualification_key=('2'*64);core=[pscustomobject]@{"
        "protected_boundary=[pscustomobject]@{origin='https://aurum-signal-room.yiyousiow1234.workers.dev'};"
        "repository_artifacts=[ordered]@{auth='changed'}}}};"
        "$reason='';try{Ensure-AccessQualificationMachineReceipt $candidate|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == "ACCESS_HUMAN_REVIEW_REQUIRED:ACCESS_QUALIFICATION_KEY_CHANGED"


def test_access_audit_adapter_exhausts_real_cursor_envelope_and_finds_reverted_change(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:page=0;function Invoke-CloudflareAccessRead{param($PathAndQuery);$script:page++;"
        "$event=[pscustomobject]@{action=[pscustomobject]@{type='update';result='success'};"
        "raw=[pscustomobject]@{method='PUT';status_code=200;uri='/accounts/x/access/apps/app'};"
        "resource=[pscustomobject]@{id='app';product='Access';type='application'}};"
        "if($script:page -eq 1){[pscustomobject]@{success=$true;result=@($event);"
        "result_info=[pscustomobject]@{cursor='next'}}}else{[pscustomobject]@{success=$true;"
        "result=@();result_info=[pscustomobject]@{cursor=''}}}};"
        "$audit=Get-CloudflareAccessAuditInterval -From ([DateTimeOffset]::UtcNow.AddHours(-1)) "
        "-To ([DateTimeOffset]::UtcNow) -ApplicationId 'app' -PolicyId 'policy';"
        'Write-Output "$($audit.complete),$($audit.page_count),$($audit.event_count),'
        '$($audit.relevant_change_count),$script:page"',
    )
    assert result == "True,2,1,1,2"


def test_access_machine_renewal_uses_only_read_only_cloudflare_boundaries() -> None:
    source = _control_center_source()
    adapter = source.split("function Invoke-CloudflareAccessRead", 1)[1].split(
        "function Register-AccessProviderInspection", 1
    )[0]
    assert 'Get-ReleaseSecret -Name "CLOUDFLARE_ACCESS_READ_TOKEN"' in adapter
    assert "Invoke-RestMethod -Method Get" in adapter
    assert "-Method Post" not in adapter
    assert "/logs/audit" in adapter
    assert "result_info.cursor" in adapter
    assert "/access/apps/$applicationId" in adapter
    assert "/access/identity_providers" in adapter
    assert "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN" not in adapter


@pytest.mark.parametrize(
    ("setup", "expected"),
    (
        (
            "$script:policyDecision='deny';",
            "ACCESS_HUMAN_REVIEW_REQUIRED:ACCESS_PROVIDER_CONFIGURATION_CHANGED",
        ),
        (
            "$script:auditEvent=[pscustomobject]@{action=[pscustomobject]@{type='update';"
            "result='success'};raw=[pscustomobject]@{method='PUT';status_code=200;"
            "uri='/accounts/x/access/apps/2f91233e-cabe-4f48-806c-83699de5e713'};"
            "resource=[pscustomobject]@{id='2f91233e-cabe-4f48-806c-83699de5e713';"
            "product='Access';type='application'}};",
            "ACCESS_HUMAN_REVIEW_REQUIRED:ACCESS_PROVIDER_INSPECTION_INVALID",
        ),
        (
            "$accessProviderAuditMaximumLookback=[TimeSpan]::FromTicks(1);",
            "ACCESS_HUMAN_REVIEW_REQUIRED:ACCESS_PROVIDER_AUDIT_INTERVAL_UNCOVERED",
        ),
    ),
)
def test_access_renewal_requires_human_review_for_change_revert_or_history_gap(
    tmp_path, setup: str, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + setup
        + "$reason='';try{Ensure-AccessQualificationMachineReceipt $candidate|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == expected


@pytest.mark.parametrize(
    "mutation",
    (
        "$reuse.receipt_digest=('f'*64);$reuse|ConvertTo-Json -Depth 16|Set-Content $reusePath -Encoding UTF8;",
        "$root=Get-HistoricalAccessBoundaryReceiptByDigest $reuse.prior_access_receipt_digest;"
        "$root.receipt_digest=('f'*64);$rootPath=Get-AccessBoundaryReceiptPath $root.validation_key;"
        "$root|ConvertTo-Json -Depth 16|Set-Content $rootPath -Encoding UTF8;",
    ),
)
def test_access_renewal_fails_closed_for_corrupt_chain_or_human_root(
    tmp_path, mutation: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + mutation
        + "$reason='';try{Ensure-AccessQualificationMachineReceipt $candidate|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert "TAMPERED" in result or "HISTORICAL_RECEIPT_MISSING" in result


def test_access_renewal_fails_closed_when_previous_machine_link_is_broken(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _stale_access_reuse_ready_for_renewal()
        + _cloudflare_access_read_stubs()
        + "$first=Ensure-AccessQualificationMachineReceipt $candidate;"
        "$bad=$first.PSObject.Copy();$bad.previous_machine_receipt_digest=('f'*64);"
        "$core=Get-AccessQualificationRenewalCore $bad;"
        "$bad.receipt_digest=Get-AccessQualificationRenewalReceiptDigest $core;"
        "$badPath=Get-AccessQualificationRenewalReceiptPath $bad.receipt_digest;"
        "$bad|ConvertTo-Json -Depth 16|Set-Content $badPath -Encoding UTF8;"
        "$candidate.access_qualification.receipt_digest=$bad.receipt_digest;"
        "$candidate.validation.auth_inspection.receipt_digest=$bad.receipt_digest;"
        "$reason='';try{Assert-AccessQualificationMachineReceipt $candidate|Out-Null}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"


def test_access_key_uses_only_access_owned_repository_artifacts() -> None:
    contract = json.loads(
        (ROOT / "scripts" / "access-qualification-contract.json").read_text(encoding="utf-8")
    )
    old = "547b59d31cc60719a6cbdb85b3f2db8ff37f6066"
    current = "6ba596c62aa1a89d221d08c338b1071639802250"
    for path in contract["repository_artifacts"]:
        old_blob = subprocess.run(
            ["git", "rev-parse", f"{old}:{path}"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        current_blob = subprocess.run(
            ["git", "rev-parse", f"{current}:{path}"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert old_blob == current_blob, path


def test_access_receipt_file_is_immutable_for_one_validation_key(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _access_review_candidate()
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;$stable=$state.stable;"
        "$checklist=[ordered]@{owner_login_succeeds=$true;"
        "owner_resource_accessible=$true;unauthorized_access_denied=$true;"
        "logout_succeeds=$true;access_denied_after_logout=$true;"
        "reauthentication_succeeds=$true};"
        "$first=New-AccessBoundaryAcceptanceReceipt $candidate $stable $checklist;"
        "Write-AccessBoundaryAcceptanceReceipt $first;Start-Sleep -Milliseconds 2;"
        "$second=New-AccessBoundaryAcceptanceReceipt $candidate $stable $checklist;"
        "$reason='';try{Write-AccessBoundaryAcceptanceReceipt $second}"
        "catch{$reason=$_.Exception.Message};Write-Output $reason",
    )
    assert result == "ACCESS_RECEIPT_IMMUTABLE_CONFLICT"


@pytest.mark.parametrize("powershell", ("powershell.exe", "pwsh.exe"))
def test_protected_access_boundary_is_exact_canonical_production_origin(
    tmp_path, powershell: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$dashboardUrl='https://arbitrary-dashboard.example';"
        "$boundary=Get-ProtectedAccessBoundaryIdentity;"
        'Write-Output "$($boundary.origin),$($boundary.host),'
        '$($boundary.owner_resource)"',
        powershell=powershell,
    )
    assert result == (
        "https://aurum-signal-room.yiyousiow1234.workers.dev,"
        "aurum-signal-room.yiyousiow1234.workers.dev,/admin/api/session"
    )


@pytest.mark.parametrize(
    "protected_origin",
    (
        "https://candidate.workers.dev",
        "https://aurum-signal-room.yiyousiow1234.workers.dev.example",
        "https://aurum-signal-room.yiyousiow1234.workers.dev/other",
        "http://aurum-signal-room.yiyousiow1234.workers.dev",
    ),
)
def test_noncanonical_host_cannot_become_the_protected_access_boundary(
    tmp_path, protected_origin: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$protectedDashboardUrl='{protected_origin}';$reason='';"
        "try{Get-ProtectedAccessBoundaryIdentity|Out-Null}catch{$reason=$_.Exception.Message};"
        "Write-Output $reason",
    )
    assert result == "ACCESS_PROTECTED_HOST_INVALID"


def test_wpf_and_fallback_use_the_same_access_transition() -> None:
    source = _control_center_source()
    assert source.count('Invoke-WpfOperation "ApproveAccessBoundary"') == 1
    assert source.count('Invoke-GuiOperation -Operation "ApproveAccessBoundary"') == 1
    operation = source.split("function Invoke-ControlCenterOperationAction", 1)[1].split(
        "function Invoke-ControlCenterStructuredOperation", 1
    )[0]
    assert operation.count('"ApproveAccessBoundary"') == 1
    assert "Approve-CandidateAccessBoundary" in operation
    assert source.count("ALL_REQUIRED_ACCESS_CHECKS_PASSED") == 1


def test_promotion_rechecks_human_or_machine_access_receipt() -> None:
    source = _control_center_source()
    authority = (ROOT / "scripts" / "release_evidence_authority.ps1").read_text(
        encoding="utf-8"
    )
    coordinator = source.split("function Invoke-PromotionFreshnessCoordinator", 1)[1].split(
        "function Start-ReleasePromotion", 1
    )[0]
    promotion = source.split("function Start-ReleasePromotion", 1)[1].split(
        "function Complete-ReleasePromotion", 1
    )[0]
    assert 'Node "human_access_root"' in coordinator
    assert '"HUMAN_ACCESS_BOUNDARY_ACCEPTED"' in authority
    assert "Assert-AccessBoundaryAcceptanceReceipt" in authority
    assert '"ACCESS_QUALIFICATION_REUSED"' in authority
    assert '"ACCESS_QUALIFICATION_RENEWED"' in authority
    assert "Ensure-AccessQualificationMachineReceipt" in authority
    assert "ACCESS_EVIDENCE_AUTHORITY_CHANGED" in authority
    assert "Invoke-PromotionFreshnessCoordinator" in promotion
    assert "Test-ProductionCandidateProvenance" in promotion


def test_payload_producer_and_fixture_builder_select_worker_families(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$producer=Get-CandidateRouteValidationPlan -ChangedFiles @('scripts/run_dashboard_sync.py');"
        "$fixture=Get-CandidateRouteValidationPlan -ChangedFiles @('scripts/build_release_validation_fixtures.py');"
        "$package=Get-CandidateRouteValidationPlan -ChangedFiles @('web/package-lock.json');"
        "$build=Get-CandidateRouteValidationPlan -ChangedFiles @('web/build/sites-vite-plugin.ts');"
        "$docs=Get-CandidateRouteValidationPlan -ChangedFiles @('docs/README.md');"
        '$p=@($producer.worker_writes|Where-Object family -eq "news-content-write").Count;'
        '$f=@($fixture.worker_reads).Count+@($fixture.worker_writes).Count;'
        '$b=@($package.worker_reads|Where-Object {$_.baseline}).Count+'
        '@($package.worker_writes|Where-Object {$_.baseline}).Count;'
        '$bb=@($build.worker_reads|Where-Object {$_.baseline}).Count+'
        '@($build.worker_writes|Where-Object {$_.baseline}).Count;'
        'Write-Output "$p,$f,$b,$bb,$($docs.worker_cpu_required)"',
    )
    producer_scenarios, all_routes, baseline, build_baseline, docs = result.split(",")
    assert int(producer_scenarios) == 1
    assert int(all_routes) >= 20
    assert int(baseline) >= 5
    assert int(build_baseline) == int(baseline)
    assert docs == "False"


def test_release_validator_sends_exact_fixture_bytes(tmp_path) -> None:
    fixture = tmp_path / "repository" / "fixtures" / "utf8.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes('{"text":"精确字节"}'.encode("utf-8"))
    expected = fixture.read_bytes().hex()
    result = _run_control_center_contract(
        tmp_path,
        "$route=[pscustomobject]@{method='POST';path='/api/test';family='test';"
        "strategy='PRODUCTION_SHAPED_DRY_RUN';fixture='utf8.json'};"
        "$script:sent=$null;function Invoke-WebRequest{param($UseBasicParsing,$Method,$Uri,$Headers,$TimeoutSec,$ContentType,$Body);"
        "$script:sent=$Body;$content=[pscustomobject]@{status='DRY_RUN_OK';mutated=$false;route_family='test'}|ConvertTo-Json -Compress;"
        "return [pscustomobject]@{StatusCode=200;Content=$content}};"
        "$null=Invoke-CandidateRouteSample -Route $route -VersionHeaders @{} "
        "-ValidationRun 'run' -FixtureRoot (Join-Path $repositoryRoot 'fixtures') -IngestToken 'token';"
        "$hex=($script:sent|ForEach-Object {$_.ToString('x2')}) -join '';Write-Output $hex",
    )
    assert result == expected


def test_reverse_observation_commits_only_after_active_runtime_evidence(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$target=$state.stable;$now=$state.candidate;"
        "$state.stable=$now;$state.previous_stable=$target;"
        "$state.transaction=[pscustomobject]@{type='REVERSE';phase='REVERSE_OBSERVING';target=$target;previous=$now};"
        "Write-ReleaseControlState $state;"
        f"Write-RuntimeUpdateState @{{update_status='ACTIVE';activated_revision='{previous}';observation_mode='REVERSE'}};"
        "function Test-CloudflareRollbackTarget{return $true};"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@([pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})}};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{previous}'}}}};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($null -eq $final.transaction)"',
    )
    assert result == f"READY,{previous},True"


def test_business_transitions_do_not_call_control_bundle_installer() -> None:
    for name in (
        "Update-RuntimeCheckout",
        "Invoke-RuntimeRollback",
        "Invoke-ReleaseWindowsRestore",
        "Invoke-ReverseStable",
    ):
        body = _control_center_function_source(name)
        assert "Sync-StableRuntimeControlFiles" not in body


def test_release_gui_exposes_only_explicit_stable_candidate_controls() -> None:
    source = _control_center_source()

    assert 'New-ReleaseCard -Title "Stable"' in source
    assert 'New-ReleaseCard -Title "Release Candidate" -Emphasized $true' in source
    assert 'New-ReleaseCard -Title "Previous Stable"' in source
    assert 'New-UiButton -Text "Promote Candidate"' in source
    assert 'New-UiButton -Text "Reverse Stable"' in source
    assert 'New-UiButton -Text "Bootstrap Release Control"' in source
    assert 'New-UiButton -Text "Open Stable"' in source
    assert 'New-UiButton -Text "Open Candidate"' in source
    assert 'New-UiButton `\n        -Text "Approve Compatibility"' in source
    assert 'New-UiButton `\n        -Text "Approve Access Checks"' in source
    assert 'Git: $($state.candidate.git_sha)' in source
    assert 'Worker: $($state.candidate.worker_version_id)' in source
    assert 'Windows: $($state.candidate.windows_revision)' in source
    assert 'Git: $($state.previous_stable.git_sha)' in source
    assert 'Worker: $($state.previous_stable.worker_version_id)' in source
    assert 'Windows: $($state.previous_stable.windows_revision)' in source
    assert "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN" not in source.split(
        "function Show-ControlCenter", 1,
    )[1]


def test_release_gui_presentation_explains_action_eligibility(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stable=New-ReleaseIdentity -GitSha ('a'*40) -WorkerVersionId 'stable-worker' "
        "-WindowsRevision ('a'*40);"
        "$candidate=New-ReleaseIdentity -GitSha ('b'*40) -WorkerVersionId 'candidate-worker' "
        "-WindowsRevision ('b'*40) -ValidationState 'PASSED' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE' -Branch 'main';"
        "$candidate.compatibility_state='PASSED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key};"
        "$release=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "$release|Add-Member control_bundle_revision ('d'*40);"
        "$release|Add-Member control_bundle_exact_revision $true;"
        "$release|Add-Member control_bundle_hash_verified $true;"
        "$release.previous_stable=New-ReleaseIdentity -GitSha ('c'*40) "
        "-WorkerVersionId 'previous-worker' -WindowsRevision ('c'*40);"
        "$release.previous_stable_rollback_eligible=$true;"
        "$script:evidencePass=$true;"
        "function Assert-ReleaseEvidenceQualification{"
        "if(-not $script:evidencePass){throw 'RELEASE_EVIDENCE_WATERFALL_INCOMPLETE'};"
        "return [pscustomobject]@{state='PASSED'}};"
        "$release|Add-Member -Force release_runtime ([pscustomobject]@{"
        "drift_status='MATCHED';active=[pscustomobject]@{health='HEALTHY'};"
        "previous=[pscustomobject]@{worker_artifact=[pscustomobject]@{status='AVAILABLE'};"
        "worker_is_current_traffic_member=$false;reverse_precheck=[pscustomobject]@{"
        "can_reverse=$true;reason='READY'}}});"
        "$passed=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PREVIEW';"
        "$preview=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PRODUCTION_CANDIDATE';"
        "$release.candidate.validation_state='FAILED';"
        "$release.candidate.validation=[pscustomobject]@{error='Worker CPU evidence failed'};"
        "$failed=Get-ControlCenterReleasePresentation $release;"
        "$script:evidencePass=$false;"
        "$evidenceFailed=Get-ControlCenterReleasePresentation $release;"
        "$script:evidencePass=$true;"
        "$release.transaction=[pscustomobject]@{type='PROMOTE'};"
        "$busy=Get-ControlCenterReleasePresentation $release;"
        "$missing=Get-ControlCenterReleasePresentation $null;"
        "@($passed,$preview,$failed,$evidenceFailed,$busy,$missing) | ConvertTo-Json -Compress",
    )

    passed, preview, failed, evidence_failed, busy, missing = json.loads(result)
    assert passed["can_promote"] is True
    assert passed["can_reverse"] is True
    assert passed["promote_reason"] == "Ready to promote"
    assert preview["can_promote"] is False
    assert preview["promote_reason"] == "Preview cannot be promoted"
    # The nested validation object is a compatibility projection only.  The
    # authoritative Evidence DAG remains the Promote gate.
    assert failed["can_promote"] is True
    assert failed["candidate_detail"] == "Worker CPU evidence failed"
    assert failed["promote_reason"] == "Ready to promote"
    assert evidence_failed["can_promote"] is False
    assert evidence_failed["promote_reason"] == "RELEASE_EVIDENCE_WATERFALL_INCOMPLETE"
    assert busy["can_promote"] is False
    assert busy["can_reverse"] is False
    assert busy["promote_reason"] == "A release transaction is already in progress"
    assert missing["candidate_state"] == "UNAVAILABLE"
    assert missing["promote_reason"] == "Not bootstrapped"


def test_control_center_summary_separates_runtime_health_from_candidate_state(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stable=New-ReleaseIdentity -GitSha ('a'*40) -WorkerVersionId 'stable-worker' "
        "-WindowsRevision ('a'*40);"
        "$candidate=New-ReleaseIdentity -GitSha ('b'*40) -WorkerVersionId 'candidate-worker' "
        "-WindowsRevision ('b'*40) -ValidationState 'TESTING';"
        "$release=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "$snapshot=[pscustomobject]@{captured_at='2026-08-20T12:00:00+08:00';release=$release;"
        "services=@([pscustomobject]@{State='RUNNING'},[pscustomobject]@{State='STOPPED'})};"
        "Get-ControlCenterSummaryPresentation $snapshot | ConvertTo-Json -Compress",
    )

    summary = json.loads(result)
    assert summary["overall"] == "DEGRADED"
    assert summary["local_runtime"] == "PARTIAL"
    assert summary["candidate_state"] == "TESTING"
    assert summary["last_refresh"] == "12:00:00"


def test_code_reload_health_requires_fresh_successful_sync(tmp_path) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "dashboard-sync-status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "last_attempt": "2026-08-12T08:00:01+00:00",
        "status": "OK",
    }), encoding="utf-8")
    for name, service in (
        ("collector-status.json", "collector"),
        ("news-annotator-status.json", "annotator"),
    ):
        (status.parent / name).write_text(json.dumps({
            "service": service,
            "state": "RUNNING",
            "last_success": "2026-08-12T08:00:01+00:00",
        }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-ForecasterProcesses { return [pscustomobject]@{ ProcessId = 1 } }; "
        "function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }; "
        "$started = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "$healthy = Test-CodeReloadHealth -ReloadStarted $started; "
        f"@{{ last_attempt = '2026-08-12T08:00:01+00:00'; status = 'ERROR' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{status}'; "
        "$failed = Test-CodeReloadHealth -ReloadStarted $started; "
        "Write-Output \"$healthy,$failed\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"


def test_code_reload_accepts_fresh_service_startup_but_rejects_failed_state(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "dashboard-sync-status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "last_attempt": "2026-08-12T08:00:01+00:00",
        "status": "OK",
    }), encoding="utf-8")
    (status.parent / "collector-status.json").write_text(json.dumps({
        "service": "collector", "state": "STARTING",
        "last_success": "2026-08-12T08:00:01+00:00",
    }), encoding="utf-8")
    annotator = status.parent / "news-annotator-status.json"
    annotator.write_text(json.dumps({
        "service": "annotator", "state": "STARTING",
        "last_success": "2026-08-12T08:00:01+00:00",
    }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-ForecasterProcesses { return [pscustomobject]@{ ProcessId = 1 } }; "
        "function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }; "
        "$started = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "$servicesStarting = Test-CodeReloadHealth -ReloadStarted $started; "
        f"@{{ service = 'annotator'; state = 'ERROR'; "
        f"last_success = '2026-08-12T08:00:01+00:00' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{annotator}'; "
        "$failed = Test-CodeReloadHealth -ReloadStarted $started; "
        "Write-Output \"$servicesStarting,$failed\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"


def test_service_state_rejects_stale_worker_heartbeat(tmp_path) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "collector-status.json"
    status.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    status.write_text(json.dumps({
        "service": "collector",
        "state": "RUNNING",
        "last_success": now.isoformat(),
    }), encoding="utf-8")
    old_start = (now - timedelta(minutes=10)).isoformat()
    stale = (now - timedelta(minutes=6)).isoformat()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{old_start}') }}; "
        "$service = [pscustomobject]@{ Key = 'collector' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "$fresh = Get-ServiceState -Service $service -Processes $processes; "
        f"@{{ service = 'collector'; state = 'RUNNING'; last_success = '{stale}' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{status}'; "
        "$old = Get-ServiceState -Service $service -Processes $processes; "
        "Write-Output \"$fresh,$old\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "RUNNING,COLLECTOR STALE"


def test_worker_family_keeps_current_startup_alive_but_bounds_stalled_startup(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    status_root = repo / ".local" / "forward"
    status_root.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    current_start = (now - timedelta(minutes=8)).isoformat()
    stalled_start = (now - timedelta(minutes=16)).isoformat()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    results = []

    for service, filename in (
        ("collector", "collector-status.json"),
        ("annotator", "news-annotator-status.json"),
    ):
        status = status_root / filename
        status.write_text(json.dumps({
            "service": service,
            "state": "STARTING",
            "last_success": (now - timedelta(minutes=8)).isoformat(),
        }), encoding="utf-8")
        command = (
            f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
            f"-RepositoryRoot '{repo}'; "
            f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{current_start}') }}; "
            f"$service = [pscustomobject]@{{ Key = '{service}' }}; "
            "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
            "$current = Get-ServiceState -Service $service -Processes $processes; "
            f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{stalled_start}') }}; "
            "$stalled = Get-ServiceState -Service $service -Processes $processes; "
            "Write-Output \"$current,$stalled\""
        )
        results.append(subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, check=True,
        ).stdout.strip())

    assert results == [
        "STARTING,COLLECTOR STALE",
        "STARTING,ANNOTATOR STALE",
    ]


def test_api_and_sync_load_operator_bridge_from_user_environment(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / ".local" / "forward" / "logs").mkdir(parents=True)
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-UserEnvironmentValue { param($Name) return 'bridge-secret-from-user-environment-123456' }; "
        "$script:captured = @(); "
        "function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,"
        "$RedirectStandardOutput,$RedirectStandardError); "
        "$script:captured += $env:DASHBOARD_OPERATOR_BRIDGE_TOKEN }; "
        "$api = [pscustomobject]@{ Key='api'; Kind='Python'; Script='api.py'; Arguments=@() }; "
        "$sync = [pscustomobject]@{ Key='sync'; Kind='Python'; Script='sync.py'; Arguments=@() }; "
        "Start-ForecasterService -Service $api -SkipExistingCheck; "
        "Start-ForecasterService -Service $sync -SkipExistingCheck; "
        "Write-Output ($script:captured -join ',')"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == (
        "bridge-secret-from-user-environment-123456,"
        "bridge-secret-from-user-environment-123456"
    )


def test_broadcast_platform_readiness_is_exact_revision_and_dry_run_only(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$env:AURUM_LIVE_BROADCAST_COMPATIBLE_REVISION=''; "
        "function Get-BroadcastPublisherToken { return 'publisher-secret' }; "
        "function Invoke-RestMethod { param($Method,$Uri,$Headers,$ContentType,$Body,$TimeoutSec); "
        "if($Method -eq 'Post'){return [pscustomobject]@{valid=$true;dry_run=$true;schema_version='PUBLIC_LIVE_V1'}};"
        "return [pscustomobject]@{service='aurum-live-broadcast';"
        "schema_version='PUBLIC_LIVE_V1';code_revision='candidate-sha';"
        "binding_ready=$true;latest_available=$false} }; "
        "$ready=Test-BroadcastServiceReadiness -CandidateRevision 'candidate-sha'; "
        "$wrong=Test-BroadcastServiceReadiness -CandidateRevision 'different-sha'; "
        "Write-Output \"$($ready.state),$($ready.dry_run_storage_mutation),"
        "$($wrong.state),$($wrong.reason),$($wrong.revision_accepted)\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result == "PASSED,False,BROADCAST_BLOCKED,BROADCAST_PLATFORM_NOT_READY,False"


def test_broadcast_missing_configuration_is_retryable_and_authority_is_pinned(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-BroadcastPublisherToken { return '' }; "
        "$result=Test-BroadcastServiceReadiness -CandidateRevision ('a'*40); "
        "Write-Output \"$($result.state),$($result.retryable),$($result.authority)\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result == (
        "BROADCAST_PENDING,True,"
        "https://aurum-live-broadcast.yiyousiow1234.workers.dev/health"
    )


def test_broadcast_live_readiness_requires_fresh_matching_real_publisher(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$script:published=[DateTimeOffset]::UtcNow.AddSeconds(-30).ToString('o');"
        "function Invoke-RestMethod { return [pscustomobject]@{service='aurum-live-broadcast';"
        "schema_version='PUBLIC_LIVE_V1';binding_ready=$true;latest_available=$true;"
        "latest_sequence=9;latest_generated_at=$script:published;"
        "latest_published_at=$script:published;latest_source_revision='candidate-sha'} };"
        "function Get-ForecasterProcesses{return @([pscustomobject]@{ProcessId=1})};"
        "function Get-ServiceState{return 'RUNNING'};"
        "$fresh=Test-BroadcastLiveDeliveryReadiness -ExpectedRevision 'candidate-sha';"
        "$script:published=[DateTimeOffset]::UtcNow.AddDays(-2).ToString('o');"
        "$stale=Test-BroadcastLiveDeliveryReadiness -ExpectedRevision 'candidate-sha';"
        "Write-Output \"$($fresh.state),$($stale.state),$($stale.freshness_threshold_seconds)\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result == "PASSED,BROADCAST_LIVE_BLOCKED,90"


def test_broadcast_blocked_candidate_is_rediscovered_but_failed_is_terminal(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='BROADCAST_BLOCKED';"
        "Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "function Reconcile-ReleaseControlState{};function Find-NewCandidateRelease{return $null};"
        "$script:calls=0;function Invoke-AutomaticCandidateValidation{$script:calls++;return $true};"
        "$null=Invoke-CandidateDiscovery;$blockedCalls=$script:calls;"
        "$state=Get-ReleaseControlState;$state.candidate.validation_state='FAILED';Write-ReleaseControlState $state;"
        "$null=Invoke-CandidateDiscovery;Write-Output \"$blockedCalls,$script:calls\"",
    )
    assert result == "1,1"


def test_same_broadcast_candidate_recovers_and_continues_validation(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$script:ready=$false;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{return $true};"
        "function Test-RequiredGitHubChecks{return 'PASSED'};"
        "function Get-CandidateChangedFiles{return @('broadcast/src/index.js')};"
        "function Test-BroadcastServiceReadiness{if($script:ready){return [pscustomobject]@{"
        "state='PASSED';passed=$true;retryable=$false;reason='PASSED'}};"
        "return [pscustomobject]@{state='BROADCAST_BLOCKED';passed=$false;"
        "retryable=$true;reason='BROADCAST_PLATFORM_NOT_READY'}};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{"
        "state='COORDINATED_STORAGE_MIGRATION_REQUIRED';files=@('broadcast/wrangler.jsonc')}};"
        "$candidateRelease=(Get-ReleaseControlState).candidate;"
        "$first=Invoke-AutomaticCandidateValidation -Candidate $candidateRelease;"
        "$blocked=(Get-ReleaseControlState).candidate;"
        "$script:ready=$true;"
        "$second=Invoke-AutomaticCandidateValidation -Candidate $blocked;"
        "$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        "Write-Output \"$first,$($blocked.validation_state),$second,"
        "$($final.candidate.validation_state),$($history -match 'CANDIDATE_BROADCAST_RECOVERED')\"",
    )

    assert result == "False,BROADCAST_BLOCKED,False,REVIEW_REQUIRED,True"


def test_broadcast_publisher_reports_disabled_unconfigured_degraded_and_running(tmp_path) -> None:
    status_path = (
        tmp_path / "runtime" / ".local" / "forward"
        / "live-broadcast-publisher-status.json"
    )
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({
        "state": "RUNNING",
        "last_success": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        "$service=[pscustomobject]@{Key='broadcast'};"
        "$script:enabled=$false;$script:token='';"
        "function Test-BroadcastPublisherEnabled{return $script:enabled};"
        "function Get-BroadcastPublisherToken{return $script:token};"
        "$disabled=Get-ServiceState -Service $service -Processes @();"
        "$script:enabled=$true;"
        "$unconfigured=Get-ServiceState -Service $service -Processes @();"
        "$script:token='publisher-secret';"
        "$degraded=Get-ServiceState -Service $service -Processes @();"
        "$running=Get-ServiceState -Service $service "
        "-Processes @([pscustomobject]@{ProcessId=1});"
        "Write-Output \"$disabled,$unconfigured,$degraded,$running\"",
    )

    assert result == "DISABLED,NOT_CONFIGURED,DEGRADED,RUNNING"


def test_broadcast_owner_starts_independently_without_reclassifying_core_services(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:enabled=$true;$script:started=@();"
        "function Test-BroadcastPublisherEnabled{return $script:enabled};"
        "function Get-ForecasterStatus{return @($services|ForEach-Object{"
        "[pscustomobject]@{Key=$_.Key;State=if($_.Key -eq 'broadcast'){'DEGRADED'}else{'RUNNING'}}})};"
        "function Start-ForecasterService{param($Service,[switch]$SkipExistingCheck);"
        "$script:started+=@($Service.Key)};"
        "Start-All;"
        "$core=@((Get-ForecasterStatus)|Where-Object Key -ne 'broadcast'|Select-Object -ExpandProperty State);"
        "Write-Output \"$($script:started -join ','),$($core -contains 'DEGRADED'),"
        "$($reloadableServiceKeys -contains 'broadcast')\"",
    )

    assert result == "broadcast,False,False"
