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


_PUBLICATION_CLOCK_NAMES = frozenset({
    "published", "published_at", "published_time", "source_published_time",
})
_RECEIPT_CLOCK_NAMES = frozenset({
    "received", "received_at", "first_seen", "first_seen_time",
    "collector_first_seen_time",
})
_OBSERVATION_CLOCK_NAMES = frozenset({
    "fetched", "fetched_at", "observed", "observed_at",
})


def _name_clock_origins(name: str) -> set[str]:
    if name in _PUBLICATION_CLOCK_NAMES:
        return {"publication"}
    if name in _RECEIPT_CLOCK_NAMES:
        return {"receipt"}
    if name in _OBSERVATION_CLOCK_NAMES:
        return {"observation"}
    return set()


def _clock_origins(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        return _name_clock_origins(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value == "source_published_time":
            return {"publication"}
        if node.value == "collector_first_seen_time":
            return {"receipt"}
        return set()
    if isinstance(node, (ast.Dict, ast.List, ast.Set)):
        return set()
    origins: set[str] = set()
    for child in ast.iter_child_nodes(node):
        origins.update(_clock_origins(child, aliases))
    return origins


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            if child is not scope and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                continue
            visit(child)

    visit(scope)
    return nodes


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _assigned_names(element)
        }
    return set()


def _has_explicit_skew_threshold(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(
            child.func, (ast.Name, ast.Attribute),
        ):
            name = (
                child.func.id if isinstance(child.func, ast.Name)
                else child.func.attr
            )
            if name == "timedelta":
                return True
        if isinstance(child, ast.Name) and any(
            marker in child.id.lower()
            for marker in ("clock_skew", "future_tolerance")
        ):
            return True
    return False


def _publication_receipt_comparisons(tree: ast.AST) -> list[int]:
    offenders: list[int] = []
    scopes = [tree, *(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )]
    for scope in scopes:
        nodes = _scope_nodes(scope)
        aliases: dict[str, set[str]] = {}
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in (*scope.args.posonlyargs, *scope.args.args,
                             *scope.args.kwonlyargs):
                aliases[argument.arg] = _name_clock_origins(argument.arg)
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value_origins = _clock_origins(node.value, aliases)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for name in _assigned_names(target):
                        prior = aliases.setdefault(name, set())
                        if not value_origins.issubset(prior):
                            prior.update(value_origins)
                            changed = True
        for node in nodes:
            if not isinstance(node, ast.Compare):
                continue
            origins = _clock_origins(node, aliases)
            if (
                {"publication", "receipt"}.issubset(origins)
                or (
                    {"publication", "observation"}.issubset(origins)
                    and _has_explicit_skew_threshold(node)
                )
            ):
                offenders.append(node.lineno)
    return sorted(set(offenders))


def test_publication_receipt_comparison_is_owned_by_news_time() -> None:
    """Reject aliased publication/receipt policy comparisons outside the owner."""
    offenders: list[str] = []
    for root in (ROOT / "xauusd_forecaster", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path == ROOT / "xauusd_forecaster" / "news_time.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            offenders.extend(
                f"{path.relative_to(ROOT)}:{line}"
                for line in _publication_receipt_comparisons(tree)
            )

    assert offenders == [], (
        "publication-vs-receipt admission belongs in news_time.py: "
        + ", ".join(offenders)
    )


def test_publication_receipt_guard_follows_local_aliases() -> None:
    tree = ast.parse(
        """
def bypass(row):
    published = row["source_published_time"]
    received = row["collector_first_seen_time"]
    if published > received:
        return False
    return True
"""
    )

    assert _publication_receipt_comparisons(tree) == [5]


def test_publication_receipt_guard_catches_fetched_at_tolerance() -> None:
    tree = ast.parse(
        """
def bypass(record, fetched_at):
    published = record["source_published_time"]
    if published > fetched_at + timedelta(minutes=5):
        return False
    return True
"""
    )

    assert _publication_receipt_comparisons(tree) == [4]
