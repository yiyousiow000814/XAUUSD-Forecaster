from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
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
    "control_center.xaml",
    "xauusd_control_center_launcher.vbs",
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs",
)


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
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

    assert "Test-ExpectedWeeklyMarketClosure" in control_center
    assert "Get-BrokerMarketSession" in control_center
    assert 'return "MARKET CLOSED"' in control_center
    assert '"MARKET CLOSED", "API OK"' in control_center
    assert '"SYNC ERROR", "SYNC STALE"' in control_center
    assert '"COLLECTOR STALE", "ANNOTATOR STALE"' in control_center
    assert '"SESSION STALE"' in control_center


def test_control_center_loads_collector_keys_without_exposing_them() -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

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


def test_observability_retry_classifier_excludes_deterministic_query_failure(tmp_path) -> None:
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

    assert result == "True,True,False"


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


def test_candidate_validation_retries_delayed_observability_evidence(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}';"
        "$route=[pscustomobject]@{path='/api/status';request_query='';method='GET';"
        "family='status-read';scenario='status';boundary='WORKER_READ';warmup_samples=1;"
        "acceptance_samples=2};"
        "$plan=[pscustomobject]@{static_assets=@();worker_reads=@($route);worker_writes=@()};"
        "function Start-Sleep{};$script:countAttempts=0;$script:countWindows=@();"
        "$script:phases=@();function Get-CandidateInvocationCount{"
        "param($Candidate,$From,$To,$ValidationRun);"
        "$script:countAttempts++;$script:countWindows+=$To;"
        "if($script:countAttempts -eq 1){0}else{2}};"
        "$script:requestIndex=0;function Invoke-CandidateRouteSample{param($ValidationPhase);"
        "$script:phases+=$ValidationPhase;$script:requestIndex++;return [pscustomobject]@{passed=$true;"
        "request_id=('request-'+$script:requestIndex);status=200}};"
        "$script:evidenceAttempts=0;$script:evidenceTo=$null;"
        "function Get-CandidateFrozenPlatformEvidence{"
        "param($Candidate,$From,$To,$ExpectedRequests,$ValidationRun);"
        "$script:evidenceAttempts++;$script:evidenceTo=$To;$script:expected=$ExpectedRequests.Count;"
        "return [pscustomobject]@{"
        "invocations=2;passed=$true;gate_state='PASSED'}};"
        "$validation=Invoke-CandidateWorkerValidation -Candidate $candidate -RoutePlan $plan;"
        'Write-Output "$script:countAttempts,$script:evidenceAttempts,$script:expected,'
        '$($validation.observed_worker_invocations),$($script:phases -join \':\'),'
        '$($validation.observability_diagnostic)"',
    )

    assert result == "0,1,2,2,warmup:acceptance:acceptance,"


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


def test_frozen_raw_telemetry_reconciles_identical_event_universe(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}';"
        "function Start-Sleep{};function New-Event($id,$request,$cpu){"
        "[pscustomobject]@{event_id=$id;event_type='cf-worker-event';worker_version_id='worker';"
        "request_id=$request;validation_run='run';validation_phase='acceptance';method='GET';"
        "path='/api/status';status=200;outcome='ok';cpu_ms=$cpu;wall_ms=2}};"
        "$script:queries=0;$script:observedVersions=@();$script:observedKeys=@();"
        "function Invoke-WorkersObservabilityEventsQuery{param($Filters);"
        "$script:queries++;"
        "$script:observedVersions+=@($Filters|Where-Object key -eq '$workers.scriptVersion.id')[0].value;"
        "$script:observedKeys+=@($Filters|ForEach-Object key);"
        "[pscustomobject]@{total_count=2;page_count=2;records=@("
        "(New-Event 'event-1' 'request-1' 0),(New-Event 'event-2' 'request-2' 2));"
        "next_offset=''}};"
        "$expected=@([pscustomobject]@{request_id='request-1';family='status-read';scenario='a'},"
        "[pscustomobject]@{request_id='request-2';family='status-read';scenario='a'});"
        "$e=Get-CandidateFrozenPlatformEvidence -Candidate $candidate "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-ExpectedRequests $expected -ValidationRun 'run';"
        'Write-Output "$script:queries,$($e.invocations),$($e.stable_reads),'
        '$($e.request_reconciliation.matched),$($e.universe_digest.Length),'
        '$($e.p95_cpu_ms),$($e.routes[0].invocations),'
        '$($e.family_reconciliation[0].matched),$($e.scenario_reconciliation[0].actual),'
        '$(@($script:observedVersions|Select-Object -Unique)-join \';\'),'
        '$(@($script:observedKeys|Select-Object -Unique)-contains \'$metadata.type\'),'
        '$(@($script:observedKeys|Select-Object -Unique)-contains \'$workers.event.request.headers.x-aurum-validation-run\'),'
        '$(@($script:observedKeys|Select-Object -Unique)-contains \'$workers.event.request.headers.x-aurum-validation-phase\')"',
    )

    assert result == "2,2,2,True,64,2,2,True,2,worker,True,True,True"


def test_frozen_raw_telemetry_reconciles_exact_310_request_universe(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}';"
        "function Start-Sleep{};function New-Event($number){"
        "$id=('event-{0:d3}' -f $number);$request=('request-{0:d3}' -f $number);"
        "[pscustomobject]@{event_id=$id;event_type='cf-worker-event';worker_version_id='worker';"
        "request_id=$request;validation_run='run-310';validation_phase='acceptance';method='GET';"
        "path='/api/status';status=200;outcome='ok';cpu_ms=1;wall_ms=2}};"
        "$script:queries=0;function Invoke-WorkersObservabilityEventsQuery{$script:queries++;"
        "[pscustomobject]@{total_count=310;page_count=310;records=@(1..310|ForEach-Object{New-Event $_});"
        "next_offset=''}};"
        "$expected=@(1..310|ForEach-Object{[pscustomobject]@{"
        "request_id=('request-{0:d3}' -f $_);family='status-read';scenario='status'}});"
        "$e=Get-CandidateFrozenPlatformEvidence -Candidate $candidate "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-ExpectedRequests $expected -ValidationRun 'run-310';"
        'Write-Output "$script:queries,$($e.invocations),$($e.stable_reads),'
        '$($e.request_reconciliation.matched),$($e.universe_digest.Length)"',
    )

    assert result == "2,310,2,True,64"


@pytest.mark.parametrize(
    ("case", "expected_diagnostic"),
    [
        ("duplicate_event", "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"),
        ("duplicate_request", "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"),
        ("wrong_version", "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"),
        ("wrong_run", "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"),
    ],
)
def test_frozen_raw_telemetry_rejects_malformed_universes(
    tmp_path, case: str, expected_diagnostic: str,
) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}';$case='{case}';"
        "function Start-Sleep{};function New-Event($id,$request){"
        "[pscustomobject]@{event_id=$id;event_type='cf-worker-event';"
        "worker_version_id=if($case -eq 'wrong_version'){'other'}else{'worker'};"
        "request_id=$request;validation_run=if($case -eq 'wrong_run'){'other'}else{'run'};"
        "validation_phase='acceptance';method='GET';path='/api/status';status=200;"
        "outcome='ok';cpu_ms=1;wall_ms=2}};"
        "function Invoke-WorkersObservabilityEventsQuery{"
        "$event2=if($case -eq 'duplicate_event'){'event-1'}else{'event-2'};"
        "$request2=if($case -eq 'duplicate_request'){'request-1'}else{'request-2'};"
        "[pscustomobject]@{total_count=2;page_count=2;records=@((New-Event 'event-1' 'request-1'),"
        "(New-Event $event2 $request2));next_offset=''}};"
        "$expected=@([pscustomobject]@{request_id='request-1';family='status';scenario='a'},"
        "[pscustomobject]@{request_id='request-2';family='status';scenario='a'});"
        "$null=Get-CandidateFrozenPlatformEvidence -Candidate $candidate "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-ExpectedRequests $expected -ValidationRun 'run';"
        "Write-Output $script:lastWorkersObservabilityDiagnostic",
    )

    assert result == expected_diagnostic


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
        "request_id=$request;validation_run='run';validation_phase='acceptance';method='GET';"
        "path='/api/status';status=200;outcome='ok';cpu_ms=0;wall_ms=1}};"
        "$script:queries=0;$script:bounds=@();function Invoke-WorkersObservabilityEventsQuery{"
        "param($To,$Offset);$script:queries++;$script:bounds+=$To.ToString('o');"
        "if($Offset){[pscustomobject]@{total_count=2001;page_count=1;records=@((New-Event 2001));next_offset=''}}"
        "else{[pscustomobject]@{total_count=2001;page_count=2000;records=@(1..2000|ForEach-Object{New-Event $_});"
        "next_offset='event-2000'}}};"
        "$expected=@(1..2001|ForEach-Object{[pscustomobject]@{"
        "request_id=('request-{0:d4}' -f $_);family='status-read';scenario='status'}});"
        "$e=Get-CandidateFrozenPlatformEvidence -Candidate $candidate "
        "-From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) -To ([DateTimeOffset]::UtcNow) "
        "-ExpectedRequests $expected -ValidationRun 'run';"
        '$uniqueBounds=@($script:bounds|Select-Object -Unique);'
        'Write-Output "$script:queries,$($e.invocations),$($e.stable_reads),$($uniqueBounds.Count)"',
    )

    assert result == "4,2001,2,1"


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
        "observability_diagnostic='OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING';"
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
        "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING,1,True,True"
    )


def test_platform_retry_resumes_exact_telemetry_receipt_without_replaying_routes(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$candidate=$state.candidate;"
        "$state.candidate.compatibility_state='APPROVED';"
        "$state.candidate.validation_state='PLATFORM_PENDING';"
        "$state.candidate.validation=[pscustomobject]@{key=$candidate.validation_key;"
        "repository='PASSED';windows='PASSED';cloudflare='PENDING';"
        "reason='OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING';"
        "observability_diagnostic='OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING';"
        "validation_run='run-resume';expected_worker_invocations=1;static_worker_invocations=0;"
        "static_observability_state='PASSED';telemetry_window_from='2026-08-27T20:00:00Z';"
        "telemetry_window_to='2026-08-27T20:01:00Z';"
        "expected_requests=@([pscustomobject]@{request_id='request-1';family='status-read';"
        "scenario='status';method='GET';path='/api/status'});"
        "routes=@([pscustomobject]@{route='/api/status';boundary='WORKER_READ';passed=$true})};"
        "Write-ReleaseControlState $state;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('web/app/api/status/route.ts')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{worker_cpu_required=$true;"
        "requires_validation=$true;static_assets=@();worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{passed=$true}};"
        "function Invoke-CandidateWorkerValidation{throw 'ROUTES_REPLAYED'};"
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
        '$($saved.candidate.validation.routes[0].route)"',
    )

    assert result == "True,1,PASSED,/api/status"


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
    control_center = path.read_text(encoding="utf-8")

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
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

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
        "$stable=[pscustomobject]@{git_sha=('a'*40);worker_version_id="
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
        "return [pscustomobject]@{resources=[pscustomobject]@{bindings=@("
        "[pscustomobject]@{type='d1';name='DB';database_id=$script:testDatabaseId})}}};"
        "function Invoke-WranglerJson{param($Arguments);return [pscustomobject]@{"
        "uuid=$script:testDatabaseId;name='aurum-signal-room'}};"
        "function Invoke-CoordinatedMigrationD1Query{param($Sql);"
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
        "function Invoke-Utf8NativeProcess{param($FilePath,$Arguments);"
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
        + "$script:checkouts = @(); "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
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
        + f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
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
    _write_control_bundle(runtime, "reviewed", scripts_dir=True)
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

    assert result == f"{revision},True,{len(RUNTIME_CONTROL_FILES)}"


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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8",
    )
    assert 'event = "CONTROL_CENTER_UI_STARTED"' in source
    assert 'Write-ControlCenterUiStarted -Mode "WPF"' in source
    assert 'Write-ControlCenterUiStarted -Mode "WINFORMS_FALLBACK"' in source
    assert "Protect-PreflightDiagnosticText $FailureReason" in source


def test_candidate_auth_evidence_uses_formal_access_host_only() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    assert "function Show-WpfControlCenter" in source
    assert "if (Show-WpfControlCenter)" in source
    assert "using WinForms fallback" in source
    assert 'Invoke-WpfOperation ([string]$button.CommandParameter)' in source
    assert 'Get-ControlCenterReleasePresentation -Release $release' in source


def test_control_center_route_status_is_not_inherited_from_another_gate() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8",
    )
    assert '$apiRouteState = if ($directed.tested -gt 0)' in source
    assert '$contractCheck = if ($directed.tested -gt 0)' in source
    assert '"API routes: $contractCheck | $($directed.passed)/$($directed.tested)"' in source


def test_release_gui_actions_are_tracked_single_flight_in_both_shells() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8",
    )
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
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
        + "$script:failedCandidateCopy = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
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
        + "$script:failedCandidateMove = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
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
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Restart-CodeReloadableServices {}; "
        "function Write-RuntimeCodeState {}; function Write-RuntimeUpdateFailure {}; "
        "function Write-WatchdogEvent {}; "
        f"$restored = Invoke-RuntimeRollback -FailedRevision '{candidate}' "
        f"-PreviousRevision '{previous}' -Reason 'contract test'; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output $restored",
    ).splitlines()

    assert result == [
        ",".join(f"{candidate}|{name}" for name in RUNTIME_CONTROL_FILES),
        "True",
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
        'Write-Output "$first,$second,$third,$script:rollbacks,$($state.update_status)"',
    )

    assert result == "True,True,False,1,ROLLED_BACK"


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
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")
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
        + "$state = Get-ReleaseControlState; "
        "$state.transaction = [pscustomobject]@{ type='PROMOTE'; phase='OBSERVING'; "
        "target=$state.candidate; previous=$state.stable }; "
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


def test_candidate_cpu_evidence_comes_from_one_raw_event_universe() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8",
    )

    assert "function Get-CandidateCpuEvidence" not in source
    assert "function Get-CandidatePlatformEvidence" not in source
    assert "Get-CandidateFrozenPlatformEvidence -Candidate $Candidate" in source
    assert "CLOUDFLARE_WORKERS_OBSERVABILITY_NORMALIZED_EVENTS" in source
    assert "universe_digest = $stableDigest" in source


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


def test_passed_candidate_promotes_only_after_observation_commit(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "function Assert-ActiveControlBundle { return [pscustomobject]@{exact_revision=$true} }; "
        "function Test-ProductionCandidateProvenance { return $true }; "
        "function Test-CloudflareRollbackTarget { return $true }; "
        "function Test-CloudflareReleasePlacement { return $true }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }}; "
        "function Test-SingleProductionOwner { return $true }; "
        "function Update-RuntimeCheckout { return $true }; "
        "$script:cutover=@(); "
        "function Restart-CodeReloadableServices { $script:cutover += 'windows-with-sync-paused'; return [DateTimeOffset]::UtcNow }; "
        "function Complete-DeferredServiceReload { $script:cutover += 'sync-resumed' }; "
        "function Start-RuntimeObservation {}; "
        "function Write-RuntimeCodeState {}; "
        "function Write-WatchdogEvent {}; "
        "function Invoke-CloudflareDeployment { $script:cutover += 'worker' }; "
        "$started=Start-ReleasePromotion; $during=Get-ReleaseControlState; "
        "Complete-ReleasePromotion; $after=Get-ReleaseControlState; "
        'Write-Output "$started,$($during.stable.git_sha),$($during.transaction.phase),$($after.stable.git_sha),$($script:cutover -join \';\')"',
    )

    assert result == (
        f"True,{previous},OBSERVING,{candidate},"
        "windows-with-sync-paused;worker;sync-resumed"
    )


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
        "$state.transaction=[pscustomobject]@{type='PROMOTE';phase='OBSERVING';"
        "target=$state.candidate;previous=$state.stable;"
        "deferred_projection_obligations=@($obligation)};Write-ReleaseControlState $state;"
        "Write-RuntimeUpdateState @{update_status='ACTIVE';"
        "observation_validation_key=$state.candidate.validation_key;"
        "observation_deferred_projection_state='PENDING'};"
        "function Enter-ReleaseTransactionLock{return $true};"
        "function Exit-ReleaseTransactionLock{};function Test-CloudflareRollbackTarget{return $true};"
        "Complete-ReleasePromotion;$pending=Get-ReleaseControlState;"
        "$runtime=Get-RuntimeUpdateState;"
        "$runtime.observation_deferred_projection_state='PASSED';"
        "$runtime|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $runtimeUpdateStatePath;"
        "Complete-ReleasePromotion;$passed=Get-ReleaseControlState;"
        'Write-Output "$($pending.transaction.phase),$($pending.stable.git_sha),'
        '$($null -eq $passed.transaction),$($passed.stable.git_sha)"',
    )
    assert result == f"OBSERVING,{previous},True,{candidate}"


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
        + "$state=Get-ReleaseControlState;"
        "$state.transaction=[pscustomobject]@{type='PROMOTE';phase='OBSERVING';target=$state.candidate;previous=$state.stable};"
        "Write-ReleaseControlState $state;"
        f"Write-RuntimeUpdateState @{{update_status='ACTIVE';activated_revision='{candidate}'}};"
        "function Test-CloudflareRollbackTarget { return $true };"
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='22222222-2222-4222-8222-222222222222';percentage=100})} };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{candidate}'}} }};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($null -eq $final.transaction)"',
    )

    assert result == f"READY,{candidate},True"


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
        "function Test-CloudflareRollbackTarget { return $true };"
        "function Test-SingleProductionOwner { return $true };"
        "function Stop-ForecasterService {};"
        "function Start-RuntimeObservation {};"
        "$script:worker='';$script:windows='';"
        "function Invoke-CloudflareDeployment { param($StableVersionId);$script:worker=$StableVersionId };"
        "function Invoke-ReleaseWindowsRestore { param($Revision);$script:windows=$Revision };"
        "$ok=Invoke-ReverseStable;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.stable.git_sha),$script:worker,$script:windows"',
    )
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    reverse_body = source.split("function Invoke-ReverseStable", 1)[1].split(
        "function Reconcile-ReleaseControlState", 1,
    )[0]

    assert result == f"True,{current},11111111-1111-4111-8111-111111111111,{previous}"
    assert "D1" not in reverse_body
    assert "database" not in reverse_body.lower()


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


def test_migration_capability_reuses_json_projection_from_one_bounded_scan() -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")
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
            "return [pscustomobject]@{resources=[pscustomobject]@{bindings=@("
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
    control = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
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
        "reason='WORKER_CPU_HEADROOM_REVIEW_REQUIRED';tested_at='prior'};"
        "$candidate|Add-Member -Force migration_acceptance ([pscustomobject]@{"
        "validation_key=$candidate.validation_key;receipt_digest='receipt'});"
        "Write-ReleaseControlState $state;$script:preflights=0;"
        "function Test-ProductionCandidateProvenance{return $true};"
        "function Invoke-ProductionShapePreflight{$script:preflights++;return $true};"
        "function Test-RequiredGitHubChecks{'PASSED'};"
        "function Get-CandidateChangedFiles{return @('docs/README.md')};"
        "function Get-CandidateCompatibilityRequirement{return [pscustomobject]@{"
        "state='AUTOMATIC';files=@()}};"
        "function Get-CandidateRouteValidationPlan{return [pscustomobject]@{"
        "worker_cpu_required=$false;requires_validation=$false;static_assets=@();"
        "worker_reads=@();worker_writes=@()}};"
        "function Set-CloudflareCandidatePointer{};"
        "function Wait-CandidatePlacementPropagation{return [pscustomobject]@{"
        "passed=$true;state='READY'}};"
        "function Test-CandidateDataParity{return [pscustomobject]@{"
        "passed=$true;state='PASSED'}};"
        "function Get-CandidateAuthInspection{return [pscustomobject]@{state='PASSED'}};"
        "$ok=Retry-CandidateValidation;$final=Get-ReleaseControlState;"
        "$history=Get-Content -LiteralPath $releaseHistoryPath -Raw;"
        'Write-Output "$ok,$script:preflights,$($final.candidate.validation_state),'
        '$($final.candidate.migration_acceptance.receipt_digest),'
        '$($history.Contains(\'WORKER_CPU_HEADROOM_REVIEW_REQUIRED\')),'
        '$($history.Contains(\'CANDIDATE_VALIDATION_RETRY_REQUESTED\'))"',
    )
    assert result == (
        "True,0,PASSED,receipt,True,True"
    )


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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
    assert source.count('Invoke-WpfOperation "ApproveAccessBoundary"') == 1
    assert source.count('Invoke-GuiOperation -Operation "ApproveAccessBoundary"') == 1
    operation = source.split("function Invoke-ControlCenterOperationAction", 1)[1].split(
        "function Invoke-ControlCenterStructuredOperation", 1
    )[0]
    assert operation.count('"ApproveAccessBoundary"') == 1
    assert "Approve-CandidateAccessBoundary" in operation
    assert source.count("ALL_REQUIRED_ACCESS_CHECKS_PASSED") == 1


def test_promotion_rechecks_human_access_receipt() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
    promotion = source.split("function Start-ReleasePromotion", 1)[1].split(
        "function Complete-ReleasePromotion", 1
    )[0]
    assert '"HUMAN_ACCESS_BOUNDARY_ACCEPTED"' in promotion
    assert "Assert-AccessBoundaryAcceptanceReceipt" in promotion
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
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    for start, end in (
        ("function Update-RuntimeCheckout", "function Get-RuntimeCodeState"),
        ("function Invoke-RuntimeRollback", "function Test-CloudflareReleasePlacement"),
        ("function Invoke-ReleaseWindowsRestore", "function Invoke-ReverseStable"),
        ("function Invoke-ReverseStable", "function Complete-ReleaseReverse"),
    ):
        body = source.split(start, 1)[1].split(end, 1)[0]
        assert "Sync-StableRuntimeControlFiles" not in body


def test_release_gui_exposes_only_explicit_stable_candidate_controls() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")

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
        "$passed=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PREVIEW';"
        "$preview=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PRODUCTION_CANDIDATE';"
        "$release.candidate.validation_state='FAILED';"
        "$release.candidate.validation=[pscustomobject]@{error='Worker CPU evidence failed'};"
        "$failed=Get-ControlCenterReleasePresentation $release;"
        "$release.transaction=[pscustomobject]@{type='PROMOTE'};"
        "$busy=Get-ControlCenterReleasePresentation $release;"
        "$missing=Get-ControlCenterReleasePresentation $null;"
        "@($passed,$preview,$failed,$busy,$missing) | ConvertTo-Json -Compress",
    )

    passed, preview, failed, busy, missing = json.loads(result)
    assert passed["can_promote"] is True
    assert passed["can_reverse"] is True
    assert passed["promote_reason"] == "Ready to promote"
    assert preview["can_promote"] is False
    assert preview["promote_reason"] == "Preview cannot be promoted"
    assert failed["can_promote"] is False
    assert failed["candidate_detail"] == "Worker CPU evidence failed"
    assert failed["promote_reason"] == "Candidate failed validation"
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
