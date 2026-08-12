from pathlib import Path
import json
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]


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
    assert 'return "MARKET CLOSED"' in control_center
    assert '"MARKET CLOSED", "API OK"' in control_center
    assert '@("STOPPED", "DATA STALE", "API ERROR")' in control_center


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


def test_control_center_updates_only_the_isolated_main_runtime() -> None:
    path = ROOT / "scripts" / "xauusd_control_center.ps1"
    control_center = path.read_text(encoding="utf-8")

    assert '$reloadableServiceKeys = @("collector", "annotator", "api", "sync")' in control_center
    assert 'CODE_REVISION_RELOAD_APPLIED' in control_center
    assert 'Write-RuntimeCodeState -Revision $Revision' in control_center
    assert 'currentRevision -ne $appliedRevision' in control_center
    assert "Get-SignaledMainRevision" in control_center
    assert "Get-VerifiedOriginMain" in control_center
    assert "Test-RevisionDescendsFrom" in control_center
    assert "Test-MainCandidate" in control_center
    assert "accepted_main_revision" in control_center
    assert "Install-ProductionRuntime" in control_center
    assert 'RuntimeRoot must be separate from the development checkout' in control_center
    assert 'worktree add --detach --quiet' in control_center
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


def test_watchdog_autostart_uses_one_windowless_registration_path(tmp_path) -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")
    launcher = ROOT / "scripts" / "xauusd_watchdog_launcher.vbs"
    launcher_text = launcher.read_text(encoding="utf-8")

    assert "function Register-AutoStartTask" in control_center
    assert control_center.count("Register-ScheduledTask -TaskName $taskName") == 1
    assert '"System32\\wscript.exe"' in control_center
    assert 'Join-Path $moduleRoot "scripts\\xauusd_watchdog_launcher.vbs"' in control_center
    assert "shell.Run(command, 0, True)" in launcher_text
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


def test_main_checkpoint_accepts_squash_merge_without_accepting_stale_main(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    (repo / "value.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    (repo / "value.txt").write_text("feature", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "feature"], cwd=repo, check=True)
    feature = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    squash = subprocess.run(
        ["git", "commit-tree", tree, "-p", base, "-m", "squash"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    state = repo / ".local" / "forward" / "runtime-update-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps({"accepted_main_revision": base}), encoding="utf-8",
    )
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"$advance = Test-MainCandidate -CurrentRevision '{feature}' "
        f"-CandidateRevision '{squash}'; "
        f"$stale = Test-MainCandidate -CurrentRevision '{feature}' "
        f"-CandidateRevision '{base}'; Write-Output \"$advance,$stale\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"
