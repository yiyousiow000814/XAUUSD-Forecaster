import ast
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


def test_publication_receipt_comparison_is_owned_by_news_time() -> None:
    """Reject direct field-to-field time comparisons outside the owner module."""
    offenders: list[str] = []
    for root in (ROOT / "xauusd_forecaster", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path == ROOT / "xauusd_forecaster" / "news_time.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                field_names = {
                    value.value
                    for value in ast.walk(node)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                }
                if {
                    "source_published_time", "collector_first_seen_time",
                }.issubset(field_names):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}"
                    )

    assert offenders == [], (
        "publication-vs-receipt admission belongs in news_time.py: "
        + ", ".join(offenders)
    )
