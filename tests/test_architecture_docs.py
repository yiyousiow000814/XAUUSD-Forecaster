from pathlib import Path

from scripts.check_architecture_docs import (
    validate_codebase_paths,
    validate_markdown_links,
)


def test_architecture_document_links_and_codebase_paths_exist():
    root = Path(__file__).resolve().parents[1]

    assert validate_markdown_links(root) == []
    assert validate_codebase_paths(root) == []


def test_checker_reports_broken_link_and_explicit_repository_path(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    guide = docs / "guide.md"
    guide.write_text("[missing](missing.md)\n", encoding="utf-8")
    codebase = docs / "map.md"
    codebase.write_text("`scripts/missing.py`\n", encoding="utf-8")

    assert validate_markdown_links(tmp_path, (Path("docs/guide.md"),)) == [
        "broken link: docs/guide.md -> missing.md"
    ]
    assert validate_codebase_paths(tmp_path, Path("docs/map.md")) == [
        "missing repository path: scripts/missing.py"
    ]
