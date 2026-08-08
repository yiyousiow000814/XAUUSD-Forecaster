from pathlib import Path


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
