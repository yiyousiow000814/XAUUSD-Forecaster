from pathlib import Path

from scripts.check_architecture_docs import validate_architecture_docs


def test_current_architecture_documents_bind_existing_sources_and_links() -> None:
    root = Path(__file__).resolve().parents[1]

    assert validate_architecture_docs(root) == []


def test_architecture_checker_fails_closed_on_a_missing_source(tmp_path) -> None:
    docs = tmp_path / "docs"
    (docs / "contracts").mkdir(parents=True)
    (docs / "design").mkdir()
    (docs / "audits").mkdir()
    (docs / "README.md").write_text("docs\n", encoding="utf-8")
    (docs / "contracts" / "ARCHITECTURE_RULES.md").write_text(
        "`scripts/missing.py`\n", encoding="utf-8",
    )
    (docs / "design" / "SYSTEM_ARCHITECTURE.md").write_text(
        "[missing](missing.md)\n", encoding="utf-8",
    )
    (docs / "audits" / "CURRENT_MAIN_ARCHITECTURE_2026_09_01.md").write_text(
        "audit\n", encoding="utf-8",
    )

    assert validate_architecture_docs(tmp_path) == [
        "missing repository path: scripts/missing.py",
        "broken link: docs/design/SYSTEM_ARCHITECTURE.md -> missing.md",
    ]
