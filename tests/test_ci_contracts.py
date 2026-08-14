from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_runtime_gate_tracks_its_complete_code_dependency_family() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "windows-runtime-gates.yml"
    ).read_text(encoding="utf-8")
    expected_paths = (
        ".github/workflows/**",
        "ctrader/**",
        "scripts/**",
        "xauusd_forecaster/**",
        "tests/**",
        "pyproject.toml",
    )

    for path in expected_paths:
        assert workflow.count(f'- "{path}"') == 2, path

    assert '"docs/**"' not in workflow
    assert '"web/**"' not in workflow
