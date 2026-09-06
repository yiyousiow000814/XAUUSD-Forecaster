"""Local research-only filesystem authority, not a production path resolver."""
import os
from pathlib import Path
import tempfile


def research_path(value, *, roots=None):
    """Contain resolved CLI locators before use, including existing link targets.

    Caller-owned tests may supply isolated roots. CLI callers cannot supply or
    expand the roots. This is not a sandbox against the same OS user racing a
    directory replacement; do not run this research CLI with elevated privileges.
    """
    roots = roots if roots is not None else (
        Path(__file__).resolve().parents[1],
        Path.home() / "Documents" / "Codex",
        Path(tempfile.gettempdir()),
    )
    resolved = os.path.realpath(os.fspath(value))
    for root in roots:
        trusted = os.path.realpath(os.fspath(root))
        if resolved == trusted:
            return Path(trusted)
        # Canonical directory prefix including its separator, never a bare
        # string prefix (which would also admit a sibling such as root-extra).
        if resolved.startswith(trusted + os.sep):
            return Path(resolved)
    raise ValueError("OUTSIDE_OFFLINE_RESEARCH_ROOT")
