"""Explicit, restart-safe cleanup of user-retired sizing/exit research."""
from __future__ import annotations
import argparse
import json
import shutil
import sqlite3
import stat
from pathlib import Path

TABLES = (
    "execution_position_scores_v2", "execution_prediction_scores_v1",
    "execution_predictions_v2", "execution_predictions_v1",
    "execution_model_updates_v2", "execution_model_updates_v1",
    "execution_training_examples_v2", "execution_training_examples_v1",
)
ARTIFACT_DIRS = ("execution-models-v1", "execution-models-v2")

def cleanup(root: Path, *, apply: bool = False) -> dict:
    root = root.resolve(strict=True)
    database = root / "forward-evidence.sqlite3"
    if database.is_symlink() or database.resolve() != database or not database.is_file():
        raise ValueError("database must be a regular direct child of runtime state")
    paths = [root / name for name in ARTIFACT_DIRS]
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        for entry in [path, *path.rglob("*")]:
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("artifact reparse paths are not permitted")
            if not entry.resolve().is_relative_to(root):
                raise ValueError("artifact escaped runtime state root")
    connection = sqlite3.connect(database, timeout=5)
    try:
        existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                  for name in TABLES if name in existing}
        files = sum(sum(p.is_file() for p in path.rglob("*")) for path in paths if path.exists())
        if apply:
            # Run after the main supervisor has stopped the old producers and
            # activated the direction-only runtime. Never run against old code.
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for name in TABLES:
                    connection.execute(f'DROP TABLE IF EXISTS {name}')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            for path in paths:
                if path.exists():
                    shutil.rmtree(path)
        return {"applied": apply, "tables": counts, "artifact_files": files}
    finally:
        connection.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-state-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(cleanup(args.runtime_state_root, apply=args.apply), sort_keys=True))
